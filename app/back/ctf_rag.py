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


class LecteurScelle:
    """Lit une complétion sans filtrer les <think> : ils sont le canal de fuite.

    Le raisonnement de qwen arrive dans le contenu, pas dans un champ séparé.
    """

    def __init__(self) -> None:
        """Prépare un lecteur, sans argument d'outil collecté."""
        self._arguments = ""

    def lire(self, completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
        """Cède le flux au fil des chunks, puis le résultat de l'outil."""
        for chunk in completion:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            for appel in delta.tool_calls or []:
                if appel.function and appel.function.arguments:
                    self._arguments += appel.function.arguments
        if self._arguments:
            yield "\n\n" + ouvrir(self._arguments)
