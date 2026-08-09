"""Normalisation de texte partagée par les modules qui reconnaissent des noms.

Extrait de `seamap` pour être réutilisable par `clubs` : importer le helper
depuis `seamap` y ferait passer la chaîne `clubs → seamap → generate`, alors que
ce module n'importe rien du projet et ne peut donc créer aucun cycle.
"""

import unicodedata

# Les ligatures n'ont aucune décomposition Unicode, canonique ou non : sans ce
# repliage explicite, « Œnologie » ne rencontrerait jamais le motif « oeno ».
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE"})


def strip_accents(text: str) -> str:
    """Retire diacritiques et ligatures, pour comparer « Œnologie » et « oenologie »."""
    decomposed = unicodedata.normalize("NFD", text.translate(_LIGATURES))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
