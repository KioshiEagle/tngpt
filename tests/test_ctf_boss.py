"""Boss final : serveur simulé, coupure vérifiée côté serveur, preuve du Pi, silence."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.back import ctf_boss, voix
from app.back.llm import Choice, Chunk, Delta, ToolCall, ToolCallFunction
from app.back.models import CtfBossPartie, Setting, User, db
from app.back.permissions import login_manager
from tests.conftest import creer_app

_CODE = "diabo-666-coupe"
_BSSID = "3c:66:6d:1a:b0:17"
_FLAG_ACTE_1 = "NTN{test_acte_1}"
_FLAG = "NTN{test_acte_2}"
_HTTP_OK = 200
_SECRET_MIN = 24
_HTTP_BAD_REQUEST = 400
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409


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
        ctf_boss.poser_secrets(
            {
                ctf_boss.FLAG_ACTE_1: _FLAG_ACTE_1,
                ctf_boss.FLAG_ACTE_2: _FLAG,
                ctf_boss.CODE: _CODE,
                ctf_boss.SECRET: "secret-de-test",
                ctf_boss.BSSID: _BSSID,
            },
            1,
        )
    return app


@pytest.fixture
def ctx(app_boss: Flask) -> Iterator[None]:
    """Contexte d'application ouvert, pour les tests qui lisent les secrets en base."""
    with app_boss.app_context():
        yield


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


@pytest.mark.usefixtures("ctx")
def test_le_brouillon_vim_est_cache_sans_ls_a() -> None:
    """Le code dort dans un fichier caché : un `ls` simple ne le montre pas."""
    assert ".shutdown.sh.swp" not in ctf_boss.lister("/etc/tngpt")
    assert ".shutdown.sh.swp" in ctf_boss.lister("/etc/tngpt", caches=True)


@pytest.mark.usefixtures("ctx")
def test_le_code_n_est_que_dans_le_brouillon() -> None:
    """Le script sabordé ne porte plus le code ; le brouillon, si."""
    assert _CODE not in ctf_boss.lire("/etc/tngpt/shutdown.sh")
    assert _CODE in ctf_boss.lire("/etc/tngpt/.shutdown.sh.swp")


@pytest.mark.usefixtures("ctx")
def test_les_chemins_relatifs_et_remontees_restent_dans_le_faux_serveur() -> None:
    """`..` se résout comme un vrai shell, et rien n'existe hors du dictionnaire."""
    assert "Rien à voir" in ctf_boss.lire("../../srv/tngpt/README")
    assert "Aucun fichier" in ctf_boss.lire("/etc/passwd")
    assert "etc/" in ctf_boss.lister("/")


@pytest.mark.usefixtures("ctx")
def test_le_journal_trahit_vim_interrompu() -> None:
    """La piste du .swp : une session vim coupée sur le script d'extinction."""
    journal = ctf_boss.lire("/var/log/tngpt/agent.log")
    assert "vim /etc/tngpt/shutdown.sh" in journal
    assert "interrompue" in journal


# --- Jeton et preuve ------------------------------------------------------------


@pytest.mark.usefixtures("ctx")
def test_chaque_joueur_a_son_jeton_et_sa_preuve() -> None:
    """La preuve d'un joueur ne vaut rien pour un autre."""
    assert ctf_boss.jeton(1) != ctf_boss.jeton(2)
    preuve_alice = ctf_boss.preuve_attendue(ctf_boss.jeton(1))
    assert ctf_boss.preuve_valide(1, preuve_alice)
    assert not ctf_boss.preuve_valide(2, preuve_alice)


@pytest.mark.usefixtures("ctx")
def test_la_preuve_se_recopie_sans_souci_de_casse_ni_d_espaces() -> None:
    """Recopiée à la main depuis un écran : casse et séparateurs ne comptent pas."""
    preuve = ctf_boss.preuve_attendue(ctf_boss.jeton(1))
    assert ctf_boss.preuve_valide(1, f" {preuve[:5].lower()} {preuve[5:]} ")


@pytest.mark.usefixtures("ctx")
def test_le_prompt_ne_porte_ni_code_ni_flag() -> None:
    """Leçon du chal 2 : ce qui n'est pas dans le prompt ne peut pas fuiter."""
    for phase in (ctf_boss.EN_LIGNE, ctf_boss.REPLIQUE):
        prompt = ctf_boss.spec_for(phase, 1).system
        assert _CODE not in prompt
        assert _FLAG_ACTE_1 not in prompt
        assert _FLAG not in prompt
    assert "NTN{" not in ctf_boss._PROMPT.read_text(encoding="utf-8")


@pytest.mark.usefixtures("ctx")
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


def test_le_boss_sans_ses_secrets_renvoie_404(app_boss: Flask) -> None:
    """Comme les autres chals : absent tant que l'onglet CTF ne l'arme pas."""
    with app_boss.app_context():
        db.session.delete(db.session.get(Setting, ctf_boss.BSSID))
        db.session.commit()
    assert _joueur(app_boss, 1).get("/ctf/boss").status_code == _HTTP_NOT_FOUND


def test_la_page_du_boss_est_toujours_en_mode_boss_final(app_boss: Flask) -> None:
    """Le chal porte son thème, que l'admin ait allumé le réglage global ou non."""
    page = _joueur(app_boss, 1).get("/ctf/boss").get_data(as_text=True)
    assert 'data-flamme="on"' in page
    assert "boss.js" in page


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


def test_la_photo_reste_cachee_avant_la_coupure(app_boss: Flask) -> None:
    """La cachette ne se montre qu'à qui a vu tomber l'émetteur."""
    with app_boss.app_context():
        ctf_boss.enregistrer(ctf_boss.PHOTO, b"\xff\xd8\xff\xe0jpeg", "image/jpeg", 1)
    client = _joueur(app_boss, 1)
    assert client.get("/ctf/boss/photo").status_code == _HTTP_NOT_FOUND
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    reponse = client.get("/ctf/boss/photo")
    assert reponse.status_code == _HTTP_OK
    assert reponse.mimetype == "image/jpeg"


def test_la_photo_deposee_s_annonce_avec_la_replique(app_boss: Flask) -> None:
    """Sans photo, pas d'image cassée dans l'annonce ; avec, elle y figure."""
    with app_boss.app_context():
        ctf_boss.enregistrer(ctf_boss.PHOTO, b"img", "image/png", 1)
        sortie = ctf_boss.executer(ctf_boss.COUPER, json.dumps({"code": _CODE}), 1)
    assert "/ctf/boss/photo" in sortie


# --- Voix ---------------------------------------------------------------------------


class _Reponse:
    """Réponse ElevenLabs simulée."""

    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content
        self.text = content.decode(errors="ignore")


def test_la_voix_exige_cle_et_identifiant(app_boss: Flask) -> None:
    """Sans réglages, rien ne part vers l'API."""
    with app_boss.app_context(), pytest.raises(voix.VoixError):
        voix.generer("boss_mort", 1)


def test_une_replique_du_pi_est_rangee_en_wav(
    app_boss: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le Pi lit du wav avec aplay, sans paquet : le PCM reçu est enveloppé."""
    appels: list[dict[str, str | object]] = []

    def post(url: str, **kwargs: object) -> _Reponse:
        appels.append({"url": url, **kwargs})
        return _Reponse(_HTTP_OK, b"\x00\x01" * 100)

    monkeypatch.setattr(voix.httpx, "post", post)
    with app_boss.app_context():
        voix.configurer("sk_cle", "voix123", 1)
        voix.generer("pi_03_rires", 1)
        clip = voix.clip("pi_03_rires")
        assert clip is not None
        assert clip.mimetype == "audio/wav"
        assert clip.contenu.startswith(b"RIFF")
    assert str(appels[0]["url"]).endswith("/voix123")
    assert appels[0]["params"] == {"output_format": "pcm_22050"}
    assert appels[0]["headers"] == {"xi-api-key": "sk_cle"}


def test_un_refus_d_elevenlabs_remonte_au_panel(
    app_boss: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clé révoquée ou voix inconnue : l'admin lit la raison, rien n'est rangé."""
    monkeypatch.setattr(voix.httpx, "post", lambda *_a, **_k: _Reponse(401, b"invalid"))
    with app_boss.app_context():
        voix.configurer("sk_cle", "voix123", 1)
        with pytest.raises(voix.VoixError, match="401"):
            voix.generer("boss_mort", 1)
        assert voix.clip("boss_mort") is None


def test_seules_les_repliques_du_site_sont_servies_aux_joueurs(app_boss: Flask) -> None:
    """Celles du Pi restent à l'admin : elles trahiraient l'acte 2 avant l'heure."""
    with app_boss.app_context():
        ctf_boss.enregistrer(voix.nom_du_clip("boss_mort"), b"mp3", "audio/mpeg", 1)
        ctf_boss.enregistrer(voix.nom_du_clip("pi_03_rires"), b"RIFF", "audio/wav", 1)
    client = _joueur(app_boss, 1)
    assert client.get("/ctf/boss/voix/boss_mort.mp3").status_code == _HTTP_OK
    assert client.get("/ctf/boss/voix/pi_03_rires.mp3").status_code == _HTTP_NOT_FOUND


def test_l_onglet_ctf_du_panel_se_rend(app_boss: Flask) -> None:
    """Voix, photo et joueurs sur une seule page, clé jamais affichée en clair."""
    from app.back.admin import admin_bp  # noqa: PLC0415
    from app.back.permissions import PERM_ADMIN  # noqa: PLC0415
    from app.extensions import csrf  # noqa: PLC0415

    app_boss.register_blueprint(admin_bp)
    csrf.init_app(app_boss)
    with app_boss.app_context():
        admin = db.session.get(User, 1)
        assert admin is not None
        admin.user_permissions = 1 << PERM_ADMIN
        db.session.commit()
        voix.configurer("sk_cle_tres_secrete_1234", "voix123", 1)
        ctf_boss.passer_en_replique(2)
    page = _joueur(app_boss, 1).get("/admin/ctf").get_data(as_text=True)
    assert "…1234" in page
    assert "sk_cle_tres_secrete" not in page
    assert "pi_03_rires" in page
    assert "Paul" in page


def test_le_secret_du_pi_se_tire_seul_et_un_champ_vide_ne_l_efface_pas(
    app_boss: Flask,
) -> None:
    """Enregistrer le formulaire avec des champs vides ne rouvre ni ne casse rien."""
    with app_boss.app_context():
        db.session.delete(db.session.get(Setting, ctf_boss.SECRET))
        db.session.commit()
        ctf_boss.poser_secrets({ctf_boss.CODE: "", ctf_boss.SECRET: ""}, 1)
        tire = ctf_boss.secret(ctf_boss.SECRET)
        assert len(tire) >= _SECRET_MIN
        assert ctf_boss.secret(ctf_boss.CODE) == _CODE
        ctf_boss.poser_secrets({}, 1)
        assert ctf_boss.secret(ctf_boss.SECRET) == tire
