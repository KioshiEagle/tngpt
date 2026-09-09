"""Vue d'ensemble du panel : questions fréquentes et export des journaux.

Les journaux portent les questions de vraies personnes : la garde d'accès sur
la route vaut autant que le code qui la sert.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from flask import Flask

from app.back.admin import _questions_frequentes
from app.back.models import Query, User, db

_ADMIN = Path(__file__).resolve().parent.parent / "app" / "back" / "admin.py"

# Le jeu d'essai : « wifi du campus » posé trois fois, par deux personnes.
_OCCURRENCES = 3
_AUTEURS = 2


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


def _poser(questions: list[tuple[int, str]]) -> None:
    """Écrit des questions en base, chacune attribuée à l'utilisateur donné."""
    for user_id in {user_id for user_id, _ in questions}:
        db.session.add(
            User(
                user_id=user_id,
                user_firstname=f"Testeur{user_id}",
                user_surname="Anonyme",
                user_mail=f"testeur{user_id}@example.org",
                user_permissions=0,
            )
        )
    for user_id, question in questions:
        db.session.add(
            Query(
                user_id=user_id,
                question=question,
                top_k=5,
                created_at=datetime.now(UTC),
            )
        )
    db.session.commit()


@pytest.mark.usefixtures("app_base")
def test_les_questions_sont_classees_par_frequence() -> None:
    """La plus posée arrive en tête, et chaque ligne compte ses auteurs."""
    _poser(
        [
            (1, "wifi du campus"),
            (2, "wifi du campus"),
            (1, "où imprimer"),
            (1, "wifi du campus"),
        ]
    )

    lignes = _questions_frequentes(10)

    assert lignes[0].question == "wifi du campus"
    assert lignes[0].occurrences == _OCCURRENCES
    assert lignes[0].personnes == _AUTEURS
    assert lignes[1].occurrences == 1


@pytest.mark.usefixtures("app_base")
def test_la_casse_et_les_espaces_ne_font_pas_deux_questions() -> None:
    """Sans regroupement, le classement se disperserait en variantes d'une même."""
    _poser([(1, "Wifi du campus"), (1, "  wifi du campus  "), (1, "wifi du campus")])

    lignes = _questions_frequentes(10)

    assert len(lignes) == 1
    assert lignes[0].occurrences == _OCCURRENCES


@pytest.mark.usefixtures("app_base")
def test_une_base_vide_ne_rapporte_aucune_question() -> None:
    """La page doit pouvoir s'afficher avant la première question posée."""
    assert list(_questions_frequentes(10)) == []


def test_l_export_des_journaux_est_reserve_aux_admins() -> None:
    """Les journaux portent les questions posées : la garde ne doit pas sauter."""
    source = _ADMIN.read_text(encoding="utf-8")
    declaration = source.index('@admin_bp.route("/export/logs")')
    assert "@admin_required" in source[declaration : declaration + 200]
