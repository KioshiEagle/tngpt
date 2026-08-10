import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_compress import Compress
from flask_migrate import Migrate
from werkzeug.exceptions import HTTPException

from app.back.admin import admin_bp
from app.back.auth import auth_bp
from app.back.generate import SYSTEM_PROMPT_PATH
from app.back.models import User, db
from app.back.permissions import login_manager
from app.cli import register_cli
from app.extensions import csrf, limiter
from app.routes import bp

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = Flask(
    __name__,
    template_folder="app/front/templates",
    static_folder="app/front/static",
)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-moi-en-prod")

# Pas de repli sur SQLite : sans DATABASE_URL, l'app démarrerait sur une base
# vide et paraîtrait fonctionner (zéro utilisateur, zéro document) au lieu
# d'échouer. Mieux vaut refuser de démarrer.
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    msg = "DATABASE_URL est absent de l'environnement (voir .env)."
    raise RuntimeError(msg)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url

# Fichiers déposés dans le panel admin : zone de transit uniquement. Ils sont
# supprimés dès l'ingestion terminée, le contenu vivant ensuite dans Qdrant.
app.config["UPLOAD_DIR"] = Path(__file__).parent / "uploads"
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 Mo par requête

# Quota de questions par jour appliqué aux utilisateurs sans surcharge explicite.
# Les administrateurs n'y sont pas soumis.
app.config["DEFAULT_DAILY_QUOTA"] = int(os.environ.get("DEFAULT_DAILY_QUOTA", "100"))

Compress(app)
limiter.init_app(app)
csrf.init_app(app)
db.init_app(app)
migrate = Migrate(app, db)

login_manager.init_app(app)
login_manager.login_view = "auth.login_page"


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Charge un utilisateur depuis la session Flask-Login.

    Un banni est traité comme non connecté : c'est ce qui rend le bannissement
    immédiat. Sans ce contrôle, sa session resterait valide jusqu'à expiration du
    cookie et il continuerait d'utiliser le chat.
    """
    user = db.session.get(User, int(user_id))
    if user is not None and user.is_banned():
        return None
    return user


@login_manager.unauthorized_handler
def unauthorized() -> ResponseReturnValue:
    """Redirige la navigation vers le login, mais répond 401 JSON aux appels API.

    Sans cela, un fetch() dont la session a expiré suit la redirection et reçoit
    le HTML de la page de login en 200 — que le front afficherait comme une
    réponse du chat.
    """
    if request.is_json or request.accept_mimetypes.best == "application/json":
        return jsonify(
            error="Session expirée", login_url=url_for("auth.login_page")
        ), 401
    return redirect(url_for("auth.login_page", next=request.path))


@app.errorhandler(429)
def too_many_requests(error: HTTPException) -> ResponseReturnValue:
    """Répond en JSON aux appels API quand le rate-limiter renvoie un 429.

    Le quota journalier renvoie déjà son propre JSON ; ce handler couvre la
    rafale bloquée par flask-limiter, qui produirait sinon une page HTML que le
    front afficherait comme une réponse du chat.
    """
    if request.is_json or request.accept_mimetypes.best == "application/json":
        description = getattr(error, "description", None)
        return jsonify(
            error=f"Trop de requêtes. {description}"
            if description
            else "Trop de requêtes, ralentis un peu."
        ), 429
    return error


@app.template_global()
def asset(filename: str) -> str:
    """URL d'un fichier statique, suffixée de son horodatage de modification.

    Sans cette empreinte, un navigateur continue de servir l'ancien main.js ou
    l'ancien style.css après un déploiement : le front tourne alors sur une
    version périmée sans que rien ne le signale.
    """
    path = Path(app.static_folder or "") / filename
    stamp = int(path.stat().st_mtime) if path.is_file() else 0
    return f"/static/{filename}?v={stamp}"


app.register_blueprint(bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

# Le chat est appelé en fetch() JSON, sans jeton CSRF : son Content-Type
# application/json empêche déjà un POST de formulaire cross-origin.
csrf.exempt(bp)

register_cli(app)

# Le schéma est géré par Alembic (`flask db upgrade`), pas par db.create_all() :
# create_all() crée les tables manquantes mais n'ajoute jamais une colonne à une
# table existante, ce qui laisserait le schéma diverger en silence.

if __name__ == "__main__":
    # Point d'entrée de développement uniquement : la production sert `main:app`
    # par gunicorn et ne passe jamais ici.
    #
    # `debug=False` explicite, et non l'argument omis : sans lui, Flask lit
    # FLASK_DEBUG dans l'environnement et rallume le débogueur de lui-même — or
    # .env porte FLASK_DEBUG=true pour l'OAuth en http local. Le débogueur
    # Werkzeug affiche une console d'exécution de code Python sur ses pages
    # d'erreur ; il n'a rien à faire sur un serveur, fût-il local.
    #
    # Le rechargeur, lui, reste. Il ne surveille que les modules Python, d'où
    # `extra_files` : `CHAT_SYSTEM` est lu une fois à l'import, donc sans cette
    # ligne une retouche de system_prompt.md resterait sans effet jusqu'au
    # prochain redémarrage — et sans rien signaler.
    app.run(
        host="127.0.0.1",
        port=8501,
        debug=False,
        use_reloader=True,
        extra_files=[SYSTEM_PROMPT_PATH],
    )
