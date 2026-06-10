import os

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

load_dotenv()


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
            size=384,  # Dimension pour all-MiniLM-L6-v2
            distance=models.Distance.COSINE,
        ),
    )

    # Nettoyage du fichier log local
    log_file = "app/back/processed_files.json"
    if os.path.exists(log_file):
        os.remove(log_file)
        print(f"Fichier {log_file} supprimé pour forcer la ré-ingestion.")

    print("Base de données prête pour une nouvelle ingestion.")


if __name__ == "__main__":
    confirm = input("Êtes-tu sûr de vouloir tout supprimer dans Qdrant ? (y/n) : ")
    if confirm.lower() == "y":
        reset_qdrant()
    else:
        print("Opération annulée.")
