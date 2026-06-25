from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import flash, redirect, request, url_for
from flask_login import LoginManager, current_user

login_manager = LoginManager()

permission_table = [
    "User Management",  # 0
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


def check_user_perm(user: Any, perm: int) -> bool:
    """Vérifie si l'utilisateur possède la permission donnée."""
    return check_perm(user.user_permissions, perm)


def list_perm(user: Any) -> list[str]:
    """Retourne la liste des noms de permissions de l'utilisateur."""
    return [
        permission_table[perm]
        for perm in range(len(permission_table))
        if check_user_perm(user, perm)
    ]


def perm_required(perm: int) -> Callable[..., Any]:
    """Décorateur qui restreint l'accès à une permission spécifique."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        """Enveloppe la vue avec la vérification de permission."""

        @wraps(func)
        def decorated_view(*args: Any, **kwargs: Any) -> Any:
            """Vue décorée vérifiant la permission avant exécution."""
            if not current_user.is_authenticated:
                return login_manager.unauthorized()

            if not check_user_perm(current_user, perm):
                flash(
                    f"Vous n'avez pas la permission "
                    f'"{permission_table[perm]}" pour accéder à cette page.',
                    "warning",
                )
                if request.referrer is None:
                    return redirect(url_for("dashboard.dashboard"))
                return redirect(request.referrer)

            return func(*args, **kwargs)

        return decorated_view

    return decorator


manage_users_required = perm_required(0)


def can_manage_users(user: Any) -> bool:
    """Vérifie si l'utilisateur peut gérer les autres utilisateurs."""
    return check_user_perm(user, 0)
