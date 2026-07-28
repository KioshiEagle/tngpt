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


def test_sanitize_conserve_un_bloc_valide() -> None:
    """Un bloc déjà conforme traverse l'assainissement sans dégât."""
    raw = '```mermaid\ngraph LR\n  TN --> BDE["BDE"]\n```'
    assert sanitize_mermaid(raw) == 'graph LR\n  TN --> BDE["BDE"]'


def test_sanitize_ajoute_les_guillemets_manquants() -> None:
    """Les libellés accentués ou parenthésés non quotés sont réparés."""
    raw = "graph LR\n  BDE --> C1[Club Œnologie (asso)]\n"
    assert sanitize_mermaid(raw) == 'graph LR\n  BDE --> C1["Club Œnologie (asso)"]'


def test_sanitize_ne_reecrit_pas_un_libelle_deja_quote() -> None:
    """Des parenthèses à l'intérieur d'un libellé quoté ne sont pas re-quotées."""
    raw = 'graph LR\n  BDE --> C1["Club Impro (2019)"]'
    assert sanitize_mermaid(raw) == 'graph LR\n  BDE --> C1["Club Impro (2019)"]'


def test_sanitize_force_len_tete_graph_lr() -> None:
    """Un en-tête absent ou différent est normalisé en `graph LR`."""
    sans_entete = sanitize_mermaid('  TN --> BDE["BDE"]')
    assert sans_entete is not None
    assert sans_entete.splitlines()[0] == "graph LR"

    autre_entete = sanitize_mermaid('flowchart TD\n  TN --> BDE["BDE"]')
    assert autre_entete is not None
    assert autre_entete.splitlines()[0] == "graph LR"


def test_sanitize_supprime_les_directives_interdites() -> None:
    """click, style et classDef sont retirés du diagramme."""
    raw = (
        "graph LR\n"
        '  TN --> BDE["BDE"]\n'
        '  click BDE "https://exemple.fr"\n'
        "  style BDE fill:#f00\n"
        "  classDef gros font-size:20px\n"
    )
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    assert "click" not in cleaned
    assert "style" not in cleaned
    assert "classDef" not in cleaned
    assert 'TN --> BDE["BDE"]' in cleaned


def test_sanitize_ignore_ce_qui_suit_la_fence_fermante() -> None:
    """La prose émise après le bloc n'entre pas dans le diagramme."""
    raw = '```mermaid\ngraph LR\n  TN --> BDE["BDE"]\n```\nvoilà la carte !'
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    assert "voilà" not in cleaned


def test_sanitize_deduplique_les_lignes() -> None:
    """Une arête répétée n'apparaît qu'une fois."""
    raw = 'graph LR\n  TN --> BDE["BDE"]\n  TN --> BDE["BDE"]\n'
    cleaned = sanitize_mermaid(raw)
    assert cleaned is not None
    assert cleaned.count("TN --> BDE") == 1


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "```mermaid\ngraph LR\n```",
        'graph LR\n  TN["TELECOM Nancy"]\n  BDE["BDE"]',
        "je sais pas, je trouve pas dans mes archives",
    ],
)
def test_sanitize_renvoie_none_sans_arete(raw: str) -> None:
    """Sans arête exploitable, aucun bloc n'est émis plutôt qu'un bloc cassé."""
    assert sanitize_mermaid(raw) is None


def _chunked(text: str, size: int = 7) -> list[str]:
    """Découpe un texte comme le ferait le streaming Groq, marqueurs coupés inclus."""
    return [text[i : i + size] for i in range(0, len(text), size)]


def test_split_streame_la_prose_puis_le_diagramme() -> None:
    """La prose sort telle quelle, le diagramme sort en un bloc assaini."""
    reponse = (
        "voilà la carte des mers, matelot\n\n"
        "```mermaid\ngraph LR\n  TN --> BDE[BDE]\n```\n"
    )
    out = "".join(_split_prose_and_diagram(iter(_chunked(reponse))))
    assert out.startswith("voilà la carte des mers, matelot")
    assert '```mermaid\ngraph LR\n  TN --> BDE["BDE"]\n```' in out


def test_split_sans_diagramme_laisse_la_reponse_intacte() -> None:
    """Une réponse sans bloc mermaid traverse le découpage sans perte."""
    reponse = "je sais pas, je trouve pas dans mes archives"
    assert "".join(_split_prose_and_diagram(iter(_chunked(reponse)))) == reponse


def test_split_abandonne_un_diagramme_inexploitable() -> None:
    """Un bloc sans arête est supprimé, la prose est conservée."""
    reponse = "hop la carte\n\n```mermaid\ngraph LR\n  TN[TN]\n```\n"
    out = "".join(_split_prose_and_diagram(iter(_chunked(reponse))))
    assert "hop la carte" in out
    assert "mermaid" not in out
