from datetime import UTC, datetime

from bcrypt import checkpw, gensalt, hashpw
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

from .permissions import can_manage_users, is_admin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """Modèle principal gérant l'authentification et l'identité."""

    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True)
    user_firstname = db.Column(db.String(100), nullable=False)
    user_surname = db.Column(db.String(100), nullable=False)
    user_mail = db.Column(db.String(150), nullable=False, unique=True)
    user_pwd = db.Column(db.String(255), nullable=False)
    user_permissions = db.Column(db.Integer, nullable=False, default=0)
    first_login_at = db.Column(db.DateTime(timezone=True), nullable=True)
    theme = db.Column(db.String(5), nullable=False, default="light")
    user_picture = db.Column(db.String(500), nullable=True)

    conversations = db.relationship(
        "Conversation", back_populates="user", lazy="dynamic"
    )

    def __repr__(self) -> str:
        """Représentation lisible de l'utilisateur."""
        return f"User {self.user_surname} {self.user_firstname}"

    def set_password(self, password: str) -> None:
        """Hache et stocke le mot de passe."""
        self.user_pwd = hashpw(password.encode("utf-8"), gensalt()).decode("utf-8")

    def check_password(self, pw_candidate: str) -> bool:
        """Vérifie le mot de passe contre le hash bcrypt stocké."""
        if not self.user_pwd:
            return False
        stored_hash = self.user_pwd
        if isinstance(stored_hash, str):
            stored_hash = stored_hash.encode("utf-8")
        try:
            return checkpw(pw_candidate.encode("utf-8"), stored_hash)
        except ValueError:
            return False

    def get_id(self) -> str:
        """Retourne l'identifiant de l'utilisateur sous forme de chaîne."""
        return str(self.user_id)

    def can_manage_users(self) -> bool:
        """Vérifie si l'utilisateur peut gérer les autres utilisateurs."""
        return can_manage_users(self)

    def is_admin(self) -> bool:
        """Vérifie si l'utilisateur est administrateur."""
        return is_admin(self)


class Conversation(db.Model):
    """Conversation entre un utilisateur et TN-GPT."""

    __tablename__ = "conversations"

    conversation_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    title = db.Column(db.String(200), nullable=True)
    messages = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user = db.relationship("User", back_populates="conversations")

    def __repr__(self) -> str:
        """Représentation lisible de la conversation."""
        return f"Conversation {self.conversation_id} — {self.title!r}"
