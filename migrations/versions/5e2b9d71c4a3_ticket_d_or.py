"""ticket d'or : partie, propositions et conversations de jeu

Revision ID: 5e2b9d71c4a3
Revises: a3f70c1d5e88
Create Date: 2026-09-26 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5e2b9d71c4a3'
down_revision = 'a3f70c1d5e88'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ticket_dor',
    sa.Column('ticket_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('cible', sa.String(length=150), nullable=True),
    sa.Column('code', sa.String(length=100), nullable=True),
    sa.Column('indices', sa.Text(), nullable=True),
    sa.Column('ouvert', sa.Boolean(), nullable=False),
    sa.Column('gagnant_id', sa.Integer(), nullable=True),
    sa.Column('gagne_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['gagnant_id'], ['users.user_id'], ),
    sa.ForeignKeyConstraint(['updated_by'], ['users.user_id'], ),
    sa.PrimaryKeyConstraint('ticket_id')
    )
    op.create_table('ticket_dor_propositions',
    sa.Column('proposition_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('texte', sa.String(length=100), nullable=False),
    sa.Column('juste', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ),
    sa.PrimaryKeyConstraint('proposition_id')
    )
    op.create_index(op.f('ix_ticket_dor_propositions_user_id'), 'ticket_dor_propositions', ['user_id'], unique=False)
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.add_column(sa.Column('jeu', sa.String(length=20), nullable=True))
        batch_op.create_index(batch_op.f('ix_conversations_jeu'), ['jeu'], unique=False)


def downgrade():
    with op.batch_alter_table('conversations') as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversations_jeu'))
        batch_op.drop_column('jeu')
    op.drop_index(op.f('ix_ticket_dor_propositions_user_id'), table_name='ticket_dor_propositions')
    op.drop_table('ticket_dor_propositions')
    op.drop_table('ticket_dor')
