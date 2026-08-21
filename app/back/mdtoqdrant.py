import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient, models

from .chunking import get_hybrid_chunks
from .embedding import embed_documents

# Bumper cette version force la re-ingestion de tous les documents
_CHUNK_VERSION = "v5-clean"

# Documents sous embargo : `visible_from` dans le frontmatter (AAAA-MM-JJ) fixe
# la date d'ouverture, filtrée à la recherche (voir retrieval._embargo_filter).
# 0 = visible tout de suite, valeur des documents qui n'en portent pas.
NO_EMBARGO = 0


def embargo_timestamp(visible_from: str) -> int:
    """Convertit une date d'ouverture AAAA-MM-JJ en secondes Unix, 0 si absente.

    Une date illisible vaut un embargo absent : la recherche verrait sinon un
    document indisponible pour toujours, sans rien pour le signaler.
    """
    if not visible_from:
        return NO_EMBARGO
    try:
        jour = datetime.fromisoformat(visible_from)
    except ValueError:
        print(f"⚠️  visible_from illisible ({visible_from!r}) : embargo ignoré.")
        return NO_EMBARGO
    if jour.tzinfo is None:
        jour = jour.replace(tzinfo=UTC)
    return int(jour.timestamp())


def _file_hash(content: str) -> str:
    """Hash du contenu incluant la version de chunking pour forcer la re-ingestion."""
    return hashlib.sha256((content + _CHUNK_VERSION).encode()).hexdigest()


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse le YAML frontmatter et retourne (metadata, body)."""
    match = re.match(r"^---\n(.*?)\n---\n\n?", content, re.DOTALL)
    if not match:
        return {}, content
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
    return meta, content[match.end() :]


@dataclass
class IngestResult:
    """Métadonnées d'un document ingéré dans Qdrant."""

    source_id: str
    title: str
    date: str
    author: str
    chunk_count: int
    file_hash: str
    # Secondes Unix avant lesquelles le document reste hors des recherches.
    visible_from_ts: int = NO_EMBARGO


class VectorStore:
    """Gestionnaire de la base vectorielle Qdrant."""

    def __init__(self, url: str, api_key: str) -> None:
        """Initialise la connexion à Qdrant et les index de payload."""
        self.client = QdrantClient(url=url, api_key=api_key, timeout=60)
        self.collection = "documents"
        self.client.create_payload_index(
            collection_name=self.collection,
            field_name="source",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        self.client.create_payload_index(
            collection_name=self.collection,
            field_name="date",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        self.client.create_payload_index(
            collection_name=self.collection,
            field_name="visible_from_ts",
            field_schema=models.PayloadSchemaType.INTEGER,
        )

    def delete_source(self, source_id: str) -> None:
        """Supprime de Qdrant tous les chunks d'un document source."""
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source",
                            match=models.MatchValue(value=source_id),
                        )
                    ]
                )
            ),
        )

    def upload_file(self, md_path: Path) -> IngestResult | None:
        """Ingère un fichier Markdown dans Qdrant, ou None si le fichier est vide.

        Chaque chunk est préfixé de sa date et de son titre avant encodage, et
        les chunks du même document sont purgés d'abord : ré-ingérer remplace.
        """
        source_id = md_path.stem
        with md_path.open(encoding="utf-8") as f:
            content = f.read()

        meta, body = _parse_frontmatter(content)
        title = meta.get("title", source_id)
        date = meta.get("date", "")
        author = meta.get("author", "Inconnu")
        visible_from_ts = embargo_timestamp(meta.get("visible_from", ""))

        print(f"📤 Ingestion sémantique : {title or source_id}")
        chunks = get_hybrid_chunks(body, chunk_size=800, chunk_overlap=240)
        if not chunks:
            return None

        self.delete_source(source_id)

        date_prefix = f"[Date: {date}] " if date else ""
        title_prefix = f"[Source: {title}] " if title else ""
        prefixed_chunks = [f"{date_prefix}{title_prefix}{c}" for c in chunks]
        embeddings = embed_documents(prefixed_chunks)

        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{source_id}_{i}")),
                vector=embedding,
                payload={
                    "text": text,
                    "source": source_id,
                    "title": title,
                    "date": date,
                    "author": author,
                    "visible_from_ts": visible_from_ts,
                },
            )
            for i, (text, embedding) in enumerate(zip(chunks, embeddings, strict=False))
        ]

        batch_size = 50
        for i in range(0, len(points), batch_size):
            self.client.upsert(
                collection_name=self.collection,
                points=points[i : i + batch_size],
            )

        return IngestResult(
            source_id=source_id,
            title=title,
            date=date,
            author=author,
            chunk_count=len(chunks),
            file_hash=_file_hash(content),
            visible_from_ts=visible_from_ts,
        )

    def upload_directory(self, md_dir: Path, log_file: Path) -> None:
        """Ingère les fichiers Markdown d'un dossier dans Qdrant.

        Seuls les fichiers modifiés (hash différent) sont ré-ingérés.
        """
        raw = json.loads(Path(log_file).read_text()) if Path(log_file).exists() else {}
        processed = dict.fromkeys(raw) if isinstance(raw, list) else raw

        for md_path in Path(md_dir).glob("*.md"):
            source_id = md_path.stem
            current_hash = _file_hash(md_path.read_text(encoding="utf-8"))
            if processed.get(source_id) == current_hash:
                print(f"⏭️  Déjà à jour : {source_id}")
                continue

            result = self.upload_file(md_path)
            if result is None:
                continue

            processed[source_id] = result.file_hash
            # Sauvegarde après chaque document : une interruption ne coûte que
            # le document en cours, pas les appels d'embedding déjà payés.
            with Path(log_file).open("w") as f:
                json.dump(processed, f)


if __name__ == "__main__":
    from dotenv import load_dotenv

    BASE_DIR = Path(__file__).parent.resolve()
    load_dotenv(BASE_DIR.parent.parent / ".env")

    TEMP_MD = BASE_DIR / "temp/markdowns"
    logfile = BASE_DIR / "processed_files.json"

    vs = VectorStore(os.getenv("QDRANT_URL", ""), os.getenv("QDRANT_API_KEY", ""))
    vs.upload_directory(TEMP_MD, logfile)
    print("✅ MD -> Qdrant terminé.")
