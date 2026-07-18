from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user
from flask_wtf import CSRFProtect

from .back.models import USER_LIMITED

CHAT_RATE_DEFAULT = "20 per minute"
CHAT_RATE_LIMITED = "3 per minute"


def chat_rate_limit() -> str:
    """Débit autorisé sur le chat, selon le statut de modération.

    Un utilisateur « limited » garde l'accès mais voit son débit réduit : c'est
    la sanction intermédiaire entre ne rien faire et bannir.
    """
    if current_user.is_authenticated and current_user.status == USER_LIMITED:
        return CHAT_RATE_LIMITED
    return CHAT_RATE_DEFAULT


def rate_limit_key() -> str:
    """Clé de rate limiting : l'utilisateur s'il est connecté, sinon l'IP.

    Sans cela, tous les utilisateurs derrière une même IP (NAT de l'école)
    partagent le même quota.
    """
    if current_user.is_authenticated:
        return f"user:{current_user.get_id()}"
    return get_remote_address()


def api_rate_key() -> str:
    """Clé de rate limiting de l'API : le jeton Bearer présenté, sinon l'IP.

    Chaque clé a ainsi son propre budget de rafale ; sans cela, tous les scripts
    derrière une même IP partageraient la même limite.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return f"apikey:{header[7:].strip()[:16]}"
    return get_remote_address()


limiter = Limiter(rate_limit_key, default_limits=[], storage_uri="memory://")

# Protège les formulaires du panel admin : sans jeton CSRF, un admin visitant
# une page malveillante pourrait être amené à accorder les droits admin à un
# tiers à son insu. Les endpoints JSON du chat en sont exemptés (voir main.py).
csrf = CSRFProtect()
