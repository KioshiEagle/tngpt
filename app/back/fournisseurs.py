"""Reconnaissance du fournisseur d'une clé d'API, et ce qui en découle.

Le pool héberge des clés de plusieurs fournisseurs sans qu'aucune colonne ne le
dise : le préfixe du secret est le seul discriminant disponible. Modèle et
paramètres d'appel se déduisent ensuite de ce fournisseur, et non de la
configuration — une clé DeepSeek tirée du pool ne doit jamais partir avec un id
de modèle Groq.
"""

import logging
import os
from typing import cast

from .types import GroqParams

logger = logging.getLogger(__name__)

GROQ = "groq"
DEEPSEEK = "deepseek"
CEREBRAS = "cerebras"
MISTRAL = "mistral"

# Valeurs admises dans la colonne `fournisseur` du pool, et proposées au panel.
FOURNISSEURS = (GROQ, DEEPSEEK, CEREBRAS, MISTRAL)

# Point d'entrée par fournisseur, pour les SDK compatibles OpenAI. Groq est
# absent : son propre SDK connaît déjà son URL.
BASE_URLS = {
    DEEPSEEK: "https://api.deepseek.com",
    CEREBRAS: "https://api.cerebras.ai/v1",
    MISTRAL: "https://api.mistral.ai/v1",
}

# Hôte d'appel → fournisseur, pour retrouver la destination d'un client déjà
# construit (voir `groqpool.fournisseur_du_client`).
HOTES = {
    "api.groq.com": GROQ,
    "api.deepseek.com": DEEPSEEK,
    "api.cerebras.ai": CEREBRAS,
    "api.mistral.ai": MISTRAL,
}

# Comparés par `startswith`, donc « csk- » ne peut pas être pris pour « sk- ».
# Mistral est absent volontairement : ses clés sont des chaînes opaques, sans
# préfixe. Un client Mistral se construit donc explicitement, jamais par tirage
# au sort dans le pool — tant que la table des clés n'a pas de colonne dédiée.
_PREFIXES = (
    ("gsk_", GROQ),
    ("csk-", CEREBRAS),
    ("sk-", DEEPSEEK),
)

_GROQ_CHAT = "qwen/qwen3.6-27b"
_GROQ_REPLI = "openai/gpt-oss-120b"
_DEEPSEEK_CHAT = "deepseek-v4-flash"

# Repli sur le petit modèle : chez Mistral la facture se compte au token, et le
# chat restitue du contexte plutôt qu'il ne raisonne. Ids épinglés et non
# « -latest » : un modèle qui change sous les pieds invaliderait le banc.
_MISTRAL_CHAT = "mistral-small-2603"
_MISTRAL_REPLI = "mistral-medium-3.5"

# Efforts de raisonnement acceptés par DeepSeek. Le vocabulaire de Groq
# (« none », « default », « medium ») n'y a pas cours : ce qui n'est pas dans
# cette liste est retiré plutôt que traduit de travers.
_EFFORTS_DEEPSEEK = frozenset({"low", "high", "max"})

# Paramètres que Groq est seul à comprendre.
_PARAMS_GROQ_SEULEMENT = ("reasoning_format", "reasoning_effort")


def fournisseur(secret: str) -> str | None:
    """Fournisseur reconnu au préfixe du secret, ou None s'il est inconnu.

    Renvoyer None plutôt que de supposer Groq : une clé mal reconnue partirait
    vers la mauvaise API et n'échouerait qu'en 401, loin d'ici.
    """
    debut = (secret or "").strip()
    for prefixe, nom in _PREFIXES:
        if debut.startswith(prefixe):
            return nom
    logger.warning(
        "Préfixe de clé non reconnu (%r…) : fournisseur indéterminé.", debut[:4]
    )
    return None


def resoudre(secret: str, declare: str | None) -> str | None:
    """Fournisseur d'une clé du pool : le déclaré prime sur le préfixe.

    Une clé Mistral n'a pas de préfixe reconnaissable. Sans colonne pour le
    dire, elle partirait chez Groq et n'échouerait qu'en 401, loin d'ici.
    """
    if declare in FOURNISSEURS:
        return declare
    if declare:
        logger.warning(
            "Fournisseur déclaré inconnu (%r) : on retombe sur le préfixe.", declare
        )
    return fournisseur(secret)


def modeles(nom: str) -> tuple[str, str]:
    """(modèle principal, modèle de repli) d'un fournisseur.

    Lu à chaque appel, et non à l'import : `load_dotenv` s'exécute après les
    imports, un `os.getenv` figé ici manquerait les valeurs du `.env`.
    """
    if nom == DEEPSEEK:
        # Repli identique au principal : chez DeepSeek la limite se compte en
        # connexions simultanées, pas en quota par modèle. Rien à gagner à
        # basculer, et `repli_possible` reste donc faux.
        return (_DEEPSEEK_CHAT, _DEEPSEEK_CHAT)
    if nom == MISTRAL:
        return (
            os.getenv("MISTRAL_CHAT_MODEL", _MISTRAL_CHAT),
            os.getenv("MISTRAL_CHAT_MODEL_REPLI", _MISTRAL_REPLI),
        )
    return (
        os.getenv("GROQ_CHAT_MODEL", _GROQ_CHAT),
        os.getenv("GROQ_CHAT_MODEL_REPLI", _GROQ_REPLI),
    )


def adapter_params(params: GroqParams, nom: str) -> GroqParams:
    """Traduit des paramètres écrits en vocabulaire Groq vers un fournisseur.

    Les specs déclarent leurs paramètres à la façon de Groq, seul fournisseur
    historique. Chez DeepSeek le raisonnement se pilote par `thinking`, et
    `reasoning_format` n'existe pas : l'envoyer vaut un 400.
    """
    if nom == GROQ:
        return params
    adapte: dict = {c: v for c, v in params.items() if c not in _PARAMS_GROQ_SEULEMENT}
    if nom != DEEPSEEK:
        # Autre API compatible OpenAI : on retire ce qui est propre à Groq sans
        # rien inventer, faute de savoir ce qu'elle accepte.
        return cast("GroqParams", adapte)
    effort = params.get("reasoning_effort")
    actif = effort != "none"
    # `thinking` n'est pas dans la signature de `Completions.create` : passé en
    # nommé, le SDK lève un TypeError avant même d'émettre la requête.
    corps = dict(adapte.get("extra_body") or {})
    corps["thinking"] = {"type": "enabled" if actif else "disabled"}
    adapte["extra_body"] = corps
    if actif and effort in _EFFORTS_DEEPSEEK:
        adapte["reasoning_effort"] = effort
    return cast("GroqParams", adapte)
