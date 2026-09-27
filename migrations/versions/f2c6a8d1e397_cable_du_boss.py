"""boss final : câble du Pi attribué à chaque joueur de l'acte 2

Revision ID: f2c6a8d1e397
Revises: e5f1b3a7c260
Create Date: 2026-09-27 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2c6a8d1e397'
down_revision = 'e5f1b3a7c260'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ctf_boss_parties') as batch_op:
        batch_op.add_column(sa.Column('cable', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('ctf_boss_parties') as batch_op:
        batch_op.drop_column('cable')
