"""Fiches des personnes : le SQL dit qui occupe quel poste.

Le RAG retrouve bien un nom, mais noyé dans une liste de présents à une
réunion : il montre que quelqu'un était là, jamais ce qu'il y fait. La table
des bureaux, elle, le sait. Sans elle, le modèle comble le vide en attribuant
un rôle au hasard — d'où ce bloc, sur le modèle de `clubs.py`.
"""

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import takewhile

from .clubs import blanchir, motif_mot, normalize
from .models import Asso, AssoRole, Club, ClubRole, Role, db

logger = logging.getLogger(__name__)

# Plafond de personnes par bloc : au-delà, la reconnaissance a dérapé sur des
# mots courants et le bloc pèse plus qu'il ne renseigne.
MAX_PERSONNES = 3

# Mandats montrés par personne, du plus récent au plus ancien. Deux situent
# quelqu'un ; la liste entière remonte à 2016.
MAX_MANDATS = 2

# Marque portée en base par ceux qui siègent sans être élèves de l'école.
_EXTERIEUR = re.compile(r"\s*\(\s*ext[^)]*\)\s*$", re.IGNORECASE)

# Ce qui signale une question sur quelqu'un. Cherché sur la forme normalisée,
# donc sans accents ni majuscules.
_CUE_PERSONNE = re.compile(
    r"\bqui\s+est\b|\bqui\s+sont\b|\bc'?est\s+qui\b|\bqui\s+c'?est\b"
    r"|\bconnais\b|\bconnait\b|\bparle\s+moi\s+de\b"
)

# Force d'une reconnaissance : un nom complet identifie à lui seul, un
# patronyme seul demande que la question cherche bien quelqu'un.
_COMPLET = 2
_PATRONYME = 1


@dataclass(frozen=True)
class Poste:
    """Un mandat : la saison, le poste occupé, l'entité où il l'est."""

    mandat: str
    role: str
    entite: str


@dataclass(frozen=True)
class Personne:
    """Quelqu'un qui occupe ou a occupé un poste de bureau.

    `nom` est la graphie de la base, « NOM Prénom » : les archives emploient la
    même, et c'est elle qu'on écrit dans la fiche.
    """

    nom: str
    patronyme: str
    prenom: str
    postes: tuple[Poste, ...] = ()
    exterieur: bool = False


# --- Reconnaissance ------------------------------------------------------------


def decouper(brut: str) -> tuple[str, str, bool]:
    """Sépare « NOM Prénom » en patronyme et prénom, et relève la marque exté.

    Les mots en capitales ouvrent la chaîne et forment le patronyme, qui peut
    en compter plusieurs. Faute de capitales, le dernier mot fait le prénom.
    """
    exterieur = bool(_EXTERIEUR.search(brut))
    mots = _EXTERIEUR.sub("", brut).split()
    if not mots:
        return "", "", exterieur
    capitales = list(takewhile(str.isupper, mots))
    if not capitales or len(capitales) == len(mots):
        capitales = mots[:-1] or mots
    return " ".join(capitales), " ".join(mots[len(capitales) :]), exterieur


def _formes(personne: Personne) -> list[tuple[str, int]]:
    """Graphies qui désignent cette personne, avec ce que chacune vaut.

    Les deux ordres du nom complet : on écrit aussi bien « Prénom NOM » que
    l'inverse. Le prénom seul n'y est pas — il en vise plusieurs.
    """
    patronyme, prenom = normalize(personne.patronyme), normalize(personne.prenom)
    if not patronyme:
        return []
    if not prenom:
        return [(patronyme, _PATRONYME)]
    return [
        (f"{patronyme} {prenom}", _COMPLET),
        (f"{prenom} {patronyme}", _COMPLET),
        (patronyme, _PATRONYME),
    ]


def _par_forme(
    annuaire: Sequence[Personne],
) -> dict[str, tuple[int, list[Personne]]]:
    """Indexe les personnes par graphie : un patronyme peut être partagé.

    Grouper avant de chercher évite qu'un homonyme consomme le texte au
    détriment de l'autre : les deux doivent sortir ensemble.
    """
    formes: dict[str, tuple[int, list[Personne]]] = {}
    for personne in annuaire:
        for forme, force in _formes(personne):
            _, partages = formes.setdefault(forme, (force, []))
            partages.append(personne)
    return formes


def match_personnes(question: str, annuaire: Sequence[Personne]) -> list[Personne]:
    """Personnes citées dans la question, par nom complet ou par patronyme.

    Les graphies les plus longues passent d'abord et consomment le texte
    trouvé, pour qu'un nom complet ne compte pas aussi comme un patronyme. Le
    patronyme seul ne suffit qu'à une question qui cherche manifestement
    quelqu'un : plusieurs patronymes sont aussi des mots courants.
    """
    haystack = normalize(question)
    cherche_quelqu_un = bool(_CUE_PERSONNE.search(haystack))
    formes = _par_forme(annuaire)

    trouves: dict[str, Personne] = {}
    for forme in sorted(formes, key=len, reverse=True):
        force, partages = formes[forme]
        haystack, hit = blanchir(haystack, motif_mot(forme))
        if not hit or (force == _PATRONYME and not cherche_quelqu_un):
            continue
        for personne in partages:
            trouves.setdefault(personne.nom, personne)
    return list(trouves.values())[:MAX_PERSONNES]


# --- Mise en forme -------------------------------------------------------------

# Dire ce que la base couvre autant que ce qu'elle contient : sans cette
# limite écrite, l'absence de quelqu'un se lit comme un fait sur lui.
_ENTETE = (
    "FICHE PERSONNE — base de données de l'école, fait autorité.\n"
    "Cette base recense les élèves qui occupent un poste dans un bureau de club "
    "ou d'association, et eux seuls : elle ne dit rien du personnel de l'école, "
    "ni des élèves sans mandat."
)


def _mandats(postes: Sequence[Poste]) -> list[str]:
    """Les mandats à montrer, du plus récent au plus ancien.

    L'ordre lexicographique d'une saison « 2025-2026 » est chronologique.
    """
    return sorted({poste.mandat for poste in postes}, reverse=True)[:MAX_MANDATS]


def _ligne(personne: Personne) -> str:
    """Une entrée : qui, à quel titre, et ses derniers mandats."""
    qualite = (
        "membre extérieur à l'école" if personne.exterieur else "élève de TELECOM Nancy"
    )
    ligne = f"- {personne.nom} ({qualite})"
    for mandat in _mandats(personne.postes):
        postes = " ; ".join(
            f"{poste.role} de {poste.entite}"
            for poste in personne.postes
            if poste.mandat == mandat
        )
        ligne += f" — mandat {mandat} : {postes}"
    return ligne


def format_personnes(personnes: Sequence[Personne]) -> str:
    """Rend les fiches en texte, ou une chaîne vide s'il n'y a rien à montrer.

    Le bloc finit par une ligne blanche, étant concaténé tel quel devant le
    reste du contexte.
    """
    lignes = [_ligne(personne) for personne in personnes if personne.postes]
    if not lignes:
        return ""
    return f"{_ENTETE}\n" + "\n".join(lignes) + "\n\n"


# --- Accès à la base -----------------------------------------------------------


def load_annuaire() -> list[Personne]:
    """Charge les personnes des deux tables de bureaux, leurs postes compris.

    Deux requêtes pour une centaine de lignes : négligeable devant l'appel à
    Groq, et le lot tient en mémoire.
    """
    postes: dict[str, list[Poste]] = {}
    tables = (
        (ClubRole, Club, Club.club_name, ClubRole.club_id == Club.club_id),
        (AssoRole, Asso, Asso.asso_name, AssoRole.asso_id == Asso.asso_id),
    )
    for modele, entite, colonne_nom, jointure in tables:
        rows = db.session.execute(
            db.select(modele.personne, modele.mandat, Role.role_name, colonne_nom)
            .join(Role, Role.role_id == modele.role_id)
            .join(entite, jointure)
        ).all()
        for brut, mandat, role_name, nom_entite in rows:
            postes.setdefault(brut, []).append(
                Poste(mandat=mandat, role=role_name, entite=nom_entite)
            )

    annuaire = []
    for brut, liste in postes.items():
        patronyme, prenom, exterieur = decouper(brut)
        annuaire.append(
            Personne(
                nom=_EXTERIEUR.sub("", brut).strip(),
                patronyme=patronyme,
                prenom=prenom,
                postes=tuple(sorted(liste, key=lambda p: (p.mandat, p.role))),
                exterieur=exterieur,
            )
        )
    return annuaire


def lookup_personnes(question: str) -> str:
    """Bloc de fiches personnes à placer en tête du contexte, ou chaîne vide.

    Vide dans tous les cas incertains, comme `clubs.lookup_context` : le chat
    retombe alors exactement sur son comportement RAG.
    """
    annuaire = load_annuaire()
    if not annuaire:
        return ""
    trouves = match_personnes(question, annuaire)
    if not trouves:
        return ""
    # Le compte, pas les noms : ce journal n'a pas à tenir un registre de qui
    # est demandé.
    logger.debug("Fiches personnes : %d reconnue(s).", len(trouves))
    return format_personnes(trouves)
