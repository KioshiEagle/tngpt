import os

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session, abort
from flask_login import current_user, login_user, logout_user, login_required

from app.forms import LoginForm, RegisterForm, UpdateUserForm, ChangePWForm, ChangeEmailForm

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

    #form = LoginForm()