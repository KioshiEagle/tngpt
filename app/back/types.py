from typing import TypedDict


class SearchResult(TypedDict):
    content: str
    metadata: dict
    score: float
    semantic_score: float
    freshness_score: float


class HistoryMessage(TypedDict):
    role: str
    content: str
