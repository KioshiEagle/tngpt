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
    queries = db.relationship("Query", back_populates="user", lazy="dynamic")

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


class Query(db.Model):
    """Une question posée à TN-GPT.

    Une ligne par question : c'est l'unité de comptage des quotas d'usage.
    """

    __tablename__ = "queries"

    query_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id"), nullable=False, index=True
    )
    question = db.Column(db.String(500), nullable=False)
    top_k = db.Column(db.Integer, nullable=False)
    # Nombre de chunks réellement retournés : 0 signale une question sans
    # contexte trouvé (sous le seuil de score), signal utile en monitoring.
    result_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )

    user = db.relationship("User", back_populates="queries")
    events = db.relationship(
        "RetrievalEvent", back_populates="query", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        """Représentation lisible de la question."""
        return f"Query {self.query_id} — {self.question[:40]!r}"


class RetrievalEvent(db.Model):
    """Un chunk retrouvé dans Qdrant pour répondre à une question.

    Une ligne par chunk et par question. Alimente le classement des chunks les
    plus utilisés (candidats à la mise en cache).
    """

    __tablename__ = "retrieval_events"

    event_id = db.Column(db.Integer, primary_key=True)
    query_id = db.Column(
        db.Integer, db.ForeignKey("queries.query_id"), nullable=False, index=True
    )
    # Identifiant du point Qdrant : la clé d'agrégation du barchart.
    point_id = db.Column(db.String(64), nullable=False, index=True)
    # Document d'origine (drive_id) et titre, dénormalisés : ils permettent
    # d'afficher les statistiques sans réinterroger Qdrant, et de les conserver
    # même si le document est supprimé de la base vectorielle.
    source_id = db.Column(db.String(128), nullable=True, index=True)
    title = db.Column(db.String(300), nullable=True)
    rank = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Float, nullable=False)
    semantic_score = db.Column(db.Float, nullable=False)
    freshness_score = db.Column(db.Float, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )

    query = db.relationship("Query", back_populates="events")

    def __repr__(self) -> str:
        """Représentation lisible de l'événement."""
        return f"RetrievalEvent {self.point_id} (rang {self.rank})"
