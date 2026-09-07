"""L'habillage flamme est un réglage global, jamais une préférence locale.

Deux garde-fous : la valeur par défaut d'un réglage absent de la table, et le
fait que le gabarit du chat n'offre aucune prise dessus à l'utilisateur.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from app.back.models import db
from app.back.reglages import FLAMME, basculer, est_actif

_FRONT = Path(__file__).resolve().parent.parent / "app" / "front"
_TEMPLATES = _FRONT / "templates"


@pytest.fixture
def app_base() -> Iterator[Flask]:
    """Application jetable sur une base SQLite en mémoire."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.mark.usefixtures("app_base")
def test_un_reglage_absent_vaut_eteint() -> None:
    """Une base neuve n'a aucune ligne : rien ne doit s'allumer tout seul."""
    assert est_actif(FLAMME) is False


@pytest.mark.usefixtures("app_base")
def test_la_bascule_allume_puis_eteint() -> None:
    """La ligne est créée au premier allumage, puis réutilisée."""
    basculer(FLAMME, actif=True)
    assert est_actif(FLAMME) is True

    basculer(FLAMME, actif=False)
    assert est_actif(FLAMME) is False


def test_le_chat_ne_donne_aucune_prise_sur_la_flamme() -> None:
    """L'habillage vient du serveur : aucun bouton, aucun localStorage côté chat."""
    index = (_TEMPLATES / "index.html").read_text(encoding="utf-8")
    assert '{% if flamme %} data-flamme="on"{% endif %}' in index
    assert "localStorage.getItem('flamme')" not in index


def test_le_brainrot_reste_neon_sous_l_habillage_flamme() -> None:
    """Régression : la braise s'invitait sur le toggle brainrot, qui doit rester rose.

    L'habillage flamme cède la place au brainrot ; chacune de ses règles porte
    donc le `:not`, faute de quoi un clic sur « mode brainrot » rallume le feu.
    """
    css = (_FRONT / "static" / "style.css").read_text(encoding="utf-8")
    regles = [ligne for ligne in css.splitlines() if '[data-flamme="on"]' in ligne]
    assert regles, "aucune règle d'habillage flamme dans la feuille de style"
    sans_garde = [r for r in regles if ':not([data-brainrot="on"])' not in r]
    assert not sans_garde, f"règles flamme sans garde brainrot : {sans_garde}"


def test_seul_un_admin_bascule_l_habillage() -> None:
    """Le réglage vaut pour tout le monde : la route reste derrière `admin_required`."""
    source = (
        Path(__file__).resolve().parent.parent / "app" / "back" / "admin.py"
    ).read_text(encoding="utf-8")
    route = source.index('@admin_bp.route("/apparence/flamme", methods=["POST"])')
    assert "@admin_required" in source[route : route + 200]
