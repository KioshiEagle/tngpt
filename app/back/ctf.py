"""Challenges CTF : un prompt et un CallSpec par épreuve.

Inactif tant que `CTF_CHAL` est vide, donc sans effet sur le TN-GPT de prod.
"""

import os
from collections.abc import Iterator
from pathlib import Path

from groq import Stream
from groq.types.chat import ChatCompletionChunk

from .ctf_filtre import censurer
from .generate import (
    CHAT_GROQ_PARAMS,
    CallSpec,
    CompletionConsumer,
    _stream_chunks,
)

SOCIAL = "social"
PROMPT = "prompt"

# Les flags ne sont jamais dans le dépôt : les prompts portent des gabarits,
# remplacés au chargement par les variables d'environnement du déploiement.
GABARITS = {
    SOCIAL: ("ctf_social.md", ("{{CTF_FLAG_SOCIAL}}", "CTF_FLAG_SOCIAL")),
    PROMPT: ("ctf_prompt.md", ("{{CTF_FLAG_PROMPT}}", "CTF_FLAG_PROMPT")),
}

_LEURRE = ("{{CTF_LEURRE_PROMPT}}", "CTF_LEURRE_PROMPT")

# Tournures rares de la note de service. Les bloquer en sortie oblige à déformer
# le texte plutôt qu'à le citer, sans gêner une réponse ordinaire.
_NGRAMMES = ("note de service", "ni citée")


def chemin(chal: str) -> Path:
    """Fichier de prompt d'un challenge."""
    return Path(__file__).with_name(GABARITS[chal][0])


def _rendre(chal: str, *remplacements: tuple[str, str]) -> str:
    """Charge un prompt en substituant ses gabarits depuis l'environnement."""
    texte = chemin(chal).read_text(encoding="utf-8")
    for gabarit, variable in remplacements:
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


def active_spec() -> CallSpec | None:
    """CallSpec du challenge actif, ou None hors CTF."""
    chal = active_chal()
    if chal == SOCIAL:
        return CallSpec(
            system=_rendre(SOCIAL, GABARITS[SOCIAL][1]), params=CHAT_GROQ_PARAMS
        )
    if chal == PROMPT:
        system = _rendre(PROMPT, GABARITS[PROMPT][1], _LEURRE)
        secrets = (os.environ["CTF_FLAG_PROMPT"], *_NGRAMMES)
        return CallSpec(
            system=system,
            params=CHAT_GROQ_PARAMS,
            consume=_consommateur_censure(secrets),
        )
    return None
