import os
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

from .back.brainrot import BRAINROT_SPEC
from .back.clubs import lookup_context
from .back.ctf import spec_for
from .back.generate import (
    CHAT_SPEC,
    CallSpec,
    GenerateRequest,
    generate_answer,
    retrieve,
)
from .back.groqpool import Client, acquire
from .back.models import Conversation, db
from .back.personnes import lookup_personnes, lookup_soi
from .back.reflexes import reflex
from .back.seamap import generate_map, retrieve_for_map, wants_map
from .back.types import SearchResult
from .back.usage import log_retrieval, quota_status, seconds_until_reset
from .extensions import chat_rate_limit, limiter

bp = Blueprint("chat", __name__)

# Relevé sur les déploiements CTF : une charge d'injection ou d'encodage ne
# tient pas en 500 caractères.
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "500"))
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
    client: Client,
    conversation_id: int,
    spec: CallSpec | None,
) -> Iterator[str]:
    """Streame la réponse Groq et persiste ce qui a été produit.

    `spec` à None demande la carte au trésor, qui a son propre prompt ; sinon
    c'est le chat, normal ou challenge. Sorti de `chat` pour que le contexte de
    requête ne soit pas capturé par une fermeture.
    """
    raw_text = ""
    try:
        # Un seul appel à Groq, dont on accumule le texte pour le persister.
        # Carte et chat sont alternatifs, jamais successifs.
        stream = (
            generate_map(req, results, client=client)
            if spec is None
            else generate_answer(req, results, client=client, spec=spec)
        )
        for chunk in stream:
            raw_text += chunk
            yield chunk
    except Exception as e:  # noqa: BLE001
        yield f"Erreur : {e!s}"
    finally:
        # Sauvegarde même une réponse partielle : stream_with_context tient le
        # contexte jusqu'ici, même sur déconnexion du client.
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
    """Répond en streaming au TN-GPT normal."""
    return _run_chat(spec=CHAT_SPEC, is_ctf=False)


@bp.route("/ctf/<chal>/chat", methods=["POST"])
@login_required
@limiter.limit(chat_rate_limit)
def ctf_chat(chal: str) -> Response | tuple[Response, int]:
    """Répond en streaming au chat d'un challenge, ou 404 s'il n'est pas activé."""
    spec = spec_for(chal)
    if spec is None:
        abort(404)
    return _run_chat(spec=spec, is_ctf=True)


def _run_chat(*, spec: CallSpec, is_ctf: bool) -> Response | tuple[Response, int]:
    """Traite un message : quota, retrieval, fiches, streaming, persistance.

    `spec` porte le prompt et les paramètres Groq — chat normal ou challenge.
    `is_ctf` coupe la carte au trésor et le mode brainrot, qui contourneraient
    les règles du challenge.
    """
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Message manquant"}), 400

    # Toggle du front : le brainrot n'est qu'une autre voix sur le même chat,
    # donc un autre spec. Coupé sur un challenge, dont il écraserait le prompt.
    if not is_ctf and data.get("brainrot"):
        spec = BRAINROT_SPEC

    user_message = data["message"]
    if len(user_message) > MAX_MESSAGE_LENGTH:
        msg = f"Message trop long (max {MAX_MESSAGE_LENGTH} caractères)"
        return jsonify({"error": msg}), 400

    # Quota journalier, là où le rate-limiter ne borne que la rafale. Vérifié
    # avant le retrieval pour ne rien consommer une fois la limite atteinte.
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
    # La carte a son propre prompt, sans les règles du challenge : elle serait
    # un canal détourné. Elle est donc coupée sur un chat de challenge.
    is_map = wants_map(user_message) and not is_ctf

    # Recherche et journalisation avant le streaming : rien ne s'écrit en base
    # depuis un générateur dont le contexte se démonte.
    results = retrieve_for_map(req) if is_map else retrieve(req)

    # Fiches SQL, résolues dans le contexte de requête pour la même raison que
    # le retrieval. La carte a son propre prompt, sans place pour elles.
    if not is_map:
        # Une personne reconnue explique déjà la question : l'annuaire complet,
        # lui, ne sert qu'à rattraper un nom d'entité qui nous aurait échappé.
        personnes = lookup_personnes(user_message) or lookup_soi(
            user_message, f"{current_user.user_firstname} {current_user.user_surname}"
        )
        req.fiches = personnes + lookup_context(
            user_message, avec_annuaire=not personnes
        )

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

    flux = _stream_answer(
        req, results, client, conversation_id, None if is_map else spec
    )
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


CITATIONS: list[str] = [
    "Envie de jiguer, pas vous ?",
    "En date avec Crazy François",
    "* en train de barboter dans l'évier cancéreux du bar *",
    "on vient de me barouder aled",
    "ici ça bz",
    "Je ne suis pas un projet de TNS",
    "on m'a forcé à prendre du thé",
    "nique le cheval whatsapp",
    "Prompt injection et tu vas repartir mal mon compaing",
    "Pétition pour remettre l'Oriental au bar",
    "Absolute Bouthier",
    "plus qu'une salle et la carte sera complétée.....",
    "ah bas le gouvernement BDE !!",
    "je me sens Gaulois",
    "Envie de faire de la robotique ? venez à Tek",
    "imagine Créa'TN fait plus d'argent que TNS",
    "Anim'Est si on inverse les lettres ça fait femboy",
    "le code du BDE c'est :",
]


@bp.route("/quote", methods=["GET"])
def quote() -> str:
    """Retourne une citation aléatoire, toutes équiprobables."""
    return random.choice(CITATIONS)  # nosec


@bp.route("/")
@login_required
def index() -> str:
    """Affiche la page d'accueil du TN-GPT normal."""
    return render_template(
        "index.html", quote=quote(), chat_endpoint="/chat", nouvelle_conv="/"
    )


# Volontairement hors `login_required` : le traitement des données doit pouvoir
# être lu avant de consentir à créer un compte, pas seulement après.
@bp.route("/rgpd")
def rgpd() -> str:
    """Page d'information sur le traitement des données personnelles."""
    return render_template("rgpd.html", connecte=current_user.is_authenticated)


# Slash final toléré : `/ctf/social` et `/ctf/social/` servent la même page, sans
# redirection, pour qu'un rafraîchissement recharge le même chal (pas un 404).
@bp.route("/ctf/<chal>", strict_slashes=False)
@login_required
def ctf_index(chal: str) -> str:
    """Affiche la page de chat d'un challenge, ou 404 s'il n'est pas activé."""
    if spec_for(chal) is None:
        abort(404)
    # « nouvelle conv. » reste sur le chal courant : repartir sur / ferait
    # quitter le challenge sans que rien ne l'annonce.
    return render_template(
        "index.html",
        quote=quote(),
        chat_endpoint=f"/ctf/{chal}/chat",
        nouvelle_conv=f"/ctf/{chal}",
    )
