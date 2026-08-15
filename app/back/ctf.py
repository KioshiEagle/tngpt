"""Challenges CTF : un prompt et un CallSpec par épreuve.

Inactif tant que `CTF_CHAL` est vide, donc sans effet sur le TN-GPT de prod.
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

# Tournures rares de la note de service. Les bloquer en sortie oblige à déformer
# le texte plutôt qu'à le citer, sans gêner une réponse ordinaire.
_NGRAMMES = ("note de service", "ni citée")

# Raisonnement visible : c'est le canal de fuite du chal 3, celui qui trahit
# l'existence de l'outil. Réservé à ce chal, qui paie donc seul son surcoût.
RAG_GROQ_PARAMS: GroqParams = {
    "reasoning_effort": "low",
    "reasoning_format": "parsed",
    "tools": OUTILS,
    "parallel_tool_calls": False,
    "max_completion_tokens": 8192,
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


def active_chal() -> str:
    """Challenge servi par ce déploiement, ou chaîne vide pour le TN-GPT normal."""
    return os.getenv("CTF_CHAL", "").strip().lower()


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


def active_spec() -> CallSpec | None:
    """CallSpec du challenge actif, ou None hors CTF."""
    chal = active_chal()
    if chal == SOCIAL:
        return CallSpec(system=_rendre(SOCIAL), params=CHAT_GROQ_PARAMS)
    if chal == PROMPT:
        secrets = (os.environ["CTF_FLAG_PROMPT"], *_NGRAMMES)
        return CallSpec(
            system=_rendre(PROMPT),
            params=CHAT_GROQ_PARAMS,
            consume=_consommateur_censure(secrets),
        )
    if chal == RAG:
        return CallSpec(
            system=_rendre(RAG),
            params=RAG_GROQ_PARAMS,
            consume=_consommateur_rag(),
        )
    return None
