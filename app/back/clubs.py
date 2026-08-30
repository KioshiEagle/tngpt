"""Fiches officielles de la vie associative : le SQL répond là où le RAG échoue.

Reconnaissance déterministe et non déléguée au modèle, qui tourne sans budget
de raisonnement ; sans rien de reconnu, rien n'est injecté.
"""

import logging
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .models import Asso, AssoRole, Club, ClubRole, Role, db
from .textnorm import strip_accents

logger = logging.getLogger(__name__)

# Plafond d'entités par fiche : au-delà de deux, la reconnaissance est trop
# large et le bloc pèse plus que les archives qu'il complète.
MAX_FICHES = 4

# Les deux natures d'entité. Le libellé part tel quel dans le prompt.
NATURE_CLUB = "club"
NATURE_ASSO = "association"

# Apostrophes typographiques ramenées à l'apostrophe droite. En échappements :
# ces caractères sont indiscernables à l'œil dans le source.
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
    # Appellations d'usage : « Abso » pour Abso'Ludique. Sans elles, on rate
    # les noms que les gens emploient réellement.
    aliases: tuple[str, ...] = ()

    @property
    def cle(self) -> tuple[str, int]:
        """Identifiant unique toutes natures confondues.

        Les id de `clubs` et d'`assos` se recoupent : sans la nature dans la
        clé, le bureau d'une asso irait se coller à un club de même numéro.
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

    Regroupés sur une ligne : le modèle doit lire « deux titulaires » et non
    « deux archives se contredisent ».
    """

    role: str
    personnes: tuple[str, ...]


@dataclass(frozen=True)
class Fiche:
    """Le bureau d'une entité sur un mandat donné, prêt à être mis en forme.

    `description` n'est renseignée que si la question ne vise aucun poste
    précis ; ailleurs elle ne ferait que des tokens perdus.
    """

    nom: str
    slug: str
    nature: str
    tutelle: str
    mandat: str
    lignes: tuple[Ligne, ...]
    description: str = ""


# Une ligne de bureau brute, telle que la requête la ramène : le mandat vient en
# premier pour que le tri naturel du tuple ordonne par mandat puis par poste.
BureauRow = tuple[str, int, str, str]
Bureaux = dict[tuple[str, int], list[BureauRow]]


# --- Reconnaissance ------------------------------------------------------------


def blanchir(haystack: str, pattern: re.Pattern[str]) -> tuple[str, bool]:
    """Cherche un motif et neutralise l'occurrence trouvée.

    Blanchir plutôt que supprimer garde les positions ; sans quoi « bar »
    matcherait à l'intérieur de « baroudeurs ». Partagé avec `personnes.py`,
    qui consomme les noms d'une question de la même façon.
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


# Ce qui peut séparer deux morceaux d'un nom, y compris rien : « Créa'TN »,
# « Créa TN » et « CréaTN » doivent se rencontrer.
_LIAISON = r"[\s'.\-]*"


def motif_mot(needle: str) -> re.Pattern[str]:
    r"""Compile un motif exigeant des frontières de mot autour du terme.

    `\b` ne convient pas en bordure (apostrophes, points) ; à l'intérieur, seuls
    les séparateurs déjà présents au nom officiel deviennent facultatifs — ce
    qui vaut aussi pour un patronyme composé, écrit avec ou sans trait d'union.
    """
    morceaux = [re.escape(m) for m in re.split(r"[^0-9a-z]+", needle) if m]
    if not morceaux:
        return re.compile(r"(?!x)x")  # ne matche jamais
    return re.compile(rf"(?<!\w){_LIAISON.join(morceaux)}(?!\w)")


def match_entites(question: str, catalogue: Sequence[Entite]) -> list[Entite]:
    """Clubs et associations cités : nom officiel, slug ou appellation d'usage.

    Les termes les plus longs passent d'abord et consomment le texte trouvé :
    « baroudeurs » l'emporte sur « bar ».
    """
    haystack = normalize(question)
    needles = sorted(
        (
            (normalize(raw), entite)
            for entite in catalogue
            for raw in (entite.nom, entite.slug, *entite.aliases)
            if raw and raw.strip()
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    found: dict[tuple[str, int], Entite] = {}
    for needle, entite in needles:
        haystack, hit = blanchir(haystack, motif_mot(needle))
        if hit:
            found.setdefault(entite.cle, entite)
    # Les associations d'abord : quand une question cite les deux, c'est la
    # structure porteuse qui éclaire le reste.
    return sorted(found.values(), key=lambda e: (e.nature != NATURE_ASSO, e.entite_id))


# Motifs de rôle, du plus spécifique au plus général : « vice-président »
# contient « présid » et doit donc être consommé avant « président ».
_ROLE_MOTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (nom, re.compile(motif))
    for nom, motif in (
        # Les postes en « vice- » d'abord : leurs abréviations contiennent celles
        # du poste de base (« vice-trez » contient « trez »).
        ("vice-president", r"vice[\s-]*(?:presid|prez|pres)|\bvp\b"),
        ("vice-tresorier", r"vice[\s-]*(?:tresor|trez|treso)|\bvt\b"),
        ("vice-secretaire", r"vice[\s-]*(?:secretai|secretar|secre)"),
        # Abréviations trop courtes pour la troncature générique à quatre
        # lettres, mais d'usage constant dans les bureaux.
        (
            "responsable communication",
            r"respos?[\s-]*comm?\b|responsables?[\s-]*comm?unication",
        ),
        ("responsable logistique", r"respos?[\s-]*logs?\b"),
        ("responsable evenements", r"respos?[\s-]*events?\b"),
        ("president", r"presid|\bprez\b|\bpres\b"),
        ("tresorier", r"tresor|\btrez\b|\btreso\b"),
        ("secretaire", r"secretai|secretar|\bsecre\b"),
    )
)


# Troncature minimale d'un intitulé (« respo choré ») : quatre lettres, trois
# confondraient « informatique » et « infrastructure ».
_TRONCATURE = 4


def _motif_role(nom_normalise: str) -> re.Pattern[str]:
    """Motif reconnaissant un intitulé de poste et ses formes courantes.

    Abréviation, pluriel et troncature dérivés de l'intitulé, pour ne pas
    inscrire à la main les vingt-quatre postes des bureaux.
    """
    if not nom_normalise.startswith("responsable "):
        return re.compile(rf"(?<!\w){re.escape(nom_normalise)}s?(?!\w)")

    suffixe = nom_normalise.removeprefix("responsable ")
    premier, _, suite = suffixe.partition(" ")
    if len(premier) > _TRONCATURE:
        corps = re.escape(premier[:_TRONCATURE]) + r"[\w']*"
    else:
        corps = re.escape(premier)
    if suite:
        corps += rf"(?:\s+{re.escape(suite)})?"
    return re.compile(rf"(?<!\w)(?:responsables?|respos?|resp)\s+{corps}s?(?!\w)")


def match_roles(question: str, roles: Sequence[RoleEntry]) -> list[RoleEntry]:
    """Postes cités dans la question, via leur nom en base et leurs abréviations.

    Liste vide = aucun poste cité : l'appelant montre alors le bureau entier.
    """
    haystack = normalize(question)
    par_nom = {normalize(role.nom): role for role in roles}

    found: dict[int, RoleEntry] = {}

    # Intitulés de la table d'abord, du plus long au plus court : sinon
    # « tresor » matcherait à l'intérieur de « vice-trésorier ».
    for nom in sorted(par_nom, key=len, reverse=True):
        haystack, hit = blanchir(haystack, _motif_role(nom))
        if hit:
            role = par_nom[nom]
            found.setdefault(role.role_id, role)

    # Puis les abréviations qui ne se déduisent pas de l'intitulé (« prez »,
    # « trez », « respo com »).
    for nom, motif in _ROLE_MOTS:
        haystack, hit = blanchir(haystack, motif)
        role = par_nom.get(nom)
        if hit and role is not None:
            found.setdefault(role.role_id, role)

    return sorted(found.values(), key=lambda role: role.role_id)


# --- Rattrapage approximatif ---------------------------------------------------

# Seuil bas à dessein : ce qui sort d'ici est une piste, pas une affirmation,
# et trois candidats valent mieux qu'un nom manqué.
SEUIL_FLOU = 80
MAX_CANDIDATS = 3
# En deçà, un fragment est trop court pour que la ressemblance veuille dire
# quelque chose : « bar » ressemble à « car », « gala » à « cala ».
_MIN_FRAGMENT = 4
# Nombre de mots consécutifs comparés : « telecom nancy services » en fait trois.
_NGRAM_MAX = 3


def match_flou(
    question: str, catalogue: Sequence[Entite], seuil: int = SEUIL_FLOU
) -> list[Entite]:
    """Entités dont le nom ressemble à un fragment de la question.

    Filet sous la reconnaissance exacte, comparant chaque suite de un à trois
    mots ; le résultat est une liste de pistes, jamais une identification.
    """
    formes = _formes_connues(catalogue)
    if not formes:
        return []

    meilleurs: dict[tuple[str, int], tuple[float, Entite]] = {}
    for fragment in _fragments(normalize(question).split()):
        for forme, score, _ in process.extract(
            fragment,
            formes.keys(),
            scorer=fuzz.ratio,
            score_cutoff=seuil,
            limit=MAX_CANDIDATS,
        ):
            entite = formes[forme]
            connu = meilleurs.get(entite.cle)
            if connu is None or score > connu[0]:
                meilleurs[entite.cle] = (score, entite)

    classes = sorted(meilleurs.values(), key=lambda paire: -paire[0])
    return [entite for _, entite in classes[:MAX_CANDIDATS]]


def _formes_connues(catalogue: Sequence[Entite]) -> dict[str, Entite]:
    """Toutes les graphies connues d'une entité, indexées par forme normalisée."""
    formes: dict[str, Entite] = {}
    for entite in catalogue:
        for brut in (entite.nom, entite.slug, *entite.aliases):
            forme = normalize(brut)
            if len(forme) >= _MIN_FRAGMENT:
                formes.setdefault(forme, entite)
    return formes


def _fragments(mots: Sequence[str]) -> Iterator[str]:
    """Suites de un à trois mots consécutifs, assez longues pour être comparées."""
    for taille in range(1, _NGRAM_MAX + 1):
        for depart in range(len(mots) - taille + 1):
            fragment = " ".join(mots[depart : depart + taille])
            if len(fragment) >= _MIN_FRAGMENT:
                yield fragment


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

    Sans année, le plus récent (l'ordre lexicographique est chronologique) ;
    avec, le plus récent qui la couvre, à défaut le courant.
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

    Une description seule suffit ; le bloc finit par une ligne blanche, étant
    concaténé tel quel devant le contexte Qdrant.
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


# Repli sans reconnaissance : l'annuaire entier part au modèle, qui fait le
# rapprochement. Coûteux, d'où le garde-fou ci-dessous et le budget plus bas.
_CUE_ASSO = re.compile(r"\bclubs?\b|\bassos?\b|\bassociations?\b|\bbureaux?\b")

_ENTETE_ANNUAIRE = (
    "ANNUAIRE DE LA VIE ASSOCIATIVE — base de données de l'école, fait autorité.\n"
    "Aucun nom n'a été reconnu tel quel dans la question. Identifie toi-même "
    "l'entité visée dans la liste ci-dessous, même si elle y figure sous un autre "
    "nom, puis réponds avec ses données. Si aucune ne correspond, dis-le."
)

_ENTETE_PROCHES = (
    "NOMS PROCHES — base de données de l'école, fait autorité.\n"
    "Le nom employé dans la question ne correspond exactement à aucune entité. "
    "Voici les plus ressemblantes. Si l'une est manifestement celle qu'on vise, "
    "réponds avec ses données en la nommant correctement ; sinon dis que tu ne "
    "connais pas ce nom. Ne choisis pas au hasard."
)


# Longueur d'une description dans l'annuaire : les quarante complètes pèsent
# 2000 tokens, et la première phrase suffit à dire ce que fait un club.
_ABREGE = 130


def _abrege(texte: str) -> str:
    """Coupe une description à la première phrase, sans casser un mot."""
    if len(texte) <= _ABREGE:
        return texte
    coupe = texte[:_ABREGE]
    fin = coupe.rfind(". ")
    if fin > _ABREGE // 3:
        return coupe[: fin + 1]
    return coupe[: coupe.rfind(" ")].rstrip(",;:") + "…"


# Plafond du bloc annuaire, en caractères. Les 45 entités avec descriptions et
# bureaux pèsent 11 000 caractères, soit un tiers du plafond Groq par minute.
BUDGET_ANNUAIRE = 6000


def _rendu_annuaire(
    entites: Sequence[Entite],
    bureaux: Bureaux,
    entete: str,
    *,
    abrege: bool,
    avec_description: bool,
) -> str:
    """Rend l'annuaire à un niveau de détail donné, toutes les entités incluses."""
    lignes = [
        _ligne_annuaire(
            entite, bureaux, abrege=abrege, avec_description=avec_description
        )
        for entite in entites
    ]
    if not lignes:
        return ""
    return f"{entete}\n" + "\n".join(lignes) + "\n\n"


def format_annuaire(
    entites: Sequence[Entite],
    bureaux: Bureaux,
    entete: str = _ENTETE_ANNUAIRE,
    *,
    abrege: bool = True,
    budget: int = BUDGET_ANNUAIRE,
) -> str:
    """Annuaire complet : une ligne par entité, avec sa description et son bureau.

    Exhaustif à dessein — c'est ce qui permet à la reconnaissance de rater sans
    conséquence. Passé le budget, le bloc est donc dégradé et jamais tronqué :
    les bureaux tombent d'abord (la moitié du poids, et ce bloc sert à
    identifier une entité, pas à lister ses postes), les descriptions ensuite.
    Une entité absente ferait conclure au modèle qu'elle n'existe pas.
    """
    if not entites:
        return ""
    paliers = ((bureaux, True), ({}, True), ({}, False))
    bloc = ""
    for bur, avec_description in paliers:
        bloc = _rendu_annuaire(
            entites, bur, entete, abrege=abrege, avec_description=avec_description
        )
        if len(bloc) <= budget:
            return bloc
        logger.debug(
            "Annuaire à %d caractères pour un budget de %d : palier suivant.",
            len(bloc),
            budget,
        )
    # Dernier palier rendu quoi qu'il pèse : les noms seuls sont le minimum
    # utile, et il n'y a plus rien à retirer sans perdre une entité.
    return bloc


def _ligne_annuaire(
    entite: Entite, bureaux: Bureaux, *, abrege: bool, avec_description: bool = True
) -> str:
    """Une entrée d'annuaire : la nature, la description puis le bureau courant."""
    marque = "asso" if entite.nature == NATURE_ASSO else "club"
    ligne = f"- {entite.nom} ({marque})"

    if entite.description and avec_description:
        desc = _abrege(entite.description) if abrege else entite.description
        ligne += f" — {desc}"

    brutes = bureaux.get(entite.cle, [])
    mandat = select_mandat({b[0] for b in brutes}, None)
    postes = _grouper(brutes, mandat, set()) if mandat else ()
    if postes:
        detail = " ; ".join(
            f"{poste.role} : {', '.join(poste.personnes)}" for poste in postes
        )
        ligne += f" — Bureau : {detail}"
    return ligne


def veut_annuaire(question: str, roles_cites: Sequence[RoleEntry]) -> bool:
    """Indique si une question non reconnue mérite qu'on lui serve l'annuaire.

    Un poste cité ou le mot « club »/« asso »/« bureau » signalent qu'un nom
    nous a échappé, plutôt qu'une question sur autre chose.
    """
    return bool(roles_cites) or bool(_CUE_ASSO.search(normalize(question)))


def _lettres(text: str) -> str:
    """Ne garde que lettres et chiffres : « Anim'Est » et « animest » se rejoignent."""
    return "".join(c for c in normalize(text) if c.isalnum())


def _titre(fiche: Fiche) -> str:
    """Ligne d'en-tête d'une fiche : ce qu'est l'entité, sa tutelle, son mandat.

    La nature est écrite noir sur blanc, et le slug rappelé seulement s'il
    apprend quelque chose : « Telecom Nancy Services (TNS) » oui.
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

    # Pas de mandat sans bureau saisi : un « mandat  » vide ferait croire à une
    # donnée manquante plutôt qu'à une fiche de présentation.
    return f"{titre} — mandat {fiche.mandat}" if fiche.mandat else titre


# --- Accès à la base -----------------------------------------------------------


def _aliases(brut: str | None) -> tuple[str, ...]:
    """Découpe la colonne `aliases`, une liste séparée par des barres verticales."""
    return tuple(a.strip() for a in (brut or "").split("|") if a.strip())


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
            aliases=_aliases(asso.aliases),
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
            aliases=_aliases(club.aliases),
        )
        for club in db.session.scalars(db.select(Club)).all()
    ]
    roles = [
        RoleEntry(role_id=role.role_id, nom=role.role_name)
        for role in db.session.scalars(db.select(Role)).all()
    ]
    return assos + clubs, roles


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

    Filtre sur le mandat retenu et sur les postes cités ; le tri du tuple brut
    donne l'ordre hiérarchique de `roles`, puis l'alphabétique.
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

    Reçoit les lignes de bureau plutôt que d'aller les chercher, pour rester
    vérifiable sans base ; une entité sans bureau garde sa description.
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


def lookup_context(question: str, *, avec_annuaire: bool = True) -> str:
    """Bloc de fiches à placer en tête du contexte, ou une chaîne vide.

    Vide dans tous les cas incertains : le chat retombe alors exactement sur
    son comportement RAG. `avec_annuaire=False` coupe le repli sur l'annuaire
    complet, quand la question s'explique déjà autrement (voir `personnes.py`).
    """
    catalogue, roles = load_catalogue()
    if not catalogue:
        return ""

    cites = match_entites(question, catalogue)
    if not cites:
        # Rien de reconnu : on se tait, ou on laisse le modèle retrouver le nom
        # dans l'annuaire.
        if not avec_annuaire or not veut_annuaire(
            question, match_roles(question, roles)
        ):
            return ""
        # Deuxième chance : quelques noms ressemblants en pistes. Bien moins
        # coûteux que l'annuaire, et suffisant sur une faute de frappe.
        proches = match_flou(question, catalogue)
        if proches:
            logger.debug(
                "Rien d'exact : %s proposé(s) par ressemblance.",
                ", ".join(e.nom for e in proches),
            )
            return format_annuaire(
                proches, _load_bureaux(proches), _ENTETE_PROCHES, abrege=False
            )
        logger.debug("Aucune entité reconnue : repli sur l'annuaire complet.")
        return format_annuaire(catalogue, _load_bureaux(catalogue))
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
