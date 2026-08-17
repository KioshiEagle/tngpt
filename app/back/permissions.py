from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, Protocol, TypeVar
from werkzeug.urls import url_parse

from flask import flash, redirect, request, url_for
from flask_login import LoginManager, current_user
from werkzeug.wrappers import Response

login_manager = LoginManager()

P = ParamSpec("P")
R = TypeVar("R")


class HasPermissions(Protocol):
    """Objet exposant l'entier de permissions d'un utilisateur."""

    user_permissions: int


PERM_ADMIN = 0
PERM_MANAGE_USERS = 1
PERM_MANAGE_DOCUMENTS = 2
PERM_VIEW_ANALYTICS = 3
PERM_MODERATE = 4

permission_table = [
    "Administration",  # 0
    "User Management",  # 1
    "Document Management",  # 2
    "Analytics",  # 3
    "Moderation",  # 4
]

nb_perms = len(permission_table)


def encode_perms(perms: list[int]) -> int:
    """Encode une liste d'indices de permissions en un entier de bits."""
    return sum(1 << perm for perm in perms)


all_perms = encode_perms(list(range(nb_perms)))


def check_perm(perms: int, perm: int) -> bool:
    """Vérifie si le bit de permission est activé."""
    return perms >> perm & 1 == 1


def decode_perms(perms: int) -> list[int]:
    """Décode un entier de permissions en liste d'indices."""
    return [perm for perm in range(len(permission_table)) if check_perm(perms, perm)]


def is_admin(user: HasPermissions) -> bool:
    """Vérifie si l'utilisateur détient le bit Administration."""
    return check_perm(user.user_permissions, PERM_ADMIN)


def check_user_perm(user: HasPermissions, perm: int) -> bool:
    """Vérifie si l'utilisateur possède la permission donnée.

    Le bit Administration implique toutes les autres permissions : ajouter une
    entrée à permission_table ne retire donc aucun droit aux admins existants.
    """
    return is_admin(user) or check_perm(user.user_permissions, perm)


def list_perm(user: HasPermissions) -> list[str]:
    """Retourne la liste des noms de permissions effectives de l'utilisateur."""
    return [
        permission_table[perm]
        for perm in range(len(permission_table))
        if check_user_perm(user, perm)
    ]


def perm_required(perm: int) -> Callable[[Callable[P, R]], Callable[P, R | Response]]:
    """Décorateur qui restreint l'accès à une permission spécifique."""

    def decorator(func: Callable[P, R]) -> Callable[P, R | Response]:
        """Enveloppe la vue avec la vérification de permission."""

        @wraps(func)
        def decorated_view(*args: P.args, **kwargs: P.kwargs) -> R | Response:
            """Vue décorée vérifiant la permission avant exécution."""
            if not current_user.is_authenticated:
                return login_manager.unauthorized()

            if not check_user_perm(current_user, perm):
                flash(
                    f"Vous n'avez pas la permission "
                    f'"{permission_table[perm]}" pour accéder à cette page.',
                    "warning",
                )
                referrer = request.referrer
                # On ne fait confiance au Referer que s'il pointe sur ce même
                # site : cet en-tête est fourni par le client et peut être
                # falsifié pour rediriger vers un site tiers (open redirect).
                if referrer:
                    parsed_referrer = url_parse(referrer)
                    if (
                        parsed_referrer.scheme in {"", "http", "https"}
                        and (not parsed_referrer.netloc or parsed_referrer.netloc == request.host)
                    ):
                        return redirect(referrer)
                return redirect(url_for("chat.index"))

            return func(*args, **kwargs)

        return decorated_view

    return decorator


admin_required = perm_required(PERM_ADMIN)
manage_users_required = perm_required(PERM_MANAGE_USERS)
manage_documents_required = perm_required(PERM_MANAGE_DOCUMENTS)
view_analytics_required = perm_required(PERM_VIEW_ANALYTICS)
moderate_required = perm_required(PERM_MODERATE)


def can_manage_users(user: HasPermissions) -> bool:
    """Vérifie si l'utilisateur peut gérer les autres utilisateurs."""
    return check_user_perm(user, PERM_MANAGE_USERS)
