"""Réponses réflexes : les questions dont la réponse ne dépend pas du modèle.

Trois plaisanteries maison, mécaniques : en code elles sont exactes, gratuites
en tokens, et court-circuitent Qdrant comme Groq.
"""

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

_REPONSES = {
    "feur": "-rouge",
    "gorge": "profonde",
}


def reflex(question: str) -> str | None:
    """Retourne la réponse réflexe à cette question, ou None s'il n'y en a pas."""
    q = question.strip().lower()
    if q in _REPONSES:
        return _REPONSES[q]
    if len(q) == 1 and q in _ALPHABET:
        return _ALPHABET[(_ALPHABET.index(q) + 1) % len(_ALPHABET)]
    return None
