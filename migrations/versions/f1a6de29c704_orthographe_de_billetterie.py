"""orthographe : Responsable billetterie

Revision ID: f1a6de29c704
Revises: e4c07a15b8d3
Create Date: 2026-08-07 13:24:07.882015

Le rôle avait été saisi « Responsable billeterie », avec un seul T. Ce n'est pas
qu'une coquille d'affichage : la reconnaissance compare la question à
l'intitulé, donc « respo billetterie » — l'orthographe correcte, celle que les
gens tapent — ne retrouvait pas le poste.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a6de29c704'
down_revision = 'e4c07a15b8d3'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text("UPDATE roles SET role_name = :apres WHERE role_name = :avant")
        .bindparams(apres="Responsable billetterie", avant="Responsable billeterie")
    )


def downgrade():
    op.execute(
        sa.text("UPDATE roles SET role_name = :apres WHERE role_name = :avant")
        .bindparams(apres="Responsable billeterie", avant="Responsable billetterie")
    )
