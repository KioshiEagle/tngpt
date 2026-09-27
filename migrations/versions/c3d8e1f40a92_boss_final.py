"""boss final : partie de chaque joueur

Revision ID: c3d8e1f40a92
Revises: 5e2b9d71c4a3
Create Date: 2026-09-27 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d8e1f40a92'
down_revision = '5e2b9d71c4a3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ctf_boss_parties',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('phase', sa.String(length=20), nullable=False),
    sa.Column('coupe_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('debranche_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ),
    sa.PrimaryKeyConstraint('user_id')
    )


def downgrade():
    op.drop_table('ctf_boss_parties')
