"""Tests des réponses réflexes, sorties du prompt système vers le code.

Tant qu'elles étaient des règles de prompt, leur exactitude dépendait du modèle
et rien ne pouvait l'éprouver. Le passage en code les rend testables : c'est la
moitié de l'intérêt du déplacement.
"""

import pytest

from app.back.reflexes import reflex


@pytest.mark.parametrize(
    ("question", "attendu"),
    [
        ("a", "b"),
        ("b", "c"),
        ("z", "a"),
        ("A", "b"),
        ("  m  ", "n"),
    ],
)
def test_lettre_seule_donne_la_suivante(question: str, attendu: str) -> None:
    """Une lettre de l'alphabet appelle la suivante, et z reboucle sur a."""
    assert reflex(question) == attendu


@pytest.mark.parametrize(
    ("question", "attendu"),
    [
        ("feur", "-rouge"),
        ("FEUR", "-rouge"),
        ("gorge", "profonde"),
        (" Gorge ", "profonde"),
    ],
)
def test_mots_declencheurs(question: str, attendu: str) -> None:
    """Les deux mots-réflexes répondent quelle que soit la casse."""
    assert reflex(question) == attendu


@pytest.mark.parametrize(
    "question",
    [
        "qui est le prez du BDE ?",
        "feur ?",
        "ab",
        "é",
        "1",
        "",
        "gorge profonde",
    ],
)
def test_le_reste_part_au_modele(question: str) -> None:
    """Hors correspondance exacte, la question suit le chemin RAG normal."""
    assert reflex(question) is None
