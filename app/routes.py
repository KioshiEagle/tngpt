import random
from collections.abc import Iterator

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    render_template,
    request,
    stream_with_context,
)
from flask_login import current_user, login_required
from groq import Groq

from .back.clubs import lookup_context
from .back.generate import GenerateRequest, generate_answer, retrieve
from .back.groqpool import acquire
from .back.models import Conversation, db
from .back.reflexes import reflex
from .back.seamap import generate_map, retrieve_for_map, wants_map
from .back.types import SearchResult
from .back.usage import log_retrieval, quota_status, seconds_until_reset
from .extensions import chat_rate_limit, limiter

bp = Blueprint("chat", __name__)

MAX_MESSAGE_LENGTH = 500
TOP_K = 5
# Nombre de tours passés inclus dans le prompt, pour enrichir la recherche
# Qdrant sans faire exploser la taille du contexte envoyé à Groq.
HISTORY_CONTEXT_SIZE = 4
# Longueur du titre auto-généré à partir du premier message, alignée sur la
# troncature déjà faite côté front (voir shortTitle dans main.js).
TITLE_MAX_LENGTH = 40
_HTTP_TOO_MANY_REQUESTS = 429


def _make_title(message: str) -> str:
    """Dérive un titre de conversation à partir du premier message."""
    if len(message) <= TITLE_MAX_LENGTH:
        return message
    return message[:TITLE_MAX_LENGTH].rstrip() + "…"


def _get_owned_conversation(conversation_id: int) -> Conversation:
    """Charge une conversation appartenant à l'utilisateur courant, ou 404.

    404 plutôt que 403 : ne révèle pas l'existence d'une conversation d'autrui.
    """
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.user_id:
        abort(404)
    return conversation


def _append_message(conversation_id: int, role: str, content: str) -> None:
    """Ajoute un message à une conversation et commite, si elle existe encore."""
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None:
        return
    conversation.messages = [*conversation.messages, {"role": role, "content": content}]
    db.session.commit()


def _stream_answer(
    req: GenerateRequest,
    results: list[SearchResult],
    client: Groq,
    conversation_id: int,
    *,
    is_map: bool,
) -> Iterator[str]:
    """Streame la réponse Groq et persiste ce qui a été produit.

    Sorti de `chat` pour que le contexte de requête ne soit pas capturé par une
    fermeture : tout ce dont le générateur a besoin lui est passé en argument.
    """
    raw_text = ""
    try:
        # Un seul appel à Groq, dont on accumule le texte au passage pour le
        # persister ensuite. La carte au trésor et le chat sont deux
        # générateurs alternatifs, jamais successifs.
        stream = (
            generate_map(req, results, client=client)
            if is_map
            else generate_answer(req, results, client=client)
        )
        for chunk in stream:
            raw_text += chunk
            yield chunk
    except Exception as e:  # noqa: BLE001
        yield f"Erreur : {e!s}"
    finally:
        # Sauvegarde même une réponse partielle (arrêt manuel, erreur) :
        # stream_with_context maintient le contexte de requête jusqu'ici,
        # y compris quand le client se déconnecte (GeneratorExit).
        if raw_text:
            _append_message(conversation_id, "assistant", raw_text)


def _reflex_response(
    conversation: Conversation, user_message: str, answer: str
) -> Response:
    """Répond sans Qdrant ni Groq, en persistant l'échange comme les autres.

    Les deux messages sont écrits d'un coup : il n'y a pas de streaming à
    attendre, donc rien qui justifie de commiter en deux temps.
    """
    conversation.messages = [
        *conversation.messages,
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": answer},
    ]
    db.session.commit()
    response = Response(answer, mimetype="text/plain")
    response.headers["X-Conversation-Id"] = str(conversation.conversation_id)
    return response


def _resolve_conversation(
    conversation_id: int | None, user_id: int, user_message: str
) -> Conversation:
    """Récupère la conversation visée, ou en ouvre une nouvelle sur ce message."""
    if conversation_id is not None:
        return _get_owned_conversation(conversation_id)
    conversation = Conversation(
        user_id=user_id, title=_make_title(user_message), messages=[]
    )
    db.session.add(conversation)
    return conversation


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
    # current_user est un proxy Flask-Login ; @login_required garantit un User.
    status = quota_status(current_user)  # ty: ignore[invalid-argument-type]
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

    conversation = _resolve_conversation(
        data.get("conversation_id"), user_id, user_message
    )

    # Plaisanteries maison : la réponse est connue d'avance, elle ne vaut ni un
    # aller-retour Qdrant ni un appel Groq (voir `back/reflexes.py`).
    reflexe = reflex(user_message)
    if reflexe is not None:
        return _reflex_response(conversation, user_message, reflexe)

    # L'historique d'enrichissement vient de la base, pas du client : une
    # conversation a désormais une source de vérité côté serveur.
    history = conversation.messages[-HISTORY_CONTEXT_SIZE:]

    req = GenerateRequest(
        question=user_message,
        history=history,
        top_k=TOP_K,
        user_name=user_name,
    )

    # Prélève une clé du pool Groq (round-robin) avant le streaming, pour pouvoir
    # attribuer la question à cette clé dans le journal.
    client, groq_key_id = acquire()

    # Une demande de carte des mers emprunte un chemin distinct : le TOP_K du
    # chat ne suffit pas à énumérer les clubs à travers toutes les archives.
    is_map = wants_map(user_message)

    # Recherche et journalisation avant le streaming : l'événement est ainsi
    # enregistré même si le client se déconnecte pendant la réponse, et on
    # n'écrit pas en base depuis un générateur dont le contexte se démonte.
    results = retrieve_for_map(req) if is_map else retrieve(req)

    # Fiches SQL des clubs cités, quand la question en nomme un. Résolues ici,
    # dans le contexte de requête, et non dans le générateur : même raison que
    # le retrieval ci-dessus, la session SQLAlchemy ne doit pas être sollicitée
    # depuis un générateur dont le contexte se démonte. La carte a son propre
    # prompt, sans emplacement pour les fiches.
    if not is_map:
        req.fiches = lookup_context(user_message)

    log_retrieval(
        user_id=user_id,
        question=user_message,
        top_k=len(results) if is_map else TOP_K,
        results=results,
        groq_key_id=groq_key_id,
    )

    # Sauvegardé tout de suite, avant le streaming, pour la même raison que le
    # log de retrieval ci-dessus.
    conversation.messages = [
        *conversation.messages,
        {"role": "user", "content": user_message},
    ]
    db.session.commit()
    conversation_id = conversation.conversation_id

    flux = _stream_answer(req, results, client, conversation_id, is_map=is_map)
    response = Response(stream_with_context(flux), mimetype="text/plain")
    response.headers["X-Conversation-Id"] = str(conversation_id)
    return response


@bp.route("/conversations", methods=["GET"])
@login_required
def list_conversations() -> Response:
    """Liste les conversations de l'utilisateur courant, les plus récentes d'abord."""
    conversations = db.session.scalars(
        db.select(Conversation)
        .filter_by(user_id=current_user.user_id)
        .order_by(Conversation.updated_at.desc())
    ).all()
    return jsonify(
        [
            {
                "id": c.conversation_id,
                "title": c.title,
                "updated_at": c.updated_at.isoformat(),
            }
            for c in conversations
        ]
    )


@bp.route("/conversations/<int:conversation_id>", methods=["GET"])
@login_required
def get_conversation(conversation_id: int) -> Response:
    """Renvoie le détail d'une conversation, messages inclus."""
    conversation = _get_owned_conversation(conversation_id)
    return jsonify(
        {
            "id": conversation.conversation_id,
            "title": conversation.title,
            "messages": conversation.messages,
        }
    )


@bp.route("/conversations/<int:conversation_id>", methods=["DELETE"])
@login_required
def delete_conversation(conversation_id: int) -> Response:
    """Supprime une conversation."""
    conversation = _get_owned_conversation(conversation_id)
    db.session.delete(conversation)
    db.session.commit()
    return jsonify({"message": "Conversation supprimée"})


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
