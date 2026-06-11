import os
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR.parent.parent / ".env")


def reset_qdrant() -> None:
    """Supprime et recrée la collection pour un nouveau départ."""
    client = QdrantClient(
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY"),
    )

    collection_name = "documents"

    if client.collection_exists(collection_name):
        print(f"Suppression de la collection '{collection_name}'...")
        client.delete_collection(collection_name=collection_name)

    print(f"Création d'une collection '{collection_name}' toute neuve...")
    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=384,
            distance=models.Distance.COSINE
        )
    )

    log_file = BASE_DIR / "processed_files.json"
    if log_file.exists():
        log_file.unlink()
        print(f"Fichier {log_file.name} supprimé pour forcer la ré-ingestion.")

    print("Base de données prête pour une nouvelle ingestion.")


if __name__ == "__main__":
    confirm = input("Êtes-tu sûr de vouloir tout supprimer dans Qdrant ? (y/n) : ")
    if confirm.lower() == "y":
        reset_qdrant()
    else:
        print("Opération annulée.")
