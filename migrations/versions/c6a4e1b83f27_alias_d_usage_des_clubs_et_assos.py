"""appellations d'usage : colonne aliases sur clubs et assos

Revision ID: c6a4e1b83f27
Revises: a91d3f60c25e
Create Date: 2026-08-07 11:31:52.604118

« Qui est le président d'Abso ? » ne trouvait rien : le club s'appelle
Abso'Ludique en base et son slug est absoludique, or personne ne dit ça. Le nom
officiel et le slug ne suffisent pas — il faut les appellations réelles.

Les alias sont séparés par « | ». Ceux inscrits ici viennent de sources
existantes : le CSV des bureaux du CETEN (qui emploie les noms d'usage) et les
sigles développés par la plaquette. Rien n'est inventé.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6a4e1b83f27'
down_revision = 'a91d3f60c25e'
branch_labels = None
depends_on = None


# (club_id, alias séparés par « | »)
ALIAS_CLUBS = [
    (6, "Intégration|Club Intégration|Intéductibles|Intéductibles Gaulois"),
    (8, "Studio TN"),
    (9, "Chok Bar|Chokbar"),
    (11, "Club Gala"),
    (13, "Abso|Club Abso|Absoludique"),
    (14, "Tek TN|TekTN"),
    (16, "Telegame Design|Telegame|TéléGame Design"),
    (17, "Marché|Marché de Telecom"),
    (18, "Telecom Cooking|Cooking"),
    (19, "Gaming"),
    (20, "SDF|Supers Dégommeurs de Fromage|Dégommeurs de Fromage"),
    (21, "Bravo"),
    (26, "Oenologie|Œnologie"),
    (27, "Brasserie|Brewery Club"),
    (28, "Café'lecom|Cafe lecom|Café lecom|Cafelecom"),
    (30, "Instant Thé|L'Instant The|Instant The"),
    (31, "Mini'Tel|Mini Tel|Minitel|Club Journal"),
    (34, "All In TN|All In|Allin"),
    (36, "Tourist'TN|Touristn|Tourist TN"),
]

# Les cinq associations : sigles et formes développées que la plaquette donne.
ALIAS_ASSOS = [
    (0, "BDE|Bureau Des Élèves|Cercle des Élèves de TELECOM Nancy"),
    (4, "Bureau Des Sports"),
    (5, "Telecom Nancy Services|TN Services"),
]


def upgrade():
    for table in ("clubs", "assos"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('aliases', sa.Text(), nullable=True))

    for cible, colonne, lignes in (
        ("clubs", "club_id", ALIAS_CLUBS),
        ("assos", "asso_id", ALIAS_ASSOS),
    ):
        for identifiant, alias in lignes:
            op.execute(
                sa.text(
                    f"UPDATE {cible} SET aliases = :a WHERE {colonne} = :i"
                ).bindparams(a=alias, i=identifiant)
            )


def downgrade():
    for table in ("clubs", "assos"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('aliases')
