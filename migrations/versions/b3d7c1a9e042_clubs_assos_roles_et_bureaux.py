"""clubs, assos, roles et bureaux

Revision ID: b3d7c1a9e042
Revises: fec317213eee
Create Date: 2026-08-06 14:12:03.881204

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d7c1a9e042'
down_revision = 'fec317213eee'
branch_labels = None
depends_on = None


# Énumérations amorcées ici parce qu'elles font partie du schéma : les
# identifiants sont fixes et référencés à la main dans les INSERT de clubs et de
# bureaux. `clubs` et `club_roles` restent vides, elles sont saisies à la main.
ASSOS = [
    {'asso_id': 0, 'asso_name': 'CETEN'},
    {'asso_id': 1, 'asso_name': "Humani'TN"},
    {'asso_id': 4, 'asso_name': 'BDS'},
    {'asso_id': 5, 'asso_name': 'TNS'},
]

ROLES = [
    {'role_id': 0, 'role_name': 'Président'},
    {'role_id': 1, 'role_name': 'Vice-président'},
    {'role_id': 2, 'role_name': 'Trésorier'},
    {'role_id': 3, 'role_name': 'Secrétaire'},
    {'role_id': 4, 'role_name': 'Responsable communication'},
]


def upgrade():
    op.create_table('assos',
    sa.Column('asso_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('asso_name', sa.String(length=100), nullable=False),
    sa.PrimaryKeyConstraint('asso_id'),
    sa.UniqueConstraint('asso_name')
    )
    op.create_table('roles',
    sa.Column('role_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('role_name', sa.String(length=80), nullable=False),
    sa.PrimaryKeyConstraint('role_id'),
    sa.UniqueConstraint('role_name')
    )
    op.create_table('clubs',
    sa.Column('club_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('club_name', sa.String(length=120), nullable=False),
    sa.Column('slug', sa.String(length=40), nullable=True),
    sa.Column('asso_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['asso_id'], ['assos.asso_id'], ),
    sa.PrimaryKeyConstraint('club_id')
    )
    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_clubs_slug'), ['slug'], unique=False)

    # Clé primaire technique, et non (role_id, club_id, mandat) : un même poste
    # peut avoir plusieurs titulaires — Anim'Est a deux présidents, le CETEN deux
    # responsables communication. Seule la répétition exacte d'une personne sur
    # un même poste est interdite, par la contrainte d'unicité.
    op.create_table('club_roles',
    sa.Column('club_role_id', sa.Integer(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.Column('club_id', sa.Integer(), nullable=False),
    sa.Column('mandat', sa.String(length=9), nullable=False),
    sa.Column('personne', sa.String(length=150), nullable=False),
    sa.ForeignKeyConstraint(['club_id'], ['clubs.club_id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['roles.role_id'], ),
    sa.PrimaryKeyConstraint('club_role_id'),
    sa.UniqueConstraint('role_id', 'club_id', 'mandat', 'personne',
                        name='uq_club_roles_poste_personne')
    )
    with op.batch_alter_table('club_roles', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_club_roles_club_id'), ['club_id'], unique=False
        )

    op.bulk_insert(
        sa.table('assos',
                 sa.column('asso_id', sa.Integer),
                 sa.column('asso_name', sa.String)),
        ASSOS,
    )
    op.bulk_insert(
        sa.table('roles',
                 sa.column('role_id', sa.Integer),
                 sa.column('role_name', sa.String)),
        ROLES,
    )


def downgrade():
    with op.batch_alter_table('club_roles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_club_roles_club_id'))

    op.drop_table('club_roles')
    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clubs_slug'))

    op.drop_table('clubs')
    op.drop_table('roles')
    op.drop_table('assos')
