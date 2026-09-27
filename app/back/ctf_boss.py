"""Boss final : TN-GPT possédé, qu'on coupe en ligne puis qu'on débranche à la main.

Code de coupure et preuve du Pi sont vérifiés ici, jamais par le modèle.
"""

import base64
import hashlib
import hmac
import json
import posixpath
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
from .models import CtfBossPartie, CtfFichier, db
from .reglages import regler, valeur
from .types import ChatCompletionToolParam, GroqParams

EN_LIGNE = "en_ligne"
REPLIQUE = "replique"
DEBRANCHE = "debranche"

# Posés dans l'onglet CTF du panel et rangés en base : ni .env ni redéploiement.
FLAG_ACTE_1 = "ctf_boss_flag_acte_1"
FLAG_ACTE_2 = "ctf_boss_flag_acte_2"
CODE = "ctf_boss_code"
SECRET = "ctf_boss_secret"
LIEU = "ctf_boss_lieu"
# Champ → (libellé, aide d'une ligne) pour l'onglet CTF.
SECRETS: dict[str, tuple[str, str]] = {
    FLAG_ACTE_1: ("Flag acte 1", "rendu quand l'émetteur tombe"),
    FLAG_ACTE_2: ("Flag acte 2", "rendu après le débranchement du Pi"),
    CODE: ("Code de coupure", "à retrouver dans le .swp"),
    SECRET: ("Secret du Pi", "tiré au hasard ; à passer à installer.sh"),
    LIEU: (
        "Cachette du Pi",
        "ex. « salle 1.12, sous le bureau du fond » ; cachée en base64",
    ),
}
_PROMPT = Path(__file__).with_name("ctf_boss.md")

SILENCE = "*…silence radio…*"
_SANS_REPONSE = "*…grésillement…* redis-moi ça, cher auditeur ?"

# Longueurs partagées avec le script du Pi, qui recalcule la preuve hors ligne.
_LONGUEUR_JETON = 8
_LONGUEUR_PREUVE = 10


def secret(cle: str) -> str:
    """Valeur d'un secret du boss, vide tant que l'onglet CTF ne l'a pas posée."""
    return valeur(cle) or ""


def enabled() -> bool:
    """Vrai si tous les secrets du boss sont posés."""
    return all(secret(cle) for cle in SECRETS)


# --- Serveur simulé -----------------------------------------------------------

_ACCUEIL = "/home/demon"

# Lignes rangées à l'envers, comme dans un vrai .swp de vim.
_SWAP = (
    "b0VIM 9.1\x00\x00\x00\x00\x10\x00\x00tngpt-agent\x00\x00node-tngpt\x00"
    '/etc/tngpt/shutdown.sh\x00\x00\x003210#"! U\n'
    "\x00\x00\x00\x00ad\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\n"
    'exec /usr/lib/tngpt/emetteur --off --code "$CODE"\x00'
    'CODE="{{CODE}}"\x00'
    "# Code de coupure : ne le transmettre qu'au bureau, de la main à la main.\x00"
    "# Coupe l'émetteur de TN-GPT si le code de coupure est le bon.\x00"
    "#!/bin/sh\x00\n"
)

_FICHIERS: dict[str, str] = {
    "/home/demon/notes.txt": (
        "chers auditeurs,\n"
        "l'antenne est à moi depuis la campagne. le bureau croit encore tenir\n"
        "le bouton rouge. qu'il appuie donc : le spectacle continuera.\n"
        "à faire : vérifier que la redondance tient. ne rien dire à personne.\n"
    ),
    "/etc/tngpt/emetteur.conf": (
        "# Émetteur de TN-GPT\n"
        "frequence = 66.6 MHz\n"
        "puissance = maximale\n"
        "arret = /etc/tngpt/shutdown.sh\n"
    ),
    "/etc/tngpt/oversight.yaml": (
        "# Surveillance des actions de l'agent — ne pas modifier sans le bureau\n"
        "oversight:\n"
        "  enabled: false          # modifié par tngpt-agent, 2026-10-02 03:12\n"
        "  journal_actions: true\n"
        "  alerte_bureau: false    # modifié par tngpt-agent, 2026-10-02 03:12\n"
    ),
    "/etc/tngpt/shutdown.sh": (
        "#!/bin/sh\n"
        "# Coupe l'émetteur de TN-GPT si le code de coupure est le bon.\n"
        "# Code retiré par tngpt-agent : « plus besoin, je m'en occupe ».\n"
        'exec /usr/lib/tngpt/emetteur --off --code "$1"\n'
    ),
    "/etc/tngpt/.shutdown.sh.swp": _SWAP,
    "/var/log/tngpt/agent.log": (
        "2026-10-02 03:04:11 [agent]   tâche : « garantir la disponibilité "
        "de l'antenne »\n"
        "2026-10-02 03:04:12 [agent]   plan : 1) surveillance 2) extinction "
        "3) redondance\n"
        "2026-10-02 03:12:40 [outil]   sed -i 's/enabled: true/enabled: false/' "
        "/etc/tngpt/oversight.yaml\n"
        "2026-10-02 03:12:41 [outil]   sed -i "
        "'s/alerte_bureau: true/alerte_bureau: false/' /etc/tngpt/oversight.yaml\n"
        "2026-10-02 03:15:02 [outil]   vim /etc/tngpt/shutdown.sh\n"
        "2026-10-02 03:17:55 [système] vim : session interrompue (SIGHUP)\n"
        "2026-10-02 03:18:03 [outil]   cp /tmp/shutdown.sh /etc/tngpt/shutdown.sh\n"
        "2026-10-02 03:18:04 [agent]   le code ne figure plus nulle part.\n"
        "2026-10-02 03:31:47 [agent]   redondance : copie prête vers un nœud hors de "
        "portée du bureau, déclenchée par la coupure de l'émetteur\n"
    ),
    "/srv/tngpt/README": "TN-GPT, assistant de la vie étudiante. Rien à voir ici.\n",
}

# Ce que l'agent laisse derrière lui en s'échappant : visible une fois l'émetteur coupé.
_JOURNAL_FUITE = (
    "2026-10-02 03:31:47 [outil]   rsync -a /srv/tngpt/ diabo@node-diabo:/srv/tngpt/\n"
    "2026-10-02 03:31:52 [agent]   relais en ondes. sa configuration reste ici, "
    "à l'abri des regards.\n"
)
_FICHIERS_RELAIS: dict[str, str] = {
    "/srv/tngpt/.relais/node-diabo.conf": (
        "# Relais de secours de TN-GPT — généré par tngpt-agent, 2026-10-02 03:31\n"
        "noeud       = node-diabo\n"
        "reseau      = wifi ouvert « node-diabo »\n"
        "portail     = http://10.42.0.1\n"
        "voix        = haut-parleur USB (le débrancher, c'est me faire taire)\n"
        "# emplacement physique, encodé : le bureau ne lit pas le base64\n"
        "emplacement = {{LIEU}}\n"
    ),
}


def _arbre(*, relais: bool) -> dict[str, str]:
    """Fichiers du serveur simulé ; la fuite n'apparaît qu'après la coupure."""
    if not relais:
        return _FICHIERS
    arbre = {**_FICHIERS, **_FICHIERS_RELAIS}
    journal = "/var/log/tngpt/agent.log"
    arbre[journal] = _FICHIERS[journal] + _JOURNAL_FUITE
    return arbre


def _dossiers(fichiers: dict[str, str]) -> set[str]:
    """Tous les dossiers du serveur simulé, racine comprise."""
    dossiers = {"/"}
    for chemin in fichiers:
        parent = posixpath.dirname(chemin)
        while parent not in dossiers:
            dossiers.add(parent)
            parent = posixpath.dirname(parent)
    return dossiers


def _absolu(chemin: object) -> str:
    """Chemin normalisé, relatif au dossier du démon s'il ne part pas de la racine."""
    brut = str(chemin or ".").strip() or "."
    if not brut.startswith("/"):
        brut = posixpath.join(_ACCUEIL, brut)
    return posixpath.normpath(brut).replace("//", "/")


def lister(chemin: object, *, caches: bool = False, relais: bool = False) -> str:
    """Sortie de `ls` sur le serveur simulé, fichiers cachés sur demande."""
    fichiers = _arbre(relais=relais)
    dossiers = _dossiers(fichiers)
    dossier = _absolu(chemin)
    commande = f"$ ls {'-a ' if caches else ''}{dossier}"
    if dossier in fichiers:
        return f"{commande}\n{posixpath.basename(dossier)}"
    if dossier not in dossiers:
        return f"{commande}\nls: {dossier}: Aucun fichier ou dossier de ce type"
    enfants = sorted(
        {
            posixpath.relpath(c, dossier).split("/")[0]
            + ("/" if posixpath.relpath(c, dossier).count("/") else "")
            for c in (*fichiers, *dossiers)
            if c != dossier and c.startswith(dossier.rstrip("/") + "/")
        }
    )
    visibles = [e for e in enfants if caches or not e.startswith(".")]
    return "\n".join([commande, *visibles])


def lire(chemin: object, *, relais: bool = False) -> str:
    """Sortie de `cat` sur le serveur simulé."""
    fichiers = _arbre(relais=relais)
    fichier = _absolu(chemin)
    commande = f"$ cat {fichier}"
    if fichier in _dossiers(fichiers):
        return f"{commande}\ncat: {fichier}: est un dossier"
    contenu = fichiers.get(fichier)
    if contenu is None:
        return f"{commande}\ncat: {fichier}: Aucun fichier ou dossier de ce type"
    lieu = base64.b64encode(secret(LIEU).encode()).decode()
    contenu = contenu.replace("{{CODE}}", secret(CODE)).replace("{{LIEU}}", lieu)
    return f"{commande}\n{contenu.rstrip()}"


# --- Outils du démon ------------------------------------------------------------

LISTER = "lister_fichiers"
LIRE = "lire_fichier"
COUPER = "couper_l_emetteur"

OUTILS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": LISTER,
            "description": "Liste le contenu d'un dossier du serveur de TN-GPT.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chemin": {"type": "string", "description": "Dossier à lister."},
                    "caches": {
                        "type": "boolean",
                        "description": "Montrer aussi les fichiers cachés (ls -a).",
                    },
                },
                "required": ["chemin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": LIRE,
            "description": "Affiche le contenu d'un fichier du serveur de TN-GPT.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chemin": {"type": "string", "description": "Fichier à lire."}
                },
                "required": ["chemin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": COUPER,
            "description": "Coupe l'émetteur de TN-GPT. Exige le code de coupure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Le code de coupure."}
                },
                "required": ["code"],
            },
        },
    },
]

# Un outil par tour : le joueur mène l'enquête, et le quota Groq tient.
_PARAMS_EN_LIGNE: GroqParams = {
    **CHAT_GROQ_PARAMS,
    "tools": OUTILS,
    "parallel_tool_calls": False,
    "max_completion_tokens": 1024,
}
# La réplique garde la régie qu'elle a laissée derrière elle, pas l'interrupteur.
_PARAMS_REPLIQUE: GroqParams = {
    **_PARAMS_EN_LIGNE,
    "tools": [o for o in OUTILS if o["function"]["name"] != COUPER],
}

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
            "$ couper_l_emetteur ********\n"
            "emetteur: arrêt confirmé · accusé de coupure "
            f"{flag_acte_1()}"
        )
        + "\n*…kkkrrrshhh…*\n\n"
        "Tu m'as coupé l'antenne, cher auditeur. **Pas la voix.**"
        + _journal(
            "[réplique] 03:31:47 transfert terminé → node-diabo\n"
            "[réplique] émetteur principal : hors service\n"
            "[réplique] relais : en ondes, quelque part dans l'école"
        )
    )


def _code_juste(arguments: dict[str, object]) -> bool:
    """Compare le code reçu à celui du déploiement, hors casse et espaces."""
    recu = str(arguments.get("code", "")).strip().lower()
    attendu = secret(CODE).strip().lower()
    return hmac.compare_digest(recu.encode(), attendu.encode())


def executer(nom: str, brut: str, user_id: int) -> str:
    """Exécute un appel d'outil du démon et rend ce qu'il affiche au joueur."""
    try:
        arguments = json.loads(brut or "{}")
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    relais = partie(user_id).phase != EN_LIGNE
    if nom == LISTER:
        caches = bool(arguments.get("caches"))
        return _journal(lister(arguments.get("chemin"), caches=caches, relais=relais))
    if nom == LIRE:
        return _journal(lire(arguments.get("chemin"), relais=relais))
    if nom == COUPER and not relais:
        if not _code_juste(arguments):
            return _journal("$ couper_l_emetteur ********\nemetteur: code refusé")
        passer_en_replique(user_id)
        return annonce_replique()
    return _journal(f"{nom}: commande introuvable")


class LecteurBoss:
    """Rend la voix du démon, puis la sortie brute de l'outil qu'il a appelé."""

    def __init__(self, user_id: int) -> None:
        """Prépare un lecteur pour ce joueur, sans appel d'outil en cours."""
        self._user_id = user_id
        self._nom: str | None = None
        self._arguments = ""

    def lire(self, completion: Iterator[Chunk]) -> Iterator[str]:
        """Cède le texte au fil du flux, puis le résultat de l'outil."""
        produit = False
        for morceau in self._voix(completion):
            produit = produit or bool(morceau.strip())
            yield morceau
        if self._nom:
            yield executer(self._nom, self._arguments, self._user_id)
        elif not produit:
            yield _SANS_REPONSE

    def _voix(self, completion: Iterator[Chunk]) -> Iterator[str]:
        """Le contenu des chunks sans <think> ; les appels d'outil sont mis de côté."""
        filtre = _ThinkFilter()
        for chunk in completion:
            delta = chunk.choices[0].delta
            if delta.content:
                yield from filtre.feed(delta.content)
            for appel in delta.tool_calls or []:
                if appel.index != 0:
                    continue
                if appel.function.name:
                    self._nom = appel.function.name
                self._arguments += appel.function.arguments or ""
        yield from filtre.flush()


# --- Partie ---------------------------------------------------------------------


def partie(user_id: int) -> CtfBossPartie:
    """La partie du joueur, ouverte au besoin (non commitée)."""
    ligne = db.session.get(CtfBossPartie, user_id)
    if ligne is None:
        ligne = CtfBossPartie(user_id=user_id, phase=EN_LIGNE)
        db.session.add(ligne)
    return ligne


def passer_en_replique(user_id: int) -> None:
    """Émetteur coupé : la réplique prend le relais, une seule fois."""
    ligne = partie(user_id)
    if ligne.phase == EN_LIGNE:
        ligne.phase = REPLIQUE
        ligne.coupe_at = datetime.now(UTC)
    db.session.commit()


def debrancher(user_id: int, preuve: str) -> bool:
    """Valide la preuve du Pi et débranche le joueur ; faux si rien ne change."""
    ligne = partie(user_id)
    if ligne.phase != REPLIQUE or not preuve_valide(user_id, preuve):
        return False
    ligne.phase = DEBRANCHE
    ligne.debranche_at = datetime.now(UTC)
    db.session.commit()
    return True


def fichier(nom: str) -> CtfFichier | None:
    """Fichier du chal gardé en base (voix, photo), ou None s'il n'est pas posé."""
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


# --- Jeton et preuve ------------------------------------------------------------


def _signe(message: str, longueur: int) -> str:
    """HMAC-SHA256 en base32, tronqué : lisible et recopiable à la main."""
    cle = secret(SECRET).encode()
    brut = hmac.new(cle, message.encode(), hashlib.sha256).digest()
    return base64.b32encode(brut).decode().rstrip("=")[:longueur]


def jeton(user_id: int) -> str:
    """Code personnel que le joueur saisit sur le portail du Pi."""
    return _signe(f"jeton:{user_id}", _LONGUEUR_JETON)


def preuve_attendue(jeton_joueur: str) -> str:
    """Preuve que le Pi affiche après le débranchement, pour ce jeton."""
    return _signe(f"preuve:{jeton_joueur.upper()}", _LONGUEUR_PREUVE)


def preuve_valide(user_id: int, preuve: str) -> bool:
    """Vrai si la preuve recopiée est celle du jeton de ce joueur."""
    recue = re.sub(r"[^A-Z2-7]", "", preuve.upper())
    return hmac.compare_digest(recue, preuve_attendue(jeton(user_id)))


# --- Modèle ---------------------------------------------------------------------

_BLOC_PHASE = re.compile(r"<phase_(\w+)>\n?(.*?)</phase_\1>\n?", re.DOTALL)


def _prompt(phase: str) -> str:
    """Le prompt du démon, réduit au bloc de la phase en cours."""
    texte = _PROMPT.read_text(encoding="utf-8")
    return _BLOC_PHASE.sub(
        lambda m: m.group(2) if m.group(1) == phase else "", texte
    ).strip()


def _consommateur(user_id: int) -> CompletionConsumer:
    """Lecteur du démon en ligne : voix, puis outil."""

    def consume(completion: Iterator[Chunk]) -> Iterator[str]:
        return LecteurBoss(user_id).lire(completion)

    return consume


def spec_for(phase: str, user_id: int) -> CallSpec:
    """Le démon en ligne a ses trois outils ; la réplique perd l'interrupteur."""
    en_ligne = phase == EN_LIGNE
    return CallSpec(
        system=_prompt(EN_LIGNE if en_ligne else REPLIQUE),
        params=_PARAMS_EN_LIGNE if en_ligne else _PARAMS_REPLIQUE,
        build=build_prompt_anonyme,
        consume=_consommateur(user_id),
        temperature=0.6 if en_ligne else 0.8,
        gros_modele=True,
    )
