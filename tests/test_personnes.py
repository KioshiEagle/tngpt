"""Tests des fiches personnes : découpage des noms, reconnaissance, rendu.

Sans base ni réseau : l'annuaire est bâti à la main, comme dans `test_clubs`.
Les noms employés ici sont fictifs.
"""

import pytest

from app.back.personnes import (
    MAX_MANDATS,
    MAX_PERSONNES,
    Personne,
    Poste,
    decouper,
    format_personnes,
    match_personnes,
)


def _personne(nom: str, *postes: Poste) -> Personne:
    """Une entrée d'annuaire bâtie comme `load_annuaire` la produirait."""
    patronyme, prenom, exterieur = decouper(nom)
    return Personne(
        nom=nom.replace(" (exté)", ""),
        patronyme=patronyme,
        prenom=prenom,
        postes=postes,
        exterieur=exterieur,
    )


PREZ = Poste(mandat="2026-2027", role="Président", entite="Tek'TN")
TREZ = Poste(mandat="2026-2027", role="Trésorier", entite="Oenologie")
ANCIEN = Poste(mandat="2024-2025", role="Secrétaire", entite="Algo")

DUPONT = _personne("DUPONT Jean", PREZ, TREZ)
MARTIN = _personne("MARTIN-LUCAS Alice", ANCIEN)
ANNUAIRE = [DUPONT, MARTIN]


# --- Découpage « NOM Prénom » --------------------------------------------------


def test_le_patronyme_est_la_tete_en_capitales() -> None:
    """Format de la base : les capitales ouvrent, le prénom suit."""
    assert decouper("DUPONT Jean") == ("DUPONT", "Jean", False)


def test_un_patronyme_compose_reste_entier() -> None:
    """Plusieurs mots en capitales font un seul nom de famille."""
    assert decouper("MARTIN-LUCAS Alice") == ("MARTIN-LUCAS", "Alice", False)
    assert decouper("DE LA TOUR Alice") == ("DE LA TOUR", "Alice", False)


def test_la_marque_exte_est_relevee_et_retiree() -> None:
    """Elle qualifie la personne, elle ne fait pas partie de son nom."""
    assert decouper("DUPONT Jean (exté)") == ("DUPONT", "Jean", True)


def test_une_saisie_sans_capitales_garde_un_prenom() -> None:
    """Repli documenté : le dernier mot fait le prénom."""
    assert decouper("Dupont Jean") == ("Dupont", "Jean", False)


# --- Reconnaissance dans une question ------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "c'est qui dupont jean ?",
        "jean dupont il fait quoi ?",
        "parle moi de Jean DUPONT",
    ],
)
def test_un_nom_complet_est_reconnu_dans_les_deux_ordres(question: str) -> None:
    """On écrit aussi bien « Prénom NOM » que l'inverse."""
    assert match_personnes(question, ANNUAIRE) == [DUPONT]


def test_un_nom_complet_se_passe_de_tournure_interrogative() -> None:
    """Il identifie à lui seul : rien d'autre n'a ce nom-là."""
    assert match_personnes("dupont jean est prez de quoi", ANNUAIRE) == [DUPONT]


def test_un_patronyme_seul_demande_une_question_sur_quelqu_un() -> None:
    """Plusieurs patronymes sont aussi des mots courants."""
    assert match_personnes("qui est dupont ?", ANNUAIRE) == [DUPONT]
    assert match_personnes("dupont", ANNUAIRE) == []


def test_un_prenom_seul_ne_designe_personne() -> None:
    """Un prénom en vise plusieurs : le prompt interdit de choisir au hasard."""
    assert match_personnes("qui est jean ?", ANNUAIRE) == []


def test_un_patronyme_compose_se_reconnait_sans_trait_d_union() -> None:
    """Les séparateurs du nom officiel sont facultatifs à la lecture."""
    assert match_personnes("qui est martin lucas ?", ANNUAIRE) == [MARTIN]


def test_les_homonymes_sortent_ensemble() -> None:
    """Le premier trouvé ne doit pas consommer le nom au détriment de l'autre."""
    autre = _personne("DUPONT Alice", ANCIEN)
    trouves = match_personnes("qui est dupont ?", [DUPONT, autre])
    assert set(trouves) == {DUPONT, autre}


def test_le_nombre_de_personnes_est_plafonne() -> None:
    """Au-delà, la reconnaissance a dérapé et le bloc pèse pour rien."""
    annuaire = [_personne(f"NOM{i} Alice", ANCIEN) for i in range(MAX_PERSONNES + 2)]
    question = "qui est " + " ".join(p.nom for p in annuaire)
    assert len(match_personnes(question, annuaire)) == MAX_PERSONNES


def test_une_question_sans_nom_ne_reconnait_rien() -> None:
    """Sans personne citée, aucun bloc n'est injecté."""
    assert match_personnes("c'est quoi le BDE ?", ANNUAIRE) == []


# --- Rendu ---------------------------------------------------------------------


def test_la_fiche_porte_la_qualite_et_les_postes() -> None:
    """Ce que la base sait : qui, à quel titre, sur quel mandat."""
    bloc = format_personnes([DUPONT])
    assert "FICHE PERSONNE" in bloc
    assert "- DUPONT Jean (élève de TELECOM Nancy)" in bloc
    assert "mandat 2026-2027 : Président de Tek'TN ; Trésorier de Oenologie" in bloc
    assert bloc.endswith("\n\n")


def test_un_membre_exterieur_est_annonce_comme_tel() -> None:
    """La base marque ceux qui siègent sans être élèves : ça se dit."""
    bloc = format_personnes([_personne("DUPONT Jean (exté)", PREZ)])
    assert "- DUPONT Jean (membre extérieur à l'école)" in bloc


def test_les_mandats_vont_du_plus_recent_au_plus_ancien() -> None:
    """L'ordre lexicographique d'une saison est chronologique."""
    bloc = format_personnes([_personne("DUPONT Jean", ANCIEN, PREZ)])
    assert bloc.index("2026-2027") < bloc.index("2024-2025")


def test_les_mandats_montres_sont_plafonnes() -> None:
    """Deux situent quelqu'un ; la liste entière remonte à 2016."""
    postes = [
        Poste(mandat=f"20{an}-20{an + 1}", role="Président", entite="Algo")
        for an in (20, 21, 22, 23)
    ]
    bloc = format_personnes([_personne("DUPONT Jean", *postes)])
    assert bloc.count("mandat 20") == MAX_MANDATS


def test_sans_poste_il_n_y_a_rien_a_montrer() -> None:
    """Une fiche vide vaut moins que pas de fiche du tout."""
    assert format_personnes([_personne("DUPONT Jean")]) == ""


def test_l_entete_dit_ce_que_la_base_ne_couvre_pas() -> None:
    """Sans cette limite écrite, une absence se lit comme un fait."""
    bloc = format_personnes([DUPONT])
    assert "eux seuls" in bloc
    assert "personnel de l'école" in bloc
