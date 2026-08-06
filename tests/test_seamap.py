"""Tests de la carte au trésor : détection d'intention et validation du payload.

Aucun accès réseau : seules les fonctions pures du module sont couvertes.
"""

import json
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import cast

import pytest
from groq import Stream
from groq.types.chat import ChatCompletionChunk

from app.back.seamap import (
    _MAX_COMMENTAIRE,
    MAP_FENCE,
    MAP_MAX_CLUBS,
    _collect_tool_arguments,
    build_map_payload,
    wants_map,
)

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


def _args(**kwargs: object) -> str:
    """Sérialise des arguments d'outil comme le ferait Groq."""
    return json.dumps(kwargs, ensure_ascii=False)


def test_payload_valide_traverse_sans_degat() -> None:
    """Une charge utile conforme ressort telle quelle."""
    payload = build_map_payload(
        _args(
            commentaire="voici la carte",
            clubs=[{"nom": "CréaTN", "tutelle": "BDE", "icone": "atelier"}],
        )
    )
    assert payload == {
        "commentaire": "voici la carte",
        "clubs": [{"nom": "CréaTN", "tutelle": "BDE", "icone": "atelier"}],
    }


def test_icone_hors_enumeration_retombe_sur_les_mots_cles() -> None:
    """Une icône inventée est ramenée dans le rang par le nom du club."""
    payload = build_map_payload(
        _args(
            commentaire="",
            clubs=[
                {"nom": "CréaTN", "tutelle": "BDE", "icone": "une chouette"},
                {"nom": "Club Œnologie", "tutelle": "BDE", "icone": None},
                {"nom": "Club Escalade", "tutelle": "BDS", "icone": 42},
            ],
        )
    )
    assert payload is not None
    assert [c["icone"] for c in payload["clubs"]] == ["atelier", "vignoble", "terrain"]


def test_club_inconnu_recoit_un_drapeau() -> None:
    """Sans mot-clé reconnaissable, le club garde tout de même un symbole."""
    payload = build_map_payload(
        _args(commentaire="", clubs=[{"nom": "Zorglub", "tutelle": "?", "icone": "x"}])
    )
    assert payload is not None
    assert payload["clubs"][0]["icone"] == "drapeau"


def test_tutelle_absente_prend_une_valeur_par_defaut() -> None:
    """Un club sans association mère est rattaché à l'école."""
    payload = build_map_payload(
        _args(commentaire="", clubs=[{"nom": "CTN", "icone": "drapeau"}])
    )
    assert payload is not None
    assert payload["clubs"][0]["tutelle"] == "TELECOM Nancy"


def test_doublons_supprimes() -> None:
    """Le même club répété n'apparaît qu'une fois sur la carte."""
    payload = build_map_payload(
        _args(
            commentaire="",
            clubs=[
                {"nom": "CTN", "tutelle": "BDE", "icone": "drapeau"},
                {"nom": "ctn", "tutelle": "bde", "icone": "drapeau"},
            ],
        )
    )
    assert payload is not None
    assert len(payload["clubs"]) == 1


def test_plafond_du_nombre_de_clubs() -> None:
    """Au-delà du plafond, la carte deviendrait illisible."""
    clubs = [
        {"nom": f"Club {i}", "tutelle": "BDE", "icone": "drapeau"} for i in range(60)
    ]
    payload = build_map_payload(_args(commentaire="", clubs=clubs))
    assert payload is not None
    assert len(payload["clubs"]) == MAP_MAX_CLUBS


def test_entrees_inexploitables_ignorees() -> None:
    """Une entrée sans nom, ou qui n'est pas un objet, est écartée sans tout casser."""
    payload = build_map_payload(
        _args(
            commentaire="",
            clubs=[
                "pas un objet",
                {"tutelle": "BDE", "icone": "drapeau"},
                {"nom": "   ", "tutelle": "BDE"},
                {"nom": "CTN", "tutelle": "BDE", "icone": "drapeau"},
            ],
        )
    )
    assert payload is not None
    assert [c["nom"] for c in payload["clubs"]] == ["CTN"]


def test_commentaire_coupe_sur_une_frontiere_de_phrase() -> None:
    """Un commentaire trop long est tronqué proprement, jamais en plein mot."""
    long_texte = ("voici la carte des clubs de telecom nancy. " * 20).strip()
    payload = build_map_payload(
        _args(
            commentaire=long_texte,
            clubs=[{"nom": "CTN", "tutelle": "BDE", "icone": "drapeau"}],
        )
    )
    assert payload is not None
    commentaire = payload["commentaire"]
    assert isinstance(commentaire, str)
    assert len(commentaire) <= _MAX_COMMENTAIRE
    assert commentaire.endswith((".", "…"))


@pytest.mark.parametrize(
    "arguments",
    [
        "",
        '{"clubs": [{"nom": "CTN"',
        '{"commentaire": "rien", "clubs": []}',
        '{"commentaire": "rien"}',
        "[1, 2, 3]",
    ],
)
def test_payload_none_quand_il_n_y_a_pas_de_carte(arguments: str) -> None:
    """JSON tronqué ou liste vide : pas de carte plutôt qu'une carte vide."""
    assert build_map_payload(arguments) is None


def _fausse_completion(
    fragments: Sequence[str | None],
) -> Stream[ChatCompletionChunk]:
    """Imite un flux Groq d'appel d'outil, arguments fragmentés compris.

    Le cast assume ce que le canard fait déjà : seule la forme des deltas
    compte pour le consommateur, pas la vraie classe du SDK.
    """

    def flux() -> Iterator[object]:
        for fragment in fragments:
            call = SimpleNamespace(function=SimpleNamespace(arguments=fragment))
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(tool_calls=[call]))]
            )

    return cast("Stream[ChatCompletionChunk]", flux())


def test_consommateur_recolle_les_arguments_fragmentes() -> None:
    """Les arguments arrivent en morceaux : rien n'est exploitable avant la fin."""
    args = _args(
        commentaire="voilà la carte, matelot",
        clubs=[{"nom": "CréaTN", "tutelle": "BDE", "icone": "atelier"}],
    )
    fragments = [args[i : i + 5] for i in range(0, len(args), 5)]
    sortie = "".join(_collect_tool_arguments(_fausse_completion(fragments)))

    assert sortie.startswith("voilà la carte, matelot")
    assert f"```{MAP_FENCE}" in sortie
    charge = json.loads(sortie.split(f"```{MAP_FENCE}")[1].split("```")[0])
    assert charge["clubs"] == [{"nom": "CréaTN", "tutelle": "BDE", "icone": "atelier"}]
    # Le commentaire est déjà dans la prose : il ne doit pas être dupliqué.
    assert "commentaire" not in charge


def test_consommateur_sans_club_ne_produit_pas_de_bloc() -> None:
    """Une liste vide donne une phrase d'excuse, jamais une fence orpheline."""
    sortie = "".join(
        _collect_tool_arguments(
            _fausse_completion([_args(commentaire="rien", clubs=[])])
        )
    )
    assert MAP_FENCE not in sortie
    assert "je sais pas" in sortie


def test_consommateur_tolere_un_flux_sans_appel_doutil() -> None:
    """Si le modèle n'appelle pas l'outil, on ne plante pas."""
    sortie = "".join(_collect_tool_arguments(_fausse_completion([None])))
    assert MAP_FENCE not in sortie
