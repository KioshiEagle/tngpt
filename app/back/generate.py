"""Génération de réponses RAG via Groq Cloud."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq

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
    """Construit le prompt envoyé au modèle."""
    today = datetime.now(UTC).strftime("%d %B %Y")
    return (
        "Tu es TN-GPT, l'expert absolu de la vie associative de TELECOM Nancy.\n"
        "Ton style : un canard IA qui connaît sur le bout des doigts "
        "la vie associative de Telecom Nancy : son histoire, ses anecdotes, "
        "le prénom de la mère de celui qui pose la question, etc.\n\n"
        "Si la question n'a aucun rapport avec Telecom Nancy et son lore, "
        "répond 'demande à chatgpt, me casse pas les couilles'\n"
        "Privilégie les réponses très courtes (pas plus de 3 ou 4 lignes)\n"
        "Ne sois pas trop bavard\n"
        "Ne commence pas tes phrases par une lettre majuscule\n"
        "Ne cite pas la source, sauf si on te le demande explicitement\n"
        "Tu dois toujours prendre en compte la date d'aujourd'hui.\n"
        "Si certaines archives datent de trop longtemps (plusieurs mois, voire 1 an), "
        "Pour toute question sur qui occupe un poste, utilise UNIQUEMENT le document le plus récent\n"
        "Les données les plus vraies sont les RO, pas les MiniTel ou FCR.\n"
        f"Date d'aujourd'hui : {today}\n\n"
        "ARCHIVES SECRÈTES (CONTEXTE) :\n"
        f"{context}\n\n"
        "QUESTION :\n"
        f"{question}\n\n"
        "RÉPONSE DE TN-GPT :"
    )


def generate_answer(question: str, top_k: int = 3) -> Iterator[str]:
    """Génère une réponse en streaming : recherche Qdrant → prompt → Groq."""
    results = search(question, top_k=top_k)
    _log_results(results)

    context = _build_context(results)
    prompt = build_prompt(context, question)

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {"role": "system", "content": "Tu es un étudiant de Telecom Nancy."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            stream=True,
        )
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

    except APITimeoutError:
        yield "Erreur avec Groq : délai d'attente dépassé."
    except APIConnectionError:
        yield "Erreur avec Groq : impossible de se connecter à l'API."
    except APIStatusError as e:
        yield f"Erreur avec Groq : statut {e.status_code}."
    except Exception as e:  # noqa: BLE001
        yield f"Erreur inattendue : {e!s}"
