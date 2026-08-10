import logging
import math
import os
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from .embedding import embed_query
from .reranking import rerank
from .types import SearchResult

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# Poids du score sémantique dans le score hybride (1 - ALPHA = poids fraîcheur)
FRESHNESS_ALPHA = 0.7
# Taux de décroissance : demi-vie ≈ 350 jours (un document d'un an vaut ~0.5)
DECAY_RATE = 0.002
# Plus de seuil de similarité. Le 0.5 qui vivait ici avait été calibré sur les
# embeddings Gemini et n'a jamais été remesuré après le passage à bge-m3 — le
# commentaire d'alors le réclamait déjà.
#
# Remesuré sur 10 questions dont on sait quel chunk doit remonter :
#
#     seuil   cible atteinte   bruit servi
#     0.5          5/10            3.3
#     0.4          7/10            4.0
#     0.3          8/10            5.0
#     aucun        8/10            5.0
#
# Il coupait donc la moitié des questions légitimes — toutes celles qui portent
# sur une personne, un nom propre étant un signal trop court face à des chunks
# de prose — sans arrêter le bruit pour autant : « comment tricoter une écharpe »
# score 0.627, au-dessus de 8 des 10 questions valides. Les deux populations se
# recouvrent, aucun seuil ne les sépare.
#
# Le tri de pertinence revient au reranker (voir `reranking.py`) et le refus du
# hors-sujet au prompt, qui s'en acquitte déjà. 0.3 donnant exactement le même
# résultat qu'aucun seuil, on garde la forme la plus honnête : pas de seuil.
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


def _candidates(query: str, top_k: int, collection_name: str) -> list[SearchResult]:
    """Candidats hybrides pour une formulation, du meilleur au moins bon."""
    query_vector = embed_query(query)
    response = get_client().query_points(
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
    return results


def _interleave(
    first: list[SearchResult], second: list[SearchResult]
) -> list[SearchResult]:
    """Entrelace deux listes de candidats, tête à tête et sans doublon.

    Les scores de deux recherches ne se comparent pas : ils mesurent la distance
    à deux vecteurs différents. Les fusionner par tri laisserait la formulation
    la mieux notée occuper à elle seule la short-list du reranker, qui n'en
    retient que les vingt premiers. L'alternance garantit que les deux y sont.
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

    `query` est la question telle qu'elle a été posée : c'est elle, et non une
    reformulation, qui sert au reclassement. `context_query` est la variante
    enrichie des tours précédents ; quand elle diffère, elle est cherchée en
    plus et ses candidats sont entrelacés aux premiers. Une question qui tient
    debout seule retrouve ainsi ses chunks même après dix tours sur un autre
    sujet, et une question de suite garde le contexte qui la rend lisible.

    `rerank_results=False` rend l'ordre hybride seul. La carte au trésor s'en
    sert : elle enchaîne une dizaine de recherches pour couvrir tous les clubs,
    y reclasser chacune ferait autant d'appels API pour un résultat qu'elle
    déduplique et retrie ensuite de toute façon.
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
