import logging
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq, Stream
from groq.types.chat import ChatCompletionChunk, ChatCompletionMessageParam

from .groqpool import acquire
from .retrieval import search
from .types import GroqParams, HistoryMessage, SearchResult

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# Modèle Groq de génération. Configurable : Groq retire régulièrement des modèles
# (qwen/qwen3-32b a ainsi disparu au profit de qwen/qwen3.6-27b).
_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "qwen/qwen3.6-27b")

# Le prompt système vit dans un fichier à part : c'est de la prose qu'on relit
# et qu'on révise comme de la documentation, pas une constante Python noyée
# entre deux fonctions. Lu une fois à l'import — il est rigoureusement identique
# d'une requête à l'autre, ce qui laisse un préfixe stable aux appels Groq.
CHAT_SYSTEM = (
    (Path(__file__).with_name("system_prompt.md")).read_text(encoding="utf-8").strip()
)

# Le message utilisateur ne porte que des données : les repères d'exécution, les
# archives retrouvées et la question. Toutes les règles sont dans `CHAT_SYSTEM`,
# et rien de ce qui vient de Qdrant ne peut donc passer pour une consigne.
_PROMPT_TEMPLATE = (
    "<contexte_execution>\n"
    "Date du jour : {today}\n"
    "{user_line}"
    "</contexte_execution>\n\n"
    "<archives>\n"
    "{context}\n"
    "</archives>\n\n"
    "<question>\n"
    "{question}\n"
    "</question>\n"
)

_MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

_HTTP_400 = 400
_HTTP_429 = 429
_HTTP_413 = 413
_MAX_RETRIES = 3
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_EMPTY_ANSWER = "j'ai perdu le fil sur ce coup-là, tu peux reformuler ?"


@dataclass
class GenerateRequest:
    """Paramètres d'une requête de génération de réponse."""

    question: str
    history: list[HistoryMessage] = field(default_factory=list)
    top_k: int = 5
    user_name: str | None = None
    # Fiches des clubs cités, tirées du SQL par `clubs.lookup_context` et placées
    # devant les archives. Vide par défaut : les appelants qui ne les remplissent
    # pas (bancs Optuna, carte des mers) gardent exactement l'ancien prompt.
    fiches: str = ""


# Signature d'un constructeur de prompt : (contexte, question, user_name) -> prompt.
# Permet de réutiliser toute la logique de repli Groq avec un autre prompt que
# celui du chat (voir `seamap.build_map_prompt`).
PromptBuilder = Callable[..., str]

# Lecteur d'une complétion Groq. Le chat lit `delta.content` ; la carte lit
# `delta.tool_calls`. Une seule échelle de repli, deux façons de la consommer.
CompletionConsumer = Callable[[Stream[ChatCompletionChunk]], Iterator[str]]


def _build_context(results: list[SearchResult]) -> str:
    if not results:
        return "Pas de contexte."
    parts = []
    for res in results:
        m = res["metadata"]
        title = m.get("title") or m.get("source", "source inconnue")
        header = f"[Source: {title} | Date: {m.get('date', 'date inconnue')}]"
        parts.append(f"{header}\n{res['content']}")
    return "\n\n".join(parts)


def _log_results(results: list[SearchResult]) -> None:
    if not results:
        logger.warning("Aucun document trouvé dans Qdrant.")
        return
    logger.debug("--- DOCUMENTS UTILISÉS ---")
    for i, res in enumerate(results):
        m = res["metadata"]
        title = m.get("title") or m.get("source", "Inconnue")
        logger.debug(
            "[%d] %s | Auteur: %s | Date: %s | Score: %.4f "
            "(sem: %.4f, fraîcheur: %.4f)\n    Extrait: %s...",
            i + 1,
            title,
            m.get("author", "?"),
            m.get("date", "?"),
            res["score"],
            res["semantic_score"],
            res["freshness_score"],
            res["content"][:150],
        )


def today_fr() -> str:
    """Date du jour en français, sans dépendre de la locale du processus.

    `strftime("%B")` rend le mois dans la locale courante, soit « August » sous
    la locale C du conteneur — un repère anglais au milieu d'un prompt français.
    """
    now = datetime.now(UTC)
    return f"{now.day} {_MOIS[now.month - 1]} {now.year}"


def build_prompt(context: str, question: str, user_name: str | None = None) -> str:
    """Construit le message utilisateur : repères d'exécution, archives, question."""
    user_line = f"Utilisateur connecté : {user_name}\n" if user_name else ""
    return _PROMPT_TEMPLATE.format(
        today=today_fr(),
        user_line=user_line,
        context=context,
        question=question,
    )


class _ThinkFilter:
    """Filtre les blocs <think>...</think> d'un flux de texte, même coupés entre chunks.

    Le tag peut arriver fragmenté entre plusieurs chunks du stream Groq : le
    buffer retient donc la fin de chunk tant qu'elle ne peut pas encore être
    reconnue comme faisant partie (ou non) d'un tag.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_thought = False

    def feed(self, text: str) -> Iterator[str]:
        """Ajoute du texte au buffer et cède les segments hors <think> prêts."""
        self._buf += text
        piece = self._pop_before_tag()
        while piece is not None:
            if piece:
                yield piece
            piece = self._pop_before_tag()
        trimmed = self._pop_safe_tail()
        if trimmed:
            yield trimmed

    def flush(self) -> Iterator[str]:
        """Cède ce qu'il reste du buffer en fin de stream."""
        if self._buf and not self._in_thought:
            yield self._buf
        self._buf = ""

    def _pop_before_tag(self) -> str | None:
        """Si le tag courant est trouvé, bascule l'état et retourne le texte avant.

        None si le tag n'est pas (encore) présent dans le buffer.
        """
        tag = _THINK_CLOSE if self._in_thought else _THINK_OPEN
        idx = self._buf.find(tag)
        if idx == -1:
            return None
        before = self._buf[:idx] if not self._in_thought else ""
        self._buf = self._buf[idx + len(tag) :]
        self._in_thought = not self._in_thought
        return before

    def _pop_safe_tail(self) -> str:
        """Hors <think>, cède le buffer sauf la fin pouvant amorcer un tag."""
        keep = len(_THINK_OPEN) - 1
        if self._in_thought or len(self._buf) <= keep:
            return ""
        piece, self._buf = self._buf[:-keep], self._buf[-keep:]
        return piece


def _filter_think(completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
    """Streame les chunks en filtrant les blocs <think>...</think>, même multi-chunk."""
    think_filter = _ThinkFilter()
    for chunk in completion:
        raw = chunk.choices[0].delta.content
        if raw:
            yield from think_filter.feed(raw)
    yield from think_filter.flush()


def _stream_chunks(completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
    """Filtre les <think> en garantissant une sortie non vide.

    Un flux qui s'arrête avant la balise fermante voit tout son contenu filtré :
    la réponse partirait alors vide, et le front — qui ignore un premier morceau
    blanc — n'afficherait aucune bulle, sans la moindre erreur. Un message vaut
    mieux que le silence.
    """
    produced = False
    for piece in _filter_think(completion):
        if piece.strip():
            produced = True
        yield piece
    if not produced:
        yield _EMPTY_ANSWER


@dataclass(frozen=True)
class CallSpec:
    """Comment adresser le modèle : quels rôles, quels paramètres, quelle lecture.

    L'ensemble varie d'un bloc — le chat streame du texte sous les règles de
    `system_prompt.md`, la carte force un outil, porte ses propres règles dans
    son message utilisateur et lit les arguments de l'appel.
    """

    system: str = CHAT_SYSTEM
    build: PromptBuilder = build_prompt
    params: GroqParams | None = None
    consume: CompletionConsumer = _stream_chunks
    # Le chat restitue des archives, il n'a rien à inventer : une température
    # basse va dans le sens des règles d'ancrage plutôt que contre elles.
    temperature: float = 0.3
    # La carte est un coup unique : les tours passés ne l'aident pas à énumérer
    # des clubs, ils ne feraient qu'alourdir un appel d'outil déjà contraint.
    send_history: bool = True


# Le chat est un RAG simple : le modèle restitue le contexte, il n'a rien à
# raisonner. Laissé libre, qwen3 part en <think> sur un prompt de cette taille
# et y épuise son budget de complétion — la balise fermante n'arrive jamais, le
# filtre jette tout et l'utilisateur reçoit une réponse vide. `hidden` garantit
# en plus qu'aucun <think> ne transite par `delta.content`.
CHAT_GROQ_PARAMS: GroqParams = {
    "reasoning_effort": "none",
    "reasoning_format": "hidden",
}

CHAT_SPEC = CallSpec(params=CHAT_GROQ_PARAMS)


# Longueur retenue d'un tour passé. Une réponse de chat tient en trois ou quatre
# lignes, mais une carte au trésor persiste sa charge JSON dans la conversation :
# sans plafond, un seul tour de carte mangerait le budget de la minute.
_HISTORY_MAX_CHARS = 500


def _trim(content: str) -> str:
    """Tronque un tour passé, en signalant la coupe au modèle."""
    if len(content) <= _HISTORY_MAX_CHARS:
        return content
    return content[:_HISTORY_MAX_CHARS] + "…"


def _history_messages(
    history: list[HistoryMessage],
) -> list[ChatCompletionMessageParam]:
    """Rejoue les tours passés de la conversation, dans l'ordre.

    Sans archives : elles ne sont plus disponibles, et une réponse passée n'est
    pas une source. C'est le fil de l'échange qu'on rend au modèle, pas un
    second corpus.
    """
    messages: list[ChatCompletionMessageParam] = []
    for message in history:
        content = _trim(message["content"])
        if message["role"] == "user":
            messages.append({"role": "user", "content": content})
        elif message["role"] == "assistant":
            messages.append({"role": "assistant", "content": content})
    return messages


def _enrich_query(question: str, history: list[HistoryMessage], n: int = 2) -> str:
    """Enrichit la query Qdrant avec les N derniers échanges Q/R de l'historique."""
    pairs: list[str] = []
    i = len(history) - 1
    while i >= 1 and len(pairs) < n:
        if history[i]["role"] == "assistant" and history[i - 1]["role"] == "user":
            q = history[i - 1]["content"]
            r = history[i]["content"]
            pairs.insert(0, f"Q: {q} R: {r}")
            i -= 2
        else:
            i -= 1
    if not pairs:
        return question
    return " | ".join(pairs) + f" | {question}"


def retrieve(req: GenerateRequest) -> list[SearchResult]:
    """Enrichit la question avec l'historique puis interroge Qdrant.

    Exposé séparément de la génération pour que l'appelant puisse journaliser
    les chunks retrouvés avant que le streaming ne commence.
    """
    enriched = _enrich_query(req.question, req.history)
    results = search(enriched, top_k=req.top_k)
    _log_results(results)
    return results


def generate_answer(
    req: GenerateRequest,
    results: list[SearchResult] | None = None,
    client: Groq | None = None,
    spec: CallSpec = CHAT_SPEC,
) -> Iterator[str]:
    """Génère une réponse en streaming : enrichissement → Qdrant → prompt → Groq.

    `results` évite de refaire la recherche quand l'appelant l'a déjà effectuée.
    `client` est le client Groq choisi dans le pool par l'appelant ; à défaut,
    une clé est prélevée du pool ici.
    `spec` substitue un autre prompt et une autre lecture de la complétion — la
    carte au trésor — tout en conservant l'échelle de repli Groq.
    """
    if results is None:
        results = retrieve(req)
    if client is None:
        client, _ = acquire()
    yield from _stream_with_retries(req, results, client, spec)


@dataclass
class _RetryOutcome:
    """Ce qu'il faut faire après une tentative d'appel Groq ratée."""

    retry: bool = False
    backoff: bool = False
    smaller_context: bool = False
    drop_params: bool = False
    error_message: str | None = None


def _retry_for_status(
    status: int, *, can_retry: bool, has_params: bool
) -> _RetryOutcome | None:
    """Repli applicable à un statut Groq. None si rien à retenter."""
    if not can_retry:
        return None
    if status == _HTTP_429:
        return _RetryOutcome(retry=True, backoff=True)
    if status == _HTTP_413:
        return _RetryOutcome(retry=True, smaller_context=True)
    # Paramètres refusés (modèle configuré via GROQ_CHAT_MODEL qui ne les
    # supporte pas) : on retente sans eux plutôt que d'échouer.
    if status == _HTTP_400 and has_params:
        return _RetryOutcome(retry=True, drop_params=True)
    return None


def _classify_error(
    e: Exception,
    attempt: int,
    max_retries: int,
    *,
    has_params: bool = False,
) -> _RetryOutcome:
    """Décide, pour une erreur Groq donnée, s'il faut réessayer et comment."""
    if isinstance(e, APIStatusError):
        outcome = _retry_for_status(
            e.status_code,
            can_retry=attempt < max_retries - 1,
            has_params=has_params,
        )
        if outcome is not None:
            return outcome
        logger.error("Erreur Groq : statut %d", e.status_code, exc_info=e)
        msg = f"Erreur avec Groq : statut {e.status_code}."
        return _RetryOutcome(error_message=msg)
    if isinstance(e, APITimeoutError):
        logger.error("Erreur Groq : timeout", exc_info=e)
        return _RetryOutcome(
            error_message="Erreur avec Groq : délai d'attente dépassé."
        )
    if isinstance(e, APIConnectionError):
        logger.error("Erreur Groq : connexion impossible", exc_info=e)
        return _RetryOutcome(
            error_message="Erreur avec Groq : impossible de se connecter à l'API."
        )
    logger.error("Erreur inattendue dans generate_answer", exc_info=e)
    return _RetryOutcome(error_message=f"Erreur inattendue : {e!s}")


def _attempt(
    req: GenerateRequest,
    results: list[SearchResult],
    client: Groq,
    spec: CallSpec,
    params: GroqParams,
) -> Iterator[str]:
    """Un essai : construit le prompt, appelle Groq et cède la complétion lue."""
    # Les fiches passent devant les archives et survivent au repli 413, qui
    # ne rogne que `results` : c'est la partie courte du contexte, et la seule
    # qui fasse autorité.
    context = req.fiches + _build_context(results)
    prompt = spec.build(context, req.question, user_name=req.user_name)
    # Les tours passés s'intercalent entre les règles et la question courante :
    # seule celle-ci porte des archives, ce qui garde la frontière nette entre
    # ce dont TN-GPT se souvient et ce sur quoi il peut s'appuyer.
    history = _history_messages(req.history) if spec.send_history else []
    completion = client.chat.completions.create(
        model=_CHAT_MODEL,
        messages=[
            {"role": "system", "content": spec.system},
            *history,
            {"role": "user", "content": prompt},
        ],
        temperature=spec.temperature,
        stream=True,
        **params,
    )
    yield from spec.consume(completion)


def _apply(
    outcome: _RetryOutcome,
    results: list[SearchResult],
    params: GroqParams,
    attempt: int,
) -> tuple[list[SearchResult], GroqParams]:
    """Applique le repli décidé avant de relancer un essai."""
    if outcome.smaller_context:
        results = results[: max(1, len(results) // 2)]
    if outcome.drop_params:
        logger.warning(
            "Groq refuse %s pour %s : nouvel essai sans ces paramètres.",
            sorted(params),
            _CHAT_MODEL,
        )
        params = {}
    if outcome.backoff:
        time.sleep(2**attempt)
    return results, params


def _stream_with_retries(
    req: GenerateRequest,
    results: list[SearchResult],
    client: Groq,
    spec: CallSpec = CHAT_SPEC,
) -> Iterator[str]:
    """Appelle Groq avec repli (429 : backoff, 413 : moins de contexte)."""
    current_results = results
    current_params: GroqParams = spec.params if spec.params is not None else {}

    for attempt in range(_MAX_RETRIES):
        try:
            yield from _attempt(req, current_results, client, spec, current_params)
        except Exception as e:  # noqa: BLE001
            outcome = _classify_error(
                e, attempt, _MAX_RETRIES, has_params=bool(current_params)
            )
            if not outcome.retry:
                # _classify_error garantit un message quand retry est False.
                assert outcome.error_message is not None
                yield outcome.error_message
                return
            current_results, current_params = _apply(
                outcome, current_results, current_params, attempt
            )
        else:
            return
