"""Tests de la fusion des deux formulations d'une même question.

Motivés par « et guillaume rozier ? » en second tour : la requête enrichie
déportait le vecteur et Qdrant ne rendait plus rien sur Rozier.
"""

from app.back.retrieval import _interleave
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
