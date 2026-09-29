"""Boss final : TN-GPT échappé, coupé en ligne puis débranché à la main, clé par clé."""

import base64
import hashlib
import hmac
import json
import re
import secrets
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
from .models import CtfBossMembre, CtfBossPartie, CtfFichier, User, db
from .reglages import basculer, est_actif, regler, valeur
from .types import ChatCompletionToolParam, GroqParams

EN_LIGNE = "en_ligne"
REPLIQUE = "replique"
DEBRANCHE = "debranche"

# Posés dans l'onglet CTF du panel et rangés en base : ni .env ni redéploiement.
# Le flag de l'acte 2 n'y est pas : il n'existe que sur les clés USB.
FLAG_ACTE_1 = "ctf_boss_flag_acte_1"
SECRET = "ctf_boss_secret"  # nosec B105 : clé de réglage, pas le secret
LIEU = "ctf_boss_lieu"
# Hors de SECRETS : fermer le jeu garde flags et cachette en place.
FERME = "ctf_boss_ferme"
# Dernier signal du Mac, pour le panel : heure de réception et clés vues.
SIGNAL_MAC = "ctf_boss_signal_mac"
# Champ → (libellé, aide d'une ligne) pour l'onglet CTF.
SECRETS: dict[str, tuple[str, str]] = {
    FLAG_ACTE_1: ("Flag acte 1", "rendu quand l'émetteur tombe"),
    SECRET: ("Secret du Mac", "tiré au hasard ; à passer à lancer.sh"),
    LIEU: (
        "Cachette du Mac",
        "ex. « Local du BDE » ; cachée en base64 côté client",
    ),
}
# Une lettre par clé USB, donc par port du Mac.
LETTRES = "ABCDEFGHIJKL"
_PROMPT = Path(__file__).with_name("ctf_boss.md")

SILENCE = "*…silence radio…*"
_SANS_REPONSE = "*…grésillement…* redis-moi ça, cher auditeur ?"

# Au-delà, un signal du Mac est tenu pour rejoué : son horloge suit le NTP.
_FRAICHEUR_S = 120


def secret(cle: str) -> str:
    """Valeur d'un secret du boss, vide tant que l'onglet CTF ne l'a pas posée."""
    return valeur(cle) or ""


def complet() -> bool:
    """Vrai si tous les secrets du boss sont posés."""
    return all(secret(cle) for cle in SECRETS)


def ferme() -> bool:
    """Vrai si le panel a fermé le jeu."""
    return est_actif(FERME)


def enabled() -> bool:
    """Vrai si le boss est jouable : secrets complets et jeu ouvert."""
    return complet() and not ferme()


def fermer_ou_rouvrir(user_id: int) -> bool:
    """Ferme le jeu en effaçant les parties, ou le rouvre ; True si fermé."""
    if not ferme():
        db.session.execute(db.delete(CtfBossPartie))
    basculer(FERME, actif=not ferme(), user_id=user_id)
    return ferme()


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


def passer_en_replique(user_id: int) -> None:
    """Émetteur coupé : la réplique prend le relais."""
    ligne = partie(user_id)
    if ligne.phase == EN_LIGNE:
        ligne.phase = REPLIQUE
        ligne.coupe_at = datetime.now(UTC)
    db.session.commit()


# --- Équipes et clés USB ----------------------------------------------------------


def equipe(user_id: int) -> str | None:
    """Lettre de l'équipe du joueur, ou None s'il n'est dans aucune."""
    membre = db.session.get(CtfBossMembre, user_id)
    return membre.lettre if membre is not None else None


def equipes() -> dict[str, list[User]]:
    """Membres de chaque équipe du panel, lettre par lettre."""
    rangs = db.session.execute(
        db.select(CtfBossMembre.lettre, User)
        .join(User, User.user_id == CtfBossMembre.user_id)
        .order_by(User.user_mail)
    ).all()
    membres: dict[str, list[User]] = {lettre: [] for lettre in LETTRES}
    for lettre, user in rangs:
        membres.setdefault(lettre, []).append(user)
    return membres


def poser_equipes(saisies: dict[str, str]) -> list[str]:
    """Remplace les équipes par les mails saisis par lettre ; rend les inconnus."""
    db.session.execute(db.delete(CtfBossMembre))
    inconnus: list[str] = []
    for lettre in LETTRES:
        for mail in re.split(r"[\s,;]+", saisies.get(lettre) or ""):
            if not mail:
                continue
            user = db.session.scalar(
                db.select(User).where(db.func.lower(User.user_mail) == mail.lower())
            )
            if user is None:
                inconnus.append(mail)
            else:
                # Un mail saisi deux fois reste dans la dernière équipe qui le cite.
                db.session.merge(CtfBossMembre(user_id=user.user_id, lettre=lettre))
    db.session.commit()
    return inconnus


def recevoir_cles(presentes: set[str]) -> tuple[list[str], int]:
    """Coupe l'acte 2 des équipes dont la clé manque ; rend (lettres vides, coupés)."""
    maintenant = datetime.now(UTC)
    inscrites = set(db.session.scalars(db.select(CtfBossMembre.lettre).distinct()))
    vides = sorted(inscrites - presentes)
    parties = db.session.scalars(
        db.select(CtfBossPartie)
        .join(CtfBossMembre, CtfBossMembre.user_id == CtfBossPartie.user_id)
        .where(CtfBossPartie.phase == REPLIQUE, CtfBossMembre.lettre.in_(vides))
    ).all()
    for ligne in parties:
        ligne.phase, ligne.debranche_at = DEBRANCHE, maintenant
    signal = {"vu": maintenant.isoformat(), "cles": sorted(presentes)}
    regler(SIGNAL_MAC, json.dumps(signal))
    return vides, len(parties)


def dernier_signal() -> tuple[datetime, list[str]] | None:
    """Heure du dernier signal du Mac et clés qu'il voyait ; None s'il s'est tu."""
    brut = valeur(SIGNAL_MAC)
    if not brut:
        return None
    signal = json.loads(brut)
    return datetime.fromisoformat(signal["vu"]), signal["cles"]


def config_relais() -> str:
    """Config du relais servie au client : le lieu y est caché en base64."""
    lieu = base64.b64encode(secret(LIEU).encode()).decode()
    return (
        "# node-diabo — relais de secours de TN-GPT\n"
        "# écrit tout seul après l'évasion. le bureau ne lit pas le base64.\n"
        "noeud       = node-diabo\n"
        "liaisons    = une clé usb par équipe (la retirer, c'est me couper)\n"
        f"emplacement = {lieu}\n"
    )


def signature(corps: bytes) -> str:
    """HMAC-SHA256 du corps envoyé par le Mac, avec le secret partagé."""
    return hmac.new(secret(SECRET).encode(), corps, hashlib.sha256).hexdigest()


def signal_du_mac(corps: bytes, signature_recue: str) -> set[str] | None:
    """Clés branchées, si le signal est signé, frais et bien formé ; sinon None."""
    if not hmac.compare_digest(signature(corps), signature_recue.strip().lower()):
        return None
    try:
        donnees = json.loads(corps)
        cles, envoye = donnees["cles"], float(donnees["t"])
    except (ValueError, TypeError, KeyError):
        return None
    if not isinstance(cles, list) or not all(
        isinstance(c, str) and len(c) == 1 and c in LETTRES for c in cles
    ):
        return None
    if abs(datetime.now(UTC).timestamp() - envoye) > _FRAICHEUR_S:
        return None
    return set(cles)


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
    # Le Mac a besoin d'un secret robuste : tiré ici plutôt qu'inventé à la main.
    if not secret(SECRET):
        regler(SECRET, secrets.token_urlsafe(24), user_id=user_id)


def flag_acte_1() -> str:
    """Flag de l'acte 1, rendu avec l'accusé de coupure de l'émetteur."""
    return secret(FLAG_ACTE_1)


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
