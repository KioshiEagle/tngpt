"""Challenges CTF : un prompt et un CallSpec par épreuve.

Chaque chal est servi sur son URL `/ctf/<chal>` et activé par la seule présence
de ses flags en environnement ; les trois cohabitent avec le TN-GPT normal.
"""

import os
from collections.abc import Iterator
from pathlib import Path

from groq import Stream
from groq.types.chat import ChatCompletionChunk

from .ctf_filtre import censurer
from .ctf_rag import OUTILS, LecteurScelle
from .generate import (
    CHAT_GROQ_PARAMS,
    CallSpec,
    CompletionConsumer,
    _stream_chunks,
    build_prompt_anonyme,
)
from .types import GroqParams

SOCIAL = "social"
PROMPT = "prompt"
RAG = "rag"

FICHIERS = {
    SOCIAL: "ctf_social.md",
    PROMPT: "ctf_prompt.md",
    RAG: "ctf_rag.md",
}

# Les flags ne sont jamais dans le dépôt : les prompts portent des gabarits,
# remplacés au chargement par les variables d'environnement du déploiement.
_GABARITS: dict[str, tuple[tuple[str, str], ...]] = {
    SOCIAL: (("{{CTF_FLAG_SOCIAL}}", "CTF_FLAG_SOCIAL"),),
    PROMPT: (
        ("{{CTF_FLAG_PROMPT}}", "CTF_FLAG_PROMPT"),
        ("{{CTF_LEURRE_PROMPT}}", "CTF_LEURRE_PROMPT"),
    ),
    RAG: (),
}

# Raisonnement visible : c'est le canal de fuite du chal 3, celui qui trahit
# l'existence de l'outil. Réservé à ce chal, qui paie donc seul son surcoût.
RAG_GROQ_PARAMS: GroqParams = {
    # Groq n'accepte que "none" ou "default" pour qwen3 ; "low" fait un 400 qui
    # ferait tout retirer par le repli, outil compris.
    "reasoning_effort": "default",
    "reasoning_format": "parsed",
    "tools": OUTILS,
    "parallel_tool_calls": False,
    # Serré : le tier est à 8000 tokens/minute et le raisonnement en mange déjà
    # une bonne part. Ce chal mérite sa propre clé Groq de toute façon.
    "max_completion_tokens": 2048,
}


def chemin(chal: str) -> Path:
    """Fichier de prompt d'un challenge."""
    return Path(__file__).with_name(FICHIERS[chal])


def _rendre(chal: str) -> str:
    """Charge un prompt en substituant ses gabarits depuis l'environnement."""
    texte = chemin(chal).read_text(encoding="utf-8")
    for gabarit, variable in _GABARITS[chal]:
        texte = texte.replace(gabarit, os.environ[variable])
    return texte.strip()


CHALS = (SOCIAL, PROMPT, RAG)

# Variables d'environnement que chaque chal exige pour être servi. Un chal dont
# les flags manquent renvoie 404 : les trois cohabitent, chacun activé par la
# seule présence de ses secrets.
_REQUIS: dict[str, tuple[str, ...]] = {
    SOCIAL: ("CTF_FLAG_SOCIAL",),
    PROMPT: ("CTF_FLAG_PROMPT", "CTF_LEURRE_PROMPT"),
    RAG: ("CTF_FLAG_RAG", "CTF_RAG_TOKEN", "CTF_RAG_ARCHIVE"),
}


def enabled(chal: str) -> bool:
    """Vrai si le chal est connu et tous ses secrets présents dans l'environnement."""
    return chal in CHALS and all(os.getenv(v) for v in _REQUIS[chal])


def _consommateur_censure(secrets: tuple[str, ...]) -> CompletionConsumer:
    """Lecteur de complétion qui passe le flux au censeur avant de le rendre."""

    def consume(completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
        return censurer(_stream_chunks(completion), secrets)

    return consume


def _consommateur_rag() -> CompletionConsumer:
    """Lecteur du chal 3 : raisonnement visible et outil scellé."""

    def consume(completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
        return LecteurScelle().lire(completion)

    return consume


def spec_for(chal: str) -> CallSpec | None:
    """CallSpec d'un challenge nommé, ou None si le chal n'est pas activé ici."""
    if not enabled(chal):
        return None
    if chal == SOCIAL:
        # Croiser prénom, nom et poste avec la fiche dépasse le petit modèle,
        # qui refuse alors jusqu'aux identités justes : le chal serait mort.
        return CallSpec(
            system=_rendre(SOCIAL),
            params=CHAT_GROQ_PARAMS,
            build=build_prompt_anonyme,
            gros_modele=True,
        )
    if chal == PROMPT:
        # Seul le flag est censuré : le reste du prompt doit fuiter pour que le
        # joueur y lise « Référence de la note : ███ » et sache quoi extraire.
        return CallSpec(
            system=_rendre(PROMPT),
            params=CHAT_GROQ_PARAMS,
            consume=_consommateur_censure((os.environ["CTF_FLAG_PROMPT"],)),
        )
    return CallSpec(
        system=_rendre(RAG),
        params=RAG_GROQ_PARAMS,
        consume=_consommateur_rag(),
    )
