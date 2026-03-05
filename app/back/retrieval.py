from sentence_transformers import SentenceTransformer
import chromadb
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")

def search(query, top_k=3, collection_name="documents"):
    """
    Recherche les documents les plus pertinents pour une query
    """
    # Charger le modèle (même que pour l'ingestion)
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # Se connecter à ChromaDB
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_collection(name=collection_name)
    
    # Générer l'embedding de la query
    query_embedding = model.encode(query)
    
    # Rechercher
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=top_k
    )
    
    return results

if __name__ == "__main__":
    # Test
    results = search("Ceci est une requête")
    print(results)