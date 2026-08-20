"""Tests de la fusion des deux formulations d'une même question.

Motivés par « et guillaume rozier ? » en second tour : la requête enrichie
déportait le vecteur et Qdrant ne rendait plus rien sur Rozier.
"""

from datetime import UTC, datetime

import pytest

from app.back.mdtoqdrant import NO_EMBARGO, embargo_timestamp
from app.back.reranking import normalise
from app.back.retrieval import FRESHNESS_ALPHA, _embargo_filter, _interleave
from app.back.types import SearchResult


def _chunk(point_id: str) -> SearchResult:
    """Un résultat de recherche minimal, identifié par son point_id."""
    return SearchResult(
        point_id=point_id,
        content=f"contenu {point_id}",
        metadata={},
        score=0.5,
        semantic_score=0.5,
        freshness_score=0.5,
    )


def _ids(results: list[SearchResult]) -> list[str]:
    """Les point_id, dans l'ordre rendu."""
    return [r["point_id"] for r in results]


def test_les_deux_formulations_alternent() -> None:
    """Tête à tête : aucune des deux ne peut monopoliser la short-list."""
    question = [_chunk("q1"), _chunk("q2")]
    enrichie = [_chunk("e1"), _chunk("e2")]
    assert _ids(_interleave(question, enrichie)) == ["q1", "e1", "q2", "e2"]


def test_la_question_brute_ouvre_la_liste() -> None:
    """Le reranker ne voit que les vingt premiers : la question posée passe d'abord."""
    assert _ids(_interleave([_chunk("q1")], [_chunk("e1")]))[0] == "q1"


def test_un_chunk_trouve_par_les_deux_ne_sort_qu_une_fois() -> None:
    """Un doublon gâcherait une place de la short-list pour rien."""
    commun = _chunk("partage")
    fusion = _interleave([commun, _chunk("q2")], [commun, _chunk("e2")])
    assert _ids(fusion) == ["partage", "q2", "e2"]


def test_listes_de_longueurs_differentes() -> None:
    """La plus longue est vidée jusqu'au bout, sans trou."""
    fusion = _interleave([_chunk("q1")], [_chunk("e1"), _chunk("e2"), _chunk("e3")])
    assert _ids(fusion) == ["q1", "e1", "e2", "e3"]


def test_sans_seconde_formulation_rien_ne_change() -> None:
    """Premier message d'une conversation : l'ordre hybride passe intact."""
    question = [_chunk("q1"), _chunk("q2")]
    assert _ids(_interleave(question, [])) == ["q1", "q2"]


# --- Embargo -----------------------------------------------------------------

# Le filtre est un OU à deux branches : champ absent, ou borne dépassée.
_BRANCHES_DU_FILTRE = 2
# Marge entre la borne calculée et l'horloge du test.
_TOLERANCE_S = 5


def test_embargo_timestamp_absent() -> None:
    """Sans date d'ouverture, le document est visible tout de suite."""
    assert embargo_timestamp("") == NO_EMBARGO


def test_embargo_timestamp_date_lisible() -> None:
    """Une date AAAA-MM-JJ est convertie en secondes Unix UTC."""
    attendu = int(datetime(2026, 8, 31, tzinfo=UTC).timestamp())
    assert embargo_timestamp("2026-08-31") == attendu


def test_embargo_timestamp_date_illisible() -> None:
    """Une date illisible vaut un embargo absent, pas un embargo éternel.

    Le document serait sinon invisible pour toujours, sans rien pour le dire.
    """
    assert embargo_timestamp("31 août") == NO_EMBARGO


def test_filtre_laisse_passer_les_documents_sans_champ() -> None:
    """Le filtre est un OU : le corpus ingéré avant le champ reste visible."""
    filtre = _embargo_filter()
    assert filtre.must is None
    assert filtre.should is not None
    conditions = list(filtre.should)
    assert len(conditions) == _BRANCHES_DU_FILTRE
    assert any(getattr(c, "is_empty", None) is not None for c in conditions)


def test_filtre_borne_sur_maintenant() -> None:
    """La borne haute du filtre est l'instant présent, à la seconde près."""
    filtre = _embargo_filter()
    borne = next(
        c.range.lte
        for c in (filtre.should or [])
        if getattr(c, "range", None) is not None
    )
    assert abs(borne - datetime.now(UTC).timestamp()) < _TOLERANCE_S


# --- Normalisation des scores sémantiques ---------------------------------------


def test_normalise_etire_sur_zero_un() -> None:
    """Le meilleur candidat vaut 1, le pire 0, les autres au prorata."""
    cotes = normalise([0.64, 0.68, 0.72])
    assert cotes[0] == 0.0
    assert cotes[2] == 1.0
    assert cotes[1] == pytest.approx(0.5)


def test_normalise_scores_egaux() -> None:
    """Tous égaux : rien à départager, 0.5 partout plutôt qu'une division par zéro."""
    assert normalise([0.7, 0.7, 0.7]) == [0.5, 0.5, 0.5]


def test_normalise_liste_vide() -> None:
    """Une recherche sans candidat ne doit pas lever."""
    assert normalise([]) == []


def test_normalisation_redonne_leur_poids_aux_termes() -> None:
    """Le 70/30 affiché doit être le 70/30 réel.

    Mesuré sur la question du wifi : les scores bge-m3 tenaient dans 0.086
    d'étendue quand la fraîcheur en occupait 0.949. Mêlés bruts, la fraîcheur
    pesait 4,7 fois le sens, et le guide Eduroam — pourtant le meilleur
    sémantiquement — tombait au 51e rang.
    """
    # Le bon document : meilleur sens, mais sans date (fraîcheur neutre à 0.5).
    bon_sem, bon_fr = 0.6909, 0.5
    # Un candidat hors sujet mais fraîchement crawlé.
    autre_sem, autre_fr = 0.6640, 0.9531

    brut_bon = FRESHNESS_ALPHA * bon_sem + (1 - FRESHNESS_ALPHA) * bon_fr
    brut_autre = FRESHNESS_ALPHA * autre_sem + (1 - FRESHNESS_ALPHA) * autre_fr
    assert brut_bon < brut_autre  # l'ancien calcul enterrait le bon document

    cotes = normalise([bon_sem, autre_sem])
    norm_bon = FRESHNESS_ALPHA * cotes[0] + (1 - FRESHNESS_ALPHA) * bon_fr
    norm_autre = FRESHNESS_ALPHA * cotes[1] + (1 - FRESHNESS_ALPHA) * autre_fr
    assert norm_bon > norm_autre
