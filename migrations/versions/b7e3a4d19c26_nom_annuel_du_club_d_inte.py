"""nom annuel du club d'inté : slug non générique et anciens noms en alias

Revision ID: b7e3a4d19c26
Revises: f1a6de29c704
Create Date: 2026-08-09 18:05:00.000000

Le club d'inté (id 6) se renomme chaque février : « L'Empire Inté'Galactique »
en 2025, « Les Intéductibles Gaulois » en 2026. Deux conséquences, corrigées
ici.

D'abord son slug valait « inte », c'est-à-dire le mot par lequel toute l'école
désigne la *période* d'intégration. `clubs.match_entites` compare le slug comme
un mot entier : « Event Inté BDS : Paintball » ou « c'est quoi l'inté ? »
reconnaissaient donc le club, et sa fiche partait en tête du contexte sous
l'en-tête FICHE OFFICIELLE, que le prompt dit faire autorité sur toute archive.
Un slug générique suffisait à faire répondre le chat à côté.

Ensuite ses noms passés n'étaient nulle part : une question portant sur
« l'Empire Inté'Galactique » ne trouvait rien, et les archives de 2025 se
voyaient étiquetées du nom de 2026. Les alias les portent désormais — liste à
compléter chaque février, au changement de nom.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b7e3a4d19c26"
down_revision = "f1a6de29c704"
branch_labels = None
depends_on = None

CLUB_INTE = 6

ANCIEN_SLUG = "inte"
NOUVEAU_SLUG = "inteductibles"

# Les alias déjà en place (migration c6a4e1b83f27), augmentés des noms portés
# les années précédentes. « Inté'Galactique » seul est la forme qui tague les
# sujets de mails de 2025.
ANCIENS_ALIAS = "Intégration|Club Intégration|Intéductibles|Intéductibles Gaulois"
NOUVEAUX_ALIAS = (
    "Intégration|Club Intégration|Intéductibles|Intéductibles Gaulois|"
    "L'Empire Inté'Galactique|Empire Inté'Galactique|Inté'Galactique"
)


def upgrade():
    """Remplace le slug générique et ajoute les anciens noms aux alias."""
    op.execute(
        sa.text("UPDATE clubs SET slug = :slug, aliases = :alias WHERE club_id = :id")
        .bindparams(slug=NOUVEAU_SLUG, alias=NOUVEAUX_ALIAS, id=CLUB_INTE)
    )


def downgrade():
    """Rétablit le slug et les alias précédents."""
    op.execute(
        sa.text("UPDATE clubs SET slug = :slug, aliases = :alias WHERE club_id = :id")
        .bindparams(slug=ANCIEN_SLUG, alias=ANCIENS_ALIAS, id=CLUB_INTE)
    )
