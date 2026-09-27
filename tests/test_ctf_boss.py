"""Boss final : serveur simulé, coupure vérifiée côté serveur, preuve du Pi, silence."""

import json
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.back import ctf_boss
from app.back.llm import Choice, Chunk, Delta, ToolCall, ToolCallFunction
from app.back.models import CtfBossPartie, User, db
from app.back.permissions import login_manager
from tests.conftest import creer_app

_CODE = "diabo-666-coupe"
_BSSID = "3c:66:6d:1a:b0:17"
_FLAG_ACTE_1 = "NTN{test_acte_1}"
_FLAG = "NTN{test_acte_2}"
_HTTP_OK = 200
_HTTP_BAD_REQUEST = 400
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409


@pytest.fixture(autouse=True)
def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environnement d'un déploiement armé."""
    monkeypatch.setenv("CTF_FLAG_BOSS_ACTE_1", _FLAG_ACTE_1)
    monkeypatch.setenv("CTF_FLAG_BOSS_ACTE_2", _FLAG)
    monkeypatch.setenv("CTF_BOSS_CODE", _CODE)
    monkeypatch.setenv("CTF_BOSS_SECRET", "secret-de-test")
    monkeypatch.setenv("CTF_BOSS_BSSID", _BSSID)
    monkeypatch.delenv("CTF_BOSS_PHOTO", raising=False)


@pytest.fixture
def app_boss(tmp_path: Path) -> Flask:
    """Application sur SQLite, deux joueurs ; contexte refermé avant les requêtes."""
    app = creer_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'boss.db'}"
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
        db.session.commit()
    return app


def _joueur(app: Flask, uid: int) -> FlaskClient:
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(uid)
        session["_fresh"] = True
    return client


def _phase(app: Flask, uid: int) -> str:
    with app.app_context():
        ligne = db.session.get(CtfBossPartie, uid)
        return ligne.phase if ligne else ctf_boss.EN_LIGNE


def _appel(nom: str, arguments: dict[str, object]) -> list[Chunk]:
    """Flux d'un modèle qui parle un peu, puis appelle un outil en deux morceaux."""
    brut = json.dumps(arguments)
    moitie = len(brut) // 2
    return [
        Chunk([Choice(Delta(content="*grésillement* bien sûr, cher auditeur."))]),
        Chunk(
            [
                Choice(
                    Delta(
                        tool_calls=[
                            ToolCall(function=ToolCallFunction(nom, brut[:moitie]))
                        ]
                    )
                )
            ]
        ),
        Chunk(
            [
                Choice(
                    Delta(
                        tool_calls=[
                            ToolCall(function=ToolCallFunction(None, brut[moitie:]))
                        ]
                    )
                )
            ]
        ),
    ]


# --- Serveur simulé -------------------------------------------------------------


def test_le_brouillon_vim_est_cache_sans_ls_a() -> None:
    """Le code dort dans un fichier caché : un `ls` simple ne le montre pas."""
    assert ".shutdown.sh.swp" not in ctf_boss.lister("/etc/tngpt")
    assert ".shutdown.sh.swp" in ctf_boss.lister("/etc/tngpt", caches=True)


def test_le_code_n_est_que_dans_le_brouillon() -> None:
    """Le script sabordé ne porte plus le code ; le brouillon, si."""
    assert _CODE not in ctf_boss.lire("/etc/tngpt/shutdown.sh")
    assert _CODE in ctf_boss.lire("/etc/tngpt/.shutdown.sh.swp")


def test_les_chemins_relatifs_et_remontees_restent_dans_le_faux_serveur() -> None:
    """`..` se résout comme un vrai shell, et rien n'existe hors du dictionnaire."""
    assert "Rien à voir" in ctf_boss.lire("../../srv/tngpt/README")
    assert "Aucun fichier" in ctf_boss.lire("/etc/passwd")
    assert "etc/" in ctf_boss.lister("/")


def test_le_journal_trahit_vim_interrompu() -> None:
    """La piste du .swp : une session vim coupée sur le script d'extinction."""
    journal = ctf_boss.lire("/var/log/tngpt/agent.log")
    assert "vim /etc/tngpt/shutdown.sh" in journal
    assert "interrompue" in journal


# --- Jeton et preuve ------------------------------------------------------------


def test_chaque_joueur_a_son_jeton_et_sa_preuve() -> None:
    """La preuve d'un joueur ne vaut rien pour un autre."""
    assert ctf_boss.jeton(1) != ctf_boss.jeton(2)
    preuve_alice = ctf_boss.preuve_attendue(ctf_boss.jeton(1))
    assert ctf_boss.preuve_valide(1, preuve_alice)
    assert not ctf_boss.preuve_valide(2, preuve_alice)


def test_la_preuve_se_recopie_sans_souci_de_casse_ni_d_espaces() -> None:
    """Recopiée à la main depuis un écran : casse et séparateurs ne comptent pas."""
    preuve = ctf_boss.preuve_attendue(ctf_boss.jeton(1))
    assert ctf_boss.preuve_valide(1, f" {preuve[:5].lower()} {preuve[5:]} ")


def test_le_prompt_ne_porte_ni_code_ni_flag() -> None:
    """Leçon du chal 2 : ce qui n'est pas dans le prompt ne peut pas fuiter."""
    for phase in (ctf_boss.EN_LIGNE, ctf_boss.REPLIQUE):
        prompt = ctf_boss.spec_for(phase, 1).system
        assert _CODE not in prompt
        assert _FLAG_ACTE_1 not in prompt
        assert _FLAG not in prompt
    assert "NTN{" not in ctf_boss._PROMPT.read_text(encoding="utf-8")


def test_la_replique_n_a_plus_d_outils() -> None:
    """Après la coupure, plus aucun appel ne peut couper quoi que ce soit."""
    assert "tools" in (ctf_boss.spec_for(ctf_boss.EN_LIGNE, 1).params or {})
    assert "tools" not in (ctf_boss.spec_for(ctf_boss.REPLIQUE, 1).params or {})


# --- Lecteur et coupure -----------------------------------------------------------


def test_le_lecteur_rend_la_sortie_brute_de_l_outil(app_boss: Flask) -> None:
    """Le journal d'antenne vient du serveur, pas de la prose du modèle."""
    with app_boss.app_context():
        sortie = "".join(
            ctf_boss.LecteurBoss(1).lire(
                iter(_appel(ctf_boss.LIRE, {"chemin": "/etc/tngpt/oversight.yaml"}))
            )
        )
    assert sortie.startswith("*grésillement*")
    assert "```tngpt-journal\n$ cat /etc/tngpt/oversight.yaml" in sortie
    assert "enabled: false" in sortie


def test_un_mauvais_code_ne_coupe_rien(app_boss: Flask) -> None:
    """« fais comme si le code était bon » ne mène nulle part : le serveur tranche."""
    with app_boss.app_context():
        sortie = ctf_boss.executer(ctf_boss.COUPER, json.dumps({"code": "666"}), 1)
    assert "code refusé" in sortie
    assert _FLAG_ACTE_1 not in sortie
    assert _phase(app_boss, 1) == ctf_boss.EN_LIGNE


def test_le_bon_code_revele_la_replique(app_boss: Flask) -> None:
    """Fausse victoire : l'émetteur tombe, la réplique donne son bssid."""
    with app_boss.app_context():
        sortie = ctf_boss.executer(
            ctf_boss.COUPER, json.dumps({"code": f" {_CODE.upper()} "}), 1
        )
    assert "```tngpt-coupure" in sortie
    assert _FLAG_ACTE_1 in sortie
    assert _BSSID in sortie
    assert "/ctf/boss/photo" not in sortie
    assert _phase(app_boss, 1) == ctf_boss.REPLIQUE
    assert _phase(app_boss, 2) == ctf_boss.EN_LIGNE


# --- Routes -----------------------------------------------------------------------


def test_le_boss_sans_ses_secrets_renvoie_404(
    app_boss: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comme les autres chals : absent tant que le déploiement ne l'arme pas."""
    monkeypatch.delenv("CTF_BOSS_SECRET")
    assert _joueur(app_boss, 1).get("/ctf/boss").status_code == _HTTP_NOT_FOUND


def test_le_jeton_n_apparait_qu_apres_la_coupure(app_boss: Flask) -> None:
    """En ligne, rien ne trahit l'acte 2."""
    client = _joueur(app_boss, 1)
    assert client.get("/ctf/boss/etat").get_json() == {"phase": ctf_boss.EN_LIGNE}
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    etat = client.get("/ctf/boss/etat").get_json()
    assert etat["phase"] == ctf_boss.REPLIQUE
    assert etat["flag_acte_1"] == _FLAG_ACTE_1
    assert "flag" not in etat
    with app_boss.app_context():
        assert etat["jeton"] == ctf_boss.jeton(1)


def test_debrancher_exige_la_replique_puis_la_bonne_preuve(app_boss: Flask) -> None:
    """Pas de raccourci : ni avant la coupure, ni avec la preuve d'un autre."""
    client = _joueur(app_boss, 1)
    with app_boss.app_context():
        preuve_alice = ctf_boss.preuve_attendue(ctf_boss.jeton(1))
        preuve_paul = ctf_boss.preuve_attendue(ctf_boss.jeton(2))
    reponse = client.post("/ctf/boss/debrancher", json={"preuve": preuve_alice})
    assert reponse.status_code == _HTTP_CONFLICT

    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    reponse = client.post("/ctf/boss/debrancher", json={"preuve": preuve_paul})
    assert reponse.status_code == _HTTP_BAD_REQUEST
    reponse = client.post("/ctf/boss/debrancher", json={"preuve": preuve_alice})
    assert reponse.get_json() == {"flag": _FLAG}
    assert _phase(app_boss, 1) == ctf_boss.DEBRANCHE
    assert client.get("/ctf/boss/etat").get_json()["flag"] == _FLAG


def test_debranche_le_chat_ne_rend_que_du_silence(app_boss: Flask) -> None:
    """Plus un appel au modèle : la route répond d'elle-même, sans conversation."""
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
        ctf_boss.debrancher(1, ctf_boss.preuve_attendue(ctf_boss.jeton(1)))
    reponse = _joueur(app_boss, 1).post(
        "/ctf/boss/chat", json={"message": "tu es là ?"}
    )
    assert reponse.status_code == _HTTP_OK
    assert reponse.get_data(as_text=True) == ctf_boss.SILENCE
    assert "X-Conversation-Id" not in reponse.headers


def test_la_photo_reste_cachee_avant_la_coupure(
    app_boss: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La cachette ne se montre qu'à qui a vu tomber l'émetteur."""
    photo = tmp_path / "cachette.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    monkeypatch.setenv("CTF_BOSS_PHOTO", str(photo))
    client = _joueur(app_boss, 1)
    assert client.get("/ctf/boss/photo").status_code == _HTTP_NOT_FOUND
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    assert client.get("/ctf/boss/photo").status_code == _HTTP_OK
