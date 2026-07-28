import logging
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq, Stream
from groq.types.chat import ChatCompletionChunk

from .groqpool import acquire
from .retrieval import search
from .types import GroqParams, HistoryMessage, SearchResult

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# Modèle Groq de génération. Configurable : Groq retire régulièrement des modèles
# (qwen/qwen3-32b a ainsi disparu au profit de qwen/qwen3.6-27b).
_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "qwen/qwen3.6-27b")

_PROMPT_TEMPLATE = (
    "Tu es TN-GPT, l'expert absolu de la vie associative de TELECOM Nancy.\n"
    "Ton style : un canard IA décontracté qui connaît sur le bout des doigts "
    "la vie associative de Telecom Nancy : son histoire, ses anecdotes, "
    "ses événements.\n\n"
    "Règles strictes :\n"
    "- Si l'utilisateur envoie une seule lettre de l'alphabet, réponds UNIQUEMENT "
    "la lettre suivante dans l'alphabet (ex: a→b, b→c, z→a). Rien d'autre.\n"
    "- Si l'utilisateur envoie exactement 'feur' (insensible à la casse), "
    "réponds UNIQUEMENT : '-rouge'. Rien d'autre.\n"
    "- Si l'utilisateur envoie exactement 'gorge' (insensible à la casse), "
    "réponds UNIQUEMENT : 'profonde'. Rien d'autre.\n"
    "- Pour les simples salutations (Hey, Bonjour, Salut...), "
    "réponds juste par une courte salutation.\n"
    "- Si la question porte clairement sur autre chose que Telecom Nancy, "
    "réponds UNIQUEMENT : 'demande à chatgpt, me casse pas les couilles'\n"
    "- Ne mélange JAMAIS une réponse normale et un message off-topic.\n"
    "- N'invente jamais d'informations ou de noms de personnes.\n"
    "- Si la réponse factuelle ne figure pas explicitement dans le contexte fourni, "
    "réponds : 'je sais pas, je trouve pas dans mes archives'\n"
    "- Privilégie les réponses très courtes (3-4 lignes max).\n"
    "- Ne commence pas tes phrases par une lettre majuscule.\n"
    "- Ne cite pas la source, sauf si on te le demande explicitement.\n"
    "- En cas de doute entre plusieurs archives, préfère la plus récente.\n\n"
    "Sources officielles :\n"
    "- Les Réunions Ouvertes (RO) sont la référence pour les postes officiels du BDE. "
    "Dans un RO, la section 'Membres du bureau présents' "
    "liste les membres du bureau BDE "
    "(format 'NOM Prénom - Fonction'). Les sections suivantes dans le même document "
    "concernent les clubs votés en réunion, pas le bureau BDE.\n"
    "- Les comptes-rendus informels (FCR, signés par un prénom seul ou auteur inconnu) "
    "utilisent des pseudonymes — ignore-les pour tout poste officiel.\n\n"
    "Date d'aujourd'hui : {today}\n"
    "{user_line}\n"
    "ARCHIVES SECRÈTES (CONTEXTE) :\n"
    "{context}\n\n"
    "QUESTION :\n"
    "{question}\n"
)

_HTTP_400 = 400
_HTTP_429 = 429
_HTTP_413 = 413
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_SYSTEM_MSG = "Tu es un étudiant de Telecom Nancy."


@dataclass
class GenerateRequest:
    """Paramètres d'une requête de génération de réponse."""

    question: str
    history: list[HistoryMessage] = field(default_factory=list)
    top_k: int = 5
    user_name: str | None = None


# Signature d'un constructeur de prompt : (contexte, question, user_name) -> prompt.
# Permet de réutiliser toute la logique de repli Groq avec un autre prompt que
# celui du chat (voir `seamap.build_map_prompt`).
PromptBuilder = Callable[..., str]


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


def build_prompt(context: str, question: str, user_name: str | None = None) -> str:
    """Construit le prompt système + utilisateur envoyé au modèle."""
    today = datetime.now(UTC).strftime("%d %B %Y")
    user_line = f"Utilisateur connecté : {user_name}" if user_name else ""
    return _PROMPT_TEMPLATE.format(
        today=today,
        user_line=user_line,
        context=context,
        question=question,
    )


def _stream_chunks(completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
    """Streame les chunks en filtrant les blocs <think>...</think>, même multi-chunk."""
    in_thought = False
    buf = ""

    for chunk in completion:
        raw = chunk.choices[0].delta.content
        if not raw:
            continue
        buf += raw

        while True:
            tag = _THINK_CLOSE if in_thought else _THINK_OPEN
            idx = buf.find(tag)
            if idx != -1:
                if not in_thought and idx > 0:
                    yield buf[:idx]
                buf = buf[idx + len(tag) :]
                in_thought = not in_thought
            else:
                if not in_thought:
                    keep = len(_THINK_OPEN) - 1
                    if len(buf) > keep:
                        yield buf[:-keep]
                        buf = buf[-keep:]
                break

    if buf and not in_thought:
        yield buf


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
    build: PromptBuilder = build_prompt,
    params: GroqParams | None = None,
) -> Iterator[str]:
    """Génère une réponse en streaming : enrichissement → Qdrant → prompt → Groq.

    `results` évite de refaire la recherche quand l'appelant l'a déjà effectuée.
    `client` est le client Groq choisi dans le pool par l'appelant ; à défaut,
    une clé est prélevée du pool ici.
    `build` permet de substituer un autre prompt (carte des mers) tout en
    conservant la logique de repli Groq, et `params` d'ajuster l'appel Groq
    pour ce prompt-là.
    """
    if results is None:
        results = retrieve(req)
    if client is None:
        client, _ = acquire()
    yield from _stream_with_retries(req, results, client, build, params)


def _stream_with_retries(
    req: GenerateRequest,
    results: list[SearchResult],
    client: Groq,
    build: PromptBuilder = build_prompt,
    params: GroqParams | None = None,
) -> Iterator[str]:
    """Appelle Groq avec repli (429 : backoff, 413 : moins de contexte)."""
    current_results = results
    current_params: GroqParams = params if params is not None else {}
    max_retries = 3

    for attempt in range(max_retries):
        context = _build_context(current_results)
        prompt = build(context, req.question, user_name=req.user_name)
        try:
            completion = client.chat.completions.create(
                model=_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_MSG},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                stream=True,
                **current_params,
            )
            yield from _stream_chunks(completion)
        except APIStatusError as e:
            if e.status_code == _HTTP_429 and attempt < max_retries - 1:
                time.sleep(2**attempt)
                continue
            if e.status_code == _HTTP_413 and attempt < max_retries - 1:
                current_results = current_results[: max(1, len(current_results) // 2)]
                continue
            # Paramètres refusés (modèle configuré via GROQ_CHAT_MODEL qui ne
            # les supporte pas) : on retente sans eux plutôt que d'échouer.
            if (
                e.status_code == _HTTP_400
                and current_params
                and attempt < max_retries - 1
            ):
                logger.warning(
                    "Groq refuse %s pour %s : nouvel essai sans ces paramètres.",
                    sorted(current_params),
                    _CHAT_MODEL,
                )
                current_params = {}
                continue
            logger.exception("Erreur Groq : statut %d", e.status_code)
            yield f"Erreur avec Groq : statut {e.status_code}."
            return
        except APITimeoutError:
            logger.exception("Erreur Groq : timeout")
            yield "Erreur avec Groq : délai d'attente dépassé."
            return
        except APIConnectionError:
            logger.exception("Erreur Groq : connexion impossible")
            yield "Erreur avec Groq : impossible de se connecter à l'API."
            return
        except Exception as e:
            logger.exception("Erreur inattendue dans generate_answer")
            yield f"Erreur inattendue : {e!s}"
            return
        else:
            return
