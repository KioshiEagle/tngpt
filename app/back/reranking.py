"""Reclassement des chunks retrouvés, via l'API de reranking Workers AI.

Le banc le donne gagnant sur les six modèles testés (docs/rapports.md), mais en
CrossEncoder local ; servi par API, il est plus léger, et jamais une dépendance.
"""

import logging
import os
from typing import Any

import httpx

from .embedding import WorkersAIError, get_client
from .types import SearchResult

logger = logging.getLogger(__name__)

# 20 comme au banc, indépendamment du top_k : même pool de candidats d'un essai
# à l'autre.
# Fusion de rangs (Reciprocal Rank Fusion) : l'ordre final mêle le rang de
# pertinence rendu par le reranker et le rang de fraîcheur. Les deux scores
# n'ont ni la même échelle ni la même étendue — le reranker sort 0.90 sur une
# question et 0.03 sur une autre — et les normaliser sur le lot étire un écart
# infime en écart maximal dès que les candidats sont peu nombreux. Un rang,
# lui, ne dépend d'aucune échelle.
#
# L'amortisseur : plus il est grand, moins les premiers rangs se détachent. 60
# est la valeur d'usage. Il donne à la fraîcheur de quoi renverser des rangs
# voisins, jamais un écart de pertinence franc.
_RRF_K = 60

# Poids du rang de fraîcheur devant celui de pertinence. À 1, les deux rangs
# pèsent pareil ; au-delà, la fraîcheur commence à faire remonter des chunks
# que le reranker a écartés (mesuré : à 1.5, le guide wifi passe derrière deux
# pages du site sans rapport).
POIDS_FRAICHEUR = 1.0

# Part du meilleur score de pertinence en deçà de laquelle un chunk ne profite
# plus du départage par la fraîcheur. Un rang ignore les amplitudes : sans ce
# garde-fou, un document que le reranker note vingt fois moins bien passerait
# devant, au seul motif qu'il est plus récent. Un ratio, donc sans échelle.
_RATIO_COMPARABLE = 0.5

# Nombre de chunks sur lesquels la fusion s'applique, pris en tête du
# reclassement. Au-delà, le reranker note près de zéro : laisser la fraîcheur
# réordonner cette queue ferait remonter du hors-sujet fraîchement crawlé.
_FUSION_MAX = 10

# En deçà, les scores sont tenus pour égaux : les normaliser étirerait du bruit.
_ETENDUE_MINIMALE = 1e-9

_POOL_SIZE_DEFAUT = 20
_MODELE_DEFAUT = "@cf/baai/bge-reranker-base"
# En dessous de deux candidats, il n'y a rien à reclasser.
_MIN_CANDIDATS = 2
# Valeurs qui désactivent le reclassement. Tout le reste l'active, y compris une
# variable absente : le reranker est un acquis du banc, pas une option exotique.
_FAUX = frozenset({"0", "false", "no", "off", "non"})


def normalise(valeurs: list[float]) -> list[float]:
    """Ramène des scores à [0, 1] par min-max ; 0.5 partout s'ils sont égaux.

    Mêler deux scores d'étendues très différentes rend les poids déclarés
    fictifs : les cosinus bge-m3 comme les scores du reranker se tassent dans
    une bande étroite, là où la fraîcheur occupe tout l'intervalle.
    """
    if not valeurs:
        return []
    bas, haut = min(valeurs), max(valeurs)
    if haut - bas < _ETENDUE_MINIMALE:
        return [0.5] * len(valeurs)
    return [(v - bas) / (haut - bas) for v in valeurs]


def _par_hybride(results: list[SearchResult], top_k: int) -> list[SearchResult]:
    """Repli sans reclassement : l'ordre hybride, seul disponible.

    Les candidats arrivent triés par sens pur, pour que la fraîcheur ne filtre
    pas l'entrée du reranker ; quand celui-ci manque, elle reprend son rôle.
    """
    return sorted(results, key=lambda r: r["score"], reverse=True)[:top_k]


def is_enabled() -> bool:
    """Le reclassement est-il actif ? (RERANK_ENABLED, actif par défaut)."""
    return os.getenv("RERANK_ENABLED", "true").strip().lower() not in _FAUX


def _pool_size() -> int:
    """Taille de la short-list soumise au reranker (RERANK_POOL_SIZE)."""
    brut = os.getenv("RERANK_POOL_SIZE", "")
    if not brut.strip():
        return _POOL_SIZE_DEFAUT
    try:
        taille = int(brut)
    except ValueError:
        logger.warning(
            "RERANK_POOL_SIZE=%r illisible, %d retenu.", brut, _POOL_SIZE_DEFAUT
        )
        return _POOL_SIZE_DEFAUT
    return taille if taille > 0 else _POOL_SIZE_DEFAUT


def _classement(payload: dict[str, Any], taille: int) -> list[tuple[int, float]]:
    """Paires (indice, score) rendues par l'API, du plus pertinent au moins.

    L'API renvoie déjà trié, mais on ne s'y fie pas : un indice hors bornes ou
    répété décalerait silencieusement la short-list sur les mauvais chunks.
    """
    if not payload.get("success", False):
        msg = f"Workers AI a renvoyé une erreur : {payload.get('errors')}"
        raise WorkersAIError(msg)

    vus: set[int] = set()
    classement: list[tuple[int, float]] = []
    for item in payload["result"]["response"]:
        indice = item["id"]
        if isinstance(indice, int) and 0 <= indice < taille and indice not in vus:
            vus.add(indice)
            classement.append((indice, float(item["score"])))
    return classement


def _rangs(valeurs: list[float]) -> list[int]:
    """Rang de chaque valeur dans sa liste, 0 pour la plus haute."""
    ordre = sorted(range(len(valeurs)), key=lambda i: valeurs[i], reverse=True)
    rangs = [0] * len(valeurs)
    for rang, indice in enumerate(ordre):
        rangs[indice] = rang
    return rangs


def _ordonner(
    shortlist: list[SearchResult], classement: list[tuple[int, float]]
) -> list[SearchResult]:
    """Fusionne le rang de pertinence et le rang de fraîcheur (RRF).

    La pertinence a déjà fait son travail : ces chunks sont ceux que le
    reranker a retenus. Reste à les ordonner, et un compte rendu de cette
    année vaut mieux qu'un de 2018 jugé aussi pertinent que lui.
    """
    par_pertinence = sorted(classement, key=lambda paire: paire[1], reverse=True)
    tete, queue = par_pertinence[:_FUSION_MAX], par_pertinence[_FUSION_MAX:]

    chunks = [dict(shortlist[indice]) for indice, _ in tete]
    rangs_fraicheur = _rangs([c["freshness_score"] for c in chunks])

    for rang_p, (chunk, (_, score), rang_f) in enumerate(
        zip(chunks, tete, rangs_fraicheur, strict=True)
    ):
        chunk["rerank_score"] = round(score, 6)
        chunk["score"] = 1 / (_RRF_K + rang_p) + POIDS_FRAICHEUR / (_RRF_K + rang_f)

    # À égalité — deux rangs qu'on échange, les signaux s'annulent — c'est la
    # fraîcheur qui tranche, mais seulement entre pertinences comparables.
    meilleur = max((score for _, score in tete), default=0.0)
    seuil = meilleur * _RATIO_COMPARABLE

    def _cle(chunk: SearchResult) -> tuple[float, float]:
        comparable = chunk.get("rerank_score", 0.0) >= seuil
        return (chunk["score"], chunk["freshness_score"] if comparable else -1.0)

    chunks.sort(key=_cle, reverse=True)
    # La queue reste derrière, dans l'ordre du reranker : la fraîcheur n'a pas
    # à repêcher ce qu'il a jugé sans rapport.
    for indice, score in queue:
        reste = dict(shortlist[indice])
        reste["rerank_score"] = round(score, 6)
        chunks.append(reste)
    return chunks  # ty: ignore[invalid-return-type]


def rerank(query: str, results: list[SearchResult], top_k: int) -> list[SearchResult]:
    """Reclasse les meilleurs chunks pour `query` et renvoie les `top_k` premiers.

    Rend l'ordre hybride tronqué, sans lever, dès que le reclassement est
    désactivé, sans objet (moins de deux candidats) ou impossible.
    """
    if not is_enabled() or len(results) < _MIN_CANDIDATS:
        return _par_hybride(results, top_k)

    shortlist = results[: _pool_size()]
    try:
        # Un seul essai, sans backoff : sur le chemin de la réponse, un ordre
        # hybride tout de suite vaut mieux qu'un bon ordre dans cinq secondes.
        reponse = get_client().post(
            os.getenv("RERANK_MODEL", _MODELE_DEFAUT),
            json={
                "query": query,
                "contexts": [{"text": r["content"]} for r in shortlist],
            },
        )
        reponse.raise_for_status()
        classement = _classement(reponse.json(), len(shortlist))
    except (httpx.HTTPError, WorkersAIError, KeyError, TypeError, ValueError):
        logger.warning("Reranker indisponible : ordre hybride conservé.", exc_info=True)
        return _par_hybride(results, top_k)

    if not classement:
        logger.warning("Reranker sans classement exploitable : ordre hybride conservé.")
        return _par_hybride(results, top_k)

    reclasses = _ordonner(shortlist, classement)

    # Un chunk que l'API n'a pas rendu reprend sa place derrière les reclassés,
    # dans l'ordre hybride, plutôt que d'être perdu.
    rendus = {indice for indice, _ in classement}
    reclasses.extend(c for i, c in enumerate(shortlist) if i not in rendus)

    logger.debug("Reranker : %d candidats reclassés, %d rendus.", len(shortlist), top_k)
    return reclasses[:top_k]
