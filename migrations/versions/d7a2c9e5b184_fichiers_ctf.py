"""fichiers des chals ctf : voix et photo du boss final

Revision ID: d7a2c9e5b184
Revises: c3d8e1f40a92
Create Date: 2026-09-27 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7a2c9e5b184'
down_revision = 'c3d8e1f40a92'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ctf_fichiers',
    sa.Column('nom', sa.String(length=50), nullable=False),
    sa.Column('contenu', sa.LargeBinary(), nullable=False),
    sa.Column('mimetype', sa.String(length=50), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['users.user_id'], ),
    sa.PrimaryKeyConstraint('nom')
    )


def downgrade():
    op.drop_table('ctf_fichiers')
