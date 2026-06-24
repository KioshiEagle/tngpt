from flask import flash, request, redirect, url_for
from flask_login import current_user
from functools import wraps
from config import login_manager

permission_table = [
    'User Management', # 0
]

nb_perms = len(permission_table)

def encode_perms(perms: list[int]) -> int:
    return sum([1<<perm for perm in perms])

all_perms = encode_perms([i for i in range(nb_perms)])

def check_perm(perms: int, perm: int) -> bool:
    return (perms>>perm & 1 == 1)


def decode_perms(perms: int) -> list[int]:
    max_perm = len(permission_table)
    return [perm for perm in range(max_perm) if check_perm(perms, perm)]

def check_user_perm(user, perm: int) -> bool:
    return check_perm(user.permission, perm)

def list_perm(user) -> list[str]:
    perms = []
    for perm in range(len(permission_table)):
        if check_user_perm(user, perm):
            perms.append(permission_table[perm])
    return perms

def perm_required(perm: int):
    def decorator(func):
        @wraps(func)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()

            if not check_user_perm(current_user, perm):
                flash(f"Vous n'avez pas la permission \"{permission_table[perm]}\" pour accéder à cette page.", 'warning')
                if request.referrer is None:
                    return redirect(url_for('dashboard.dashboard'))
                else:
                    return redirect(request.referrer)

            return func(*args, **kwargs)
            return current_app.ensure_sync(func)(*args, **kwargs)
        return decorated_view
    return decorator

manage_users_required = perm_required(0)

def can_manage_users(user) -> bool:
    return check_user_perm(user, 0)
