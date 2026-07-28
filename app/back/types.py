from typing import Literal, TypedDict


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


class GroqParams(TypedDict, total=False):
    """Paramètres d'appel Groq ajustables selon le prompt utilisé.

    Les types reprennent ceux du SDK Groq pour rester vérifiables au dépaquetage.
    """

    reasoning_effort: Literal["none", "default", "low", "medium", "high"]
    max_completion_tokens: int
