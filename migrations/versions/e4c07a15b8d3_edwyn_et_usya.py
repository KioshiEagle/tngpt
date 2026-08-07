"""corrections : CHEVEREAU Edwyn et USYA Amélie

Revision ID: e4c07a15b8d3
Revises: d8b2f04ce591
Create Date: 2026-08-07 13:02:19.550431

Deux points laissés en suspens à la migration précédente, tranchés par le CETEN :

- « CHEVEREAU Edwin » (respo billetterie d'Anim'Est) et « CHEVEREAU Edwyn »
  (secrétaire de DawaTN) sont la même personne : Edwyn.
- « Amélie Usya » n'avait pas de patronyme en capitales, donc pas d'ordre
  déductible. C'est USYA le nom de famille.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4c07a15b8d3'
down_revision = 'd8b2f04ce591'
branch_labels = None
depends_on = None


# (graphie à corriger, graphie retenue)
CORRECTIONS = [
    ("CHEVEREAU Edwin", "CHEVEREAU Edwyn"),
    ("Amélie Usya (exté)", "USYA Amélie (exté)"),
]

TABLES = ("club_roles", "asso_roles")


def _renommer(couples):
    for avant, apres in couples:
        for table in TABLES:
            op.execute(
                sa.text(
                    f"UPDATE {table} SET personne = :apres WHERE personne = :avant"
                ).bindparams(apres=apres, avant=avant)
            )


def upgrade():
    _renommer(CORRECTIONS)


def downgrade():
    _renommer([(apres, avant) for avant, apres in CORRECTIONS])
