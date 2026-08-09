"""bureaux d'associations : table asso_roles, les assos sortent de clubs

Revision ID: e07b45fd913a
Revises: c4e91a72d508
Create Date: 2026-08-06 17:31:08.442915

Les cinq associations figuraient dans `clubs` faute d'un endroit où porter un
bureau. Le modèle y voyait donc des clubs, et les fiches sortaient « CETEN —
rattaché à CETEN ». `asso_roles` leur donne leur propre table de bureaux ;
`assos` récupère le slug et la description ; les lignes correspondantes de
`clubs` disparaissent.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e07b45fd913a'
down_revision = 'c4e91a72d508'
branch_labels = None
depends_on = None


# Les cinq lignes de `clubs` à reverser dans `assos` : (club_id, asso_id).
# Le slug et la description sont recopiés plutôt que réécrits ici — ils restent
# la propriété de la migration qui les a introduits.
REVERSEMENT = [(1, 0), (2, 1), (3, 2), (4, 4), (5, 5)]


def upgrade():
    with op.batch_alter_table('assos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slug', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))
        batch_op.create_index(batch_op.f('ix_assos_slug'), ['slug'], unique=False)

    op.create_table('asso_roles',
    sa.Column('asso_role_id', sa.Integer(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.Column('asso_id', sa.Integer(), nullable=False),
    sa.Column('mandat', sa.String(length=9), nullable=False),
    sa.Column('personne', sa.String(length=150), nullable=False),
    sa.ForeignKeyConstraint(['asso_id'], ['assos.asso_id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['roles.role_id'], ),
    sa.PrimaryKeyConstraint('asso_role_id'),
    sa.UniqueConstraint('role_id', 'asso_id', 'mandat', 'personne',
                        name='uq_asso_roles_poste_personne')
    )
    with op.batch_alter_table('asso_roles', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_asso_roles_asso_id'), ['asso_id'], unique=False
        )

    for club_id, asso_id in REVERSEMENT:
        op.execute(
            "UPDATE assos SET slug = c.slug, description = c.description "
            f"FROM clubs c WHERE c.club_id = {club_id} AND assos.asso_id = {asso_id}"
        )

    # Un bureau a pu être saisi à la main entre-temps : on le transporte avant
    # de retirer les clubs, sans quoi la clé étrangère bloquerait la suppression.
    for club_id, asso_id in REVERSEMENT:
        op.execute(
            "INSERT INTO asso_roles (role_id, asso_id, mandat, personne) "
            f"SELECT role_id, {asso_id}, mandat, personne "
            f"FROM club_roles WHERE club_id = {club_id} "
            "ON CONFLICT ON CONSTRAINT uq_asso_roles_poste_personne DO NOTHING"
        )

    anciens = ", ".join(str(club_id) for club_id, _ in REVERSEMENT)
    op.execute(f"DELETE FROM club_roles WHERE club_id IN ({anciens})")
    op.execute(f"DELETE FROM clubs WHERE club_id IN ({anciens})")


def downgrade():
    # Les associations redeviennent des clubs, rattachées à elles-mêmes.
    for club_id, asso_id in REVERSEMENT:
        op.execute(
            "INSERT INTO clubs (club_id, club_name, slug, asso_id, description) "
            f"SELECT {club_id}, asso_name, slug, {asso_id}, description "
            f"FROM assos WHERE asso_id = {asso_id}"
        )
        op.execute(
            "INSERT INTO club_roles (role_id, club_id, mandat, personne) "
            f"SELECT role_id, {club_id}, mandat, personne "
            f"FROM asso_roles WHERE asso_id = {asso_id} "
            "ON CONFLICT ON CONSTRAINT uq_club_roles_poste_personne DO NOTHING"
        )

    with op.batch_alter_table('asso_roles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_asso_roles_asso_id'))

    op.drop_table('asso_roles')
    with op.batch_alter_table('assos', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assos_slug'))
        batch_op.drop_column('description')
        batch_op.drop_column('slug')
