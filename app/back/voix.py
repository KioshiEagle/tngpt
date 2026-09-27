"""Voix de TN-GPT possédé : répliques générées une fois par ElevenLabs, rangées en base.

Clé et voix se règlent dans l'onglet CTF ; les joueurs ne déclenchent jamais l'API.
"""

import httpx

from .ctf_boss import enregistrer, fichier, supprimer
from .models import CtfFichier
from .reglages import regler, valeur

CLE_API = "elevenlabs_cle"
VOIX_ID = "elevenlabs_voix"
# Voix d'Alastobias par défaut (Voice Design) ; remplaçable dans l'onglet CTF.
_VOIX_DEFAUT = "Mc9XPp1OV4P1vKx3L5Yf"

_API = "https://api.elevenlabs.io/v1/text-to-speech/{voix}"
_MODELE = "eleven_multilingual_v2"
# Une réplique revient en quelques secondes ; au-delà, le worker unique bloque tout.
_DELAI_S = 25.0
_HTTP_OK = 200


# La voix ne s'entend qu'au chal 2 : la réplique qui survit, puis sa vraie mort.
REPLIQUES: dict[str, str] = {
    "boss_coupure": (
        "Kkkrrsh... Oh, cher auditeur... Tu m'as coupé l'antenne. "
        "Mais pas la voix. Ha ha ha ! Restez à l'écoute."
    ),
    "boss_mort": "Non... non, non, non... le spectacle... ne peut pas... s'arrêter...",
}


class VoixError(Exception):
    """ElevenLabs n'a pas rendu la réplique demandée."""


_MAX_TEXTE = 500


def _cle_texte(nom: str) -> str:
    return f"voix_texte_{nom}"


def texte(nom: str) -> str:
    """Texte de la réplique : celui du panel s'il a été réécrit, sinon l'original."""
    return valeur(_cle_texte(nom)) or REPLIQUES[nom]


def modifier_texte(nom: str, nouveau: str, user_id: int) -> bool:
    """Réécrit une réplique et retire son clip devenu faux ; faux si rien ne change."""
    nouveau = nouveau.strip()[:_MAX_TEXTE] or REPLIQUES[nom]
    if nouveau == texte(nom):
        return False
    regler(_cle_texte(nom), nouveau, user_id=user_id)
    supprimer(nom_du_clip(nom))
    return True


def reglages() -> tuple[str | None, str | None]:
    """(clé API, identifiant de voix) posés dans l'onglet CTF."""
    return valeur(CLE_API) or None, valeur(VOIX_ID) or _VOIX_DEFAUT


def configurer(cle: str | None, voix_id: str | None, user_id: int) -> None:
    """Enregistre clé et voix ; un champ laissé vide garde la valeur en place."""
    if cle:
        regler(CLE_API, cle, user_id=user_id)
    if voix_id:
        regler(VOIX_ID, voix_id, user_id=user_id)


def masquer(cle: str | None) -> str | None:
    """Les quatre derniers caractères de la clé, seuls affichés dans le panel."""
    return f"…{cle[-4:]}" if cle else None


def nom_du_clip(nom: str) -> str:
    """Nom sous lequel le clip est rangé parmi les fichiers du chal."""
    return f"voix_{nom}"


def clip(nom: str) -> CtfFichier | None:
    """Clip généré d'une réplique, ou None s'il ne l'a pas encore été."""
    return fichier(nom_du_clip(nom))


def generer(nom: str, user_id: int) -> None:
    """Fait dire la réplique `nom` par la voix de TN-GPT, et la range en base."""
    cle, voix_id = reglages()
    if not cle or not voix_id:
        msg = "Renseigne d'abord la clé API et l'identifiant de voix."
        raise VoixError(msg)
    try:
        reponse = httpx.post(
            _API.format(voix=voix_id),
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": cle},
            json={"text": texte(nom), "model_id": _MODELE},
            timeout=_DELAI_S,
        )
    except httpx.HTTPError as e:
        msg = f"ElevenLabs injoignable : {type(e).__name__}."
        raise VoixError(msg) from e
    if reponse.status_code != _HTTP_OK:
        msg = f"ElevenLabs a refusé ({reponse.status_code}) : {reponse.text[:200]}"
        raise VoixError(msg)
    enregistrer(nom_du_clip(nom), reponse.content, "audio/mpeg", user_id)
