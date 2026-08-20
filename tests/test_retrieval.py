"""Tests de la fusion des deux formulations d'une même question.

Motivés par « et guillaume rozier ? » en second tour : la requête enrichie
déportait le vecteur et Qdrant ne rendait plus rien sur Rozier.
"""

from datetime import UTC, datetime

from app.back.mdtoqdrant import NO_EMBARGO, embargo_timestamp
from app.back.retrieval import _embargo_filter, _interleave
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
