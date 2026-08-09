"""Fusion des têtes « conversations » et « clubs ».

Les branches JC/dev@conversations et Tobias/dev@clubs-info ont toutes deux
branché depuis fec317213eee : leur merge a laissé deux têtes Alembic, et
`flask db upgrade` — lancé par le CMD du Dockerfile avant gunicorn — refusait
de démarrer. Cette révision ne touche pas au schéma, elle ne fait que rejoindre
les deux lignées.

Revision ID: cb436cf47974
Revises: 344c99caa371, b7e3a4d19c26
Create Date: 2026-08-09 18:56:54.581883

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cb436cf47974'
down_revision = ('344c99caa371', 'b7e3a4d19c26')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
