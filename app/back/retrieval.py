import logging
import math
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from .types import SearchResult

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

model = SentenceTransformer("intfloat/multilingual-e5-small")

# Poids du score sémantique dans le score hybride (1 - ALPHA = poids fraîcheur)
FRESHNESS_ALPHA = 0.7
# Taux de décroissance : demi-vie ≈ 350 jours (un document d'un an vaut ~0.5)
DECAY_RATE = 0.002
SCORE_THRESHOLD = 0.72
CANDIDATE_MULTIPLIER = 20

_client: QdrantClient | None = None


def _get_client() -> QdrantClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = QdrantClient(
            url=os.getenv("QDRANT_URL"),
            api_key=os.getenv("QDRANT_API_KEY"),
            check_compatibility=False,
        )
    return _client


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


def search(
    query: str, top_k: int = 5, collection_name: str = "documents"
) -> list[SearchResult]:
    """Recherche hybride (sémantique + fraîcheur) dans Qdrant."""
    query_vector = model.encode(f"query: {query}").tolist()
    response = _get_client().query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k * CANDIDATE_MULTIPLIER,
        score_threshold=SCORE_THRESHOLD,
        with_payload=True,
        with_vectors=False,
    )

    results: list[SearchResult] = []
    for point in response.points:
        semantic = point.score
        payload = point.payload or {}
        freshness = _freshness_score(payload.get("date", ""))
        hybrid = FRESHNESS_ALPHA * semantic + (1 - FRESHNESS_ALPHA) * freshness

        results.append(
            SearchResult(
                point_id=str(point.id),
                content=payload.get("text", "Texte non trouvé"),
                metadata={k: v for k, v in payload.items() if k != "text"},
                score=hybrid,
                semantic_score=semantic,
                freshness_score=round(freshness, 4),
            )
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    query_test = "Sabeur Aridhi"
    logger.info("Recherche hybride pour : '%s'...", query_test)

    res = search(query_test)

    if not res:
        logger.warning(
            "Aucun résultat pertinent trouvé (Score < %.2f).", SCORE_THRESHOLD
        )
    else:
        for r in res:
            m = r["metadata"]
            title = m.get("title", m.get("source", "?"))
            logger.info(
                "%s | Auteur: %s | Date: %s | Score: %.4f "
                "(sem: %.4f, fraîcheur: %.4f)\n  Extrait: %s...",
                title,
                m.get("author", "?"),
                m.get("date", "?"),
                r["score"],
                r["semantic_score"],
                r["freshness_score"],
                r["content"][:200],
            )
