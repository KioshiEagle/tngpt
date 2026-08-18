"""Tests du reclassement Workers AI.

Sans réseau. Le point à ne jamais casser est la dégradation : aucune panne du
reranker ne doit transformer une recherche correcte en recherche en erreur.
"""

from typing import Any

import httpx
import pytest

from app.back import reranking
from app.back.embedding import WorkersAIError
from app.back.types import SearchResult


def _chunk(point_id: str, contenu: str, score: float) -> SearchResult:
    """Un résultat de recherche minimal, tel que `search` en produit."""
    return SearchResult(
        point_id=point_id,
        content=contenu,
        metadata={},
        score=score,
        semantic_score=score,
        freshness_score=0.5,
    )


_CANDIDATS = [
    _chunk("a", "Le club poker se réunit le jeudi.", 0.9),
    _chunk("b", "La Coloc fait de la musique.", 0.8),
    _chunk("c", "Le BDE organise l'intégration.", 0.7),
]


class _ReponseFactice:
    """Imite la réponse httpx utilisée par `rerank`."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, Any]:
        return self._payload


def _client_qui_rend(payload: dict[str, Any]) -> type:
    """Double du client Workers AI rendant `payload` à chaque POST.

    Rend la classe, pas une instance : `get_client()` l'appelle pour obtenir
    son client, exactement comme le vrai.
    """

    class _Client:
        def post(self, *_args: object, **_kwargs: object) -> _ReponseFactice:
            return _ReponseFactice(payload)

    return _Client


def _payload(*paires: tuple[int, float]) -> dict[str, Any]:
    """Charge utile Workers AI pour un classement (indice, score)."""
    return {
        "success": True,
        "result": {"response": [{"id": i, "score": s} for i, s in paires]},
    }


@pytest.fixture(autouse=True)
def _reranker_actif(monkeypatch: pytest.MonkeyPatch) -> None:
    """Part d'un environnement neutre : reranker actif, pool par défaut."""
    monkeypatch.delenv("RERANK_ENABLED", raising=False)
    monkeypatch.delenv("RERANK_POOL_SIZE", raising=False)
    monkeypatch.delenv("RERANK_MODEL", raising=False)


# --- Activation ---------------------------------------------------------------


@pytest.mark.parametrize("valeur", ["0", "false", "FALSE", "no", "off", "non", " off "])
def test_desactive_par_les_valeurs_fausses(
    monkeypatch: pytest.MonkeyPatch, valeur: str
) -> None:
    """RERANK_ENABLED accepte les formes usuelles du « non »."""
    monkeypatch.setenv("RERANK_ENABLED", valeur)
    assert reranking.is_enabled() is False


@pytest.mark.parametrize("valeur", ["true", "1", "yes", "oui", "n'importe quoi"])
def test_actif_partout_ailleurs(monkeypatch: pytest.MonkeyPatch, valeur: str) -> None:
    """Tout le reste active le reclassement, variable absente comprise."""
    monkeypatch.setenv("RERANK_ENABLED", valeur)
    assert reranking.is_enabled() is True


def test_actif_par_defaut() -> None:
    """Sans la variable, le reranker est acquis — c'est le résultat du banc."""
    assert reranking.is_enabled() is True


def test_desactive_rend_lordre_hybride(monkeypatch: pytest.MonkeyPatch) -> None:
    """Désactivé, aucun appel réseau et l'ordre d'entrée est simplement tronqué."""
    monkeypatch.setenv("RERANK_ENABLED", "false")
    sortie = reranking.rerank("peu importe", _CANDIDATS, top_k=2)
    assert [c["point_id"] for c in sortie] == ["a", "b"]


# --- Taille de la short-list --------------------------------------------------


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [("5", 5), ("", 20), ("   ", 20), ("zero", 20), ("0", 20), ("-3", 20)],
)
def test_pool_size(monkeypatch: pytest.MonkeyPatch, brut: str, attendu: int) -> None:
    """Une valeur illisible ou absurde retombe sur le 20 du banc."""
    monkeypatch.setenv("RERANK_POOL_SIZE", brut)
    assert reranking._pool_size() == attendu


def test_la_short_list_est_bornee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seuls les `RERANK_POOL_SIZE` premiers candidats partent à l'API."""
    monkeypatch.setenv("RERANK_POOL_SIZE", "2")
    envoyes: list[int] = []

    class _Client:
        def post(self, _modele: object, *, json: dict[str, Any]) -> _ReponseFactice:
            envoyes.append(len(json["contexts"]))
            return _ReponseFactice(_payload((1, 9.0), (0, 1.0)))

    monkeypatch.setattr(reranking, "get_client", _Client)
    reranking.rerank("q", _CANDIDATS, top_k=3)
    assert envoyes == [2]


# --- Reclassement -------------------------------------------------------------


def test_reordonne_selon_le_classement_rendu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le dernier candidat hybride passe en tête si le reranker le décide."""
    monkeypatch.setattr(
        reranking,
        "get_client",
        _client_qui_rend(_payload((2, 8.0), (0, 0.1), (1, 0.0))),
    )
    sortie = reranking.rerank("Qui organise l'intégration ?", _CANDIDATS, top_k=3)
    assert [c["point_id"] for c in sortie] == ["c", "a", "b"]


def test_attache_le_score_du_reranker(monkeypatch: pytest.MonkeyPatch) -> None:
    """`rerank_score` accompagne les chunks reclassés, sans écraser `score`."""
    monkeypatch.setattr(
        reranking, "get_client", _client_qui_rend(_payload((2, 8.0), (0, 0.1)))
    )
    premier = reranking.rerank("q", _CANDIDATS, top_k=1)[0]
    assert premier["rerank_score"] == pytest.approx(8.0)
    assert premier["score"] == pytest.approx(0.7)


def test_un_chunk_non_rendu_nest_pas_perdu(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'API peut tronquer sa réponse : le reste suit, en ordre hybride."""
    monkeypatch.setattr(reranking, "get_client", _client_qui_rend(_payload((2, 8.0))))
    sortie = reranking.rerank("q", _CANDIDATS, top_k=3)
    assert [c["point_id"] for c in sortie] == ["c", "a", "b"]


def test_indices_hors_bornes_ou_repetes_ignores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un indice aberrant ne doit pas décaler la short-list sur un autre chunk."""
    charge = _payload((99, 9.0), (1, 5.0), (1, 4.0), (-1, 3.0))
    monkeypatch.setattr(reranking, "get_client", _client_qui_rend(charge))
    sortie = reranking.rerank("q", _CANDIDATS, top_k=3)
    assert [c["point_id"] for c in sortie] == ["b", "a", "c"]


# --- Dégradation --------------------------------------------------------------


def test_moins_de_deux_candidats_court_circuite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un seul candidat : rien à reclasser, aucun appel réseau."""

    def _interdit() -> None:
        raise AssertionError

    monkeypatch.setattr(reranking, "get_client", _interdit)
    assert reranking.rerank("q", _CANDIDATS[:1], top_k=5) == _CANDIDATS[:1]


@pytest.mark.parametrize(
    "erreur",
    [
        httpx.ConnectError("injoignable"),
        httpx.ReadTimeout("trop lent"),
        WorkersAIError("refus"),
        KeyError("response"),
        ValueError("json illisible"),
    ],
)
def test_toute_panne_rend_lordre_hybride(
    monkeypatch: pytest.MonkeyPatch, erreur: Exception
) -> None:
    """Le cœur du contrat : une panne dégrade, elle ne casse jamais la recherche."""

    class _Client:
        def post(self, *_a: object, **_k: object) -> None:
            raise erreur

    monkeypatch.setattr(reranking, "get_client", _Client)
    sortie = reranking.rerank("q", _CANDIDATS, top_k=2)
    assert [c["point_id"] for c in sortie] == ["a", "b"]


def test_success_false_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workers AI répond 200 avec success=false : traité comme une panne."""
    charge = {"success": False, "errors": [{"message": "quota"}], "result": {}}
    monkeypatch.setattr(reranking, "get_client", _client_qui_rend(charge))
    sortie = reranking.rerank("q", _CANDIDATS, top_k=2)
    assert [c["point_id"] for c in sortie] == ["a", "b"]


def test_classement_vide_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une réponse sans aucun indice exploitable laisse l'ordre hybride."""
    monkeypatch.setattr(reranking, "get_client", _client_qui_rend(_payload()))
    sortie = reranking.rerank("q", _CANDIDATS, top_k=2)
    assert [c["point_id"] for c in sortie] == ["a", "b"]
