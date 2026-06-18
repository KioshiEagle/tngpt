import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq, Stream
from groq.types.chat import ChatCompletionChunk

from .retrieval import search

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _build_context(results: list[dict]) -> str:
    """Assemble le contexte à injecter dans le prompt."""
    if not results:
        return "Pas de contexte."
    parts = []
    for res in results:
        m = res["metadata"]
        title = m.get("title") or m.get("source", "source inconnue")
        doc_date = m.get("date", "date inconnue")
        parts.append(f"[Source: {title} | Date: {doc_date}]\n{res['content']}")
    return "\n\n".join(parts)


def _log_results(results: list[dict]) -> None:
    """Affiche dans les logs les documents retenus pour la réponse."""
    print("\n--- DOCUMENTS UTILISÉS ---")
    if not results:
        print("⚠️ AUCUN DOCUMENT TROUVÉ DANS QDRANT")
        return
    for i, res in enumerate(results):
        m = res["metadata"]
        title = m.get("title") or m.get("source", "Inconnue")
        print(
            f"[{i + 1}] {title} | Auteur: {m.get('author', '?')} "
            f"| Date: {m.get('date', '?')}"
        )
        print(
            f"     Score: {res.get('score', 0):.4f}  "
            f"(sem: {res.get('semantic_score', 0):.4f}, "
            f"fraîcheur: {res.get('freshness_score', 0):.4f})"
        )
        print(f"     Extrait: {res['content'][:150]}...")
        print("-" * 40)


def build_prompt(context: str, question: str) -> str:
    """Construit le prompt système + utilisateur envoyé au modèle."""
    today = datetime.now(UTC).strftime("%d %B %Y")
    return (
        "Tu es TN-GPT, l'expert absolu de la vie associative de TELECOM Nancy.\n"
        "Ton style : un canard IA décontracté qui connaît sur le bout des doigts "
        "la vie associative de Telecom Nancy : son histoire, ses anecdotes, "
        "ses événements.\n\n"
        "Règles strictes :\n"
        "- Si l'utilisateur envoie une seule lettre de l'alphabet, "
        "réponds UNIQUEMENT la lettre suivante dans l'alphabet (ex: a→b, b→c, z→a). "
        "Rien d'autre.\n"
        "- Pour les simples salutations (Hey, Bonjour, Salut...), "
        "réponds juste par une courte salutation.\n"
        "- Si la question porte clairement sur autre chose que Telecom Nancy, "
        "réponds UNIQUEMENT : 'demande à chatgpt, me casse pas les couilles'\n"
        "- Ne mélange JAMAIS une réponse normale et un message off-topic.\n"
        "- N'invente jamais d'informations ou de noms de personnes.\n"
        "- Si la réponse factuelle ne figure pas explicitement "
        "dans le contexte fourni, "
        "réponds : 'je sais pas, je trouve pas dans mes archives'\n"
        "- Privilégie les réponses très courtes (3-4 lignes max).\n"
        "- Ne commence pas tes phrases par une lettre majuscule.\n"
        "- Ne cite pas la source, sauf si on te le demande explicitement.\n"
        "- En cas de doute entre plusieurs archives, préfère la plus récente.\n\n"
        "Sources officielles :\n"
        "- Les Réunions Ouvertes (RO) sont la référence "
        "pour les postes officiels du BDE. "
        "Dans un RO, la section 'Membres du bureau présents' "
        "liste les membres du bureau BDE "
        "(format 'NOM Prénom - Fonction'). "
        "Les sections suivantes dans le même document "
        "concernent les clubs votés en réunion, pas le bureau BDE.\n"
        "- Les comptes-rendus informels (FCR, signés par un prénom seul "
        "ou auteur inconnu) "
        "utilisent des pseudonymes — ignore-les pour tout poste officiel.\n\n"
        f"Date d'aujourd'hui : {today}\n\n"
        "ARCHIVES SECRÈTES (CONTEXTE) :\n"
        f"{context}\n\n"
        "QUESTION :\n"
        f"{question}\n\n"
        "RÉPONSE DE TN-GPT :"
    )


_HTTP_429 = 429


def _stream_chunks(completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
    in_thought_block = False
    for chunk in completion:
        content = chunk.choices[0].delta.content
        if content:
            if "<think>" in content:
                in_thought_block = True
            if "</think>" in content:
                in_thought_block = False
                continue
            if not in_thought_block:
                yield content


def generate_answer(question: str, top_k: int = 3) -> Iterator[str]:
    """Génère une réponse en streaming : recherche Qdrant → prompt → Groq."""
    results = search(question, top_k=top_k)
    _log_results(results)

    context = _build_context(results)
    prompt = build_prompt(context, question)

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    max_retries = 3
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model="qwen/qwen3-32b",
                messages=[
                    {
                        "role": "system",
                        "content": "Tu es un étudiant de Telecom Nancy.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                stream=True,
            )
            yield from _stream_chunks(completion)
        except APIStatusError as e:
            if e.status_code == _HTTP_429 and attempt < max_retries - 1:
                time.sleep(2**attempt)
                continue
            yield f"Erreur avec Groq : statut {e.status_code}."
            return
        except APITimeoutError:
            yield "Erreur avec Groq : délai d'attente dépassé."
            return
        except APIConnectionError:
            yield "Erreur avec Groq : impossible de se connecter à l'API."
            return
        except Exception as e:  # noqa: BLE001
            yield f"Erreur inattendue : {e!s}"
            return
        else:
            return
