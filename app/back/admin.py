import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Select

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
    RetrievalEvent,
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
    view_analytics_required,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_HTTP_BAD_REQUEST = 400
_PER_PAGE = 50
_ALLOWED_SUFFIXES = {".pdf", ".md"}
_MAX_SOURCE_ID = 128
_TOP_CHUNKS = 20
_TOP_SOURCES = 10
_WINDOWS = {0: "Depuis toujours", 7: "7 jours", 30: "30 jours"}


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


@admin_bp.route("/chunks")
@view_analytics_required
def chunks_page() -> str:
    """Fréquence de retrieval des chunks : que gagnerait-on à les mettre en cache ?"""
    days = request.args.get("days", default=0, type=int)
    if days not in _WINDOWS:
        days = 0

    since = datetime.now(UTC) - timedelta(days=days) if days else None

    def within_window(statement: Select) -> Select:
        """Restreint une requête à la fenêtre temporelle choisie."""
        if since is not None:
            return statement.where(RetrievalEvent.created_at >= since)
        return statement

    total_events = (
        db.session.scalar(
            within_window(db.select(db.func.count(RetrievalEvent.event_id)))
        )
        or 0
    )

    top_chunks = db.session.execute(
        within_window(
            db.select(
                RetrievalEvent.point_id,
                RetrievalEvent.title,
                RetrievalEvent.source_id,
                db.func.count(RetrievalEvent.event_id).label("hits"),
                db.func.avg(RetrievalEvent.score).label("avg_score"),
            )
        )
        .group_by(
            RetrievalEvent.point_id, RetrievalEvent.title, RetrievalEvent.source_id
        )
        .order_by(db.desc("hits"))
        .limit(_TOP_CHUNKS)
    ).all()

    top_sources = db.session.execute(
        within_window(
            db.select(
                RetrievalEvent.source_id,
                RetrievalEvent.title,
                db.func.count(RetrievalEvent.event_id).label("hits"),
            )
        )
        .group_by(RetrievalEvent.source_id, RetrievalEvent.title)
        .order_by(db.desc("hits"))
        .limit(_TOP_SOURCES)
    ).all()

    distinct_chunks = (
        db.session.scalar(
            within_window(
                db.select(db.func.count(db.distinct(RetrievalEvent.point_id)))
            )
        )
        or 0
    )

    # Couverture cumulée : part des retrievals absorbée par les chunks les plus
    # sollicités. C'est le chiffre qui décide de l'intérêt d'un cache — un top 20
    # couvrant 60 % des accès le justifie, 5 % ne le justifie pas.
    covered = sum(row.hits for row in top_chunks)
    coverage = round(100 * covered / total_events, 1) if total_events else 0.0
    max_hits = max((row.hits for row in top_chunks), default=0)

    questions_statement = db.select(db.func.count(Query.query_id))
    empty_statement = db.select(db.func.count(Query.query_id)).where(
        Query.result_count == 0
    )
    if since is not None:
        questions_statement = questions_statement.where(Query.created_at >= since)
        empty_statement = empty_statement.where(Query.created_at >= since)

    return render_template(
        "admin/chunks.html",
        top_chunks=top_chunks,
        top_sources=top_sources,
        total_events=total_events,
        distinct_chunks=distinct_chunks,
        coverage=coverage,
        max_hits=max_hits,
        questions=db.session.scalar(questions_statement) or 0,
        unanswered=db.session.scalar(empty_statement) or 0,
        days=days,
        windows=_WINDOWS,
        top_n=_TOP_CHUNKS,
    )


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
