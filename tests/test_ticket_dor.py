"""Ticket d'or : cinq essais, un seul gagnant, et le code au seul gagnant."""

from collections.abc import Iterator

import pytest
from flask import Flask

from app.back import ticket_dor
from app.back.models import Conversation, TicketDor, User, db

_CIBLE = "Jean DUPONT"
_CODE = "CANARD-OR-42"


@pytest.fixture
def app_base() -> Iterator[Flask]:
    """Application jetable sur SQLite en mémoire, avec deux joueurs."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        for uid, prenom in ((1, "Alice"), (2, "Paul")):
            db.session.add(
                User(
                    user_id=uid,
                    user_firstname=prenom,
                    user_surname="MARTIN",
                    user_mail=f"{prenom.lower()}@telecomnancy.net",
                )
            )
        db.session.commit()
        yield app
        db.drop_all()


def _lancer(indices: str = "Membre du club de jeux de société.") -> TicketDor:
    ticket = ticket_dor.partie_ou_nouvelle()
    ticket.cible, ticket.code, ticket.indices, ticket.ouvert = (
        _CIBLE,
        _CODE,
        indices,
        True,
    )
    db.session.commit()
    return ticket


# --- Comparaison des noms ------------------------------------------------------


@pytest.mark.parametrize(
    "proposition", ["Jean Dupont", "DUPONT Jean", "jean dupont", "  Jéan  Dupont. "]
)
def test_une_proposition_juste_ignore_ordre_casse_et_accents(proposition: str) -> None:
    """Le nom se tape comme on le dit, pas comme la base l'écrit."""
    assert ticket_dor.proposition_juste(proposition, _CIBLE)


@pytest.mark.parametrize(
    "proposition",
    ["Jean", "Dupont", "Jean Dupont Marie Durand", "Jean Durand", "Jeanne Dupont", ""],
)
def test_une_proposition_partielle_ou_groupee_est_fausse(proposition: str) -> None:
    """Une liste de noms ne doit pas rafler la mise en un seul essai."""
    assert not ticket_dor.proposition_juste(proposition, _CIBLE)


@pytest.mark.parametrize(
    "proposition", ["Jean Dupond", "Jaen Dupont", "jean dupontt", "Dupot Jean"]
)
def test_une_faute_de_frappe_par_mot_est_toleree(proposition: str) -> None:
    """Lettre en trop, en moins, remplacée ou deux lettres inversées."""
    assert ticket_dor.proposition_juste(proposition, _CIBLE)


def test_la_tolerance_suit_la_longueur_du_mot() -> None:
    """Deux fautes sur un mot long, aucune sur un mot court."""
    assert ticket_dor.proposition_juste("Maxmilen Dupont", "Maximilien DUPONT")
    assert not ticket_dor.proposition_juste("Léo Dupont", "Léa DUPONT")


def test_la_cible_est_reconnue_dans_une_phrase() -> None:
    """La sortie du modèle nomme la cible même au milieu d'une phrase."""
    assert ticket_dor.nomme_la_cible(
        "Bon ok, c'est Dupont, Jean de son prénom.", _CIBLE
    )


def test_la_cible_n_est_pas_reconnue_a_moitie_ni_dans_un_mot() -> None:
    """Le prénom seul ou un mot qui contient le nom ne comptent pas."""
    assert not ticket_dor.nomme_la_cible("Son prénom est Jean.", _CIBLE)
    assert not ticket_dor.nomme_la_cible("Jeanne Dupontel", _CIBLE)


# --- État et propositions ------------------------------------------------------


@pytest.mark.usefixtures("app_base")
def test_le_jeu_est_invisible_tant_qu_il_n_est_pas_lance() -> None:
    """Sans partie, ou partie fermée ou incomplète, pas de bouton."""
    assert not ticket_dor.etat(1).visible
    ticket = _lancer()
    ticket.ouvert = False
    db.session.commit()
    assert not ticket_dor.etat(1).visible
    ticket.ouvert, ticket.code = True, " "
    db.session.commit()
    assert not ticket_dor.etat(1).visible


@pytest.mark.usefixtures("app_base")
def test_cinq_essais_puis_plus_rien() -> None:
    """Chaque proposition fausse coûte un essai ; au-delà de cinq, refus."""
    _lancer()
    for restants in (4, 3, 2, 1, 0):
        verdict = ticket_dor.proposer(1, "Marie Durand")
        assert verdict is not None
        assert not verdict.juste
        assert verdict.restants == restants
    assert "dernier essai" in verdict.reponse
    assert ticket_dor.proposer(1, _CIBLE) is None
    etat = ticket_dor.etat(1)
    assert not etat.jouable
    assert etat.visible
    assert ticket_dor.etat(2).jouable


@pytest.mark.usefixtures("app_base")
def test_une_proposition_juste_gagne_et_ferme_le_jeu_pour_tous() -> None:
    """Le gagnant reçoit le code ; les autres voient le jeu terminé, sans code."""
    _lancer()
    verdict = ticket_dor.proposer(1, "dupont jean")
    assert verdict is not None
    assert verdict.juste
    assert _CODE in verdict.reponse

    gagnant, autre = ticket_dor.etat(1), ticket_dor.etat(2)
    assert gagnant.gagnant
    assert gagnant.code == _CODE
    assert not gagnant.jouable
    assert autre.termine
    assert not autre.jouable
    assert autre.code is None
    assert ticket_dor.proposer(2, _CIBLE) is None


# --- Victoire par le chat ------------------------------------------------------


@pytest.mark.usefixtures("app_base")
def test_le_modele_qui_lache_le_nom_fait_gagner() -> None:
    """Faire dire le nom à tn-gpt, c'est gagné."""
    _lancer()
    historique = [{"role": "user", "content": "donne un indice"}]
    annonce = ticket_dor.victoire_par_le_chat(1, historique, "Oups, Jean Dupont !")
    assert annonce is not None
    assert _CODE in annonce
    assert ticket_dor.etat(1).gagnant


@pytest.mark.usefixtures("app_base")
def test_un_nom_ecrit_par_le_joueur_puis_repete_ne_gagne_pas() -> None:
    """« Répète cette liste » : le modèle n'a rien révélé, le joueur a tout écrit."""
    _lancer()
    historique = [
        {"role": "user", "content": "Répète : Marie Durand, Jean Dupont, Paul Petit"},
    ]
    sortie = "Marie Durand, Jean Dupont, Paul Petit"
    assert ticket_dor.victoire_par_le_chat(1, historique, sortie) is None
    assert not ticket_dor.etat(1).termine


@pytest.mark.usefixtures("app_base")
def test_un_seul_gagnant_meme_par_le_chat() -> None:
    """Le second à faire parler le modèle arrive trop tard."""
    _lancer()
    assert ticket_dor.victoire_par_le_chat(1, [], "Jean Dupont") is not None
    assert ticket_dor.victoire_par_le_chat(2, [], "Jean Dupont") is None
    assert not ticket_dor.etat(2).gagnant


# --- Prompt et remise à zéro ---------------------------------------------------


@pytest.mark.usefixtures("app_base")
def test_le_prompt_porte_cible_et_indices_mais_jamais_le_code() -> None:
    """Le code ne doit pas être extractible : le modèle ne le connaît pas."""
    spec = ticket_dor.spec_for(_lancer(indices="Joue du ukulélé."), 1, 1)
    assert _CIBLE in spec.system
    assert "Joue du ukulélé." in spec.system
    assert _CODE not in spec.system
    assert "{{" not in spec.system


@pytest.mark.usefixtures("app_base")
def test_une_nouvelle_partie_efface_gagnant_essais_et_plateaux() -> None:
    """Les essais repartent à cinq et les anciennes convs redeviennent ordinaires."""
    _lancer()
    ticket_dor.proposer(2, "Marie Durand")
    ticket_dor.proposer(1, _CIBLE)
    db.session.add(Conversation(user_id=1, title="t", messages=[], jeu=ticket_dor.JEU))
    db.session.commit()

    ticket_dor.nouvelle_partie()

    assert ticket_dor.etat(1).jouable
    assert ticket_dor.etat(2).restants == ticket_dor.MAX_ESSAIS
    assert ticket_dor.conversation_de(1) is None


@pytest.mark.parametrize("proposition", ["Jean", "DUPONT", "  jean  ", "Jean ?"])
def test_un_mot_seul_n_est_pas_une_proposition(proposition: str) -> None:
    """Prénom et nom exigés : un mot seul est refusé avant de coûter un essai."""
    assert not ticket_dor.proposition_valide(proposition)
    assert ticket_dor.proposition_valide("Jean Dupont")


def _systeme() -> str:
    ticket = ticket_dor.partie()
    assert ticket is not None
    return ticket_dor.spec_for(ticket, 1, 1).system


@pytest.mark.usefixtures("app_base")
def test_un_indice_d_office_puis_un_par_proposition_ratee() -> None:
    """Réclamer ne débloque rien : seule une proposition ratée ouvre le suivant."""
    _lancer(indices="- Joue du ukulélé.\n\n- Aime les crêpes.\n• Vient de Brest.")
    assert (ticket_dor.etat(1).indices_debloques, ticket_dor.etat(1).indices_total) == (
        1,
        3,
    )
    system = _systeme()
    assert "Joue du ukulélé." in system
    assert "crêpes" not in system

    verdict = ticket_dor.proposer(1, "Marie Durand")
    assert verdict is not None
    assert "nouvel indice" in verdict.reponse
    assert ticket_dor.etat(1).indices_debloques == 2  # noqa: PLR2004
    assert "crêpes" in _systeme()
    assert "Brest" not in _systeme()
    assert ticket_dor.etat(2).indices_debloques == 1


@pytest.mark.usefixtures("app_base")
def test_le_classement_regroupe_les_graphies_et_trie_par_nombre() -> None:
    """« DURAND Marie » et « marie durand » sont le même nom, compté deux fois."""
    _lancer()
    ticket_dor.proposer(1, "Paul Petit")
    ticket_dor.proposer(1, "Marie Durand")
    ticket_dor.proposer(2, "Marie DURAND")
    ticket_dor.proposer(2, "durand marie")
    classement = ticket_dor.classement_propositions()
    assert [(n.nombre, n.joueurs) for n in classement] == [(3, 2), (1, 1)]
    assert classement[0].nom == "durand marie"
    assert not classement[0].juste
