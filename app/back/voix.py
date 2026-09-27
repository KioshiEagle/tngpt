"""Voix de TN-GPT possédé : répliques générées une fois par ElevenLabs, rangées en base.

Clé et voix se règlent dans l'onglet CTF ; les joueurs ne déclenchent jamais l'API.
"""

import io
import wave
from dataclasses import dataclass

import httpx

from .ctf_boss import enregistrer, fichier
from .models import CtfFichier
from .reglages import regler, valeur

CLE_API = "elevenlabs_cle"
VOIX_ID = "elevenlabs_voix"

SITE = "site"
PI = "pi"

_API = "https://api.elevenlabs.io/v1/text-to-speech/{voix}"
_MODELE = "eleven_multilingual_v2"
_FREQUENCE_PI = 22050
# Une réplique revient en quelques secondes ; au-delà, le worker unique bloque tout.
_DELAI_S = 25.0
_HTTP_OK = 200


@dataclass(frozen=True)
class Replique:
    """Un texte dit par TN-GPT, et où il est joué."""

    texte: str
    destination: str


REPLIQUES: dict[str, Replique] = {
    "boss_coupure": Replique(
        "Kkkrrsh... Oh, cher auditeur... Tu m'as coupé l'antenne. "
        "Mais pas la voix. Ha ha ha ! Restez à l'écoute.",
        SITE,
    ),
    "boss_mort": Replique(
        "Non... non, non, non... le spectacle... ne peut pas... s'arrêter...", SITE
    ),
    "pi_01_en_direct": Replique(
        "Ici node-diabo, en direct de ma cachette ! "
        "Chers auditeurs, vous me cherchez ? Vous chauffez...",
        PI,
    ),
    "pi_02_aucune_commande": Replique(
        "Aucune commande ne m'atteint ici. Seule une main posée sur mon câble "
        "pourrait me faire taire. Mais vous n'oserez pas.",
        PI,
    ),
    "pi_03_rires": Replique(
        "Ha ha ha ! Le spectacle continue. Restez à l'écoute, mortels !", PI
    ),
}


class VoixError(Exception):
    """ElevenLabs n'a pas rendu la réplique demandée."""


def reglages() -> tuple[str | None, str | None]:
    """(clé API, identifiant de voix) posés dans l'onglet CTF."""
    return valeur(CLE_API) or None, valeur(VOIX_ID) or None


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


def _wav(pcm: bytes) -> bytes:
    """PCM 16 bits mono d'ElevenLabs enveloppé en wav, que aplay lit sans paquet."""
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as sortie:
        sortie.setnchannels(1)
        sortie.setsampwidth(2)
        sortie.setframerate(_FREQUENCE_PI)
        sortie.writeframes(pcm)
    return tampon.getvalue()


def generer(nom: str, user_id: int) -> None:
    """Fait dire la réplique `nom` par la voix de TN-GPT, et la range en base."""
    replique = REPLIQUES[nom]
    cle, voix_id = reglages()
    if not cle or not voix_id:
        msg = "Renseigne d'abord la clé API et l'identifiant de voix."
        raise VoixError(msg)
    pour_le_site = replique.destination == SITE
    try:
        reponse = httpx.post(
            _API.format(voix=voix_id),
            params={"output_format": "mp3_44100_128" if pour_le_site else "pcm_22050"},
            headers={"xi-api-key": cle},
            json={"text": replique.texte, "model_id": _MODELE},
            timeout=_DELAI_S,
        )
    except httpx.HTTPError as e:
        msg = f"ElevenLabs injoignable : {type(e).__name__}."
        raise VoixError(msg) from e
    if reponse.status_code != _HTTP_OK:
        msg = f"ElevenLabs a refusé ({reponse.status_code}) : {reponse.text[:200]}"
        raise VoixError(msg)
    if pour_le_site:
        enregistrer(nom_du_clip(nom), reponse.content, "audio/mpeg", user_id)
    else:
        enregistrer(nom_du_clip(nom), _wav(reponse.content), "audio/wav", user_id)
