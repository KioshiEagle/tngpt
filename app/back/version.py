"""Version de l'application affichée par le panel admin.

Deux sources, dans cet ordre. L'estampille gravée dans l'image au build fait
foi : elle seule dit ce que le conteneur exécute vraiment. À défaut — build
local, ou image antérieure au marquage — on interroge GitHub pour la dernière
release publiée, en le disant, car rien ne garantit que c'est celle qui tourne.
"""

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# Posées au build par `--build-arg` (voir Dockerfile et le workflow GitHub).
VERSION = os.environ.get("APP_VERSION") or ""
REVISION = (os.environ.get("APP_REVISION") or "")[:7]

# Dépôt d'où sortent les releases, pour pointer la version affichée vers la
# sienne. Surchargeable, le jour où le dépôt déménage.
DEPOT = os.environ.get("APP_REPO") or "KioshiEagle/tngpt"

# Provenance de ce qui est affiché, pour que le panel n'affirme rien de faux.
GRAVEE = "gravee"  # estampille de l'image : c'est ce qui tourne
PUBLIEE = "publiee"  # dernière release GitHub : pas forcément celle qui tourne
INCONNUE = "inconnue"  # ni l'une ni l'autre

# Faute de mieux, un numéro plutôt qu'un mot : la place est celle d'une version.
SANS_VERSION = "v0.0.0"

_TTL = 900  # 15 min : une release ne sort pas deux fois par heure
_TTL_ECHEC = 60  # un échec ne vaut pas un quart d'heure de silence
_TIMEOUT = 3

# None, et non (0.0, None) : `monotonic()` compte depuis le démarrage de la
# machine, donc un horodatage nul passait pour tout frais juste après un boot —
# le cache rendait alors son vide initial sans jamais appeler GitHub.
_cache: tuple[float, str | None] | None = None


def derniere_release_publiee() -> str | None:
    """Nom de la dernière release GitHub, ou None si l'appel n'aboutit pas.

    Returns:
        Le nom de la release, mis en cache un quart d'heure.

    """
    global _cache  # noqa: PLW0603
    if _cache is not None:
        pose, valeur = _cache
        if time.monotonic() - pose < (_TTL if valeur else _TTL_ECHEC):
            return valeur

    try:
        reponse = httpx.get(
            f"https://api.github.com/repos/{DEPOT}/releases/latest",
            timeout=_TIMEOUT,
            headers={"Accept": "application/vnd.github+json"},
        )
        reponse.raise_for_status()
        valeur = reponse.json().get("tag_name")
    except (httpx.HTTPError, ValueError):
        # Le panel doit s'afficher même sans réseau : on note et on renonce.
        logger.warning("Dernière release GitHub introuvable.")
        valeur = None

    _cache = (time.monotonic(), valeur)
    return valeur


def version_affichee() -> tuple[str, str]:
    """Version à montrer au panel, et d'où elle sort.

    Returns:
        Le nom de la version et sa provenance (`GRAVEE`, `PUBLIEE`, `INCONNUE`).

    """
    if VERSION:
        return VERSION, GRAVEE
    publiee = derniere_release_publiee()
    if publiee:
        return publiee, PUBLIEE
    return SANS_VERSION, INCONNUE


def url_release(nom: str) -> str | None:
    """Lien vers la release GitHub portant ce nom.

    Args:
        nom: Nom de la release, tel qu'affiché.

    Returns:
        L'URL de la release, ou None quand le nom n'en désigne aucune.

    """
    if nom == SANS_VERSION:
        return None
    return f"https://github.com/{DEPOT}/releases/tag/{nom}"
