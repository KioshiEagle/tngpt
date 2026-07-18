from datetime import UTC, datetime

from bcrypt import checkpw, gensalt, hashpw
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

from .permissions import can_manage_users, is_admin

db = SQLAlchemy()

# États d'un document du catalogue.
DOC_INDEXING = "indexing"  # ingestion en cours
DOC_INDEXED = "indexed"  # présent dans Qdrant
DOC_FAILED = "failed"  # l'ingestion a échoué (voir Document.error)
DOC_MISSING = "missing"  # connu du catalogue, absent de Qdrant (désynchronisé)

DOC_ORIGIN_DRIVE = "drive"  # ingéré par la pipeline Google Drive
DOC_ORIGIN_UPLOAD = "upload"  # déposé depuis le panel admin

# États de modération d'un utilisateur.
USER_ACTIVE = "active"  # usage normal
USER_LIMITED = "limited"  # accès conservé, débit fortement réduit
USER_BANNED = "banned"  # sessions coupées, reconnexion refusée


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

    status = db.Column(db.String(20), nullable=False, default=USER_ACTIVE, index=True)
    ban_reason = db.Column(db.String(300), nullable=True)
    # Limite de questions par jour. NULL = valeur par défaut globale (config) ;
    # les administrateurs ne sont jamais plafonnés, quel que soit ce champ.
    quota_daily = db.Column(db.Integer, nullable=True)
    moderated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    moderated_by = db.Column(
        db.Integer, db.ForeignKey("users.user_id"), nullable=True
    )  # qui a pris la sanction (auto-référence)

    conversations = db.relationship(
        "Conversation", back_populates="user", lazy="dynamic"
    )
    queries = db.relationship("Query", back_populates="user", lazy="dynamic")
    moderator = db.relationship(
        "User", remote_side=[user_id], foreign_keys=[moderated_by]
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

    @property
    def is_active(self) -> bool:
        """Un banni ne peut pas ouvrir de session.

        Flask-Login consulte cette propriété dans `login_user()` : la surcharger
        (UserMixin la fixe à True) suffit à refuser toute nouvelle connexion. Les
        sessions déjà ouvertes, elles, sont coupées dans le `user_loader`.
        """
        return self.status != USER_BANNED

    def is_banned(self) -> bool:
        """Indique si l'accès de l'utilisateur est suspendu."""
        return self.status == USER_BANNED

    def is_limited(self) -> bool:
        """Indique si l'utilisateur est en usage restreint."""
        return self.status == USER_LIMITED


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


class Document(db.Model):
    """Un document source présent dans la base vectorielle Qdrant.

    Catalogue de données : reflète ce que contient Qdrant à l'instant t.
    Remplace le fichier processed_files.json, qui était local au conteneur,
    éphémère et invisible depuis l'application web.
    """

    __tablename__ = "documents"

    # Identifiant du document dans Qdrant (payload "source") : l'id Drive pour
    # la pipeline, le nom du fichier déposé pour un upload manuel.
    source_id = db.Column(db.String(128), primary_key=True)
    title = db.Column(db.String(300), nullable=True)
    author = db.Column(db.String(200), nullable=True)
    # Date issue du frontmatter, de format libre et stockée telle quelle dans
    # Qdrant (index KEYWORD) : on la conserve en texte pour rester cohérent.
    doc_date = db.Column(db.String(50), nullable=True)

    chunk_count = db.Column(db.Integer, nullable=False, default=0)
    file_hash = db.Column(db.String(64), nullable=True)

    status = db.Column(db.String(20), nullable=False, default=DOC_INDEXED, index=True)
    origin = db.Column(db.String(20), nullable=False, default=DOC_ORIGIN_DRIVE)
    error = db.Column(db.Text, nullable=True)

    ingested_by = db.Column(
        db.Integer, db.ForeignKey("users.user_id"), nullable=True
    )  # null = pipeline automatique, pas un humain
    ingested_at = db.Column(
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

    uploader = db.relationship("User")

    def __repr__(self) -> str:
        """Représentation lisible du document."""
        return f"Document {self.source_id} — {self.title!r} ({self.status})"


class Query(db.Model):
    """Une question posée à TN-GPT.

    Une ligne par question : c'est l'unité de comptage des quotas d'usage.
    """

    __tablename__ = "queries"

    query_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.user_id"), nullable=False, index=True
    )
    # Clé Groq ayant servi à générer la réponse. NULL = clé de repli (.env) ou
    # question non encore attribuée. Permet de mesurer l'usage par clé.
    groq_key_id = db.Column(
        db.Integer, db.ForeignKey("groq_keys.groq_key_id"), nullable=True, index=True
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


class GroqKey(db.Model):
    """Clé d'API Groq du pool.

    TN-GPT appelle Groq avec l'une de ces clés, choisie en round-robin parmi les
    clés actives, pour qu'aucune ne sature sous le trafic de tous les
    utilisateurs. Le secret (gsk_…) est nécessaire pour appeler Groq : il est
    stocké tel quel, affiché masqué.
    """

    __tablename__ = "groq_keys"

    groq_key_id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(100), nullable=True)
    secret = db.Column(db.String(200), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    request_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    # Ordonne le round-robin (la clé la moins récemment utilisée passe la
    # première) et affiche la récence d'usage.
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=True)

    creator = db.relationship("User", foreign_keys=[created_by])

    @property
    def masked(self) -> str:
        """Représentation masquée du secret pour l'affichage."""
        secret = self.secret or ""
        min_maskable = 10
        if len(secret) <= min_maskable:
            return "•" * len(secret)
        return f"{secret[:6]}…{secret[-4:]}"

    def __repr__(self) -> str:
        """Représentation lisible de la clé."""
        return f"GroqKey {self.masked} ({'active' if self.active else 'inactive'})"
