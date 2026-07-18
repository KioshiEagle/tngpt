import click
from flask import Flask
from flask.cli import with_appcontext

from .back.models import User, db
from .back.permissions import PERM_ADMIN, encode_perms, list_perm


def _get_user(email: str) -> User:
    """Récupère un utilisateur par e-mail ou échoue proprement."""
    user = db.session.scalar(db.select(User).filter_by(user_mail=email))
    if user is None:
        msg = f"Aucun utilisateur avec l'e-mail {email!r}."
        raise click.ClickException(msg)
    return user


@click.command("make-admin")
@click.argument("email")
@with_appcontext
def make_admin(email: str) -> None:
    """Accorde la permission Administration à EMAIL.

    Sert à créer le premier administrateur : les suivants se désignent
    depuis le panel admin.
    """
    user = _get_user(email)
    user.user_permissions |= encode_perms([PERM_ADMIN])
    db.session.commit()
    click.echo(
        f"{email} est administrateur — permissions : {', '.join(list_perm(user))}"
    )


@click.command("revoke-admin")
@click.argument("email")
@with_appcontext
def revoke_admin(email: str) -> None:
    """Retire la permission Administration à EMAIL."""
    user = _get_user(email)
    user.user_permissions &= ~encode_perms([PERM_ADMIN])
    db.session.commit()
    click.echo(f"{email} n'est plus administrateur.")


def register_cli(app: Flask) -> None:
    """Enregistre les commandes CLI de l'application."""
    app.cli.add_command(make_admin)
    app.cli.add_command(revoke_admin)
