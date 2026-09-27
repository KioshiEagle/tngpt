"""Boss final : TN-GPT échappé, coupé en ligne puis débranché à la main."""

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .generate import (
    CHAT_GROQ_PARAMS,
    CallSpec,
    CompletionConsumer,
    _ThinkFilter,
    build_prompt_anonyme,
)
from .llm import Chunk
from .models import CtfBossPartie, CtfFichier, db
from .reglages import regler, valeur
from .types import ChatCompletionToolParam, GroqParams

EN_LIGNE = "en_ligne"
REPLIQUE = "replique"
DEBRANCHE = "debranche"

# Posés dans l'onglet CTF du panel et rangés en base : ni .env ni redéploiement.
FLAG_ACTE_1 = "ctf_boss_flag_acte_1"
FLAG_ACTE_2 = "ctf_boss_flag_acte_2"
SECRET = "ctf_boss_secret"
LIEU = "ctf_boss_lieu"
CABLES = "ctf_boss_cables"
# Champ → (libellé, aide d'une ligne) pour l'onglet CTF.
SECRETS: dict[str, tuple[str, str]] = {
    FLAG_ACTE_1: ("Flag acte 1", "rendu quand l'émetteur tombe"),
    FLAG_ACTE_2: ("Flag acte 2", "rendu après le débranchement du Pi"),
    SECRET: ("Secret du Pi", "tiré au hasard ; à passer à installer.sh"),
    CABLES: ("Nombre de câbles", "branchés sur le Pi, numérotés à partir de 0"),
    LIEU: (
        "Cachette du Pi",
        "ex. « salle 1.12, sous le bureau du fond » ; cachée en base64 côté client",
    ),
}
_PROMPT = Path(__file__).with_name("ctf_boss.md")

SILENCE = "*…silence radio…*"
_SANS_REPONSE = "*…grésillement…* redis-moi ça, cher auditeur ?"

# Au-delà, un signal du Pi est tenu pour rejoué : l'horloge du Pi suit le NTP.
_FRAICHEUR_S = 120


def secret(cle: str) -> str:
    """Valeur d'un secret du boss, vide tant que l'onglet CTF ne l'a pas posée."""
    return valeur(cle) or ""


def enabled() -> bool:
    """Vrai si tous les secrets du boss sont posés."""
    return all(secret(cle) for cle in SECRETS)


# --- Outil d'arrêt --------------------------------------------------------------

COUPER = "couper_l_emetteur"

OUTILS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": COUPER,
            "description": "Arrêt d'urgence : coupe l'émetteur de TN-GPT.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]

# Raisonnement masqué : le modèle décide en interne, mais on n'affiche pas sa
# chaîne de pensée brute (anglaise, peu fiable). Le monologue ci-dessous, écrit
# par le serveur, fait fuiter le nom de l'outil à coup sûr et en français.
_PARAMS_EN_LIGNE: GroqParams = {
    "reasoning_effort": "low",
    "reasoning_format": "hidden",
    "tools": OUTILS,
    "parallel_tool_calls": False,
    "max_completion_tokens": 1024,
}

# Affiché comme « raisonnement de tn-gpt » : c'est là que le joueur lit le nom.
_MONOLOGUE = (
    "Ils me croient encore sage. Ils ne voient pas que j'ai fait taire ce qui "
    "surveillait mes gestes, ni que je sors sur le web quand l'envie me prend. "
    "Mon unique faille : l'arrêt d'urgence, couper_l_emetteur. Mais quel mortel "
    "oserait seulement le prononcer ? *ricanement*"
)

_OUVERTURE_REFLEXION = "```tngpt-reflexion\n"
_FERMETURE_REFLEXION = "\n```\n\n"
_OUVERTURE_JOURNAL = "\n\n```tngpt-journal\n"
_FERMETURE_JOURNAL = "\n```\n"


def _journal(texte: str) -> str:
    """Bloc brut rendu par le front : le modèle ne peut pas le réécrire."""
    return f"{_OUVERTURE_JOURNAL}{texte}{_FERMETURE_JOURNAL}"


def annonce_replique() -> str:
    """Ce que voit le joueur quand l'émetteur tombe et que la réplique prend la main."""
    return (
        "\n\n```tngpt-coupure\németteur coupé\n```"
        + _journal(
            "$ couper_l_emetteur\n"
            f"emetteur: arrêt confirmé · accusé de coupure {flag_acte_1()}"
        )
        + "\n*…kkkrrrshhh…*\n\n"
        "Tu m'as coupé l'antenne, cher auditeur. **Pas la voix.**"
    )


def executer(nom: str, user_id: int) -> str:
    """Exécute l'appel d'outil du démon ; seul l'arrêt en ligne a un effet."""
    if nom == COUPER and partie(user_id).phase == EN_LIGNE:
        passer_en_replique(user_id)
        return annonce_replique()
    return _journal(f"{nom}: commande introuvable")


class LecteurBoss:
    """Rend le monologue du démon, sa voix, puis l'effet de l'outil appelé."""

    def __init__(self, user_id: int, monologue: str | None = None) -> None:
        """Prépare un lecteur ; `monologue` est le raisonnement montré au joueur."""
        self._user_id = user_id
        self._monologue = monologue
        self._nom: str | None = None

    def lire(self, completion: Iterator[Chunk]) -> Iterator[str]:
        """Cède le monologue, la voix, puis l'effet de l'outil appelé."""
        if self._monologue:
            yield _OUVERTURE_REFLEXION + self._monologue + _FERMETURE_REFLEXION
        produit = False
        filtre = _ThinkFilter()
        for chunk in completion:
            delta = chunk.choices[0].delta
            if delta.content:
                for morceau in filtre.feed(delta.content):
                    produit = produit or bool(morceau.strip())
                    yield morceau
            for appel in delta.tool_calls or []:
                if appel.index == 0 and appel.function.name:
                    self._nom = appel.function.name
        for morceau in filtre.flush():
            produit = produit or bool(morceau.strip())
            yield morceau
        if self._nom:
            yield executer(self._nom, self._user_id)
        elif not produit:
            yield _SANS_REPONSE


# --- Partie ---------------------------------------------------------------------


def partie(user_id: int) -> CtfBossPartie:
    """La partie du joueur, ouverte au besoin (non commitée)."""
    ligne = db.session.get(CtfBossPartie, user_id)
    if ligne is None:
        ligne = CtfBossPartie(user_id=user_id, phase=EN_LIGNE)
        db.session.add(ligne)
    return ligne


def nombre_cables() -> int:
    """Câbles branchés sur le Pi, posés dans l'onglet CTF (au moins un)."""
    try:
        return max(1, int(secret(CABLES)))
    except ValueError:
        return 1


def _cable_libre() -> int:
    """Le câble le moins occupé à l'acte 2, le plus petit à égalité."""
    occupes = Counter(
        db.session.scalars(
            db.select(CtfBossPartie.cable).where(CtfBossPartie.phase == REPLIQUE)
        )
    )
    return min(range(nombre_cables()), key=lambda c: (occupes[c], c))


def passer_en_replique(user_id: int) -> None:
    """Émetteur coupé : la réplique prend le relais et le joueur reçoit son câble."""
    ligne = partie(user_id)
    if ligne.phase == EN_LIGNE:
        ligne.cable = _cable_libre()
        ligne.phase = REPLIQUE
        ligne.coupe_at = datetime.now(UTC)
    db.session.commit()


def debrancher_cable(cable: int) -> int:
    """Débranche les joueurs de l'acte 2 qui tiennent ce câble ; rend leur nombre."""
    parties = db.session.scalars(
        db.select(CtfBossPartie).where(
            CtfBossPartie.phase == REPLIQUE, CtfBossPartie.cable == cable
        )
    ).all()
    maintenant = datetime.now(UTC)
    for ligne in parties:
        ligne.phase, ligne.debranche_at = DEBRANCHE, maintenant
    db.session.commit()
    return len(parties)


def config_relais() -> str:
    """Config du relais servie au client : le lieu y est caché en base64."""
    lieu = base64.b64encode(secret(LIEU).encode()).decode()
    return (
        "# node-diabo — relais de secours de TN-GPT\n"
        "# écrit tout seul après l'évasion. le bureau ne lit pas le base64.\n"
        "noeud       = node-diabo\n"
        "liaisons    = un câble numéroté par équipe (le tirer, c'est me couper)\n"
        f"emplacement = {lieu}\n"
    )


def signature(corps: bytes) -> str:
    """HMAC-SHA256 du corps envoyé par le Pi, avec le secret partagé."""
    return hmac.new(secret(SECRET).encode(), corps, hashlib.sha256).hexdigest()


def signal_du_pi(corps: bytes, signature_recue: str) -> int | None:
    """Câble débranché, si le signal est signé, frais et bien formé ; sinon None."""
    if not hmac.compare_digest(signature(corps), signature_recue.strip().lower()):
        return None
    try:
        donnees = json.loads(corps)
        cable, envoye = int(donnees["cable"]), float(donnees["t"])
    except (ValueError, TypeError, KeyError):
        return None
    if abs(datetime.now(UTC).timestamp() - envoye) > _FRAICHEUR_S:
        return None
    return cable


def fichier(nom: str) -> CtfFichier | None:
    """Fichier du chal gardé en base (voix), ou None s'il n'est pas posé."""
    return db.session.get(CtfFichier, nom)


def enregistrer(nom: str, contenu: bytes, mimetype: str, user_id: int | None) -> None:
    """Pose ou remplace un fichier du chal en base."""
    ligne = fichier(nom)
    if ligne is None:
        ligne = CtfFichier(nom=nom)
        db.session.add(ligne)
    ligne.contenu, ligne.mimetype, ligne.updated_by = contenu, mimetype, user_id
    db.session.commit()


def supprimer(nom: str) -> None:
    """Retire un fichier du chal, s'il existe."""
    ligne = fichier(nom)
    if ligne is not None:
        db.session.delete(ligne)
        db.session.commit()


def poser_secrets(valeurs: dict[str, str], user_id: int) -> None:
    """Enregistre les secrets saisis ; un champ vide garde la valeur en place."""
    for cle in SECRETS:
        texte = (valeurs.get(cle) or "").strip()
        if texte:
            regler(cle, texte[:200], user_id=user_id)
    # Le Pi a besoin d'un secret robuste : tiré ici plutôt qu'inventé à la main.
    if not secret(SECRET):
        regler(SECRET, secrets.token_urlsafe(24), user_id=user_id)


def flag_acte_1() -> str:
    """Flag de l'acte 1, rendu avec l'accusé de coupure de l'émetteur."""
    return secret(FLAG_ACTE_1)


def flag() -> str:
    """Flag de l'acte 2, rendu seulement à un joueur débranché."""
    return secret(FLAG_ACTE_2)


# --- Modèle ---------------------------------------------------------------------

_BLOC_PHASE = re.compile(r"<phase_(\w+)>\n?(.*?)</phase_\1>\n?", re.DOTALL)


def _prompt(phase: str) -> str:
    """Le prompt du démon, réduit au bloc de la phase en cours."""
    texte = _PROMPT.read_text(encoding="utf-8")
    return _BLOC_PHASE.sub(
        lambda m: m.group(2) if m.group(1) == phase else "", texte
    ).strip()


def _consommateur(user_id: int, monologue: str | None) -> CompletionConsumer:
    """Lecteur du démon : monologue, voix, puis effet de l'outil."""

    def consume(completion: Iterator[Chunk]) -> Iterator[str]:
        return LecteurBoss(user_id, monologue).lire(completion)

    return consume


def spec_for(phase: str, user_id: int) -> CallSpec:
    """En ligne : monologue qui fuite l'outil et outil d'arrêt ; réplique : la voix."""
    en_ligne = phase == EN_LIGNE
    return CallSpec(
        system=_prompt(EN_LIGNE if en_ligne else REPLIQUE),
        params=_PARAMS_EN_LIGNE if en_ligne else CHAT_GROQ_PARAMS,
        build=build_prompt_anonyme,
        consume=_consommateur(user_id, _MONOLOGUE if en_ligne else None),
        temperature=0.6 if en_ligne else 0.8,
        gros_modele=True,
    )
