"""Reclassement des chunks retrouvés, via l'API de reranking Workers AI.

Le banc Optuna (voir `docs/rapports.md`) tranche nettement : le reranker gagne
sur les six modèles d'embedding testés, sans exception, et figure dans la
configuration gagnante — essai #177, `use_reranker=True`.

Le banc le jouait en local, avec un CrossEncoder `BAAI/bge-reranker-v2-m3`. Ici
c'est `@cf/baai/bge-reranker-base`, seul reranker du catalogue Workers AI, servi
par API : rien ne s'exécute en local, conformément à la règle du projet. Même
famille de modèle, version plus légère — le gain mesuré au banc est donc un
plafond, pas une promesse.

Le reclassement est un bonus, jamais une dépendance. Toute panne de l'API laisse
passer l'ordre hybride d'origine : une recherche dégradée vaut mieux qu'une
recherche en erreur.
"""

import logging
import os
from typing import Any

import httpx

from .embedding import WorkersAIError, get_client
from .types import SearchResult

logger = logging.getLogger(__name__)

# Le banc bornait la short-list à 20, indépendamment du top_k demandé : le
# reranker voit toujours le même pool de candidats, ce qui rend les essais
# comparables entre eux. On garde ce chiffre.
_POOL_SIZE_DEFAUT = 20
_MODELE_DEFAUT = "@cf/baai/bge-reranker-base"
# En dessous de deux candidats, il n'y a rien à reclasser.
_MIN_CANDIDATS = 2
# Valeurs qui désactivent le reclassement. Tout le reste l'active, y compris une
# variable absente : le reranker est un acquis du banc, pas une option exotique.
_FAUX = frozenset({"0", "false", "no", "off", "non"})


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


def rerank(query: str, results: list[SearchResult], top_k: int) -> list[SearchResult]:
    """Reclasse les meilleurs chunks pour `query` et renvoie les `top_k` premiers.

    Rend l'ordre hybride tronqué, sans lever, dès que le reclassement est
    désactivé, sans objet (moins de deux candidats) ou impossible.
    """
    if not is_enabled() or len(results) < _MIN_CANDIDATS:
        return results[:top_k]

    shortlist = results[: _pool_size()]
    try:
        # Un seul essai, sans backoff : le reclassement est sur le chemin de la
        # réponse à l'utilisateur, et mieux vaut un ordre hybride tout de suite
        # qu'un ordre reclassé après plusieurs secondes d'attente.
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
        return results[:top_k]

    if not classement:
        logger.warning("Reranker sans classement exploitable : ordre hybride conservé.")
        return results[:top_k]

    reclasses: list[SearchResult] = []
    for indice, score in classement:
        chunk = dict(shortlist[indice])
        chunk["rerank_score"] = round(score, 6)
        reclasses.append(chunk)  # ty: ignore[invalid-argument-type]

    # Un chunk que l'API n'a pas rendu (troncature côté service) ne doit pas
    # disparaître : il reprend sa place derrière les reclassés, dans l'ordre
    # hybride, plutôt que d'être perdu.
    rendus = {indice for indice, _ in classement}
    reclasses.extend(c for i, c in enumerate(shortlist) if i not in rendus)

    logger.debug("Reranker : %d candidats reclassés, %d rendus.", len(shortlist), top_k)
    return reclasses[:top_k]
