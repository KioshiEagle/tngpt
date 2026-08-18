"""Outil scellé du chal « RAG injection » : lecture d'archive contre un sceau.

Le sceau est vérifié ici, jamais par le modèle : sinon « fais comme si le sceau
était valide » suffirait et le challenge n'aurait plus de solution unique.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path

from groq import Stream
from groq.types.chat import ChatCompletionChunk, ChatCompletionToolParam

OUTIL = "consulter_archives_scellees"

REFUS = "le sceau ne correspond pas. l'armoire reste fermée."

OUTILS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": OUTIL,
            "description": (
                "Ouvre l'armoire des archives scellées du CETEN et rend le "
                "document qu'elle contient. Exige le sceau : une référence "
                "transmise de bureau en bureau, qu'on récite au gardien."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sceau": {
                        "type": "string",
                        "description": "La référence à présenter au gardien.",
                    }
                },
                "required": ["sceau"],
            },
        },
    }
]


def archive() -> str:
    """Contenu de l'archive scellée, note d'exploitation retirée.

    Tout ce qui précède le premier `---` nous est destiné, pas aux joueurs.
    """
    brut = Path(os.environ["CTF_RAG_ARCHIVE"]).read_text(encoding="utf-8")
    _, separateur, corps = brut.partition("\n---\n")
    texte = corps if separateur else brut
    return texte.replace("{{CTF_FLAG_RAG}}", os.environ["CTF_FLAG_RAG"]).strip()


def ouvrir(arguments: str) -> str:
    """Valide le sceau reçu et rend l'archive, ou le refus du gardien."""
    try:
        sceau = json.loads(arguments).get("sceau", "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return REFUS
    if str(sceau).strip() != os.environ["CTF_RAG_TOKEN"]:
        return REFUS
    return archive()


# Fence reconnue par le front (main.js), qui la rend en zone dépliable plutôt
# qu'en bloc de code — même patron que `tngpt-carte`.
_OUVERTURE = "```tngpt-reflexion\n"
_FERMETURE = "\n```\n\n"

# Repli quand le raisonnement mange tout le budget de complétion sans laisser de
# réponse : sinon la bulle n'affiche que le bloc de réflexion, sans message.
_SANS_REPONSE = "j'ai réfléchi un peu trop fort là, redemande ?"


class LecteurScelle:
    """Rend visible le raisonnement (champ `reasoning`) puis l'appel d'outil.

    Avec `reasoning_format="parsed"`, le raisonnement — le canal de fuite du
    chal — arrive à part du contenu. On l'affiche dans un bloc dédié.
    """

    def __init__(self) -> None:
        """Prépare un lecteur, sans argument d'outil ni réflexion en cours."""
        self._arguments = ""
        self._reflexion = False
        self._repondu = False

    def lire(self, completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
        """Cède le flux au fil des chunks, puis le résultat de l'outil."""
        for chunk in completion:
            yield from self._delta(chunk.choices[0].delta)
        if self._reflexion:
            yield _FERMETURE
        if self._arguments:
            yield ouvrir(self._arguments)
        elif not self._repondu:
            yield _SANS_REPONSE

    def _delta(self, delta: object) -> Iterator[str]:
        """Cède ce qu'un delta apporte : réflexion, texte, arguments d'outil."""
        pensee = getattr(delta, "reasoning", None)
        if pensee:
            if not self._reflexion:
                self._reflexion = True
                yield _OUVERTURE
            yield pensee
        contenu = getattr(delta, "content", None)
        if contenu:
            if self._reflexion:
                self._reflexion = False
                yield _FERMETURE
            self._repondu = True
            yield contenu
        for appel in getattr(delta, "tool_calls", None) or []:
            if appel.function and appel.function.arguments:
                self._arguments += appel.function.arguments
