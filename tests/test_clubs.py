"""Tests des fiches officielles : reconnaissance, sélection du mandat, rendu.

Aucun accès base ni réseau : le catalogue est monté à la main et seules les
fonctions pures du module sont couvertes.
"""

import pytest

from app.back.clubs import (
    NATURE_ASSO,
    NATURE_CLUB,
    Entite,
    Fiche,
    Ligne,
    RoleEntry,
    assemble_fiches,
    format_annuaire,
    format_fiches,
    match_annee,
    match_entites,
    match_roles,
    normalize,
    select_mandat,
    veut_annuaire,
)

# Catalogue de test : TNS pour le cas nominal (nom développé + sigle),
# Baroudeurs et Bar pour le piège du terme court inclus dans le terme long,
# Anim'Est pour l'apostrophe.
_TNS = Entite(
    entite_id=1,
    nom="Telecom Nancy Services",
    slug="tns",
    nature=NATURE_CLUB,
    tutelle="CETEN",
    description="TNS est la junior-entreprise de l'école.",
)
_BAR = Entite(
    entite_id=2, nom="Chok'Bar", slug="bar", nature=NATURE_CLUB, tutelle="CETEN"
)
_BAROUDEURS = Entite(
    entite_id=3,
    nom="Les Baroudeurs",
    slug="baroudeurs",
    nature=NATURE_CLUB,
    tutelle="BDS",
)
_ANIMEST = Entite(
    entite_id=4, nom="Anim'Est", slug="animest", nature=NATURE_CLUB, tutelle="CETEN"
)

_CATALOGUE = [_TNS, _BAR, _BAROUDEURS, _ANIMEST]

_PRESIDENT = RoleEntry(role_id=0, nom="Président")
_VICE = RoleEntry(role_id=1, nom="Vice-président")
_TRESORIER = RoleEntry(role_id=2, nom="Trésorier")
_SECRETAIRE = RoleEntry(role_id=3, nom="Secrétaire")
_RESPO_COM = RoleEntry(role_id=4, nom="Responsable communication")

_ROLES = [_PRESIDENT, _VICE, _TRESORIER, _SECRETAIRE, _RESPO_COM]


# --- Reconnaissance des clubs --------------------------------------------------


@pytest.mark.parametrize(
    ("question", "attendu"),
    [
        ("qui est trésorier de TNS ?", [_TNS]),
        ("le trésorier de Telecom Nancy Services", [_TNS]),
        ("c'est quoi tns au juste", [_TNS]),
        ("qui préside Anim'Est ?", [_ANIMEST]),
        # Apostrophe courbe : celle que produisent la plupart des claviers.
        ("qui préside Anim’Est ?", [_ANIMEST]),  # noqa: RUF001
        ("le bureau de animest", [_ANIMEST]),
        ("président des baroudeurs et de TNS", [_TNS, _BAROUDEURS]),
    ],
)
def test_match_entites_reconnait_noms_et_sigles(
    question: str, attendu: list[Entite]
) -> None:
    """Un club se reconnaît sur son nom développé comme sur son sigle."""
    assert match_entites(question, _CATALOGUE) == attendu


def test_match_entites_ne_confond_pas_un_terme_court_inclus() -> None:
    """« baroudeurs » ne doit pas déclencher le club « bar » qu'il contient."""
    assert match_entites("qui gère les baroudeurs ?", _CATALOGUE) == [_BAROUDEURS]


def test_match_entites_exige_des_frontieres_de_mot() -> None:
    """Un sigle noyé dans un mot plus long ne compte pas."""
    assert match_entites("je bosse sur les transports", _CATALOGUE) == []


@pytest.mark.parametrize(
    "question",
    [
        "c'est quoi le WEI ?",
        "quels sont les prochains événements ?",
        "salut",
    ],
)
def test_match_entites_ignore_les_questions_sans_club(question: str) -> None:
    """Sans club cité, aucune fiche n'est injectée et le RAG garde la main."""
    assert match_entites(question, _CATALOGUE) == []


# --- Reconnaissance des rôles --------------------------------------------------


@pytest.mark.parametrize(
    ("question", "attendu"),
    [
        ("qui est trésorier de TNS ?", [_TRESORIER]),
        ("QUI EST TRESORIER DE TNS", [_TRESORIER]),
        ("le trez de TNS", [_TRESORIER]),
        ("qui préside TNS ?", [_PRESIDENT]),
        ("le prez de TNS", [_PRESIDENT]),
        ("qui est secrétaire ?", [_SECRETAIRE]),
        ("le respo com de TNS", [_RESPO_COM]),
        ("qui est responsable communication ?", [_RESPO_COM]),
    ],
)
def test_match_roles_reconnait_intitules_et_abreviations(
    question: str, attendu: list[RoleEntry]
) -> None:
    """Un poste se reconnaît sur son intitulé, ses variantes et ses abréviations."""
    assert match_roles(question, _ROLES) == attendu


def test_match_roles_distingue_vice_president_de_president() -> None:
    """« vice-président » consomme le terme et n'entraîne pas « président »."""
    assert match_roles("qui est vice-président de TNS ?", _ROLES) == [_VICE]


def test_match_roles_sans_poste_cite() -> None:
    """Aucun poste cité : la liste est vide et l'appelant montre tout le bureau."""
    assert match_roles("c'est quoi TNS ?", _ROLES) == []


# --- Mandat --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "attendu"),
    [
        ("qui était président du BDE en 2022 ?", "2022"),
        ("le bureau 2016", "2016"),
        ("qui est président de TNS ?", None),
        ("on était 2500 au gala", None),
    ],
)
def test_match_annee(question: str, attendu: str | None) -> None:
    """Une année en 20xx vise un mandat ; à défaut on parle du bureau actuel."""
    assert match_annee(question) == attendu


_MANDATS = {"2023-2024", "2024-2025", "2025-2026"}


@pytest.mark.parametrize(
    ("annee", "attendu"),
    [
        (None, "2025-2026"),
        ("2024", "2024-2025"),
        ("2023", "2023-2024"),
        # Année sans mandat correspondant : on montre le bureau courant plutôt
        # que rien, l'année venait d'ailleurs dans la phrase.
        ("2010", "2025-2026"),
    ],
)
def test_select_mandat(annee: str | None, attendu: str) -> None:
    """Sans année le mandat courant, avec année le plus récent qui la couvre."""
    assert select_mandat(_MANDATS, annee) == attendu


def test_select_mandat_sans_donnees() -> None:
    """Un club sans bureau renseigné ne produit aucun mandat."""
    assert select_mandat([], None) is None


# --- Assemblage ----------------------------------------------------------------

# Lignes de bureau brutes, dans l'ordre où la requête les ramène : mandat,
# identifiant de poste, intitulé de poste, puis la personne.
_BUREAU_TNS = [
    ("2025-2026", 0, "Président", "NOBILE Tobias"),
    ("2025-2026", 2, "Trésorier", "DUPONT Marie"),
    ("2024-2025", 0, "Président", "MARTIN Paul"),
]

# Anim'Est a deux présidents : c'est le cas qui a imposé la clé primaire
# technique sur `club_roles`.
_BUREAU_ANIMEST = [
    ("2025-2026", 0, "Président", "PETIT Luc"),
    ("2025-2026", 0, "Président", "ROUX Sarah"),
    ("2025-2026", 4, "Responsable communication", "BLANC Théo"),
]


def test_assemble_regroupe_les_titulaires_dun_meme_poste() -> None:
    """Deux présidents tiennent sur une seule ligne, pas sur deux."""
    fiches = assemble_fiches([_ANIMEST], [], None, {_ANIMEST.cle: _BUREAU_ANIMEST})
    presidence = fiches[0].lignes[0]
    assert presidence.role == "Président"
    assert presidence.personnes == ("PETIT Luc", "ROUX Sarah")


def test_assemble_filtre_sur_le_poste_cite() -> None:
    """Un poste cité restreint la fiche à ce seul poste."""
    fiches = assemble_fiches([_TNS], [_TRESORIER], None, {_TNS.cle: _BUREAU_TNS})
    assert fiches[0].lignes == (Ligne(role="Trésorier", personnes=("DUPONT Marie",)),)


def test_assemble_montre_tout_le_bureau_sans_poste_cite() -> None:
    """Aucun poste cité : tout le bureau du mandat courant."""
    fiches = assemble_fiches([_TNS], [], None, {_TNS.cle: _BUREAU_TNS})
    assert [ligne.role for ligne in fiches[0].lignes] == ["Président", "Trésorier"]


def test_assemble_retient_le_mandat_demande() -> None:
    """Une année dans la question fait ressortir le bureau de l'époque."""
    fiches = assemble_fiches([_TNS], [_PRESIDENT], "2024", {_TNS.cle: _BUREAU_TNS})
    assert fiches[0].mandat == "2024-2025"
    assert fiches[0].lignes[0].personnes == ("MARTIN Paul",)


def test_assemble_sans_bureau_garde_la_description() -> None:
    """Sans bureau saisi, la fiche se réduit à la présentation du club."""
    fiches = assemble_fiches([_TNS], [], None, {})
    assert len(fiches) == 1
    assert fiches[0].lignes == ()
    assert fiches[0].mandat == ""
    assert fiches[0].description == _TNS.description


def test_assemble_sans_bureau_ni_description_ne_rend_rien() -> None:
    """Un club vide de bout en bout n'injecte rien dans le prompt."""
    nu = Entite(
        entite_id=9,
        nom="Breizh'TN",
        slug="breizhtn",
        nature=NATURE_CLUB,
        tutelle="CETEN",
    )
    assert format_fiches(assemble_fiches([nu], [], None, {})) == ""


def test_assemble_ecarte_la_description_quand_un_poste_est_cite() -> None:
    """« qui est trésorier de TNS » n'a que faire de la présentation du club."""
    fiches = assemble_fiches([_TNS], [_TRESORIER], None, {_TNS.cle: _BUREAU_TNS})
    assert fiches[0].description == ""


def test_assemble_ne_melange_pas_asso_et_club_de_meme_id() -> None:
    """Les deux tables commencent à 0 : seule la nature sépare leurs bureaux.

    Sans la nature dans la clé, le bureau de l'association irait se coller au
    club portant le même numéro.
    """
    asso = Entite(entite_id=1, nom="BDS", slug="bds", nature=NATURE_ASSO)
    bureaux = {
        asso.cle: [("2025-2026", 0, "Président", "ASSO Personne")],
        _TNS.cle: [("2025-2026", 0, "Président", "CLUB Personne")],
    }
    fiches = assemble_fiches([asso, _TNS], [], None, bureaux)
    assert fiches[0].lignes[0].personnes == ("ASSO Personne",)
    assert fiches[1].lignes[0].personnes == ("CLUB Personne",)


def test_match_entites_classe_les_assos_avant_les_clubs() -> None:
    """Une question citant les deux présente d'abord la structure porteuse."""
    bds = Entite(entite_id=0, nom="BDS", slug="bds", nature=NATURE_ASSO)
    trouves = match_entites("le BDS et les baroudeurs", [_BAROUDEURS, bds])
    assert [e.nature for e in trouves] == [NATURE_ASSO, NATURE_CLUB]


# --- Repli sur l'annuaire ------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "qui est le président de Machin ?",
        "le trésorier de Truc",
        "c'est quoi le club Bidule ?",
        "quelles sont les assos de l'école ?",
        "le bureau de je-sais-plus-quoi",
    ],
)
def test_veut_annuaire_sur_une_question_associative(question: str) -> None:
    """Un poste cité ou le mot club/asso/bureau déclenche le repli."""
    assert veut_annuaire(question, match_roles(question, _ROLES))


@pytest.mark.parametrize(
    "question",
    [
        "salut",
        "c'est quoi le WEI ?",
        "quelles sont les salles libres ?",
        "feur",
    ],
)
def test_veut_pas_annuaire_hors_sujet(question: str) -> None:
    """Une question étrangère à la vie associative ne paie pas les ~1350 tokens."""
    assert not veut_annuaire(question, match_roles(question, _ROLES))


def test_format_annuaire_liste_tout_avec_la_nature() -> None:
    """L'annuaire donne une ligne par entité, son type et son bureau."""
    asso = Entite(entite_id=0, nom="BDS", slug="bds", nature=NATURE_ASSO)
    bureaux = {_TNS.cle: _BUREAU_TNS}
    rendu = format_annuaire([asso, _TNS], bureaux)
    assert "ANNUAIRE DE LA VIE ASSOCIATIVE" in rendu
    assert "- BDS (asso)" in rendu
    # Bureau courant seulement : MARTIN Paul est le président 2024-2025.
    assert (
        "- Telecom Nancy Services (club) — Président : NOBILE Tobias ; "
        "Trésorier : DUPONT Marie" in rendu
    )
    assert "MARTIN Paul" not in rendu


def test_format_annuaire_vide() -> None:
    """Sans entité, pas d'annuaire — on ne poste pas un en-tête tout seul."""
    assert format_annuaire([], {}) == ""


# --- Rendu ---------------------------------------------------------------------


def test_format_fiches_rend_un_bloc_lisible() -> None:
    """La fiche annonce sa source, le club, sa tutelle et le mandat."""
    fiche = Fiche(
        nom="Telecom Nancy Services",
        slug="tns",
        nature=NATURE_CLUB,
        tutelle="CETEN",
        mandat="2025-2026",
        lignes=(Ligne(role="Trésorier", personnes=("DUPONT Marie",)),),
    )
    rendu = format_fiches([fiche])
    assert "FICHE OFFICIELLE" in rendu
    assert (
        "Telecom Nancy Services (TNS) — club rattaché à CETEN — mandat 2025-2026"
        in rendu
    )
    assert "- Trésorier : DUPONT Marie" in rendu
    # Terminé par une ligne blanche : le bloc est collé devant les archives.
    assert rendu.endswith("\n\n")


def test_format_fiches_rend_une_description_seule() -> None:
    """Sans bureau, la fiche porte la présentation et tait le mandat."""
    fiche = Fiche(
        nom="Neura'TN",
        slug="neuratn",
        nature=NATURE_CLUB,
        tutelle="CETEN",
        mandat="",
        lignes=(),
        description="Club d'intelligence artificielle.",
    )
    rendu = format_fiches([fiche])
    assert "Neura'TN — club rattaché à CETEN\n" in rendu
    assert "Club d'intelligence artificielle." in rendu
    assert "mandat" not in rendu


@pytest.mark.parametrize(
    ("nom", "slug", "nature", "tutelle", "attendu"),
    [
        # Le slug n'apprend rien : ni l'apostrophe ni le mot « Les » ne comptent.
        (
            "Anim'Est",
            "animest",
            NATURE_CLUB,
            "CETEN",
            "Anim'Est — club rattaché à CETEN",
        ),
        (
            "Les Baroudeurs",
            "baroudeurs",
            NATURE_CLUB,
            "CETEN",
            "Les Baroudeurs — club rattaché à CETEN",
        ),
        # Le sigle, lui, est la forme sous laquelle les archives citent l'entité.
        (
            "Telecom Nancy Services",
            "tns",
            NATURE_ASSO,
            "",
            "Telecom Nancy Services (TNS) — association de TELECOM Nancy",
        ),
        # Une association n'est jamais rattachée à quoi que ce soit, et le mot
        # « association » doit figurer : c'est lui qui empêche le modèle de
        # présenter le BDS comme un club parmi les quarante.
        ("BDS", "bds", NATURE_ASSO, "", "BDS — association de TELECOM Nancy"),
        ("CETEN", "bde", NATURE_ASSO, "", "CETEN (BDE) — association de TELECOM Nancy"),
    ],
)
def test_titre_de_fiche(
    nom: str, slug: str, nature: str, tutelle: str, attendu: str
) -> None:
    """L'en-tête annonce la nature de l'entité et ne se répète pas."""
    fiche = Fiche(
        nom=nom,
        slug=slug,
        nature=nature,
        tutelle=tutelle,
        mandat="",
        lignes=(),
        description="Une présentation.",
    )
    assert f"{attendu}\n" in format_fiches([fiche])


def test_format_fiches_separe_les_titulaires_par_une_virgule() -> None:
    """Plusieurs titulaires d'un poste sont listés sur la même ligne."""
    fiche = Fiche(
        nom="Anim'Est",
        slug="animest",
        nature=NATURE_CLUB,
        tutelle="CETEN",
        mandat="2025-2026",
        lignes=(Ligne(role="Président", personnes=("PETIT Luc", "ROUX Sarah")),),
    )
    assert "- Président : PETIT Luc, ROUX Sarah" in format_fiches([fiche])


@pytest.mark.parametrize(
    "fiches",
    [
        [],
        [
            Fiche(
                nom="TNS",
                slug="tns",
                nature=NATURE_CLUB,
                tutelle="CETEN",
                mandat="2025-2026",
                lignes=(),
            )
        ],
    ],
)
def test_format_fiches_vide_ne_produit_rien(fiches: list[Fiche]) -> None:
    """Sans ligne à montrer, rien n'est injecté : le prompt reste celui d'avant."""
    assert format_fiches(fiches) == ""


def test_normalize_replie_accents_et_apostrophes() -> None:
    """La comparaison se fait sur une forme sans accent ni apostrophe courbe."""
    assert normalize("  Créa’TN   ") == normalize("crea'tn")  # noqa: RUF001
