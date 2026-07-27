import math
import os

from google import genai
from google.genai import types

MODEL = "gemini-embedding-001"
DIMENSIONS = 768
BATCH_SIZE = 10
# Le SDK retente déjà seul sur 408/429/500/502/503/504 : on borne juste l'attente
# pour ne pas bloquer une requête Flask pendant plusieurs minutes.
RETRY_ATTEMPTS = 3
RETRY_MAX_DELAY = 10.0

_client: genai.Client | None = None


def get_client() -> genai.Client:
    """Client Gemini partagé (créé à la première utilisation).

    La clé est lue dans GEMINI_API_KEY, que l'appelant doit avoir chargée
    depuis .env avant le premier appel.
    """
    global _client  # noqa: PLW0603
    if _client is None:
        _client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=RETRY_ATTEMPTS, max_delay=RETRY_MAX_DELAY
                )
            ),
        )
    return _client


def _normalize(vector: list[float]) -> list[float]:
    """Normalise L2 : requis car les dimensions < 3072 ne sont pas normalisées."""
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    """Encode une liste de textes pour un task_type donné, par lots."""
    config = types.EmbedContentConfig(
        task_type=task_type,
        output_dimensionality=DIMENSIONS,
    )
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        response = get_client().models.embed_content(
            model=MODEL, contents=texts[start : start + BATCH_SIZE], config=config
        )
        vectors.extend(_normalize(e.values or []) for e in response.embeddings or [])
    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Encode des chunks destinés à l'indexation Qdrant."""
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float]:
    """Encode une question utilisateur."""
    return _embed([text], "RETRIEVAL_QUERY")[0]
