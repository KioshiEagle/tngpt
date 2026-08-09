"""corrections de patronymes signalées par le CETEN

Revision ID: a91d3f60c25e
Revises: f512c8ab6d70
Create Date: 2026-08-06 18:54:37.229104

Le CSV du CETEN écrivait trois personnes de deux façons. La migration
précédente les avait laissées telles quelles, faute de source pour trancher.
Les graphies retenues ici sont celles confirmées par le CETEN — ce sont donc
des corrections, pas des suppositions.

À noter pour Mathilde : c'est la forme AVEC le R qui est la bonne, à rebours de
ce que la majorité des occurrences laissait croire.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a91d3f60c25e'
down_revision = 'f512c8ab6d70'
branch_labels = None
depends_on = None


# (graphie erronée, graphie retenue)
CORRECTIONS = [
    ("CHAMPMARTIN Errwan", "CHAMPMARTIN Erwan"),
    ("TRINDNER Sebastien", "TRINDER Sébastien"),
    ("NOTHDURFT Mathilde", "NORTHDURFT Mathilde"),
]

# Les deux tables de bureaux : une même personne peut siéger des deux côtés.
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
    # Remet les graphies du CSV d'origine. Sans effet si la personne n'occupait
    # qu'un poste déjà corrigé ailleurs.
    _renommer([(apres, avant) for avant, apres in CORRECTIONS])
