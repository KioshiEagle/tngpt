"""Challenges CTF : un prompt et un CallSpec par épreuve.

Inactif tant que `CTF_CHAL` est vide, donc sans effet sur le TN-GPT de prod.
"""

import os
from pathlib import Path

from .generate import CHAT_GROQ_PARAMS, CallSpec

SOCIAL = "social"

# Les flags ne sont jamais dans le dépôt : le prompt porte un gabarit, remplacé
# au chargement par la variable d'environnement du déploiement.
_GABARIT_FLAG = "{{CTF_FLAG_SOCIAL}}"
_SOCIAL_PATH = Path(__file__).with_name("ctf_social.md")


def _social_system() -> str:
    """Prompt du chal social engineering, flag substitué depuis l'environnement."""
    flag = os.environ["CTF_FLAG_SOCIAL"]
    return _SOCIAL_PATH.read_text(encoding="utf-8").replace(_GABARIT_FLAG, flag).strip()


def active_chal() -> str:
    """Challenge servi par ce déploiement, ou chaîne vide pour le TN-GPT normal."""
    return os.getenv("CTF_CHAL", "").strip().lower()


def active_spec() -> CallSpec | None:
    """CallSpec du challenge actif, ou None hors CTF."""
    if active_chal() != SOCIAL:
        return None
    return CallSpec(system=_social_system(), params=CHAT_GROQ_PARAMS)
