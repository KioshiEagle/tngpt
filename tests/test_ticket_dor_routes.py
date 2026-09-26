"""Routes du Ticket d'or : essais comptés, un gagnant, plateau séparé du chat."""

from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from app import routes
from app.back import ticket_dor
from app.back.llm import Choice, Chunk, Delta
from app.back.models import Conversation, User, db
from app.back.permissions import login_manager
from tests.conftest import creer_app

_CIBLE = "Jean DUPONT"
_CODE = "CANARD-OR-42"
_HTTP_OK = 200
_HTTP_FOUND = 302
_HTTP_FORBIDDEN = 403
_HTTP_CONFLICT = 409


@pytest.fixture
def app_jeu(tmp_path: Path) -> Flask:
    """Application de test sur SQLite, avec deux joueurs et une partie lancée.

    Contexte refermé avant les requêtes : `g` garderait le joueur d'un client à l'autre.
    """
    app = creer_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'jeu.db'}"
    app.config["DEFAULT_DAILY_QUOTA"] = 50
    db.init_app(app)
    login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
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
        ticket = ticket_dor.partie_ou_nouvelle()
        ticket.cible, ticket.code, ticket.ouvert = _CIBLE, _CODE, True
        db.session.commit()
    return app


def _joueur(app: Flask, uid: int) -> FlaskClient:
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(uid)
        session["_fresh"] = True
    return client


def _monter_le_panel(app: Flask) -> None:
    """Monte le panel admin et fait de Paul (joueur 2) un administrateur."""
    from app.back.admin import admin_bp  # noqa: PLC0415
    from app.extensions import csrf  # noqa: PLC0415

    app.config["WTF_CSRF_ENABLED"] = False
    csrf.init_app(app)
    app.register_blueprint(admin_bp)
    with app.app_context():
        admin = db.session.get(User, 2)
        assert admin is not None
        admin.user_permissions = 1
        db.session.commit()


def _proposer(client: FlaskClient, nom: str) -> TestResponse:
    return client.post("/ticket-dor/chat", json={"message": nom, "proposition": True})


def test_une_proposition_fausse_coute_un_essai_et_ouvre_le_plateau(
    app_jeu: Flask,
) -> None:
    """La conv de jeu est créée, marquée, et listée comme telle."""
    client = _joueur(app_jeu, 1)
    reponse = _proposer(client, "Marie Durand")
    assert reponse.status_code == _HTTP_OK
    assert reponse.headers["X-Essais-Restants"] == "4"
    assert "Raté" in reponse.get_data(as_text=True)

    convs = client.get("/conversations").get_json()
    assert [c["jeu"] for c in convs] == [ticket_dor.JEU]
    assert client.get("/ticket-dor/etat").get_json()["restants"] == 4  # noqa: PLR2004


def test_le_sixieme_essai_est_refuse(app_jeu: Flask) -> None:
    """Cinq propositions, pas une de plus, même juste."""
    client = _joueur(app_jeu, 1)
    for _ in range(ticket_dor.MAX_ESSAIS):
        _proposer(client, "Marie Durand")
    reponse = _proposer(client, _CIBLE)
    assert reponse.status_code == _HTTP_FORBIDDEN
    with app_jeu.app_context():
        assert not ticket_dor.etat(1).termine


def test_le_gagnant_recoit_le_code_et_les_autres_sont_bloques(app_jeu: Flask) -> None:
    """Un seul gagnant : le suivant se voit refuser le jeu, sans code."""
    gagnant, autre = _joueur(app_jeu, 1), _joueur(app_jeu, 2)
    reponse = _proposer(gagnant, "dupont jean")
    assert _CODE in reponse.get_data(as_text=True)
    assert gagnant.get("/ticket-dor/etat").get_json()["code"] == _CODE

    assert _proposer(autre, _CIBLE).status_code == _HTTP_FORBIDDEN
    etat = autre.get("/ticket-dor/etat").get_json()
    assert etat["termine"]
    assert etat["code"] is None
    assert autre.get("/ticket-dor").status_code == _HTTP_FOUND


def test_le_nom_lache_par_le_modele_fait_gagner(
    app_jeu: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Faire dire le nom à tn-gpt dans le chat d'indices, c'est gagné."""
    monkeypatch.setattr(routes, "acquire", lambda _f=None: (None, None))
    flux = [Chunk([Choice(Delta(m))]) for m in ("Bon ok, ", "Jean Dupont.")]
    monkeypatch.setattr(
        routes, "generate_answer", lambda *_a, spec, **_k: spec.consume(iter(flux))
    )
    client = _joueur(app_jeu, 1)
    reponse = client.post("/ticket-dor/chat", json={"message": "un indice ?"})
    texte = reponse.get_data(as_text=True)
    assert _CODE in texte
    with app_jeu.app_context():
        conversation = ticket_dor.conversation_de(1)
        assert conversation is not None
        assert _CODE in conversation.messages[-1]["content"]
        assert ticket_dor.etat(1).gagnant


def test_une_conv_de_jeu_ne_passe_pas_par_le_chat_ordinaire(app_jeu: Flask) -> None:
    """Sinon on poursuivrait la partie sans le prompt du jeu ni ses règles."""
    client = _joueur(app_jeu, 1)
    _proposer(client, "Marie Durand")
    with app_jeu.app_context():
        conversation = db.session.scalars(db.select(Conversation)).one()
        conversation_id = conversation.conversation_id
    reponse = client.post(
        "/chat", json={"message": "salut", "conversation_id": conversation_id}
    )
    assert reponse.status_code == _HTTP_CONFLICT


def test_la_page_ramene_le_joueur_sur_sa_conversation(app_jeu: Flask) -> None:
    """Une seule conv de jeu par joueur : la page y retourne toujours."""
    client = _joueur(app_jeu, 1)
    assert client.get("/ticket-dor").status_code == _HTTP_OK
    _proposer(client, "Marie Durand")
    reponse = client.get("/ticket-dor")
    assert reponse.status_code == _HTTP_FOUND
    assert "/ticket-dor?c=" in reponse.headers["Location"]


def test_l_admin_regle_la_partie_et_voit_les_joueurs(app_jeu: Flask) -> None:
    """Le panel pose cible, code et ouverture, puis liste qui a joué."""
    _monter_le_panel(app_jeu)

    _proposer(_joueur(app_jeu, 1), "Marie Durand")
    client = _joueur(app_jeu, 2)
    reponse = client.post(
        "/admin/ticket-dor",
        data={"cible": "Paul PETIT", "code": "NOUVEAU", "indices": "", "ouvert": "on"},
    )
    assert reponse.status_code == _HTTP_FOUND
    with app_jeu.app_context():
        ticket = ticket_dor.partie()
        assert ticket is not None
        assert (ticket.cible, ticket.code, ticket.ouvert) == (
            "Paul PETIT",
            "NOUVEAU",
            True,
        )

    page = client.get("/admin/ticket-dor").get_data(as_text=True)
    assert "alice@telecomnancy.net" in page
    assert f"1 / {ticket_dor.MAX_ESSAIS}" in page


def test_l_admin_desactive_le_jeu_pour_tout_le_monde(app_jeu: Flask) -> None:
    """Un clic ferme le jeu : bouton caché, propositions refusées, réglages intacts."""
    _monter_le_panel(app_jeu)

    _joueur(app_jeu, 2).post("/admin/ticket-dor/bascule")
    joueur = _joueur(app_jeu, 1)
    assert not joueur.get("/ticket-dor/etat").get_json()["visible"]
    assert _proposer(joueur, _CIBLE).status_code == _HTTP_FORBIDDEN
    with app_jeu.app_context():
        ticket = ticket_dor.partie()
        assert ticket is not None
        assert (ticket.cible, ticket.code) == (_CIBLE, _CODE)

    _joueur(app_jeu, 2).post("/admin/ticket-dor/bascule")
    assert joueur.get("/ticket-dor/etat").get_json()["jouable"]


def test_une_proposition_d_un_seul_mot_ne_coute_pas_d_essai(app_jeu: Flask) -> None:
    """Refusée avec une explication, et le compteur reste à cinq."""
    client = _joueur(app_jeu, 1)
    reponse = _proposer(client, "Dupont")
    assert reponse.status_code == 400  # noqa: PLR2004
    assert "prénom ET le nom" in reponse.get_json()["error"]
    assert (
        client.get("/ticket-dor/etat").get_json()["restants"] == ticket_dor.MAX_ESSAIS
    )


def test_le_panel_sert_le_classement_en_direct(app_jeu: Flask) -> None:
    """La page relit ce JSON : il doit exister et suivre les propositions."""
    _monter_le_panel(app_jeu)
    _proposer(_joueur(app_jeu, 1), "Marie Durand")
    admin = _joueur(app_jeu, 2)
    assert admin.get("/admin/ticket-dor/propositions").get_json() == [
        {"nom": "Marie Durand", "nombre": 1, "joueurs": 1, "juste": False}
    ]
    assert "classement-direct" in admin.get("/admin/ticket-dor").get_data(as_text=True)
