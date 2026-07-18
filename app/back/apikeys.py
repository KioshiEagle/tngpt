import hashlib
import secrets
from datetime import UTC, datetime

from .models import ApiKey, db

_KEY_PREFIX = "tngpt_"
_TOKEN_BYTES = 32
_PREFIX_DISPLAY_LEN = 14  # « tngpt_ » + 8 caractères
_BEARER = "Bearer "


def hash_key(full_key: str) -> str:
    """Hash SHA-256 d'une clé — la seule forme stockée en base."""
    return hashlib.sha256(full_key.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Fabrique une nouvelle clé.

    Retourne (clé_en_clair, hash, préfixe_affichable). La clé en clair n'existe
    qu'ici et dans la réponse à l'utilisateur : elle n'est jamais persistée.
    """
    full_key = _KEY_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    return full_key, hash_key(full_key), full_key[:_PREFIX_DISPLAY_LEN]


def authenticate(authorization_header: str | None) -> ApiKey | None:
    """Résout l'en-tête `Authorization: Bearer …` en une clé active, ou None.

    Rejette une clé absente, mal formée, inconnue, révoquée, ou dont le
    propriétaire est banni.
    """
    if not authorization_header or not authorization_header.startswith(_BEARER):
        return None

    token = authorization_header[len(_BEARER) :].strip()
    if not token:
        return None

    key = db.session.scalar(db.select(ApiKey).filter_by(key_hash=hash_key(token)))
    if key is None or not key.is_active():
        return None
    return key


def touch(key: ApiKey) -> None:
    """Enregistre l'instant de dernière utilisation d'une clé."""
    key.last_used_at = datetime.now(UTC)
    db.session.commit()
