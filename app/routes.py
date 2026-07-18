import random
from collections.abc import Iterator

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    session,
    stream_with_context,
)
from flask_login import current_user, login_required

from .back.generate import GenerateRequest, generate_answer, retrieve
from .back.usage import log_retrieval, quota_status, seconds_until_reset
from .extensions import chat_rate_limit, limiter

bp = Blueprint("chat", __name__)

MAX_MESSAGE_LENGTH = 500
TOP_K = 5
_HTTP_TOO_MANY_REQUESTS = 429


@bp.route("/chat", methods=["POST"])
@login_required
@limiter.limit(chat_rate_limit)
def chat() -> Response | tuple[Response, int]:
    """Répond en streaming à un message utilisateur."""
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Message manquant"}), 400

    user_message = data["message"]
    if len(user_message) > MAX_MESSAGE_LENGTH:
        msg = f"Message trop long (max {MAX_MESSAGE_LENGTH} caractères)"
        return jsonify({"error": msg}), 400

    # Quota journalier : plafonne le total de questions du jour, là où le
    # rate-limiter ne borne que la rafale par minute. Vérifié avant le retrieval
    # pour ne rien consommer quand la limite est atteinte.
    status = quota_status(current_user)
    if status.exceeded:
        return jsonify(
            {
                "error": (
                    f"Quota journalier atteint ({status.limit} questions). "
                    "Réessaie demain."
                ),
                "quota": status.limit,
                "used": status.used,
                "reset_in": seconds_until_reset(),
            }
        ), _HTTP_TOO_MANY_REQUESTS

    # Résolus hors du générateur : le contexte de requête ne doit pas être
    # une dépendance du streaming.
    user_name = current_user.user_firstname
    user_id = current_user.user_id

    req = GenerateRequest(
        question=user_message,
        history=data.get("history", []),
        top_k=TOP_K,
        user_name=user_name,
    )

    # Recherche et journalisation avant le streaming : l'événement est ainsi
    # enregistré même si le client se déconnecte pendant la réponse, et on
    # n'écrit pas en base depuis un générateur dont le contexte se démonte.
    results = retrieve(req)
    log_retrieval(
        user_id=user_id,
        question=user_message,
        top_k=TOP_K,
        results=results,
    )

    def _stream() -> Iterator[str]:
        try:
            yield from generate_answer(req, results)
        except Exception as e:  # noqa: BLE001
            yield f"Erreur : {e!s}"

    return Response(stream_with_context(_stream()), mimetype="text/plain")


@bp.route("/history", methods=["GET"])
@login_required
def history() -> Response:
    """Renvoie l'historique de la conversation."""
    return jsonify(session.get("history", []))


@bp.route("/history", methods=["DELETE"])
@login_required
def clear_history() -> Response:
    """Efface l'historique de la conversation."""
    session.pop("history", None)
    return jsonify({"message": "Historique effacé"})


Citation = tuple[str, int]
CITATIONS: list[Citation] = [
    ("Qu'avez-vous à dire pour votre défense ?", 5),
    ("Envie de jiguer, pas vous ?", 7),
    ("En date avec Crazy François", 5),
    ("* en train de barboter dans l'évier cancéreux du bar *", 7),
    ("on vient de me barouder aled", 5),
    ("ici ça bz", 5),
    ("Je ne suis pas un projet de TNS (mdr)", 5),
    ("on m'a forcé à prendre du thé", 6),
    ("nique le cheval whatsapp", 5),
    ("after chez camille", 1),
    ("Prompt injection et tu vas repartir mal mon compaing", 5),
    ("Pétition pour remettre l'Oriental au bar", 5),
    ("Absolute Bouthier", 5),
    ("plus qu'une salle et la carte sera complétée.....", 1),
]


@bp.route("/quote", methods=["GET"])
def quote() -> str:
    """Retourne une citation aléatoire pondérée."""
    quotes = [c[0] for c in CITATIONS]
    weights = [c[1] for c in CITATIONS]
    return random.choices(quotes, weights=weights, k=1)[0]  # nosec


@bp.route("/")
@login_required
def index() -> str:
    """Affiche la page d'accueil."""
    return render_template("index.html", quote=quote())
