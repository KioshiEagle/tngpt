"""ticket d'or : comptes dispensés du jeu (2A/3A)

Revision ID: a1c4e8f7b520
Revises: f2c6a8d1e397
Create Date: 2026-09-29 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c4e8f7b520'
down_revision = 'f2c6a8d1e397'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(
            sa.Column(
                'ticket_dor_dispense',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('ticket_dor_dispense')
