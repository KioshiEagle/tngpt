import logging
import os
import threading
from datetime import UTC, datetime
from urllib.parse import urlsplit

from groq import (
    APIConnectionError as GroqConnectionError,
)
from groq import (
    APIStatusError as GroqStatusError,
)
from groq import (
    APITimeoutError as GroqTimeoutError,
)
from groq import Groq
from openai import (
    APIConnectionError as OpenAIConnectionError,
)
from openai import (
    APIStatusError as OpenAIStatusError,
)
from openai import (
    APITimeoutError as OpenAITimeoutError,
)
from openai import OpenAI
from sqlalchemy.exc import SQLAlchemyError

from .fournisseurs import BASE_URLS, GROQ, HOTES, fournisseur
from .models import GroqKey, db

logger = logging.getLogger(__name__)

# Un client de l'un ou l'autre SDK. Les deux sont générés par Stainless : même
# `chat.completions.create`, mêmes exceptions, donc interchangeables ici.
Client = Groq | OpenAI

# Les deux SDK ont des hiérarchies d'exceptions disjointes : une erreur DeepSeek
# n'est pas une `groq.APIStatusError`. Sans ces couples, l'échelle de repli ne
# voit passer aucune erreur venue d'un client OpenAI et tout finit en « erreur
# inattendue », backoff 429 et réduction de contexte compris.
ERREURS_STATUT = (GroqStatusError, OpenAIStatusError)
ERREURS_TIMEOUT = (GroqTimeoutError, OpenAITimeoutError)
ERREURS_CONNEXION = (GroqConnectionError, OpenAIConnectionError)

# Une erreur portant un statut HTTP, de l'un ou l'autre SDK.
ErreurStatut = GroqStatusError | OpenAIStatusError

# Clients réutilisés, indexés par id de clé (et None pour la clé de repli).
_clients: dict[int | None, Client] = {}
_lock = threading.Lock()


# Le SDK retente de lui-même, et sur un 429 il obéit à `retry-after` jusqu'à
# 60 secondes : une attente pareille bloque le worker gunicorn, unique, qui se
# fait tuer à 30 s en plein streaming. L'échelle de repli de `generate` prend
# donc la main, avec un plafond d'attente tenable.
_MAX_RETRIES = 0


def _construire(secret: str) -> Client:
    """Client du fournisseur reconnu au préfixe du secret.

    Un préfixe inconnu retombe sur Groq : c'est le fournisseur historique du
    pool, et l'avertissement est déjà émis par `fournisseur`.
    """
    nom = fournisseur(secret)
    if nom is None or nom == GROQ:
        return Groq(api_key=secret, max_retries=_MAX_RETRIES)
    return OpenAI(api_key=secret, base_url=BASE_URLS[nom], max_retries=_MAX_RETRIES)


def fournisseur_du_client(client: Client) -> str:
    """Fournisseur vers lequel un client déjà construit enverra ses requêtes.

    Lu sur l'hôte du client plutôt que porté de main en main : c'est lui qui
    décide réellement de la destination. Défaut Groq, fournisseur historique.
    """
    hote = urlsplit(str(getattr(client, "base_url", ""))).hostname or ""
    return HOTES.get(hote, GROQ)


def _client_for(cache_id: int | None, secret: str) -> Client:
    """Retourne un client mémoïsé pour un secret donné."""
    with _lock:
        client = _clients.get(cache_id)
        if client is None:
            client = _construire(secret)
            _clients[cache_id] = client
        return client


def acquire() -> tuple[Client, int | None]:
    """Retourne (client, id de clé) en répartissant la charge sur le pool.

    Round-robin par horodatage, donc cohérent entre workers ; repli sur
    GROQ_API_KEY et id None quand le pool est vide ou hors contexte applicatif.
    """
    key = None
    try:
        key = db.session.scalars(
            db.select(GroqKey)
            .where(GroqKey.active.is_(True))
            .order_by(GroqKey.last_used_at.asc().nullsfirst())
        ).first()
    except (SQLAlchemyError, RuntimeError):
        # RuntimeError : appel hors contexte applicatif (pas de session).
        key = None

    if key is not None:
        key.last_used_at = datetime.now(UTC)
        key.request_count = (key.request_count or 0) + 1
        db.session.commit()
        return _client_for(key.groq_key_id, key.secret), key.groq_key_id

    return _client_for(None, os.getenv("GROQ_API_KEY", "")), None
