"""Vue d'ensemble du panel : questions fréquentes et sauvegardes.

Le dump emporte les comptes, les conversations et les clés du pool : la garde
d'accès sur ces deux routes vaut autant que le code qui les sert.
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


@pytest.mark.parametrize("route", ["/export/base", "/export/logs"])
def test_les_exports_sont_reserves_aux_admins(route: str) -> None:
    """Le dump porte tous les secrets de l'app : la garde ne doit pas sauter."""
    source = _ADMIN.read_text(encoding="utf-8")
    declaration = source.index(f'@admin_bp.route("{route}")')
    assert "@admin_required" in source[declaration : declaration + 200]


def test_le_dump_ne_met_pas_le_mot_de_passe_en_ligne_de_commande() -> None:
    """Un argument de `pg_dump` se lit dans la liste des processus du conteneur."""
    source = _ADMIN.read_text(encoding="utf-8")
    assert '"PGPASSWORD": url.password' in source
    commande = source[source.index("commande = [") : source.index("]")]
    assert "url.password" not in commande


def test_l_image_embarque_pg_dump() -> None:
    """Sans le client Postgres dans l'image, le bouton ne sauvegarde rien."""
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "postgresql-client" in dockerfile
