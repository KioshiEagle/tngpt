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

from .back.generate import generate_answer

bp = Blueprint("chat", __name__)


@bp.route("/chat", methods=["POST"])
def chat() -> Response:
    """Répond en streaming à un message utilisateur."""
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Message manquant"})

    user_message = data["message"]
    top_k = data.get("top_k", 15)

    if "history" not in session:
        session["history"] = []
    session["history"].append({"role": "user", "content": user_message})
    session.modified = True

    def _stream() -> Iterator[str]:
        try:
            yield from generate_answer(user_message, top_k=top_k)
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
    ("Pétition pour remettre l'Oriental", 5),
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
