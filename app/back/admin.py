import logging
from datetime import UTC, datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from werkzeug.utils import secure_filename
from werkzeug.wrappers import Response

from .catalog import (
    delete_document,
    reset_stale_ingestions,
    start_ingestion,
    sync_from_qdrant,
)
from .models import (
    DOC_FAILED,
    DOC_INDEXED,
    DOC_INDEXING,
    DOC_MISSING,
    DOC_ORIGIN_UPLOAD,
    Conversation,
    Document,
    Query,
    User,
    db,
)
from .permissions import (
    PERM_ADMIN,
    admin_required,
    check_perm,
    encode_perms,
    list_perm,
    manage_documents_required,
    manage_users_required,
    nb_perms,
    permission_table,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_HTTP_BAD_REQUEST = 400
_PER_PAGE = 50
_ALLOWED_SUFFIXES = {".pdf", ".md"}
_MAX_SOURCE_ID = 128


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
        "questions": db.session.scalar(db.select(db.func.count(Query.query_id))) or 0,
        "documents": db.session.scalar(
            db.select(db.func.count(Document.source_id)).filter_by(status=DOC_INDEXED)
        )
        or 0,
        "chunks": db.session.scalar(db.select(db.func.sum(Document.chunk_count))) or 0,
        "desynchronised": db.session.scalar(
            db.select(db.func.count(Document.source_id)).filter_by(status=DOC_MISSING)
        )
        or 0,
    }
    return render_template("admin/index.html", stats=stats)


@admin_bp.route("/catalog")
@manage_documents_required
def catalog_page() -> str:
    """Liste les documents présents dans la base vectorielle."""
    # Rattrape ici les ingestions tuées par un redémarrage : les faire au
    # démarrage toucherait la base avant que les migrations ne l'aient créée.
    reset_stale_ingestions()

    search = (request.args.get("q") or "").strip()
    page = request.args.get("page", default=1, type=int)

    statement = db.select(Document)
    if search:
        pattern = f"%{search}%"
        statement = statement.filter(
            db.or_(
                Document.title.ilike(pattern),
                Document.source_id.ilike(pattern),
                Document.author.ilike(pattern),
            )
        )
    statement = statement.order_by(Document.chunk_count.desc(), Document.source_id)

    documents = db.paginate(statement, page=page, per_page=_PER_PAGE, error_out=False)
    return render_template("admin/catalog.html", documents=documents, search=search)


@admin_bp.route("/catalog/upload", methods=["POST"])
@manage_documents_required
def catalog_upload() -> tuple[Response, int]:
    """Reçoit les fichiers déposés et lance leur ingestion en tâche de fond."""
    uploaded = request.files.getlist("files")
    if not uploaded:
        return jsonify(error="Aucun fichier reçu."), _HTTP_BAD_REQUEST

    application = current_app._get_current_object()  # noqa: SLF001
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []

    for file_storage in uploaded:
        filename = secure_filename(file_storage.filename or "")
        suffix = Path(filename).suffix.lower()

        if not filename or suffix not in _ALLOWED_SUFFIXES:
            rejected.append(
                {
                    "filename": file_storage.filename or "(sans nom)",
                    "reason": "Format non accepté (PDF ou Markdown uniquement).",
                }
            )
            continue

        source_id = Path(filename).stem[:_MAX_SOURCE_ID]
        stored_path = upload_dir / f"{source_id}{suffix}"
        file_storage.save(stored_path)

        # Un dépôt portant un source_id existant remplace le document : c'est
        # aussi la sémantique de Qdrant, où la ré-ingestion écrase les chunks.
        document = db.session.get(Document, source_id)
        if document is None:
            document = Document(source_id=source_id)
            db.session.add(document)

        document.title = document.title or source_id
        document.status = DOC_INDEXING
        document.origin = DOC_ORIGIN_UPLOAD
        document.error = None
        document.ingested_by = current_user.user_id
        document.updated_at = datetime.now(UTC)
        db.session.commit()

        start_ingestion(application, source_id, stored_path)
        accepted.append({"source_id": source_id, "filename": filename})

        logger.info(
            "Dépôt de %s par %s — ingestion lancée",
            source_id,
            current_user.user_mail,
        )

    return jsonify(accepted=accepted, rejected=rejected), 202


@admin_bp.route("/catalog/status")
@manage_documents_required
def catalog_status() -> Response:
    """État courant des documents, pour le suivi d'ingestion côté navigateur."""
    documents = db.session.scalars(
        db.select(Document).where(Document.status.in_([DOC_INDEXING, DOC_FAILED]))
    ).all()
    return jsonify(
        {
            document.source_id: {
                "status": document.status,
                "chunk_count": document.chunk_count,
                "error": document.error,
            }
            for document in documents
        }
    )


@admin_bp.route("/catalog/<path:source_id>/delete", methods=["POST"])
@manage_documents_required
def catalog_delete(source_id: str) -> Response:
    """Supprime un document de Qdrant et du catalogue."""
    try:
        deleted = delete_document(source_id)
    except Exception:
        logger.exception("Échec de la suppression de %s", source_id)
        flash(
            f"Suppression impossible : Qdrant est injoignable ({source_id}).", "warning"
        )
        return redirect(url_for("admin.catalog_page"))

    if not deleted:
        abort(404)

    flash(f"Document supprimé : {source_id}", "success")
    return redirect(url_for("admin.catalog_page"))


@admin_bp.route("/catalog/sync", methods=["POST"])
@manage_documents_required
def catalog_sync() -> Response:
    """Resynchronise le catalogue avec l'état réel de Qdrant."""
    try:
        stats = sync_from_qdrant()
    except Exception:
        logger.exception("Échec de la synchronisation du catalogue")
        flash("Synchronisation impossible : Qdrant est injoignable.", "warning")
        return redirect(url_for("admin.catalog_page"))

    flash(
        f"Catalogue synchronisé : {stats['total']} documents "
        f"({stats['added']} ajoutés, {stats['missing']} manquants dans Qdrant).",
        "success",
    )
    return redirect(url_for("admin.catalog_page"))


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
