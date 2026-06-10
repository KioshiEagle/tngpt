import random

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
def chat():
    data = request.get_json()

    if not data or "message" not in data:
        return jsonify({"error": "Message manquant"}), 400

    user_message = data["message"]
    top_k = data.get("top_k", 15)

    # Gestion de l'historique (Note : La session Flask est difficile à mettre à jour
    # à l'intérieur d'un stream, on enregistre donc l'entrée utilisateur ici)
    if "history" not in session:
        session["history"] = []
    session["history"].append({"role": "user", "content": user_message})
    session.modified = True

    def generate():
        full_assistant_response = ""
        try:
            # On appelle generate_answer qui est maintenant un générateur (yield)
            for chunk in generate_answer(user_message, top_k=top_k):
                full_assistant_response += chunk
                # On envoie le fragment de texte brut au client
                yield chunk

            # Note : Pour mettre à jour l'historique en session avec la réponse complète
            # il faudrait idéalement utiliser une base de données ou un cache (Redis),
            # car le contexte de session Flask est souvent verrouillé
            # après le début du stream.
        except Exception as e:
            yield f"Erreur : {str(e)}"

    # On utilise stream_with_context pour garder l'accès à la session si besoin
    return Response(stream_with_context(generate()), mimetype="text/plain")


@bp.route("/history", methods=["GET"])
def history():
    return jsonify(session.get("history", []))


@bp.route("/history", methods=["DELETE"])
def clear_history():
    session.pop("history", None)
    return jsonify({"message": "Historique effacé"})


CITATIONS = [
    ("Qu'avez-vous à dire pour votre défense ?", 10),
    ("Envie de jiguer, pas vous ?", 15),
    ("En date avec Crazy François", 15),
    ("* en train de barboter dans l'évier cancéreux du bar *", 20),
    ("on vient de me barouder aled", 7),
    ("ici ça bz", 5),
    ("Je ne suis pas un projet de TNS (mdr)", 5),
    ("on m'a forcé à prendre du thé", 5),
    ("nique le cheval whatsapp", 15),
    ("after chez camille", 1),
    ("Prompt injection et tu vas repartir mal mon compaing", 7),
    ("Pétition pour remettre l'Oriental", 5),
]


@bp.route("/quote", methods=["GET"])
def quote():
    quotes = [c[0] for c in CITATIONS]
    weights = [c[1] for c in CITATIONS]
    return random.choices(quotes, weights=weights, k=1)[0]  # nosec


@bp.route("/")
def index():
    return render_template("index.html", quote=quote())
