"""réglages en texte : répliques du boss réécrites depuis le panel

Revision ID: e5f1b3a7c260
Revises: d7a2c9e5b184
Create Date: 2026-09-27 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f1b3a7c260'
down_revision = 'd7a2c9e5b184'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('settings') as batch_op:
        batch_op.alter_column('value', existing_type=sa.String(length=200), type_=sa.Text(), existing_nullable=False)


def downgrade():
    with op.batch_alter_table('settings') as batch_op:
        batch_op.alter_column('value', existing_type=sa.Text(), type_=sa.String(length=200), existing_nullable=False)
