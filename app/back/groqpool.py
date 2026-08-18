import logging
import os
import threading
from datetime import UTC, datetime

from groq import Groq
from sqlalchemy.exc import SQLAlchemyError

from .models import GroqKey, db

logger = logging.getLogger(__name__)

# Clients Groq réutilisés, indexés par id de clé (et None pour la clé de repli).
_clients: dict[int | None, Groq] = {}
_lock = threading.Lock()


def _client_for(cache_id: int | None, secret: str) -> Groq:
    """Retourne un client Groq mémoïsé pour un secret donné."""
    with _lock:
        client = _clients.get(cache_id)
        if client is None:
            client = Groq(api_key=secret)
            _clients[cache_id] = client
        return client


def acquire() -> tuple[Groq, int | None]:
    """Retourne (client Groq, id de clé) en répartissant la charge sur le pool.

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
