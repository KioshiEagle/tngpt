import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

from .chunking import get_hybrid_chunks


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


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


class VectorStore:
    """Gestionnaire de la base vectorielle Qdrant."""

    def __init__(self, url: str, api_key: str) -> None:
        """Initialise la connexion à Qdrant."""
        self.client = QdrantClient(url=url, api_key=api_key, timeout=60)
        self.model = SentenceTransformer("intfloat/multilingual-e5-small")
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

    def upload_directory(self, md_dir: Path, log_file: Path) -> None:
        """Ingère les fichiers Markdown d'un dossier dans Qdrant."""
        raw = json.loads(Path(log_file).read_text()) if Path(log_file).exists() else {}
        processed = dict.fromkeys(raw) if isinstance(raw, list) else raw

        for md_path in Path(md_dir).glob("*.md"):
            drive_id = md_path.stem
            with md_path.open(encoding="utf-8") as f:
                content = f.read()

            current_hash = _file_hash(content)
            if processed.get(drive_id) == current_hash:
                print(f"⏭️  Déjà à jour : {drive_id}")
                continue

            meta, body = _parse_frontmatter(content)
            title = meta.get("title", drive_id)
            date = meta.get("date", "")
            author = meta.get("author", "Inconnu")

            print(f"📤 Ingestion sémantique (E5) : {title or drive_id}")
            chunks = get_hybrid_chunks(body, chunk_size=800, chunk_overlap=240)

            if chunks:
                self.client.delete(
                    collection_name=self.collection,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="source",
                                    match=models.MatchValue(value=drive_id),
                                )
                            ]
                        )
                    ),
                )

                prefixed_chunks = [f"passage: {c}" for c in chunks]
                embs = self.model.encode(prefixed_chunks).tolist()

                points = [
                    models.PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{drive_id}_{i}")),
                        vector=emb,
                        payload={
                            "text": txt,
                            "source": drive_id,
                            "title": title,
                            "date": date,
                            "author": author,
                        },
                    )
                    for i, (txt, emb) in enumerate(zip(chunks, embs, strict=False))
                ]

                batch_size = 50
                for i in range(0, len(points), batch_size):
                    self.client.upsert(
                        collection_name=self.collection,
                        points=points[i : i + batch_size],
                    )

                processed[drive_id] = current_hash

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
