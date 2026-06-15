import math
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
model = SentenceTransformer("intfloat/multilingual-e5-small")

# Poids du score sémantique dans le score hybride (1 - ALPHA = poids fraîcheur)
FRESHNESS_ALPHA = 0.7
# Taux de décroissance : demi-vie ≈ 350 jours (un document d'un an vaut ~0.5)
DECAY_RATE = 0.002


def _freshness_score(date_str: str) -> float:
    """Score de fraîcheur entre 0 et 1 via décroissance exponentielle.

    Un document sans date reçoit 0.5 (neutre).
    """
    if not date_str:
        return 0.5
    try:
        doc_date = datetime.fromisoformat(date_str)
        if doc_date.tzinfo is None:
            doc_date = doc_date.replace(tzinfo=UTC)
        age_days = max((datetime.now(UTC) - doc_date).days, 0)
        return math.exp(-DECAY_RATE * age_days)
    except ValueError:
        return 0.5


def search(query: str, top_k: int = 5, collection_name: str = "documents") -> list:
    """Recherche hybride (sémantique + fraîcheur) dans Qdrant.

    Le pool de candidats est volontairement large (top_k * 20) pour ne pas
    écarter des documents pertinents avant la re-notation par fraîcheur.
    Retourne jusqu'à `top_k` résultats triés par score hybride décroissant.
    """
    client = QdrantClient(
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY"),
        check_compatibility=False,
    )

    query_vector = model.encode(f"query: {query}").tolist()
    response = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k * 20,
        score_threshold=0.72,
        with_payload=True,
        with_vectors=False,
    )

    results = []
    for point in response.points:
        semantic = point.score
        payload = point.payload or {}
        freshness = _freshness_score(payload.get("date", ""))
        hybrid = FRESHNESS_ALPHA * semantic + (1 - FRESHNESS_ALPHA) * freshness

        results.append(
            {
                "content": payload.get("text", "Texte non trouvé"),
                "metadata": {k: v for k, v in payload.items() if k != "text"},
                "score": hybrid,
                "semantic_score": semantic,
                "freshness_score": round(freshness, 4),
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


if __name__ == "__main__":
    query_test = "Sabeur Aridhi"
    print(f"🔍 Recherche hybride pour : '{query_test}'...")

    res = search(query_test)

    if not res:
        print("⚠️ Aucun résultat pertinent trouvé (Score < 0.72).")
    else:
        for r in res:
            m = r["metadata"]
            title = m.get("title", m.get("source", "?"))
            print(
                f"\n📄 {title} | Auteur: {m.get('author', '?')} "
                f"| Date: {m.get('date', '?')}"
            )
            print(
                f"   Score hybride: {r['score']:.4f}  (sémantique: "
                f"{r['semantic_score']:.4f}, fraîcheur: {r['freshness_score']:.4f})"
            )
            print(f"   Extrait: {r['content'][:200]}...")
