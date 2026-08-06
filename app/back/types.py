from typing import Literal, TypedDict

from groq.types.chat import ChatCompletionToolParam
from groq.types.chat.chat_completion_named_tool_choice_param import (
    ChatCompletionNamedToolChoiceParam,
)


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


class MapClub(TypedDict):
    """Un club tel qu'il figure sur la carte au trésor.

    `logo` porte le fichier de la plaquette quand le club en a un, sinon la
    chaîne vide : `icone` reste renseignée dans tous les cas et sert de repli.
    """

    nom: str
    tutelle: str
    icone: str
    logo: str


class MapPayload(TypedDict):
    """Charge utile validée d'une carte : le commentaire et les clubs à situer."""

    commentaire: str
    clubs: list[MapClub]


class GroqParams(TypedDict, total=False):
    """Paramètres d'appel Groq ajustables selon le prompt utilisé.

    Les types reprennent ceux du SDK Groq pour rester vérifiables au dépaquetage.
    `reasoning_format` doit valoir 'parsed' ou 'hidden' dès qu'on passe des outils :
    Groq refuse le format brut avec le tool calling.
    """

    reasoning_effort: Literal["none", "default", "low", "medium", "high"]
    reasoning_format: Literal["parsed", "hidden"]
    max_completion_tokens: int
    tools: list[ChatCompletionToolParam]
    tool_choice: ChatCompletionNamedToolChoiceParam
    parallel_tool_calls: bool
