import logging
import os

from dotenv import load_dotenv
from flask import Flask
from flask_compress import Compress

from app.back.auth import auth_bp
from app.back.models import User, db
from app.back.permissions import login_manager
from app.extensions import limiter
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
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///tngpt.db")

Compress(app)
limiter.init_app(app)
db.init_app(app)

login_manager.init_app(app)
login_manager.login_view = "auth.login_page"

@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    """Charge un utilisateur depuis la session Flask-Login."""
    return db.session.get(User, int(user_id))

app.register_blueprint(bp)
app.register_blueprint(auth_bp)

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "True").lower() in ["true", "1", "t"]
    app.run(host="127.0.0.1", debug=debug_mode, port=8501)
