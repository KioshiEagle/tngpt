import os, secrets

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session, abort
from flask_login import current_user, login_user, logout_user, login_required
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from urllib.parse import urlsplit

from forms import LoginForm
from models import User, db
from permissions import encode_perms

CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

AUTH_URL = 'https://accounts.google.com/o/oauth2/auth'

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

if os.environ.get('FLASK_ENV') == 'development':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

@auth_bp.route("/login", methods=["GET", "POST"])
def login_page():

    if current_user.is_authenticated:
        return redirect('/')

    form = LoginForm()
    user = None

    # Special account check
    if form.is_submitted() and form.user_mail.data in current_app.config['special_accounts_mails']:
        pass
    
    elif not form.validate_on_submit():
        return render_template('login.html', form=form)

    if user is None:   # Not equal to None when special account
        user: User = User.query.where(User.user_mail == form.user_mail.data).scalar()
    if user is None or not user.check_password(form.password.data):
        flash("Mot de passe invalide", "danger")
        return redirect(url_for('auth.login_page'))
    
    login_user(user, remember=form.remember_me.data)

    next_page = request.args.get('next') # redirection

    if not next_page or urlsplit(next_page).netloc != '':
        next_page = '/'

    flash(f'Utilisateur⋅trice {user.user_firstname} {user.user_surname} connecté⋅e', "success")
    return redirect(next_page)

@auth_bp.route("/login_google")
def login_google():
    flow = Flow.from_client_config(
        CLIENT_CONFIG, 
        scopes=["https://www.googleapis.com/auth/userinfo.email", "openid"]
    )

    flow.redirect_uri = url_for('auth.callback', _external=True)

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='select_account'
    )

    session['oauth_state'] = state 
    return redirect(authorization_url)

@auth_bp.route("/callback")
def callback():
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=["https://www.googleapis.com/auth/userinfo.email", "openid"])
    flow.redirect_uri = url_for('auth.callback', _external=True)

    if not session.get('oauth_state') == request.args.get('state'):
        abort(403)

    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    # fetch user info (mail) via id_token
    info = id_token.verify_oauth2_token(credentials.id_token, Request(), CLIENT_CONFIG['web']['client_id'])

    mail = info.get('email')

    if not mail or not mail.endswith("@telecomnancy.net"):
        abort(403)
    user = User.query.filter_by(user_mail=mail).first()

    if not user:
        full_name = mail.split("@")[0]
        parts = full_name.split(".", 1)
        firstname_lower = parts[0]
        surname_lower = parts[1] if len(parts) > 1 else parts[0]
        firstname, name = firstname_lower[0].upper() + firstname_lower[1:], surname_lower[0].upper() + surname_lower[1:]
        user = User(
            user_mail=mail,
            user_firstname=firstname,
            user_surname=name,
            user_permissions=encode_perms([]) # Aucune permission par défaut
        )

        token_impossible = secrets.token_urlsafe(32)
        user.set_password(token_impossible)
        # Google defines pwd by itself
        db.session.add(user)
        db.session.commit()
        flash("Compte créé automatiquement avec Google.", "info")
    
    login_user(user)
    return redirect("/")

@auth_bp.route('/logout')
def logout():
    logout_user()
    flash('Utilisateur⋅trice déconnecté⋅e', 'info')
    return redirect(url_for('auth.login_page'))



