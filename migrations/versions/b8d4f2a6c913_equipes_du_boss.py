"""boss final : équipes posées dans le panel, à la place des câbles du Pi

Revision ID: b8d4f2a6c913
Revises: a1c4e8f7b520
Create Date: 2026-09-29 18:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b8d4f2a6c913"
down_revision = "a1c4e8f7b520"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ctf_boss_membres",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lettre", sa.String(length=1), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    with op.batch_alter_table("ctf_boss_parties") as batch_op:
        batch_op.drop_column("cable")
    # Réglages du Pi devenus sans objet : le flag de l'acte 2 est sur les clés.
    op.execute(
        "DELETE FROM settings WHERE key IN ('ctf_boss_flag_acte_2', 'ctf_boss_cables')"
    )


def downgrade():
    with op.batch_alter_table("ctf_boss_parties") as batch_op:
        batch_op.add_column(sa.Column("cable", sa.Integer(), nullable=True))
    op.drop_table("ctf_boss_membres")
