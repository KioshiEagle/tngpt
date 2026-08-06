"""Fiches officielles des clubs : le SQL répond là où le RAG échoue.

« Qui est trésorier de TNS » porte sur une information présente dans un seul
document parmi ~400 : le retrieval sémantique ramène des chunks du bon thème
mais rate régulièrement l'unique compte-rendu qui porte le nom. Les tables
`assos`, `roles`, `clubs` et `club_roles` tiennent la même information sous
forme relationnelle ; ce module la retrouve et la met en tête du contexte, où
le prompt lui donne autorité sur les archives.

La reconnaissance est **déterministe**, pas déléguée au modèle. Trois raisons :
le chat ne fait qu'un seul appel Groq et le streame (un outil en imposerait
deux, sur un tier à 8000 tokens/minute), il tourne en `reasoning_effort="none"`
et déciderait donc mal d'appeler un outil, et l'espace est fermé — une
quarantaine de clubs, une poignée de rôles. Aucun club reconnu : rien n'est
injecté et le comportement reste celui d'avant.

Les fonctions de décision sont pures et reçoivent le catalogue en argument :
elles sont ainsi testables sans base ni application Flask.
"""

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import Club, ClubRole, Role, db
from .textnorm import strip_accents

logger = logging.getLogger(__name__)

# Plafond de clubs détaillés dans une même fiche. Une question en cite une ou
# deux ; au-delà, c'est une reconnaissance trop large et le bloc pèserait plus
# que les archives qu'il est censé compléter.
MAX_FICHES = 4

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
class ClubEntry:
    """Un club tel que la reconnaissance le manipule, hors de tout ORM."""

    club_id: int
    nom: str
    slug: str
    asso: str


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
    """Le bureau d'un club sur un mandat donné, prêt à être mis en forme."""

    club: str
    slug: str
    asso: str
    mandat: str
    lignes: tuple[Ligne, ...]


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


def match_clubs(question: str, catalogue: Sequence[ClubEntry]) -> list[ClubEntry]:
    """Clubs cités dans la question, reconnus sur leur nom comme sur leur slug.

    Les termes les plus longs sont essayés d'abord et consomment le texte
    trouvé : « Telecom Nancy Services » l'emporte donc sur « TNS », et
    « baroudeurs » sur « bar ».
    """
    haystack = normalize(question)
    needles = sorted(
        (
            (normalize(raw), club)
            for club in catalogue
            for raw in (club.nom, club.slug)
            if raw and raw.strip()
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    found: dict[int, ClubEntry] = {}
    for needle, club in needles:
        haystack, hit = _blank(haystack, _word(needle))
        if hit:
            found.setdefault(club.club_id, club)
    return sorted(found.values(), key=lambda club: club.club_id)


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
    """Choisit le mandat à présenter parmi ceux que le club a connus.

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

    Le bloc se termine par une ligne blanche : il est concaténé tel quel devant
    le contexte issu de Qdrant.
    """
    blocs = [
        "\n".join(
            [
                _titre(fiche),
                *(
                    f"- {ligne.role} : {', '.join(ligne.personnes)}"
                    for ligne in fiche.lignes
                ),
            ]
        )
        for fiche in fiches
        if fiche.lignes
    ]
    if not blocs:
        return ""
    return f"{_ENTETE}\n" + "\n".join(blocs) + "\n\n"


def _lettres(text: str) -> str:
    """Ne garde que lettres et chiffres : « Anim'Est » et « animest » se rejoignent."""
    return "".join(c for c in normalize(text) if c.isalnum())


def _titre(fiche: Fiche) -> str:
    """Ligne d'en-tête d'une fiche : le club, sa tutelle et le mandat couvert.

    Le slug est rappelé entre parenthèses quand il apporte quelque chose — c'est
    sous cette forme courte (« TNS ») que les archives citent souvent le club.
    La ponctuation est ignorée dans la comparaison : « Anim'Est (ANIMEST) » ne
    répéterait que le même mot.
    """
    nom = fiche.club
    if fiche.slug and _lettres(fiche.slug) != _lettres(nom):
        nom = f"{nom} ({fiche.slug.upper()})"
    return f"{nom} — rattaché à {fiche.asso} — mandat {fiche.mandat}"


# --- Accès à la base -----------------------------------------------------------


def load_catalogue() -> tuple[list[ClubEntry], list[RoleEntry]]:
    """Charge clubs et rôles depuis la base, sous une forme détachée de l'ORM.

    Les deux tables sont minuscules (une quarantaine de lignes en tout) : deux
    SELECT par question restent négligeables devant l'appel à Groq.
    """
    clubs = [
        ClubEntry(
            club_id=club.club_id,
            nom=club.club_name,
            slug=club.slug or "",
            asso=club.asso.asso_name if club.asso else "TELECOM Nancy",
        )
        for club in db.session.scalars(db.select(Club)).all()
    ]
    roles = [
        RoleEntry(role_id=role.role_id, nom=role.role_name)
        for role in db.session.scalars(db.select(Role)).all()
    ]
    return clubs, roles


# Une ligne de bureau brute, telle que la requête la ramène : le mandat vient en
# premier pour que le tri naturel du tuple ordonne par mandat puis par poste.
BureauRow = tuple[str, int, str, str]


def _load_bureaux(club_ids: Sequence[int]) -> dict[int, list[BureauRow]]:
    """Lignes de bureau des clubs demandés, groupées par club."""
    rows = db.session.execute(
        db.select(
            ClubRole.club_id,
            ClubRole.mandat,
            Role.role_id,
            Role.role_name,
            ClubRole.personne,
        )
        .join(Role, Role.role_id == ClubRole.role_id)
        .where(ClubRole.club_id.in_(club_ids))
    ).all()

    par_club: dict[int, list[BureauRow]] = {}
    for club_id, mandat, role_id, role_name, personne in rows:
        par_club.setdefault(club_id, []).append((mandat, role_id, role_name, personne))
    return par_club


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
    clubs: Sequence[ClubEntry],
    roles: Sequence[RoleEntry],
    annee: str | None,
    bureaux: dict[int, list[BureauRow]],
) -> list[Fiche]:
    """Assemble les fiches des clubs reconnus, filtrées par mandat puis par poste.

    Reçoit les lignes de bureau plutôt que d'aller les chercher : la sélection
    reste ainsi vérifiable sans base. Un poste cité restreint la fiche à ce
    poste ; aucun poste cité montre le bureau entier.
    """
    voulus = {role.role_id for role in roles}

    fiches: list[Fiche] = []
    for club in clubs:
        brutes = bureaux.get(club.club_id, [])
        mandat = select_mandat({ligne[0] for ligne in brutes}, annee)
        if mandat is None:
            continue
        fiches.append(
            Fiche(
                club=club.nom,
                slug=club.slug,
                asso=club.asso,
                mandat=mandat,
                lignes=_grouper(brutes, mandat, voulus),
            )
        )
    return fiches


def lookup_context(question: str) -> str:
    """Bloc de fiches à placer en tête du contexte, ou une chaîne vide.

    Chaîne vide dans tous les cas incertains — aucun club reconnu, bureau non
    renseigné : le chat retombe alors exactement sur son comportement RAG.
    """
    clubs, roles = load_catalogue()
    if not clubs:
        return ""

    cites = match_clubs(question, clubs)
    if not cites:
        return ""
    if len(cites) > MAX_FICHES:
        logger.debug(
            "Fiches clubs : %d clubs reconnus, plafonné à %d.", len(cites), MAX_FICHES
        )
        cites = cites[:MAX_FICHES]

    fiches = assemble_fiches(
        cites,
        match_roles(question, roles),
        match_annee(question),
        _load_bureaux([club.club_id for club in cites]),
    )
    bloc = format_fiches(fiches)
    logger.debug(
        "Fiches clubs : %s → %d ligne(s) injectée(s).",
        ", ".join(club.nom for club in cites),
        sum(len(fiche.lignes) for fiche in fiches),
    )
    return bloc
