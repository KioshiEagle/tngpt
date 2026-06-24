import os

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session, abort
from flask_login import current_user, login_user, logout_user, login_required

from forms import LoginForm
from models import User

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


os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

@auth_bp.route("/login", methods=["GET", "POST"])
def login_page():

    if current_user.is_authenticated:
        return redirect('/')

    form = LoginForm()
    user = None

    # Special account check
    if form.is_submitted() and form.usermail.data in current_app.config['special_accounts_mails']:
        pass
    
    elif not form.validate_on_submit():
        return render_template('login.html', form=form)

    if user is None:   # Not equal to None when special account
        user: User = User.query.where(Utilisateur.user_mail == form.usermail.data).scalar()
    if user is None or not user.check_password(form.password.data):
        flash("Mot de passe invalide", "danger")
        return redirect(url_for('auth.login_page'))
    
    login_user(user, remember=form.remember_me.data)

    next_page = request.args.get('next') # redirection

    if not next_page or urlsplit(next_page).netloc != '':
        next_page = '/'

    flash(f'Utilisateur⋅trice {user.user_firstname} {user.user_surname} connecté⋅e', "success")
    return redirect(next_page)


