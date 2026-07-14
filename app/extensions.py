from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user
from flask_wtf import CSRFProtect


def rate_limit_key() -> str:
    """Clé de rate limiting : l'utilisateur s'il est connecté, sinon l'IP.

    Sans cela, tous les utilisateurs derrière une même IP (NAT de l'école)
    partagent le même quota.
    """
    if current_user.is_authenticated:
        return f"user:{current_user.get_id()}"
    return get_remote_address()


limiter = Limiter(rate_limit_key, default_limits=[], storage_uri="memory://")

# Protège les formulaires du panel admin : sans jeton CSRF, un admin visitant
# une page malveillante pourrait être amené à accorder les droits admin à un
# tiers à son insu. Les endpoints JSON du chat en sont exemptés (voir main.py).
csrf = CSRFProtect()
