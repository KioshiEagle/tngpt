import logging

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from werkzeug.wrappers import Response

from .models import Conversation, User, db
from .permissions import (
    PERM_ADMIN,
    admin_required,
    check_perm,
    encode_perms,
    list_perm,
    manage_users_required,
    nb_perms,
    permission_table,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_HTTP_BAD_REQUEST = 400


@admin_bp.app_template_filter("bitwise_has")
def bitwise_has(perms: int, perm: int) -> bool:
    """Filtre Jinja indiquant si le bit `perm` est activé dans le bitmask `perms`.

    Volontairement brut (pas d'implication du bit Administration) : les cases à
    cocher doivent refléter les bits réellement stockés, pas les droits effectifs.
    """
    return check_perm(perms, perm)


@admin_bp.route("/")
@admin_required
def index() -> str:
    """Vue d'ensemble de l'application."""
    users = db.session.scalars(db.select(User)).all()
    stats = {
        "users": len(users),
        "admins": sum(1 for user in users if user.is_admin()),
        "conversations": db.session.scalar(
            db.select(db.func.count(Conversation.conversation_id))
        )
        or 0,
    }
    return render_template("admin/index.html", stats=stats)


@admin_bp.route("/permissions")
@manage_users_required
def permissions_page() -> str:
    """Liste les utilisateurs et leurs permissions."""
    users = db.session.scalars(
        db.select(User).order_by(User.user_surname, User.user_firstname)
    ).all()
    return render_template(
        "admin/permissions.html",
        users=users,
        permission_table=permission_table,
        perm_admin=PERM_ADMIN,
    )


def _parse_selected_perms() -> int:
    """Lit les permissions cochées du formulaire et les encode en bitmask."""
    try:
        selected = [int(value) for value in request.form.getlist("perms")]
    except ValueError:
        abort(_HTTP_BAD_REQUEST)

    if any(perm < 0 or perm >= nb_perms for perm in selected):
        abort(_HTTP_BAD_REQUEST)

    return encode_perms(selected)


@admin_bp.route("/permissions/<int:user_id>", methods=["POST"])
@manage_users_required
def update_permissions(user_id: int) -> Response:
    """Met à jour les permissions d'un utilisateur."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    new_perms = _parse_selected_perms()

    # Garde-fou anti-verrouillage : si le dernier admin se retire son propre bit
    # Administration, plus personne ne peut administrer l'application — seul un
    # accès shell (`flask make-admin`) permettrait d'en sortir.
    losing_own_admin = user.user_id == current_user.user_id and not check_perm(
        new_perms, PERM_ADMIN
    )
    if losing_own_admin:
        flash(
            "Vous ne pouvez pas retirer votre propre permission Administration.",
            "warning",
        )
        return redirect(url_for("admin.permissions_page"))

    user.user_permissions = new_perms
    db.session.commit()

    logger.info(
        "Permissions de %s modifiées par %s : %s",
        user.user_mail,
        current_user.user_mail,
        list_perm(user) or ["aucune"],
    )
    flash(f"Permissions de {user.user_firstname} mises à jour.", "success")
    return redirect(url_for("admin.permissions_page"))
