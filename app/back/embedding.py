import logging
import math
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Seul modèle d'embedding multilingue du catalogue Workers AI ; les bge-*-en-v1.5
# sont anglophones et inexploitables sur un corpus français.
MODEL = "@cf/baai/bge-m3"
DIMENSIONS = 1024
BATCH_SIZE = 100
TIMEOUT = 60.0
# httpx ne retente rien de lui-même : on gère 429 et 5xx ici.
RETRY_ATTEMPTS = 3
RETRY_INITIAL_DELAY = 1.0
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    """Client HTTP Workers AI partagé (créé à la première utilisation).

    CLOUDFLARE_ACCOUNT_ID et CLOUDFLARE_API_TOKEN doivent avoir été chargés
    depuis .env avant le premier appel.
    """
    global _client  # noqa: PLW0603
    if _client is None:
        account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        _client = httpx.Client(
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/",
            headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"},
            timeout=TIMEOUT,
        )
    return _client


def _normalize(vector: list[float]) -> list[float]:
    """Normalise L2 : rend la similarité cosinus bien définie côté Qdrant."""
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def _post(batch: list[str]) -> dict[str, Any]:
    """Appelle Workers AI sur un lot, avec backoff sur 429 et erreurs serveur."""
    delay = RETRY_INITIAL_DELAY
    for attempt in range(RETRY_ATTEMPTS):
        response = get_client().post(MODEL, json={"text": batch})
        if response.status_code in _RETRY_STATUS and attempt < RETRY_ATTEMPTS - 1:
            logger.warning(
                "Workers AI a répondu %s, nouvel essai dans %ss",
                response.status_code,
                delay,
            )
            time.sleep(delay)
            delay *= 2
            continue
        response.raise_for_status()
        return response.json()
    raise httpx.HTTPError(f"Workers AI injoignable après {RETRY_ATTEMPTS} tentatives")


def _vectors(payload: dict[str, Any]) -> list[list[float]]:
    """Extrait les vecteurs de la réponse Workers AI."""
    if not payload.get("success", False):
        raise httpx.HTTPError(f"Workers AI : {payload.get('errors')}")
    result = payload["result"]
    return result["data"] if "data" in result else result["response"]


def _embed(texts: list[str]) -> list[list[float]]:
    """Encode une liste de textes, par lots."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        payload = _post(texts[start : start + BATCH_SIZE])
        vectors.extend(_normalize(v) for v in _vectors(payload))
    return vectors


# bge-m3 est symétrique : contrairement à E5 ("query:"/"passage:") ou à Gemini
# (task_type), requêtes et documents s'encodent de la même façon. Les deux
# fonctions restent séparées pour garder l'intention lisible aux appels.
def embed_documents(texts: list[str]) -> list[list[float]]:
    """Encode des chunks destinés à l'indexation Qdrant."""
    return _embed(texts)


def embed_query(text: str) -> list[float]:
    """Encode une question utilisateur."""
    return _embed([text])[0]
