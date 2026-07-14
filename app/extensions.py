from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user


def rate_limit_key() -> str:
    """Clé de rate limiting : l'utilisateur s'il est connecté, sinon l'IP.

    Sans cela, tous les utilisateurs derrière une même IP (NAT de l'école)
    partagent le même quota.
    """
    if current_user.is_authenticated:
        return f"user:{current_user.get_id()}"
    return get_remote_address()


limiter = Limiter(rate_limit_key, default_limits=[], storage_uri="memory://")
