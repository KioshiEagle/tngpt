"""reglages globaux, table settings

Porte les bascules d'apparence posees depuis le panel admin, a commencer par
l'habillage flamme du mode brainrot. Cle/valeur : une bascule de plus ne
demande pas de migration. Aucune ligne semee, l'absence vaut « eteint ».

Revision ID: a3f70c1d5e88
Revises: cb1824b0b1b5
Create Date: 2026-09-06 22:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3f70c1d5e88'
down_revision = 'cb1824b0b1b5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('settings',
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('value', sa.String(length=200), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['users.user_id'], ),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade():
    op.drop_table('settings')
