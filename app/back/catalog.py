import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Flask

from .clubs import load_catalogue
from .groqpool import acquire
from .mailtomd import convert_file as convert_mail
from .mdtoqdrant import VectorStore
from .models import (
    DOC_FAILED,
    DOC_INDEXED,
    DOC_INDEXING,
    DOC_MISSING,
    DOC_ORIGIN_DRIVE,
    Document,
    db,
)
from .pdftomd import DocumentProcessor
from .retrieval import get_client

logger = logging.getLogger(__name__)

COLLECTION = "documents"
_SCROLL_BATCH = 256

# Au-delà de ce délai, une ingestion « en cours » est considérée comme morte
# (redémarrage du serveur pendant le traitement).
_STALE_INGESTION = timedelta(minutes=30)

_vector_store: VectorStore | None = None
_vector_store_lock = threading.Lock()

# Un dépôt groupé lance un thread par fichier : sans ce sémaphore, deux cents
# ingestions simultanées noieraient Workers AI sous les 429.
_INGESTIONS = threading.BoundedSemaphore(3)


def get_vector_store() -> VectorStore:
    """VectorStore partagé, créé à la première ingestion.

    Instancié paresseusement : sa création ouvre une connexion Qdrant et crée
    les index de payload, inutile tant qu'on n'ingère rien.
    """
    global _vector_store  # noqa: PLW0603
    with _vector_store_lock:
        if _vector_store is None:
            _vector_store = VectorStore(
                os.getenv("QDRANT_URL", ""), os.getenv("QDRANT_API_KEY", "")
            )
        return _vector_store


_MAX_SOURCE = 128
_MAX_TITLE = 300
_MAX_AUTHOR = 200
_MAX_DATE = 50


def _truncate(value: object, length: int) -> str | None:
    """Tronque une métadonnée Qdrant à la taille de sa colonne."""
    if not value:
        return None
    return str(value)[:length]


@dataclass
class QdrantDocument:
    """Agrégat des chunks d'un même document source dans Qdrant."""

    source_id: str
    title: str | None = None
    author: str | None = None
    doc_date: str | None = None
    chunk_count: int = 0


def scan_qdrant() -> dict[str, QdrantDocument]:
    """Parcourt la collection Qdrant et agrège les chunks par document source.

    Qdrant ne sait pas répondre à « quels documents contiens-tu ? » : il stocke
    des chunks. On les parcourt donc pour reconstituer la liste des sources.
    """
    client = get_client()
    documents: dict[str, QdrantDocument] = {}
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            limit=_SCROLL_BATCH,
            offset=offset,
            with_payload=["source", "title", "author", "date"],
            with_vectors=False,
        )

        for point in points:
            payload = point.payload or {}
            source_id = _truncate(payload.get("source"), _MAX_SOURCE)
            if not source_id:
                continue

            document = documents.get(source_id)
            if document is None:
                document = QdrantDocument(
                    source_id=source_id,
                    title=_truncate(payload.get("title"), _MAX_TITLE),
                    author=_truncate(payload.get("author"), _MAX_AUTHOR),
                    doc_date=_truncate(payload.get("date"), _MAX_DATE),
                )
                documents[source_id] = document

            document.chunk_count += 1

        if offset is None:
            break

    return documents


def sync_from_qdrant() -> dict[str, int]:
    """Réconcilie le catalogue avec l'état réel de Qdrant.

    Pour l'amorçage — Qdrant connaît déjà les documents de la pipeline Drive —
    et le rattrapage d'une désynchronisation.
    """
    scanned = scan_qdrant()
    existing = {
        document.source_id: document
        for document in db.session.scalars(db.select(Document))
    }

    added = 0
    updated = 0
    missing = 0

    for source_id, qdrant_document in scanned.items():
        document = existing.get(source_id)
        if document is None:
            document = Document(source_id=source_id, origin=DOC_ORIGIN_DRIVE)
            db.session.add(document)
            added += 1
        else:
            updated += 1

        document.title = qdrant_document.title
        document.author = qdrant_document.author
        document.doc_date = qdrant_document.doc_date
        document.chunk_count = qdrant_document.chunk_count
        document.status = DOC_INDEXED
        document.error = None

    # Catalogués mais introuvables dans Qdrant : signalés plutôt que supprimés,
    # pour que la désynchronisation reste visible. Hors ingestion en cours.
    for source_id, document in existing.items():
        if source_id not in scanned and document.status != DOC_INDEXING:
            document.status = DOC_MISSING
            document.chunk_count = 0
            missing += 1

    db.session.commit()

    logger.info(
        "Catalogue synchronisé : %d ajoutés, %d mis à jour, %d manquants",
        added,
        updated,
        missing,
    )
    return {
        "added": added,
        "updated": updated,
        "missing": missing,
        "total": len(scanned),
    }


def reset_stale_ingestions() -> int:
    """Marque en échec les ingestions figées par un redémarrage du serveur.

    Rattrapé à la consultation et non au démarrage, pour ne pas toucher la base
    à l'import — `flask db upgrade` court avant que les tables n'existent.
    """
    deadline = datetime.now(UTC) - _STALE_INGESTION
    stale = db.session.scalars(
        db.select(Document).where(
            Document.status == DOC_INDEXING, Document.updated_at < deadline
        )
    ).all()

    for document in stale:
        document.status = DOC_FAILED
        document.error = "Ingestion interrompue (redémarrage du serveur)."

    if stale:
        db.session.commit()
        logger.warning("%d ingestion(s) figée(s) marquée(s) en échec", len(stale))

    return len(stale)


def _ingest_worker(app: Flask, source_id: str, stored_path: Path) -> None:
    """Convertit puis ingère un fichier déposé. Exécuté hors du cycle HTTP.

    Toute erreur est consignée sur le document (statut `failed` + message) au
    lieu de remonter : personne n'attend cette exception, le thread est détaché.
    """
    with _INGESTIONS, app.app_context():
        document = db.session.get(Document, source_id)
        if document is None:
            logger.error("Ingestion de %s : document introuvable", source_id)
            return

        try:
            markdown_path = stored_path
            suffix = stored_path.suffix.lower()
            if suffix == ".pdf":
                # Client d'une clé du pool : l'extraction de métadonnées d'un lot
                # de PDF ne doit pas saturer une seule clé Groq.
                client, _ = acquire()
                markdown_path = DocumentProcessor().convert_file(
                    stored_path, stored_path.parent, client=client
                )
            elif suffix == ".eml":
                # Aucun appel à Groq : clubs et résumé se lisent au motif,
                # et un résumé inventé serait plus nuisible qu'absent.
                catalogue, _ = load_catalogue()
                markdown_path = convert_mail(stored_path, stored_path.parent, catalogue)

            result = get_vector_store().upload_file(markdown_path)
            if result is None:
                msg = "Le document ne produit aucun contenu exploitable."
                raise ValueError(msg)  # noqa: TRY301

            document.title = result.title or None
            document.author = result.author or None
            document.doc_date = result.date or None
            document.chunk_count = result.chunk_count
            document.file_hash = result.file_hash
            document.status = DOC_INDEXED
            document.error = None
            logger.info(
                "Ingestion réussie : %s (%d chunks)", source_id, result.chunk_count
            )

        except Exception as error:
            logger.exception("Échec de l'ingestion de %s", source_id)
            document.status = DOC_FAILED
            document.error = str(error)[:500]

        finally:
            # Les fichiers intermédiaires ne servent qu'à l'ingestion : le contenu
            # vit désormais dans Qdrant.
            for path in {stored_path, stored_path.with_suffix(".md")}:
                path.unlink(missing_ok=True)
            db.session.commit()


def start_ingestion(app: Flask, source_id: str, stored_path: Path) -> None:
    """Lance l'ingestion d'un fichier en tâche de fond.

    Convertir un PDF puis calculer ses embeddings prend des dizaines de secondes :
    impossible dans le cycle d'une requête HTTP sans faire expirer le navigateur.
    """
    thread = threading.Thread(
        target=_ingest_worker,
        args=(app, source_id, stored_path),
        daemon=True,
        name=f"ingest-{source_id}",
    )
    thread.start()


def delete_document(source_id: str) -> bool:
    """Supprime un document de Qdrant puis du catalogue.

    Qdrant d'abord : un échec laisse la ligne au catalogue, incohérence visible,
    plutôt que des chunks orphelins interrogeables et invisibles.
    """
    document = db.session.get(Document, source_id)
    if document is None:
        return False

    get_vector_store().delete_source(source_id)
    db.session.delete(document)
    db.session.commit()

    logger.info("Document supprimé : %r", source_id)
    return True
