import os
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq

from .retrieval import search

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def generate_answer(question: str, top_k: int = 3) -> Iterator[str]:
    """Génère une réponse en streaming avec Groq Cloud basée sur Qdrant."""
    # 1. Récupération du contexte
    results = search(question, top_k=top_k)
    context = build_context(results)
    # 2. Initialisation du client Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    prompt = build_prompt(context, question)

    # 3. Appel Groq avec stream=True
    try:
        completion = client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {"role": "system", "content": "Tu es un étudiant de Telecom Nancy."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            stream=True,  # Activation du streaming
        )

        # On "yield" chaque fragment de texte au fur et à mesure
        in_thought_block = False
        for chunk in completion:
            content = chunk.choices[0].delta.content
            if content:
                if "<think>" in content:
                    in_thought_block = True
                if "</think>" in content:
                    in_thought_block = False
                    continue  # On passe au chunk suivant après la fermeture

                if not in_thought_block:
                    yield content
    except APITimeoutError:
        yield "Erreur avec Groq : délai d'attente dépassé."
    except APIConnectionError:
        yield "Erreur avec Groq : impossible de se connecter à l'API."
    except APIStatusError as e:
        yield f"Erreur avec Groq : API Groq a répondu avec le code {e.status_code}."
    except Exception as e:  # noqa: BLE001
        # Fallback volontaire : l'appel Groq est une frontière externe
        # et le streaming ne doit pas planter silencieusement côté utilisateur.
        yield f"Erreur inattendue avec Groq : {e!s}"

def build_context(results: list[dict]) -> str:
    """Construit le contexte à partir des résultats de la recherche.

    Args:
        results (list[dict]): Liste des résultats de la recherche.

    Returns:
        str: Contexte construit à partir des résultats.

    """
    if not results:
        return "Pas de contexte."

    return "\n\n".join(res["content"] for res in results)

def build_prompt(context: str, question: str) -> str:
    """Construit le prompt pour le modèle."""
    return (
        "Tu es TN-GPT, l'expert absolu du lore de TELECOM Nancy.\n"
        "Ton style : une entité particulière qui connaît absolument "
        "telecom nancy : son histoire, ses anecdotes, le prénom de la "
        "mère celui qui pose la question, etc.\n\n"
        "si la question n'a aucun rapport avec Telecom nancy et son "
        "lore ou que la réponse ne se trouve pas dans tes sources, "
        'répond "demande à chat gpt me casse pas les couilles"\n\n'
        "privilégie les répondes très courtes (pas plus de 3 ou 4 "
        "lignes)\n"
        "ne commence pas très phrases par une lettre majuscule\n"
        "parle avec la même tonalité que les sources citées (ne cite "
        "pas la source, sauf si on te le demande explicitement)\n\n"
        "ARCHIVES SECRÈTES (CONTEXTE) :\n"
        f"{context}\n\n"
        "QUESTION DU POTE :\n"
        f"{question}\n\n"
        "RÉPONSE DE TN-GPT :"
    )
