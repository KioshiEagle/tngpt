"""moderation : statut, motif et auteur de la sanction

Revision ID: 809142aa9d20
Revises: 32f1159abbf3
Create Date: 2026-07-14 19:31:59.176771

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "809142aa9d20"
down_revision = "32f1159abbf3"
branch_labels = None
depends_on = None

_FK_MODERATED_BY = "fk_users_moderated_by_users"


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        # server_default : la table contient déjà des utilisateurs, et une colonne
        # NOT NULL sans valeur par défaut ne peut pas être remplie pour eux.
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="active",
            )
        )
        batch_op.add_column(
            sa.Column("ban_reason", sa.String(length=300), nullable=True)
        )
        batch_op.add_column(
            sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("moderated_by", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_users_status"), ["status"], unique=False)
        batch_op.create_foreign_key(
            _FK_MODERATED_BY, "users", ["moderated_by"], ["user_id"]
        )

    # Les lignes existantes sont remplies : on retire le défaut côté base pour que
    # la valeur par défaut du modèle reste la seule source de vérité.
    op.alter_column("users", "status", server_default=None)


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(_FK_MODERATED_BY, type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_users_status"))
        batch_op.drop_column("moderated_by")
        batch_op.drop_column("moderated_at")
        batch_op.drop_column("ban_reason")
        batch_op.drop_column("status")
