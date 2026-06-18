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

from .back.generate import GenerateRequest, generate_answer
from .extensions import limiter

bp = Blueprint("chat", __name__)

MAX_MESSAGE_LENGTH = 500
TOP_K = 5


@bp.route("/chat", methods=["POST"])
@limiter.limit("20 per minute")
def chat() -> Response:
    """Répond en streaming à un message utilisateur."""
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Message manquant"}), 400

    user_message = data["message"]
    if len(user_message) > MAX_MESSAGE_LENGTH:
        msg = f"Message trop long (max {MAX_MESSAGE_LENGTH} caractères)"
        return jsonify({"error": msg}), 400

    req = GenerateRequest(
        question=user_message,
        history=data.get("history", []),
        top_k=TOP_K,
        user_name=None,  # remplacer par la valeur de session une fois l'auth intégrée
    )

    def _stream() -> Iterator[str]:
        try:
            yield from generate_answer(req)
        except Exception as e:  # noqa: BLE001
            yield f"Erreur : {e!s}"

    return Response(stream_with_context(_stream()), mimetype="text/plain")


@bp.route("/history", methods=["GET"])
def history() -> Response:
    """Renvoie l'historique de la conversation."""
    return jsonify(session.get("history", []))


@bp.route("/history", methods=["DELETE"])
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
def index() -> str:
    """Affiche la page d'accueil."""
    return render_template("index.html", quote=quote())
