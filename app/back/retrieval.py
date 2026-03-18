import os
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

def search(query, top_k=5, collection_name="documents"):
    """
    Recherche sémantique optimisée pour le français avec E5.
    """
    client = QdrantClient(
        url=os.getenv("QDRANT_URL"), 
        api_key=os.getenv("QDRANT_API_KEY")
    )
    
    # Même modèle que pour l'ingestion
    model = SentenceTransformer('intfloat/multilingual-e5-small')
    
    # IMPORTANT : E5 demande le préfixe "query: " pour la recherche
    query_with_prefix = f"query: {query}"
    query_vector = model.encode(query_with_prefix).tolist()
    
    # Recherche avec un seuil de score pour filtrer le bruit
    response = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
        score_threshold=0.75, # Seuil de qualité (E5 donne des scores plus élevés)
        with_payload=True,
        with_vectors=False
    )
    
    formatted_results = []
    for res in response.points:
        formatted_results.append({
            "content": res.payload.get("text", "Texte non trouvé"),
            "metadata": {k: v for k, v in res.payload.items() if k != "text"},
            "score": res.score
        })
    
    return formatted_results

if __name__ == "__main__":
    query_test = "Sabeur Aridhi"
    print(f"🔍 Recherche sémantique E5 pour : '{query_test}'...")
    
    res = search(query_test)
    
    if not res:
        print("⚠️ Aucun résultat pertinent trouvé (Score < 0.75).")
    else:
        for r in res:
            source = r['metadata'].get('source', 'Inconnue')
            print(f"\n📄 Source: {source} | 📊 Score: {r['score']:.4f}")
            print(f"📝 Extrait: {r['content'][:200]}...")