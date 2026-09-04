"""Outillage commun aux tests : une application jetable, sans `main`.

`main` exige `DATABASE_URL` à l'import et échoue sans lui : le job de tests
n'en a pas, et n'a pas à en avoir. Les tests qui ont besoin de routes montent
donc les blueprints sur une application montée ici, sans base ni réseau.
"""

from pathlib import Path

import pytest
from flask import Flask

_FRONT = Path(__file__).resolve().parent.parent / "app" / "front"


def creer_app() -> Flask:
    """Application minimale portant les routes, les gabarits et leurs globales."""
    from app.back.auth import auth_bp  # noqa: PLC0415
    from app.back.permissions import login_manager  # noqa: PLC0415
    from app.routes import bp  # noqa: PLC0415

    app = Flask(
        __name__,
        template_folder=str(_FRONT / "templates"),
        static_folder=str(_FRONT / "static"),
    )
    app.secret_key = "cle-de-test"

    # `asset` est défini dans `main` : les gabarits l'appellent, il faut donc
    # un équivalent ici. L'empreinte de fraîcheur n'a pas d'intérêt en test.
    app.add_template_global(lambda fichier: f"/static/{fichier}", "asset")

    # `/rgpd` lit `current_user` : sans gestionnaire, flask_login lève. Le
    # chargeur est défini dans `main` et interroge la base ; ici personne n'est
    # connecté, ce qui est exactement l'état que ces tests examinent.
    login_manager.init_app(app)
    login_manager.user_loader(lambda _identifiant: None)

    app.register_blueprint(bp)
    app.register_blueprint(auth_bp)
    return app


@pytest.fixture
def app() -> Flask:
    """Application de test, rendue à chaque test qui la demande."""
    return creer_app()
