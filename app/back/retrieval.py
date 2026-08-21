import logging
import math
import os
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

from .embedding import embed_query
from .reranking import normalise, rerank
from .types import SearchResult

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# Poids du score sémantique dans le score hybride (1 - ALPHA = poids fraîcheur)
FRESHNESS_ALPHA = 0.7
# Taux de décroissance : demi-vie ≈ 350 jours (un document d'un an vaut ~0.5)
DECAY_RATE = 0.002
# Pas de seuil : mesuré, il coupait la moitié des questions légitimes sans
# arrêter le bruit (voir docs/rapports.md).
SCORE_THRESHOLD: float | None = None
CANDIDATE_MULTIPLIER = 20

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    """Client Qdrant partagé (créé à la première utilisation)."""
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


def _embargo_filter() -> models.Filter:
    """Écarte les documents dont la date d'ouverture n'est pas encore passée.

    Les deux conditions sont en `should` (OU) parce que le champ est récent :
    tout ce qui a été ingéré avant lui n'en porte pas, et un `must` sur la
    borne ferait disparaître le corpus entier.
    """
    maintenant = int(datetime.now(UTC).timestamp())
    return models.Filter(
        should=[
            models.IsEmptyCondition(
                is_empty=models.PayloadField(key="visible_from_ts")
            ),
            models.FieldCondition(
                key="visible_from_ts",
                range=models.Range(lte=maintenant),
            ),
        ]
    )


def _candidates(query: str, top_k: int, collection_name: str) -> list[SearchResult]:
    """Candidats d'une formulation, du plus proche du sens au moins proche.

    Triés sur le sens seul : c'est cet ordre qui remplit la short-list du
    reranker, et la fraîcheur n'a pas à décider qui il verra. Elle est portée
    par `score`, qui sert d'ordre de repli quand le reclassement manque, et
    départage les pertinents une fois le reranker passé.
    """
    query_vector = embed_query(query)
    response = get_client().query_points(
        collection_name=collection_name,
        query=query_vector,
        query_filter=_embargo_filter(),
        limit=top_k * CANDIDATE_MULTIPLIER,
        score_threshold=SCORE_THRESHOLD,
        with_payload=True,
        with_vectors=False,
    )

    # Les scores bge-m3 se tassent dans une bande étroite (0.64 à 0.73 sur une
    # question type) là où la fraîcheur occupe tout [0, 1] : mêlés bruts, le
    # 70/30 affiché donnait en réalité près de cinq fois plus de poids à la
    # fraîcheur qu'au sens. Chaque terme est donc ramené à l'étendue du lot.
    semantiques = [point.score for point in response.points]
    fraicheurs = [
        _freshness_score((point.payload or {}).get("date", ""))
        for point in response.points
    ]
    cotes = normalise(semantiques)

    results: list[SearchResult] = []
    for point, semantic, freshness, cote in zip(
        response.points, semantiques, fraicheurs, cotes, strict=True
    ):
        payload = point.payload or {}
        hybrid = FRESHNESS_ALPHA * cote + (1 - FRESHNESS_ALPHA) * freshness

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

    results.sort(key=lambda x: x["semantic_score"], reverse=True)
    return results


def _interleave(
    first: list[SearchResult], second: list[SearchResult]
) -> list[SearchResult]:
    """Entrelace deux listes de candidats, tête à tête et sans doublon.

    Les scores de deux recherches ne se comparent pas ; l'alternance garantit
    que les deux formulations atteignent la short-list du reranker.
    """
    merged: list[SearchResult] = []
    seen: set[str] = set()
    for pair in zip_longest(first, second):
        for candidate in pair:
            if candidate is not None and candidate["point_id"] not in seen:
                seen.add(candidate["point_id"])
                merged.append(candidate)
    return merged


def search(
    query: str,
    top_k: int = 5,
    collection_name: str = "documents",
    *,
    rerank_results: bool = True,
    context_query: str | None = None,
) -> list[SearchResult]:
    """Recherche hybride (sémantique + fraîcheur) dans Qdrant, puis reclassement.

    `query` sert au reclassement, `context_query` (variante enrichie) n'ajoute
    que du rappel ; `rerank_results=False` rend l'ordre hybride seul.
    """
    results = _candidates(query, top_k, collection_name)
    if context_query and context_query != query:
        results = _interleave(
            results, _candidates(context_query, top_k, collection_name)
        )
    if rerank_results:
        return rerank(query, results, top_k)
    return results[:top_k]


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    query_test = "Sabeur Aridhi"
    logger.info("Recherche hybride pour : '%s'...", query_test)

    res = search(query_test)

    if not res:
        logger.warning("Aucun résultat (seuil : %s).", SCORE_THRESHOLD or "aucun")
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
