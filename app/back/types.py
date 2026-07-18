from typing import TypedDict


class SearchResult(TypedDict):
    """Résultat d'une recherche hybride Qdrant."""

    point_id: str
    content: str
    metadata: dict
    score: float
    semantic_score: float
    freshness_score: float


class HistoryMessage(TypedDict):
    """Message de l'historique de conversation."""

    role: str
    content: str
