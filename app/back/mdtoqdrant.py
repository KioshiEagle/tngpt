import json
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

from .chunking import get_hybrid_chunks  # Utilise bien le nom de ta fonction hybride


class VectorStore:
    """Gestionnaire d'ingestion vectorielle vers Qdrant."""

    def __init__(self, url: str, api_key: str) -> None:
        """Initialise la connexion à Qdrant et charge le modèle.

        Args:
            url (str): L'URL du cluster Qdrant.
            api_key (str): La clé d'API Qdrant.

        """
        self.client = QdrantClient(url=url, api_key=api_key)
        # Passage au modèle multilingue E5
        self.model = SentenceTransformer("intfloat/multilingual-e5-small")
        self.collection = "documents"

    def upload_directory(self, md_dir: Path, log_file: Path) -> None:
        """Pousse les fichiers markdown vers Qdrant en évitant les doublons.

        Args:
            md_dir (Path): Le dossier source contenant les .md
            log_file (Path): Le fichier de log d'ingestion

        """
        if log_file.exists():
            with log_file.open() as f:
                processed = set(json.load(f))
        else:
            processed = set()

        for md_path in Path(md_dir).glob("*.md"):
            drive_id = md_path.stem
            if drive_id in processed:
                continue

            print(f"📤 Ingestion sémantique (E5) : {drive_id}")
            with md_path.open(encoding="utf-8") as f:
                content = f.read()

            # Utilisation de ta méthode hybride validée par le benchmark
            chunks = get_hybrid_chunks(content, chunk_size=800, chunk_overlap=240)

            if chunks:
                # IMPORTANT : E5 demande le préfixe "passage: " pour l'indexation
                prefixed_chunks = [f"passage: {c}" for c in chunks]
                embs = self.model.encode(prefixed_chunks).tolist()

                points = [
                    models.PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{drive_id}_{i}")),
                        vector=emb,
                        payload={
                            "text": txt,  # Texte avec préfixe de contexte [Header]
                            "source": drive_id,
                        },
                    )
                    for i, (txt, emb) in enumerate(zip(chunks, embs, strict=False))
                ]

                self.client.upsert(collection_name=self.collection, points=points)
                processed.add(drive_id)

        with log_file.open("w") as f:
            json.dump(list(processed), f)
