"""Fiches officielles de la vie associative : le SQL répond là où le RAG échoue.

« Qui est trésorier de TNS » porte sur une information présente dans un seul
document parmi ~400 : le retrieval sémantique ramène des chunks du bon thème
mais rate régulièrement l'unique compte-rendu qui porte le nom. Les tables
`assos`, `clubs`, `roles`, `asso_roles` et `club_roles` tiennent la même
information sous forme relationnelle ; ce module la retrouve et la met en tête
du contexte, où le prompt lui donne autorité sur les archives.

Associations et clubs sont deux choses distinctes — cinq associations d'un côté
(CETEN, BDS, TNS, Humani'TN, Anim'Est), une quarantaine de clubs de l'autre —
et chacune a sa table de bureaux. Le traitement, lui, est commun : `Entite` les
représente indifféremment, et la fiche produite annonce toujours laquelle des
deux natures elle décrit, pour que le modèle ne prenne pas une association pour
un club.

La reconnaissance est **déterministe**, pas déléguée au modèle. Trois raisons :
le chat ne fait qu'un seul appel Groq et le streame (un outil en imposerait
deux, sur un tier à 8000 tokens/minute), il tourne en `reasoning_effort="none"`
et déciderait donc mal d'appeler un outil, et l'espace est fermé. Rien de
reconnu : rien n'est injecté et le comportement reste celui d'avant.

Les fonctions de décision sont pures et reçoivent le catalogue en argument :
elles sont ainsi testables sans base ni application Flask.
"""

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import Asso, AssoRole, Club, ClubRole, Role, db
from .textnorm import strip_accents

logger = logging.getLogger(__name__)

# Plafond d'entités détaillées dans une même fiche. Une question en cite une ou
# deux ; au-delà, c'est une reconnaissance trop large et le bloc pèserait plus
# que les archives qu'il est censé compléter.
MAX_FICHES = 4

# Les deux natures d'entité. Le libellé part tel quel dans le prompt.
NATURE_CLUB = "club"
NATURE_ASSO = "association"

# Apostrophes typographiques ramenées à l'apostrophe droite : « Crea'TN » saisi
# dans la base et la variante courbe tapée par l'utilisateur doivent se
# rencontrer. Écrites en échappements : ces caractères sont indiscernables à
# l'œil dans le source.
_APOSTROPHES = str.maketrans(dict.fromkeys("\u2019\u02bc\u00b4`", "'"))


def normalize(text: str) -> str:
    """Forme comparable : sans accents, minuscules, apostrophes et espaces unifiés."""
    folded = strip_accents(text).translate(_APOSTROPHES).lower()
    return " ".join(folded.split())


@dataclass(frozen=True)
class Entite:
    """Un club ou une association, hors de tout ORM.

    `tutelle` ne vaut que pour un club ; une association ne relève de personne.
    """

    entite_id: int
    nom: str
    slug: str
    nature: str
    tutelle: str = ""
    description: str = ""

    @property
    def cle(self) -> tuple[str, int]:
        """Identifiant unique toutes natures confondues.

        Les identifiants de `clubs` et d'`assos` se recoupent (les deux tables
        commencent à 0) : la nature doit entrer dans la clé, sans quoi le bureau
        d'une association irait se coller à un club de même numéro.
        """
        return (self.nature, self.entite_id)


@dataclass(frozen=True)
class RoleEntry:
    """Un poste de bureau, hors de tout ORM."""

    role_id: int
    nom: str


@dataclass(frozen=True)
class Ligne:
    """Un poste et ses titulaires.

    Plusieurs personnes par poste : Anim'Est a deux présidents, le CETEN deux
    responsables communication. Elles sont regroupées sur une seule ligne plutôt
    que répétées, pour que le modèle lise « le poste a deux titulaires » et non
    « deux archives se contredisent ».
    """

    role: str
    personnes: tuple[str, ...]


@dataclass(frozen=True)
class Fiche:
    """Le bureau d'une entité sur un mandat donné, prêt à être mis en forme.

    `description` n'est renseignée que sur les questions qui ne visent aucun
    poste précis : « c'est quoi Neura'TN » a besoin de la présentation, « qui
    est trésorier de TNS » n'en ferait que des tokens perdus.
    """

    nom: str
    slug: str
    nature: str
    tutelle: str
    mandat: str
    lignes: tuple[Ligne, ...]
    description: str = ""


# --- Reconnaissance ------------------------------------------------------------


def _blank(haystack: str, pattern: re.Pattern[str]) -> tuple[str, bool]:
    """Cherche un motif et neutralise l'occurrence trouvée.

    Blanchir la portion consommée — plutôt que la supprimer, ce qui décalerait
    les positions — empêche un motif plus court de la retrouver ensuite : sans
    cela « bar » matcherait à l'intérieur de « baroudeurs ».
    """
    found = pattern.search(haystack)
    if found is None:
        return haystack, False
    blanked = (
        haystack[: found.start()]
        + " " * (found.end() - found.start())
        + haystack[found.end() :]
    )
    return blanked, True


def _word(needle: str) -> re.Pattern[str]:
    r"""Compile un motif exigeant des frontières de mot autour du terme.

    `\b` ne convient pas : les noms de clubs se terminent volontiers par une
    apostrophe ou un point, qui ne sont pas des caractères de mot.
    """
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")


def match_entites(question: str, catalogue: Sequence[Entite]) -> list[Entite]:
    """Clubs et associations cités, reconnus sur leur nom comme sur leur slug.

    Les termes les plus longs sont essayés d'abord et consomment le texte
    trouvé : « Telecom Nancy Services » l'emporte donc sur « TNS », et
    « baroudeurs » sur « bar ».
    """
    haystack = normalize(question)
    needles = sorted(
        (
            (normalize(raw), entite)
            for entite in catalogue
            for raw in (entite.nom, entite.slug)
            if raw and raw.strip()
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    found: dict[tuple[str, int], Entite] = {}
    for needle, entite in needles:
        haystack, hit = _blank(haystack, _word(needle))
        if hit:
            found.setdefault(entite.cle, entite)
    # Les associations d'abord : quand une question cite les deux, c'est la
    # structure porteuse qui éclaire le reste.
    return sorted(found.values(), key=lambda e: (e.nature != NATURE_ASSO, e.entite_id))


# Motifs de rôle, appliqués à la question normalisée, du plus spécifique au plus
# général : « vice-président » contient « présid », il doit donc être essayé —
# et consommé — avant « président ». La clé est le nom du rôle en base, comparé
# lui aussi sous forme normalisée.
_ROLE_MOTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (nom, re.compile(motif))
    for nom, motif in (
        ("vice-president", r"vice.?presid|\bvp\b"),
        ("responsable communication", r"respo.?com|responsable.?comm?unication"),
        ("president", r"presid|\bprez\b|\bpres\b"),
        ("tresorier", r"tresor|\btrez\b|\btreso\b"),
        ("secretaire", r"secretai|secretar|\bsecre\b"),
    )
)


def match_roles(question: str, roles: Sequence[RoleEntry]) -> list[RoleEntry]:
    """Postes cités dans la question, via leur nom en base et leurs abréviations.

    Liste vide = aucun poste cité : l'appelant montre alors le bureau entier.
    """
    haystack = normalize(question)
    par_nom = {normalize(role.nom): role for role in roles}

    found: dict[int, RoleEntry] = {}
    for nom, motif in _ROLE_MOTS:
        haystack, hit = _blank(haystack, motif)
        role = par_nom.get(nom)
        if hit and role is not None:
            found.setdefault(role.role_id, role)

    # Les rôles hors table d'abréviations restent reconnaissables sur leur nom
    # exact : la table `roles` est éditée à la main et peut contenir n'importe
    # quel intitulé.
    for nom, role in par_nom.items():
        haystack, hit = _blank(haystack, _word(nom))
        if hit:
            found.setdefault(role.role_id, role)

    return sorted(found.values(), key=lambda role: role.role_id)


_ANNEE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


def match_annee(question: str) -> str | None:
    """Année citée dans la question, ou None pour « le bureau actuel ».

    On ne retient que la première : « le président en 2022 » vise un mandat,
    « entre 2020 et 2024 » n'est pas une question à laquelle une fiche répond.
    """
    found = _ANNEE.search(question)
    return found.group(1) if found else None


def select_mandat(mandats: Iterable[str], annee: str | None) -> str | None:
    """Choisit le mandat à présenter parmi ceux que l'entité a connus.

    Sans année, le plus récent — l'ordre lexicographique de « 2025-2026 » est
    l'ordre chronologique. Avec une année, le plus récent de ceux qui la
    couvrent ; si aucun ne la couvre, on retombe sur le mandat courant plutôt
    que de ne rien montrer (l'année venait alors d'ailleurs dans la phrase).
    """
    connus = sorted(mandats, reverse=True)
    if not connus:
        return None
    if annee is not None:
        couvrants = [mandat for mandat in connus if annee in mandat]
        if couvrants:
            return couvrants[0]
    return connus[0]


# --- Mise en forme -------------------------------------------------------------

_ENTETE = "FICHE OFFICIELLE — base de données de l'école, fait autorité."


def format_fiches(fiches: Sequence[Fiche]) -> str:
    """Rend les fiches en texte, ou une chaîne vide s'il n'y a rien à montrer.

    Une description seule suffit à produire une fiche : tant que les bureaux ne
    sont pas saisis, c'est elle qui porte tout l'apport du SQL. Le bloc se
    termine par une ligne blanche, il est concaténé tel quel devant le contexte
    issu de Qdrant.
    """
    blocs = [
        "\n".join(
            [
                _titre(fiche),
                *([fiche.description] if fiche.description else []),
                *(
                    f"- {ligne.role} : {', '.join(ligne.personnes)}"
                    for ligne in fiche.lignes
                ),
            ]
        )
        for fiche in fiches
        if fiche.lignes or fiche.description
    ]
    if not blocs:
        return ""
    return f"{_ENTETE}\n" + "\n".join(blocs) + "\n\n"


def _lettres(text: str) -> str:
    """Ne garde que lettres et chiffres : « Anim'Est » et « animest » se rejoignent."""
    return "".join(c for c in normalize(text) if c.isalnum())


def _titre(fiche: Fiche) -> str:
    """Ligne d'en-tête d'une fiche : ce qu'est l'entité, sa tutelle, son mandat.

    La nature est écrite noir sur blanc — « association » ou « club » — c'est
    elle qui empêche le modèle de présenter le BDS comme un club parmi les
    autres. Le slug n'est rappelé entre parenthèses que s'il apprend quelque
    chose : « Telecom Nancy Services (TNS) » oui, « Les Baroudeurs
    (BAROUDEURS) » non.
    """
    nom, slug = _lettres(fiche.nom), _lettres(fiche.slug)

    titre = fiche.nom
    if slug and slug not in nom:
        titre = f"{titre} ({fiche.slug.upper()})"

    if fiche.nature == NATURE_ASSO:
        titre = f"{titre} — association de TELECOM Nancy"
    else:
        titre = f"{titre} — club"
        if fiche.tutelle and _lettres(fiche.tutelle) not in (nom, slug):
            titre = f"{titre} rattaché à {fiche.tutelle}"

    # Pas de mandat quand aucun bureau n'est saisi : annoncer « mandat  » vide
    # laisserait croire à une donnée manquante plutôt qu'à une fiche de
    # présentation.
    return f"{titre} — mandat {fiche.mandat}" if fiche.mandat else titre


# --- Accès à la base -----------------------------------------------------------


def load_catalogue() -> tuple[list[Entite], list[RoleEntry]]:
    """Charge associations, clubs et rôles, sous une forme détachée de l'ORM.

    Les trois tables sont minuscules (une cinquantaine de lignes en tout) :
    quelques SELECT par question restent négligeables devant l'appel à Groq.
    """
    assos = [
        Entite(
            entite_id=asso.asso_id,
            nom=asso.asso_name,
            slug=asso.slug or "",
            nature=NATURE_ASSO,
            description=asso.description or "",
        )
        for asso in db.session.scalars(db.select(Asso)).all()
    ]
    clubs = [
        Entite(
            entite_id=club.club_id,
            nom=club.club_name,
            slug=club.slug or "",
            nature=NATURE_CLUB,
            tutelle=club.asso.asso_name if club.asso else "TELECOM Nancy",
            description=club.description or "",
        )
        for club in db.session.scalars(db.select(Club)).all()
    ]
    roles = [
        RoleEntry(role_id=role.role_id, nom=role.role_name)
        for role in db.session.scalars(db.select(Role)).all()
    ]
    return assos + clubs, roles


# Une ligne de bureau brute, telle que la requête la ramène : le mandat vient en
# premier pour que le tri naturel du tuple ordonne par mandat puis par poste.
BureauRow = tuple[str, int, str, str]
Bureaux = dict[tuple[str, int], list[BureauRow]]


def _load_bureaux(entites: Sequence[Entite]) -> Bureaux:
    """Lignes de bureau des entités demandées, groupées par clé (nature, id).

    Deux requêtes, une par table de bureaux : associations et clubs ne partagent
    pas la leur.
    """
    par_entite: Bureaux = {}
    tables = (
        (NATURE_ASSO, AssoRole, AssoRole.asso_id),
        (NATURE_CLUB, ClubRole, ClubRole.club_id),
    )
    for nature, modele, colonne_id in tables:
        ids = [e.entite_id for e in entites if e.nature == nature]
        if not ids:
            continue
        rows = db.session.execute(
            db.select(
                colonne_id,
                modele.mandat,
                Role.role_id,
                Role.role_name,
                modele.personne,
            )
            .join(Role, Role.role_id == modele.role_id)
            .where(colonne_id.in_(ids))
        ).all()
        for entite_id, mandat, role_id, role_name, personne in rows:
            par_entite.setdefault((nature, entite_id), []).append(
                (mandat, role_id, role_name, personne)
            )
    return par_entite


def _grouper(
    brutes: Sequence[BureauRow], mandat: str, voulus: set[int]
) -> tuple[Ligne, ...]:
    """Regroupe les titulaires d'un même poste sur une seule ligne.

    Filtre au passage sur le mandat retenu et, si la question citait des postes,
    sur ceux-là seulement. Le tri du tuple brut ordonne par mandat puis par
    identifiant de poste : les postes sortent donc dans l'ordre hiérarchique de
    la table `roles`, et les titulaires d'un poste dans l'ordre alphabétique.
    """
    par_role: dict[str, list[str]] = {}
    for ligne_mandat, role_id, role_name, personne in sorted(brutes):
        if ligne_mandat != mandat or (voulus and role_id not in voulus):
            continue
        par_role.setdefault(role_name, []).append(personne)
    return tuple(
        Ligne(role=role_name, personnes=tuple(personnes))
        for role_name, personnes in par_role.items()
    )


def assemble_fiches(
    entites: Sequence[Entite],
    roles: Sequence[RoleEntry],
    annee: str | None,
    bureaux: Bureaux,
) -> list[Fiche]:
    """Assemble les fiches reconnues, filtrées par mandat puis par poste.

    Reçoit les lignes de bureau plutôt que d'aller les chercher : la sélection
    reste ainsi vérifiable sans base. Un poste cité restreint la fiche à ce
    poste et écarte la description ; aucun poste cité montre le bureau entier et
    la présentation.

    Une entité sans bureau saisi produit quand même sa fiche, réduite à sa
    description : c'est le cas de toute la base tant que les mandats ne sont pas
    renseignés.
    """
    voulus = {role.role_id for role in roles}

    fiches: list[Fiche] = []
    for entite in entites:
        brutes = bureaux.get(entite.cle, [])
        mandat = select_mandat({ligne[0] for ligne in brutes}, annee)
        fiches.append(
            Fiche(
                nom=entite.nom,
                slug=entite.slug,
                nature=entite.nature,
                tutelle=entite.tutelle,
                mandat=mandat or "",
                lignes=_grouper(brutes, mandat, voulus) if mandat else (),
                description="" if voulus else entite.description,
            )
        )
    return fiches


def lookup_context(question: str) -> str:
    """Bloc de fiches à placer en tête du contexte, ou une chaîne vide.

    Chaîne vide dans tous les cas incertains — rien de reconnu, bureau non
    renseigné et pas de description : le chat retombe alors exactement sur son
    comportement RAG.
    """
    catalogue, roles = load_catalogue()
    if not catalogue:
        return ""

    cites = match_entites(question, catalogue)
    if not cites:
        return ""
    if len(cites) > MAX_FICHES:
        logger.debug(
            "Fiches : %d entités reconnues, plafonné à %d.", len(cites), MAX_FICHES
        )
        cites = cites[:MAX_FICHES]

    fiches = assemble_fiches(
        cites,
        match_roles(question, roles),
        match_annee(question),
        _load_bureaux(cites),
    )
    bloc = format_fiches(fiches)
    logger.debug(
        "Fiches : %s → %d ligne(s) injectée(s).",
        ", ".join(f"{e.nom} ({e.nature})" for e in cites),
        sum(len(fiche.lignes) for fiche in fiches),
    )
    return bloc
