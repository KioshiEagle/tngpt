import base64
import hashlib
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_user, logout_user
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from werkzeug.wrappers import Response

from .models import User, db
from .permissions import encode_perms

CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",  # nosec B105
    }
}

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _en_local() -> bool:
    """Dit si la requête courante arrive en http sur une adresse de bouclage.

    Google autorise http sur le bouclage, oauthlib non : c'est la seule
    situation où l'exigence https mérite d'être levée.
    """
    if request.is_secure:
        return False
    return request.host.rsplit(":", 1)[0] in _LOOPBACK


@contextmanager
def _http_tolere() -> Iterator[None]:
    """Lève l'exigence https d'oauthlib le temps d'un échange en local.

    La variable est restaurée en sortie : posée durablement, elle vaudrait
    aussi pour les requêtes suivantes, y compris celles d'un vrai domaine.
    Sûr ici parce qu'un worker gunicorn sync ne sert qu'une requête à la fois.
    """
    if not _en_local():
        yield
        return
    ancien = os.environ.get("OAUTHLIB_INSECURE_TRANSPORT")
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    try:
        yield
    finally:
        if ancien is None:
            os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
        else:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = ancien


@auth_bp.route("/login")
def login_page() -> Response | str:
    """Affiche la page de connexion."""
    if current_user.is_authenticated:
        return redirect("/")
    return render_template("login.html")


@auth_bp.route("/login_google")
def login_google() -> Response:
    """Démarre le flux OAuth Google."""
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)

    redirect_uri = url_for("auth.callback", _external=True)
    flow.redirect_uri = redirect_uri

    code_verifier = secrets.token_urlsafe(96)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    with _http_tolere():
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            prompt="select_account",
            code_challenge=code_challenge,
            code_challenge_method="S256",
        )

    session["oauth_state"] = state
    session["code_verifier"] = code_verifier
    return redirect(authorization_url)


@auth_bp.route("/callback")
def callback() -> Response:
    """Reçoit le code OAuth Google et connecte l'utilisateur."""
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = url_for("auth.callback", _external=True)

    # Valeur par défaut (None) pour éviter un KeyError (500) si la session
    # a expiré ou si la page de callback est rechargée.
    if session.pop("oauth_state", None) != request.args.get("state"):
        abort(403)

    with _http_tolere():
        flow.fetch_token(
            authorization_response=request.url,
            code_verifier=session.pop("code_verifier", None),
        )
    credentials = flow.credentials
    # Tolérance d'horloge : le token Google est parfois daté quelques secondes
    # dans le futur, sans quoi la vérification lève « used too early » (500).
    info = id_token.verify_oauth2_token(
        credentials.id_token,
        Request(),
        CLIENT_CONFIG["web"]["client_id"],
        clock_skew_in_seconds=10,
    )

    mail = info.get("email")
    picture = info.get("picture")

    if not mail or not mail.endswith("@telecomnancy.net"):
        abort(403)

    user = User.query.filter_by(user_mail=mail).first()

    if not user:
        full_name = mail.split("@")[0]
        parts = full_name.split(".", 1)
        firstname_lower = parts[0]
        surname_lower = parts[1] if len(parts) > 1 else parts[0]
        firstname = firstname_lower[0].upper() + firstname_lower[1:]
        name = surname_lower[0].upper() + surname_lower[1:]
        user = User(
            user_mail=mail,
            user_firstname=firstname,
            user_surname=name,
            user_permissions=encode_perms([]),
            first_login_at=datetime.now(UTC),
            user_picture=picture,
        )
        db.session.add(user)
    else:
        user.user_picture = picture

    db.session.commit()

    # login_user() refuse un banni (`is_active` faux) : sans ce traitement il
    # rebondirait sur le login sans jamais savoir pourquoi.
    if not login_user(user):
        reason = user.ban_reason or "Aucun motif précisé."
        flash(f"Votre accès à TN-GPT a été suspendu. Motif : {reason}", "banned")
        return redirect(url_for("auth.login_page"))

    return redirect("/")


@auth_bp.route("/logout")
def logout() -> Response:
    """Déconnecte l'utilisateur."""
    logout_user()
    return redirect(url_for("auth.login_page"))
