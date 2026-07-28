"""Tests de la carte des mers : détection d'intention et assainissement mermaid.

Aucun accès réseau : seules les fonctions pures du module sont couvertes.
"""

import pytest

from app.back.seamap import _split_prose_and_diagram, sanitize_mermaid, wants_map

_WANTED = [
    "Montre-moi la carte des mers des clubs de TELECOM Nancy",
    "CARTE DES CLUBS",
    "carte des assos stp",
    "tu peux me faire un schéma des clubs ?",
    "fais un schema des associations",
    "cartographie de la vie asso",
    "un graphe des clubs du BDA",
    "donne-moi le mermaid des clubs",
]

_NOT_WANTED = [
    "c'est quoi le BDE ?",
    "Liste des clubs et associations à TELECOM Nancy",
    "quels sont les prochains événements du BDS ?",
    "salles libres maintenant",
    "carte étudiante perdue, je fais quoi ?",
    "feur",
]


@pytest.mark.parametrize("question", _WANTED)
def test_wants_map_detecte_les_demandes_de_carte(question: str) -> None:
    """Les formulations explicites déclenchent le chemin carte."""
    assert wants_map(question)


@pytest.mark.parametrize("question", _NOT_WANTED)
def test_wants_map_ignore_les_questions_normales(question: str) -> None:
    """Une question de chat classique ne doit pas basculer sur la carte."""
    assert not wants_map(question)


def test_sanitize_conserve_une_carte_valide() -> None:
    """Une carte mentale déjà conforme traverse l'assainissement sans dégât."""
    raw = '```mermaid\nmindmap\n  root(("TELECOM Nancy"))\n    N1["BDE"]\n```'
    assert sanitize_mermaid(raw) == (
        'mindmap\n  root(("TELECOM Nancy"))\n    N1["BDE"]'
    )


def test_sanitize_met_en_forme_les_libelles_nus() -> None:
    """Un libellé nu est réémis sous la forme sûre, entre crochets et guillemets."""
    raw = 'mindmap\n  root(("TELECOM Nancy"))\n    BDE\n      Club Œnologie'
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    assert '    N1["BDE"]' in cleaned
    assert '      N2["Club Œnologie"]' in cleaned


def test_sanitize_preserve_un_libelle_a_parentheses() -> None:
    """Le préfixe d'un libellé parenthésé doit survivre.

    Rendu tel quel, `Club Sportif (TOSS)` s'affiche « TOSS » : mermaid prend la
    parenthèse pour une déclaration de forme et jette le début du nom.
    """
    raw = 'mindmap\n  root(("TN"))\n    TNS\n      Club Sportif (TOSS/L-INP)'
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    assert 'N2["Club Sportif (TOSS/L-INP)"]' in cleaned


def test_sanitize_recupere_le_libelle_dun_noeud_deja_forme() -> None:
    """Un nœud déjà formé garde son libellé complet, parenthèses comprises."""
    raw = 'mindmap\n  root(("TN"))\n    c1["Club (Marché)"]'
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    assert 'N1["Club (Marché)"]' in cleaned


def test_sanitize_normalise_une_indentation_irreguliere() -> None:
    """Les niveaux sont reconstruits sur l'ordre relatif des indentations."""
    raw = 'mindmap\n   root(("TN"))\n       BDE\n           CTN\n       BDA'
    assert sanitize_mermaid(raw) == (
        'mindmap\n  root(("TN"))\n    N1["BDE"]\n      N2["CTN"]\n    N3["BDA"]'
    )


def test_sanitize_garantit_une_racine_unique() -> None:
    """Plusieurs nœuds au niveau le plus haut : mermaid refuse, on insère une racine."""
    raw = "mindmap\n  BDE\n  BDA"
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    lines = cleaned.splitlines()
    assert lines[0] == "mindmap"
    assert lines[1] == '  root(("TELECOM Nancy"))'
    assert len([line for line in lines if line.startswith("  root((")]) == 1
    assert lines[2] == '    N0["BDE"]'
    assert lines[3] == '    N1["BDA"]'


def test_sanitize_supprime_les_directives_interdites() -> None:
    """click, style, classDef et les icônes FontAwesome sont retirés."""
    raw = (
        "mindmap\n"
        '  root(("TN"))\n'
        '    N1["BDE"]\n'
        "    ::icon(fa fa-book)\n"
        '    click N1 "https://exemple.fr"\n'
        "    classDef gros font-size:20px\n"
    )
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    for interdit in ("click", "classDef", "::icon", "exemple.fr"):
        assert interdit not in cleaned


def test_sanitize_ignore_ce_qui_suit_la_fence_fermante() -> None:
    """La prose émise après le bloc n'entre pas dans le diagramme."""
    raw = '```mermaid\nmindmap\n  root(("TN"))\n    N1["BDE"]\n```\nvoilà la carte !'
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    assert "voilà" not in cleaned


def test_sanitize_neutralise_les_guillemets_internes() -> None:
    """Un guillemet dans un libellé refermerait le nœud : il est converti."""
    cleaned = sanitize_mermaid('mindmap\n  root(("TN"))\n    Le "vrai" BDE')
    assert cleaned is not None
    assert "N1[\"Le 'vrai' BDE\"]" in cleaned


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "```mermaid\nmindmap\n```",
        'mindmap\n  root(("TELECOM Nancy"))',
        "je sais pas, je trouve pas dans mes archives",
    ],
)
def test_sanitize_renvoie_none_sans_ramification(raw: str) -> None:
    """Sans au moins une branche, aucun bloc n'est émis plutôt qu'une carte vide."""
    assert sanitize_mermaid(raw) is None


def _chunked(text: str, size: int = 7) -> list[str]:
    """Découpe un texte comme le ferait le streaming Groq, marqueurs coupés inclus."""
    return [text[i : i + size] for i in range(0, len(text), size)]


def test_split_streame_la_prose_puis_le_diagramme() -> None:
    """La prose sort telle quelle, le diagramme sort en un bloc assaini."""
    reponse = (
        'voilà la carte, matelot\n\n```mermaid\nmindmap\n  root(("TN"))\n    BDE\n```\n'
    )
    out = "".join(_split_prose_and_diagram(iter(_chunked(reponse))))
    assert out.startswith("voilà la carte, matelot")
    assert '```mermaid\nmindmap\n  root(("TN"))\n    N1["BDE"]\n```' in out


def test_split_sans_diagramme_laisse_la_reponse_intacte() -> None:
    """Une réponse sans bloc mermaid traverse le découpage sans perte."""
    reponse = "je sais pas, je trouve pas dans mes archives"
    assert "".join(_split_prose_and_diagram(iter(_chunked(reponse)))) == reponse


def test_split_abandonne_un_diagramme_inexploitable() -> None:
    """Une carte réduite à sa racine est supprimée, la prose est conservée."""
    reponse = 'hop la carte\n\n```mermaid\nmindmap\n  root(("TN"))\n```\n'
    out = "".join(_split_prose_and_diagram(iter(_chunked(reponse))))
    assert "hop la carte" in out
    assert "mermaid" not in out
