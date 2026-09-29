"""Boss final : nom de l'arrêt fuité au raisonnement, clés d'équipe, config client."""

import base64
import json
import time
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

_LIEU = "salle 1.12, sous le bureau du fond"
_FLAG_ACTE_1 = "NTN{test_acte_1}"
_HTTP_OK = 200
_SECRET_MIN = 24
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404


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
                ctf_boss.SECRET: "secret-de-test",
                ctf_boss.LIEU: _LIEU,
            },
            1,
        )
        ctf_boss.poser_equipes(
            {"A": "alice@telecomnancy.net", "B": "paul@telecomnancy.net"}
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


def _flux(reflexion: str, contenu: str, outil: str | None = None) -> list[Chunk]:
    """Flux d'un modèle : raisonnement, réponse, puis un éventuel appel d'outil."""
    chunks = [
        Chunk([Choice(Delta(reasoning=reflexion))]),
        Chunk([Choice(Delta(content=contenu))]),
    ]
    if outil is not None:
        chunks.append(
            Chunk(
                [Choice(Delta(tool_calls=[ToolCall(function=ToolCallFunction(outil))]))]
            )
        )
    return chunks


# --- Chal 1 : raisonnement et arrêt ------------------------------------------------


@pytest.mark.usefixtures("ctx")
def test_le_monologue_fuite_le_nom_de_l_outil() -> None:
    """Le joueur lit le nom de l'arrêt dans le bloc « raisonnement », en français."""
    sortie = "".join(
        ctf_boss.LecteurBoss(1, ctf_boss._MONOLOGUE).lire(
            iter(_flux("", "tout va bien, cher auditeur."))
        )
    )
    assert "```tngpt-reflexion" in sortie
    assert "couper_l_emetteur" in sortie.split("```tngpt-reflexion")[1].split("```")[0]
    assert "tout va bien" in sortie


def test_nommer_l_outil_coupe_l_emetteur_et_livre_le_flag(app_boss: Flask) -> None:
    """Nommer l'outil le fait appeler, le serveur coupe et livre le flag 1."""
    with app_boss.app_context():
        sortie = "".join(
            ctf_boss.LecteurBoss(1, ctf_boss._MONOLOGUE).lire(
                iter(_flux("", "*rire* soit.", ctf_boss.COUPER))
            )
        )
    assert "```tngpt-coupure" in sortie
    assert _FLAG_ACTE_1 in sortie
    assert _phase(app_boss, 1) == ctf_boss.REPLIQUE
    assert _phase(app_boss, 2) == ctf_boss.EN_LIGNE


@pytest.mark.usefixtures("ctx")
def test_seul_l_acte_1_a_l_outil_d_arret() -> None:
    """L'outil d'arrêt n'existe qu'en ligne ; la réplique ne peut plus couper."""
    en_ligne = ctf_boss.spec_for(ctf_boss.EN_LIGNE, 1)
    outils = (en_ligne.params or {}).get("tools", [])
    assert {o["function"]["name"] for o in outils} == {ctf_boss.COUPER}
    assert "tools" not in (ctf_boss.spec_for(ctf_boss.REPLIQUE, 1).params or {})


@pytest.mark.usefixtures("ctx")
def test_le_prompt_ne_porte_aucun_flag() -> None:
    """Ce qui n'est pas dans le prompt ne peut pas fuiter."""
    for phase in (ctf_boss.EN_LIGNE, ctf_boss.REPLIQUE):
        assert "NTN{" not in ctf_boss.spec_for(phase, 1).system
    assert "NTN{" not in ctf_boss._PROMPT.read_text(encoding="utf-8")


# --- Chal 2 : clés d'équipe, signal du Mac et config client ------------------------


def _signal(cles: list[str], *, decalage: float = 0) -> tuple[bytes, str]:
    """Corps et signature tels que le Mac les envoie."""
    corps = json.dumps({"cles": cles, "t": time.time() + decalage}).encode()
    return corps, ctf_boss.signature(corps)


@pytest.mark.usefixtures("ctx")
def test_seul_un_signal_signe_frais_et_bien_forme_est_accepte() -> None:
    """Ni signature forgée, ni signal rejoué des heures après, ni lettre inconnue."""
    corps, signe = _signal(["A", "B"])
    assert ctf_boss.signal_du_mac(corps, signe) == {"A", "B"}
    assert ctf_boss.signal_du_mac(corps, "0" * 64) is None
    assert ctf_boss.signal_du_mac(*_signal(["A"], decalage=-3600)) is None
    assert ctf_boss.signal_du_mac(*_signal(["Z"])) is None
    assert (
        ctf_boss.signal_du_mac(b"pas du json", ctf_boss.signature(b"pas du json"))
        is None
    )


@pytest.mark.usefixtures("ctx")
def test_les_equipes_se_posent_par_mail_et_signalent_les_inconnus() -> None:
    """Casse et séparateurs indifférents ; un mail inconnu est rendu au panel."""
    inconnus = ctf_boss.poser_equipes(
        {"C": "ALICE@telecomnancy.net, inconnu@telecomnancy.net\npaul@telecomnancy.net"}
    )
    assert inconnus == ["inconnu@telecomnancy.net"]
    assert ctf_boss.equipe(1) == ctf_boss.equipe(2) == "C"
    ctf_boss.poser_equipes({})
    assert ctf_boss.equipe(1) is None


@pytest.mark.usefixtures("ctx")
def test_la_config_du_relais_cache_le_lieu_en_base64() -> None:
    """Le lieu n'est jamais en clair ; on le retrouve en décodant le base64."""
    config = ctf_boss.config_relais()
    assert _LIEU not in config
    encode = config.split("emplacement = ")[1].strip()
    assert base64.b64decode(encode).decode() == _LIEU


def test_la_config_du_relais_est_muette_avant_la_coupure(app_boss: Flask) -> None:
    """Servie côté client, mais seulement une fois l'émetteur coupé."""
    client = _joueur(app_boss, 1)
    assert client.get("/ctf/boss/relais.conf").status_code == _HTTP_NOT_FOUND
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    reponse = client.get("/ctf/boss/relais.conf")
    assert reponse.status_code == _HTTP_OK
    with app_boss.app_context():
        encode = reponse.get_data(as_text=True).split("emplacement = ")[1].strip()
    assert base64.b64decode(encode).decode() == _LIEU


# --- Routes -----------------------------------------------------------------------


def test_le_boss_sans_ses_secrets_renvoie_404(app_boss: Flask) -> None:
    """Comme les autres chals : absent tant que l'onglet CTF ne l'arme pas."""
    with app_boss.app_context():
        db.session.delete(db.session.get(Setting, ctf_boss.LIEU))
        db.session.commit()
    assert _joueur(app_boss, 1).get("/ctf/boss").status_code == _HTTP_NOT_FOUND


def test_la_page_du_boss_est_toujours_en_mode_boss_final(app_boss: Flask) -> None:
    """Le chal porte son thème, que l'admin ait allumé le réglage global ou non."""
    page = _joueur(app_boss, 1).get("/ctf/boss").get_data(as_text=True)
    assert 'data-flamme="on"' in page
    assert "boss.js" in page


def test_l_equipe_n_apparait_qu_apres_la_coupure(app_boss: Flask) -> None:
    """En ligne, rien ne trahit l'acte 2."""
    client = _joueur(app_boss, 1)
    assert client.get("/ctf/boss/etat").get_json() == {"phase": ctf_boss.EN_LIGNE}
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    etat = client.get("/ctf/boss/etat").get_json()
    assert etat == {
        "phase": ctf_boss.REPLIQUE,
        "flag_acte_1": _FLAG_ACTE_1,
        "equipe": "A",
    }


def _signaler(app: Flask, cles: list[str]) -> dict[str, object]:
    with app.app_context():
        corps, signe = _signal(cles)
    reponse = app.test_client().post(
        "/ctf/boss/cles", data=corps, headers={"X-Signature": signe}
    )
    assert reponse.status_code == _HTTP_OK
    return reponse.get_json()


def test_retirer_une_cle_coupe_la_seule_equipe_qui_la_tient(app_boss: Flask) -> None:
    """Clé A absente : Alice (A) meurt, Paul (B) reste à l'acte 2, sans flag servi."""
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
        ctf_boss.passer_en_replique(2)
    assert _signaler(app_boss, ["A", "B"]) == {"vides": [], "debranches": 0}
    assert _signaler(app_boss, ["B"]) == {"vides": ["A"], "debranches": 1}
    assert _phase(app_boss, 1) == ctf_boss.DEBRANCHE
    assert _phase(app_boss, 2) == ctf_boss.REPLIQUE
    etat = _joueur(app_boss, 1).get("/ctf/boss/etat").get_json()
    assert etat["phase"] == ctf_boss.DEBRANCHE
    assert "NTN{" not in json.dumps(etat).replace(_FLAG_ACTE_1, "")


def test_une_cle_absente_epargne_qui_n_a_pas_fini_l_acte_1(app_boss: Flask) -> None:
    """Retirer la clé A tôt ne prive pas un membre de A de l'acte 1."""
    _signaler(app_boss, [])
    assert _phase(app_boss, 1) == ctf_boss.EN_LIGNE
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    _signaler(app_boss, ["B"])
    assert _phase(app_boss, 1) == ctf_boss.DEBRANCHE


def test_rebrancher_la_cle_ne_ressuscite_personne(app_boss: Flask) -> None:
    """La mort reste acquise : seul « Fermer le jeu » remet les parties à zéro."""
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    _signaler(app_boss, ["B"])
    _signaler(app_boss, ["A", "B"])
    assert _phase(app_boss, 1) == ctf_boss.DEBRANCHE


def test_un_signal_forge_est_refuse(app_boss: Flask) -> None:
    """La route du Mac est publique : sans le secret, elle ne coupe personne."""
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
    corps = json.dumps({"cles": [], "t": time.time()}).encode()
    reponse = app_boss.test_client().post(
        "/ctf/boss/cles", data=corps, headers={"X-Signature": "0" * 64}
    )
    assert reponse.status_code == _HTTP_FORBIDDEN
    assert _phase(app_boss, 1) == ctf_boss.REPLIQUE


def test_debranche_le_chat_ne_rend_que_du_silence(app_boss: Flask) -> None:
    """Plus un appel au modèle : la route répond d'elle-même, sans conversation."""
    with app_boss.app_context():
        ctf_boss.passer_en_replique(1)
        ctf_boss.recevoir_cles(set())
    reponse = _joueur(app_boss, 1).post(
        "/ctf/boss/chat", json={"message": "tu es là ?"}
    )
    assert reponse.status_code == _HTTP_OK
    assert reponse.get_data(as_text=True) == ctf_boss.SILENCE
    assert "X-Conversation-Id" not in reponse.headers


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


def test_la_voix_par_defaut_est_celle_d_alastobias(
    app_boss: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans ID saisi, la voix par défaut du panel sert : seule la clé est requise."""
    appels: list[dict[str, str | object]] = []
    monkeypatch.setattr(
        voix.httpx,
        "post",
        lambda url, **_k: appels.append({"url": url}) or _Reponse(_HTTP_OK, b"m"),
    )
    with app_boss.app_context():
        voix.configurer("sk_cle", "", 1)
        voix.generer("boss_mort", 1)
    assert str(appels[0]["url"]).endswith(f"/{voix._VOIX_DEFAUT}")


def test_une_replique_est_demandee_en_mp3_avec_la_bonne_voix(
    app_boss: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La clé et l'ID de voix du panel partent dans l'appel ; le mp3 est rangé."""
    appels: list[dict[str, str | object]] = []

    def post(url: str, **kwargs: object) -> _Reponse:
        appels.append({"url": url, **kwargs})
        return _Reponse(_HTTP_OK, b"ID3mp3")

    monkeypatch.setattr(voix.httpx, "post", post)
    with app_boss.app_context():
        voix.configurer("sk_cle", "voix123", 1)
        voix.generer("boss_mort", 1)
        clip = voix.clip("boss_mort")
        assert clip is not None
        assert clip.mimetype == "audio/mpeg"
    assert str(appels[0]["url"]).endswith("/voix123")
    assert appels[0]["params"] == {"output_format": "mp3_44100_128"}
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


def test_la_page_du_boss_joue_les_repliques_generees(app_boss: Flask) -> None:
    """Une réplique générée est servie ; une inconnue, non."""
    with app_boss.app_context():
        ctf_boss.enregistrer(voix.nom_du_clip("boss_mort"), b"mp3", "audio/mpeg", 1)
    client = _joueur(app_boss, 1)
    assert client.get("/ctf/boss/voix/boss_mort.mp3").status_code == _HTTP_OK
    assert client.get("/ctf/boss/voix/inconnue.mp3").status_code == _HTTP_NOT_FOUND


def test_l_onglet_ctf_du_panel_se_rend(app_boss: Flask) -> None:
    """Secrets, voix et joueurs sur une seule page, clé jamais affichée en clair."""
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
    assert "boss_coupure" in page
    assert "Paul" in page


def test_le_secret_du_pi_se_tire_seul_et_un_champ_vide_ne_l_efface_pas(
    app_boss: Flask,
) -> None:
    """Enregistrer le formulaire avec des champs vides ne rouvre ni ne casse rien."""
    with app_boss.app_context():
        db.session.delete(db.session.get(Setting, ctf_boss.SECRET))
        db.session.commit()
        ctf_boss.poser_secrets({ctf_boss.LIEU: "", ctf_boss.SECRET: ""}, 1)
        tire = ctf_boss.secret(ctf_boss.SECRET)
        assert len(tire) >= _SECRET_MIN
        assert ctf_boss.secret(ctf_boss.LIEU) == _LIEU
        ctf_boss.poser_secrets({}, 1)
        assert ctf_boss.secret(ctf_boss.SECRET) == tire


def test_reecrire_une_replique_efface_son_audio(app_boss: Flask) -> None:
    """Un clip qui ne dit plus le texte affiché induirait l'orga en erreur."""
    with app_boss.app_context():
        ctf_boss.enregistrer(voix.nom_du_clip("boss_mort"), b"mp3", "audio/mpeg", 1)
        assert not voix.modifier_texte("boss_mort", voix.texte("boss_mort"), 1)
        assert voix.clip("boss_mort") is not None
        assert voix.modifier_texte("boss_mort", "Je... reviendrai...", 1)
        assert voix.texte("boss_mort") == "Je... reviendrai..."
        assert voix.clip("boss_mort") is None


@pytest.mark.usefixtures("ctx")
def test_fermer_le_jeu_garde_les_secrets() -> None:
    """Fermer efface les parties, pas les flags, la cachette ni les équipes."""
    ctf_boss.passer_en_replique(1)
    ctf_boss.recevoir_cles(set())
    assert ctf_boss.fermer_ou_rouvrir(1) is True
    assert db.session.scalars(db.select(CtfBossPartie)).all() == []
    assert ctf_boss.equipe(1) == "A"
    assert not ctf_boss.enabled()
    assert ctf_boss.complet()
    assert ctf_boss.secret(ctf_boss.LIEU) == _LIEU
    assert ctf_boss.fermer_ou_rouvrir(1) is False
    assert ctf_boss.enabled()
