import logging
import os
from datetime import UTC, datetime, timedelta
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
from sqlalchemy import Select
from werkzeug.utils import secure_filename
from werkzeug.wrappers import Response

from app.extensions import CHAT_RATE_DEFAULT, CHAT_RATE_LIMITED

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
    DOC_ORIGIN_MAIL,
    DOC_ORIGIN_UPLOAD,
    USER_ACTIVE,
    USER_BANNED,
    USER_LIMITED,
    Conversation,
    Document,
    GroqKey,
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
    moderate_required,
    nb_perms,
    permission_table,
    view_analytics_required,
)
from .usage import daily_quota, groq_calls_today_by_key, questions_today_all

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

_HTTP_BAD_REQUEST = 400
_PER_PAGE = 50
_ALLOWED_SUFFIXES = {".pdf", ".md", ".eml"}
# L'origine se lit sur l'extension : un mail n'a ni le même contenu ni la même
# autorité qu'un compte-rendu, et la colonne le donne à voir au catalogue.
_ORIGINS = {".eml": DOC_ORIGIN_MAIL}
_MAX_SOURCE_ID = 128
_TOP_CHUNKS = 20
_TOP_SOURCES = 10
_WINDOWS = {0: "Depuis toujours", 7: "7 jours", 30: "30 jours"}
_MODERATION_ACTIONS = {
    USER_ACTIVE: "accès rétabli",
    USER_LIMITED: "usage restreint",
    USER_BANNED: "accès suspendu",
}
_MAX_QUOTA = 100_000


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

    # current_app est un proxy : _get_current_object() donne l'app réelle à
    # passer au thread. ty ne connaît pas ce membre du LocalProxy.
    application = current_app._get_current_object()  # noqa: SLF001  # ty: ignore[unresolved-attribute]
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
        document.origin = _ORIGINS.get(suffix, DOC_ORIGIN_UPLOAD)
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
    """Fréquence de retrieval des chunks, pour décider lesquels mettre en cache."""
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

    # Couverture cumulée : part des retrievals absorbée par les chunks les
    # plus sollicités. C'est le chiffre qui décide de l'intérêt d'un cache.
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


@admin_bp.route("/quotas")
@manage_users_required
def quotas_page() -> str:
    """Usage du jour et limite quotidienne de chaque utilisateur."""
    counts = questions_today_all()
    users = db.session.scalars(
        db.select(User).order_by(User.user_surname, User.user_firstname)
    ).all()

    rows = []
    for user in users:
        used = counts.get(user.user_id, 0)
        limit = daily_quota(user)  # None pour un admin (illimité)
        rows.append(
            {
                "user": user,
                "used": used,
                "limit": limit,
                "remaining": None if limit is None else max(limit - used, 0),
                "pct": min(100, round(100 * used / limit)) if limit else 0,
                "override": user.quota_daily,
            }
        )

    return render_template(
        "admin/quotas.html",
        rows=rows,
        default_quota=current_app.config["DEFAULT_DAILY_QUOTA"],
    )


@admin_bp.route("/quotas/<int:user_id>", methods=["POST"])
@manage_users_required
def update_quota(user_id: int) -> Response:
    """Fixe (ou réinitialise) la limite quotidienne d'un utilisateur."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    raw = (request.form.get("quota_daily") or "").strip()
    if raw == "":
        # Champ vide : on retire la surcharge, l'utilisateur repasse au défaut global.
        user.quota_daily = None
    elif raw.isdigit() and int(raw) <= _MAX_QUOTA:
        # isdigit() n'accepte que des entiers >= 0 : pas de négatif, pas de texte.
        user.quota_daily = int(raw)
    else:
        abort(_HTTP_BAD_REQUEST)

    db.session.commit()

    if user.quota_daily is None:
        flash(f"{user.user_firstname} : quota par défaut rétabli.", "success")
    else:
        flash(
            f"{user.user_firstname} : quota fixé à {user.quota_daily} questions/jour.",
            "success",
        )
    return redirect(url_for("admin.quotas_page"))


@admin_bp.route("/groq-keys")
@admin_required
def groq_keys_page() -> str:
    """Pool de clés Groq : usage par clé et gestion (ajout, activation)."""
    calls_today = groq_calls_today_by_key()
    keys = db.session.scalars(
        db.select(GroqKey).order_by(GroqKey.active.desc(), GroqKey.created_at)
    ).all()

    rows = [
        {
            "key": key,
            "today": calls_today.get(key.groq_key_id, 0),
        }
        for key in keys
    ]
    return render_template(
        "admin/groq_keys.html",
        rows=rows,
        env_fallback=bool(os.getenv("GROQ_API_KEY")),
    )


@admin_bp.route("/groq-keys/create", methods=["POST"])
@admin_required
def create_groq_key() -> Response:
    """Ajoute une clé Groq au pool."""
    secret = (request.form.get("secret") or "").strip()
    if not secret:
        flash("La clé Groq est vide.", "warning")
        return redirect(url_for("admin.groq_keys_page"))

    if db.session.scalar(db.select(GroqKey).filter_by(secret=secret)):
        flash("Cette clé est déjà dans le pool.", "warning")
        return redirect(url_for("admin.groq_keys_page"))

    key = GroqKey(
        label=(request.form.get("label") or "").strip()[:100] or None,
        secret=secret,
        created_by=current_user.user_id,
    )
    db.session.add(key)
    db.session.commit()
    logger.info("Clé Groq %s ajoutée par %s", key.masked, current_user.user_mail)
    flash(f"Clé {key.masked} ajoutée au pool.", "success")
    return redirect(url_for("admin.groq_keys_page"))


@admin_bp.route("/groq-keys/<int:groq_key_id>/toggle", methods=["POST"])
@admin_required
def toggle_groq_key(groq_key_id: int) -> Response:
    """Active ou désactive une clé du pool (sans la retirer)."""
    key = db.session.get(GroqKey, groq_key_id)
    if key is None:
        abort(404)

    key.active = not key.active
    db.session.commit()
    etat = "activée" if key.active else "désactivée"
    flash(f"Clé {key.masked} {etat}.", "success")
    return redirect(url_for("admin.groq_keys_page"))


@admin_bp.route("/groq-keys/<int:groq_key_id>/delete", methods=["POST"])
@admin_required
def delete_groq_key(groq_key_id: int) -> Response:
    """Retire une clé du pool.

    Les questions déjà attribuées à cette clé conservent la référence via une
    clé étrangère nullable : on détache d'abord, puis on supprime.
    """
    key = db.session.get(GroqKey, groq_key_id)
    if key is None:
        abort(404)

    db.session.query(Query).filter_by(groq_key_id=groq_key_id).update(
        {Query.groq_key_id: None}
    )
    masked = key.masked
    db.session.delete(key)
    db.session.commit()
    logger.info("Clé Groq %s retirée par %s", masked, current_user.user_mail)
    flash(f"Clé {masked} retirée du pool.", "success")
    return redirect(url_for("admin.groq_keys_page"))


@admin_bp.route("/moderation")
@moderate_required
def moderation_page() -> str:
    """Liste les utilisateurs, leur usage et leur statut de modération."""
    question_counts = dict(
        db.session.execute(
            db.select(Query.user_id, db.func.count(Query.query_id)).group_by(
                Query.user_id
            )
        )
        .tuples()
        .all()
    )
    last_seen = dict(
        db.session.execute(
            db.select(Query.user_id, db.func.max(Query.created_at)).group_by(
                Query.user_id
            )
        )
        .tuples()
        .all()
    )

    users = db.session.scalars(
        db.select(User).order_by(User.user_surname, User.user_firstname)
    ).all()

    return render_template(
        "admin/moderation.html",
        users=users,
        question_counts=question_counts,
        last_seen=last_seen,
        statuses=_MODERATION_ACTIONS,
        rate_default=CHAT_RATE_DEFAULT,
        rate_limited=CHAT_RATE_LIMITED,
    )


@admin_bp.route("/moderation/<int:user_id>", methods=["POST"])
@moderate_required
def moderate_user(user_id: int) -> Response:
    """Applique une sanction (ou la lève) sur un utilisateur."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    action = request.form.get("action", "")
    if action not in _MODERATION_ACTIONS:
        abort(_HTTP_BAD_REQUEST)

    if user.user_id == current_user.user_id:
        flash("Vous ne pouvez pas vous modérer vous-même.", "warning")
        return redirect(url_for("admin.moderation_page"))

    # Un modérateur ne doit pas neutraliser un administrateur : sinon la
    # permission Moderation suffirait à prendre le contrôle de l'application.
    if user.is_admin():
        flash(
            f"{user.user_firstname} est administrateur et ne peut pas être modéré.",
            "warning",
        )
        return redirect(url_for("admin.moderation_page"))

    user.status = action
    user.ban_reason = (request.form.get("reason") or "").strip()[:300] or None
    user.moderated_at = datetime.now(UTC)
    user.moderated_by = current_user.user_id
    db.session.commit()

    logger.info(
        "Modération : %s -> %s par %s (motif : %s)",
        user.user_mail,
        action,
        current_user.user_mail,
        user.ban_reason or "aucun",
    )
    flash(
        f"{user.user_firstname} {user.user_surname} : {_MODERATION_ACTIONS[action]}.",
        "success",
    )
    return redirect(url_for("admin.moderation_page"))


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

    # Garde-fou anti-verrouillage : le dernier admin se retirant son bit
    # laisserait `flask make-admin` pour seule issue.
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
