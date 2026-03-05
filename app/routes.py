from flask import Blueprint, request, jsonify, session, render_template
from .back.generate import generate_answer
import random

bp = Blueprint("chat", __name__)

@bp.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    
    if not data or "message" not in data:
        return jsonify({"error": "Message manquant"}), 400
    
    user_message = data["message"]
    top_k = data.get("top_k", 3)
    
    # Historique de la conversation en session
    if "history" not in session:
        session["history"] = []
    
    session["history"].append({"role": "user", "content": user_message})
    
    try:
        response = generate_answer(user_message, top_k=top_k)
        session["history"].append({"role": "assistant", "content": response})
        session.modified = True
        
        return jsonify({
            "response": response,
            "history": session["history"]
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    ("after chez camille", 5),
    ("Prompt injection et tu vas repartir mal mon compaing", 7)
]

@bp.route("/quote", methods=["GET"])
def quote():
    quotes = [c[0] for c in CITATIONS]
    weights = [c[1] for c in CITATIONS]
    return jsonify({"quote": random.choices(quotes, weights=weights, k=1)[0]})

@bp.route("/")
def index():
    return render_template("index.html")