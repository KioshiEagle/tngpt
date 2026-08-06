"""bureaux 2026-2027 des clubs, depuis le CSV du CETEN

Revision ID: f512c8ab6d70
Revises: e07b45fd913a
Create Date: 2026-08-06 18:22:14.706318

Source : « Bureaux des clubs 2026 - Overview.csv ». La colonne « Objet du club »
est volontairement ignorée : les descriptions restent celles de la plaquette.

Trois graphies ont été unifiées, parce qu'elles ne différaient que par un accent
ou un tiret et désignent la même personne (LUKUMUENA Aïcha, ROMERA-ROMARY
Esteban, TRINDER Sébastien). Trois autres écarts ressemblent à des fautes de
frappe sur des patronymes — CHAMPMARTIN Errwan, TRINDNER Sebastien, NORTHDURFT
Mathilde — et sont laissés tels quels : corriger le nom de quelqu'un demande une
source, pas une intuition.

Les noms sont réordonnés en « NOM Prénom », la forme des archives : le CSV mêle
les deux ordres, le patronyme y est toujours en capitales.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f512c8ab6d70'
down_revision = 'e07b45fd913a'
branch_labels = None
depends_on = None


# Clubs du CSV absents de la plaquette : créés ici, sans description (la
# plaquette ne les présente pas) ni logo.
NOUVEAUX_CLUBS = [(38, 'BarberTN'),
 (39, 'BDF'),
 (40, "Bullosi'TN"),
 (41, "Caps'TN"),
 (42, 'DawaTN'),
 (43, 'LGBTN'),
 (44, "Paté'N"),
 (45, 'TN24')]

META_CLUBS = [(13, 'Loisirs', 'club-abso-bureau@telecomnancy.net', None),
 (22, 'Loisirs', 'club-algo-bureau@telecomnancy.net', None),
 (10, 'Événementiel', None, None),
 (34, 'Loisirs', 'club-all-in-tn-bureau@telecomnancy.net', None),
 (33, 'Événementiel', 'club-amphi-suze-bureau@telecomnancy.net', None),
 (9, 'Services', 'club-bar-bureau@telecomnancy.net', None),
 (38, 'Services', 'club-barberTN@telecomnancy.net', '01/10/24'),
 (7, 'Événementiel', 'club-bda-bureau@telecomnancy.net', None),
 (39, 'Événementiel', None, '16/09/2025'),
 (27, 'Événementiel', 'club-brasserie-bureau@telecomnancy.net', None),
 (21, 'Événementiel', 'club-bravo-bureau@telecomnancy.net', None),
 (37, 'Événementiel', 'club-breizhTN-bureau@telecomnancy.net', None),
 (25, 'Loisirs', None, '02/12/2025'),
 (40, 'Événementiel', 'club-bullositn-bureau@telecomnancy.net', '20/05/2025'),
 (28, 'Événementiel', 'club-cafelecom-bureau@telecomnancy.net', None),
 (41, 'Événementiel', 'club-caps-tn-bureau@telecomnancy.net', '05/03/24'),
 (32, 'Loisirs', 'club-creatn-bureau@telecomnancy.net', None),
 (42, 'Loisirs', 'club-karttn-bureau@telecomnancy.net', None),
 (29, 'Événementiel', "club-elsass'tn-bureau@telecomnancy.net", '21/10/2025'),
 (24, 'Loisirs', "club-fashion'tn-bureau@telecomnancy.net", '23/09/2025'),
 (11, 'Événementiel', 'club-gala-bureau@telecomnancy.net', None),
 (19, 'Loisirs', 'club-gamingtn-bureau@telecomnancy.net', None),
 (15, 'Loisirs', 'club-hackintn-bureau@telecomnancy.net', None),
 (30, 'Événementiel', 'club-instant-the-bureau@telecomnancy.net', None),
 (6, 'Événementiel', 'club-integration-bureau@telecomnancy.net', None),
 (35, 'Loisirs', 'club-baroudeurs-bureau@telecomnancy.net', None),
 (43, 'Événementiel', 'club-lgbtn-bureau@telecomnancy.net', None),
 (17, 'Services', 'club-marche-2-telecom-bureau@telecomnancy.net', None),
 (31, 'Services', 'club-minitel-bureau@telecomnancy.net', None),
 (26, 'Événementiel', 'club-oenologie-bureau@telecomnancy.net', None),
 (44, 'Événementiel', 'club-paten-bureau@telecomnancy.net', '04/03/2025'),
 (8, 'Services', 'club-studio-bureau@telecomnancy.net', None),
 (14, 'Services', 'club-tek-tn-bureau@telecomnancy.net', None),
 (18, 'Événementiel', 'club-telecom-cooking-bureau@telecomnancy.net', None),
 (16, 'Loisirs', 'club-telegame-bureau@telecomnancy.net', None),
 (45, 'Événementiel', 'club-tn24-bureau@telecomnancy.net', None),
 (23, 'Loisirs', "club-neura'tn-bureau@telecomnancy.net", None),
 (36, 'Événementiel', None, None),
 (12, 'Événementiel', 'club-voyage-bureau@telecomnancy.net', None)]

BUREAUX = [(0, 13, '2026-2027', 'HATEAU Mathias'),
 (2, 13, '2026-2027', 'BRIWA Tristan'),
 (3, 13, '2026-2027', 'ROSE Titouan'),
 (0, 22, '2026-2027', 'SALTEL Arthur'),
 (2, 22, '2026-2027', 'BORNE Alexis'),
 (3, 22, '2026-2027', 'VIRELY Florent'),
 (0, 10, '2026-2027', 'VALENTIN Florian'),
 (2, 10, '2026-2027', 'ROMERA-ROMARY Esteban'),
 (3, 10, '2026-2027', 'JORDAN Lucas'),
 (0, 34, '2026-2027', 'JANIN Thomas'),
 (2, 34, '2026-2027', 'ROMERA-ROMARY Esteban'),
 (3, 34, '2026-2027', 'ATTIAS Gabriel'),
 (0, 33, '2026-2027', 'DIETRICH Corentin'),
 (2, 33, '2026-2027', 'SENEMEAUD Eliott'),
 (3, 33, '2026-2027', 'MICHELI Thomas'),
 (0, 9, '2026-2027', 'FERRATO Rémi'),
 (1, 9, '2026-2027', 'NORTHDURFT Mathilde'),
 (2, 9, '2026-2027', 'SCHOESER Camilla'),
 (3, 9, '2026-2027', 'DENYS Angèle'),
 (0, 38, '2026-2027', 'MALOSSE Adrien'),
 (2, 38, '2026-2027', 'DIJOUX Maxime'),
 (3, 38, '2026-2027', 'LORAND Lenny'),
 (0, 7, '2026-2027', 'BELTRAN Loan'),
 (1, 7, '2026-2027', 'MOUTRILLE Axel'),
 (2, 7, '2026-2027', 'TRINDER Sébastien'),
 (3, 7, '2026-2027', 'CHALOT Hugo'),
 (0, 39, '2026-2027', 'NEGRO Eric'),
 (2, 39, '2026-2027', 'CHAMPMARTIN Erwan'),
 (3, 39, '2026-2027', 'CLEYET-MERLE Matthieu'),
 (0, 27, '2026-2027', 'SENEMEAUD Eliott'),
 (2, 27, '2026-2027', 'MICHELI Thomas'),
 (3, 27, '2026-2027', 'DIETRICH Corentin'),
 (0, 21, '2026-2027', 'BRAVO Quentin'),
 (2, 21, '2026-2027', 'TRINDNER Sebastien'),
 (3, 21, '2026-2027', 'JORDAN Lucas'),
 (0, 37, '2026-2027', 'BELLEC Alan'),
 (2, 37, '2026-2027', 'COSTARD Lucille'),
 (3, 37, '2026-2027', 'MAREC Etienne'),
 (0, 25, '2026-2027', 'NANCHEN Emma'),
 (2, 25, '2026-2027', 'LEHEUP Charles'),
 (3, 25, '2026-2027', 'POURRET Tom'),
 (0, 40, '2026-2027', 'FOISSELON Raphaël'),
 (2, 40, '2026-2027', 'SCHOIRFER Samuel'),
 (3, 40, '2026-2027', 'JORDAN Lucas'),
 (0, 28, '2026-2027', 'ATTIAS Gabriel'),
 (2, 28, '2026-2027', 'SCHOIRFER Samuel'),
 (3, 28, '2026-2027', 'ROMERA-ROMARY Esteban'),
 (0, 41, '2026-2027', 'FERRATO Rémi'),
 (2, 41, '2026-2027', 'LEFEBVRE Nathan'),
 (3, 41, '2026-2027', 'DEFOSSE Rémi'),
 (0, 32, '2026-2027', 'GIOVANELLA Camille'),
 (2, 32, '2026-2027', 'CHALOT Hugo'),
 (3, 32, '2026-2027', 'BELTRAN Loan'),
 (3, 32, '2026-2027', 'MESNARD-LE-RESTE Julien'),
 (0, 42, '2026-2027', 'CHEVALLET Jules'),
 (2, 42, '2026-2027', 'HATEAU Mathias'),
 (3, 42, '2026-2027', 'CHEVEREAU Edwyn'),
 (0, 29, '2026-2027', 'BELTRAN Loan'),
 (2, 29, '2026-2027', 'ROMERA-ROMARY Esteban'),
 (3, 29, '2026-2027', 'JORDAN Lucas'),
 (0, 24, '2026-2027', 'LUKUMUENA Aïcha'),
 (2, 24, '2026-2027', 'MAREC Etienne'),
 (3, 24, '2026-2027', 'BESSIERE Léna'),
 (0, 11, '2026-2027', 'DURIN Grégoire'),
 (2, 11, '2026-2027', 'LEFEBVRE Nathan'),
 (0, 19, '2026-2027', 'ROLLAND Antoine'),
 (2, 19, '2026-2027', 'HARI Théo'),
 (3, 19, '2026-2027', 'VIRELY Florent'),
 (0, 15, '2026-2027', 'TROHA Stanislas'),
 (2, 15, '2026-2027', 'MACQUART Jarod'),
 (3, 15, '2026-2027', 'BIETTI Hugues'),
 (0, 30, '2026-2027', 'JORDAN Lucas'),
 (2, 30, '2026-2027', 'LUKUMUENA Aïcha'),
 (3, 30, '2026-2027', 'GILBERT Ewen'),
 (0, 6, '2026-2027', 'CHAUMONT Camille'),
 (1, 6, '2026-2027', 'LEFEBVRE Nathan'),
 (2, 6, '2026-2027', 'ROMERA-ROMARY Esteban'),
 (3, 6, '2026-2027', 'GILBERT Ewen'),
 (0, 35, '2026-2027', 'BRITTAIN Théo'),
 (2, 35, '2026-2027', 'DEFOSSE Rémi'),
 (3, 35, '2026-2027', 'FERRATO Rémi'),
 (0, 43, '2026-2027', 'JORDAN Lucas'),
 (2, 43, '2026-2027', 'SCHOIRFER Samuel'),
 (3, 43, '2026-2027', 'TRINDER Sébastien'),
 (0, 17, '2026-2027', 'FERRIER Léonie'),
 (2, 17, '2026-2027', 'DI GALLO Clément'),
 (3, 17, '2026-2027', 'PEYNON Eléa'),
 (0, 31, '2026-2027', 'LOISIL Tom'),
 (2, 31, '2026-2027', 'SCHOIRFER Samuel'),
 (3, 31, '2026-2027', 'BRIAND Thibault'),
 (0, 26, '2026-2027', 'ROMERA-ROMARY Esteban'),
 (2, 26, '2026-2027', 'CHAMPMARTIN Erwan'),
 (3, 26, '2026-2027', 'CHAUMONT Camille'),
 (0, 44, '2026-2027', 'FERRATO Rémi'),
 (2, 44, '2026-2027', 'DUPUIS Clément'),
 (3, 44, '2026-2027', 'DEFOSSE Rémi'),
 (0, 8, '2026-2027', 'WENDLING LOMBROSO Lucie'),
 (1, 8, '2026-2027', 'SENEMEAUD Eliott'),
 (2, 8, '2026-2027', 'LUKUMUENA Aïcha'),
 (3, 8, '2026-2027', 'MICHELI Thomas'),
 (0, 14, '2026-2027', 'CHAMPMARTIN Errwan'),
 (2, 14, '2026-2027', 'HIRTZ Grégoire'),
 (3, 14, '2026-2027', 'PETIT Robin'),
 (0, 18, '2026-2027', 'MICHELI Thomas'),
 (2, 18, '2026-2027', 'CHAABANE Cyrine'),
 (3, 18, '2026-2027', 'NOTHDURFT Mathilde'),
 (0, 16, '2026-2027', 'CHALOT Hugo'),
 (2, 16, '2026-2027', 'TRINDER Sébastien'),
 (3, 16, '2026-2027', 'CHEVALLET Jules'),
 (0, 45, '2026-2027', 'WENDLING Lucie'),
 (2, 45, '2026-2027', 'FERRATO Rémi'),
 (3, 45, '2026-2027', 'MACQUART Jarod'),
 (0, 23, '2026-2027', 'NOBILE Tobias'),
 (2, 23, '2026-2027', 'AUDURIER Théo'),
 (3, 23, '2026-2027', 'ANACKER Guillaume'),
 (0, 36, '2026-2027', 'GILBERT Ewen'),
 (2, 36, '2026-2027', 'JORDAN Lucas'),
 (3, 36, '2026-2027', 'PARENT Léa'),
 (0, 12, '2026-2027', 'MACQUART Jarod'),
 (2, 12, '2026-2027', 'GUYNOT DE BOISMENU Balthazar'),
 (3, 12, '2026-2027', 'JOBARD Martin')]


def upgrade():
    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('type_club', sa.String(length=20), nullable=True))
        batch_op.add_column(
            sa.Column('contact_email', sa.String(length=150), nullable=True))
        batch_op.add_column(
            sa.Column('date_creation', sa.String(length=10), nullable=True))

    op.bulk_insert(
        sa.table('clubs',
                 sa.column('club_id', sa.Integer),
                 sa.column('club_name', sa.String),
                 sa.column('asso_id', sa.Integer)),
        [{'club_id': cid, 'club_name': nom, 'asso_id': 0}
         for cid, nom in NOUVEAUX_CLUBS],
    )

    for club_id, type_club, email, creation in META_CLUBS:
        op.execute(
            sa.text(
                "UPDATE clubs SET type_club = :t, contact_email = :e, "
                "date_creation = :d WHERE club_id = :c"
            ).bindparams(t=type_club, e=email, d=creation, c=club_id)
        )

    op.bulk_insert(
        sa.table('club_roles',
                 sa.column('role_id', sa.Integer),
                 sa.column('club_id', sa.Integer),
                 sa.column('mandat', sa.String),
                 sa.column('personne', sa.String)),
        [{'role_id': r, 'club_id': c, 'mandat': m, 'personne': p}
         for r, c, m, p in BUREAUX],
    )


def downgrade():
    mandats = sorted({m for _, _, m, _ in BUREAUX})
    liste = ", ".join(f"'{m}'" for m in mandats)
    op.execute(f"DELETE FROM club_roles WHERE mandat IN ({liste})")

    nouveaux = ", ".join(str(cid) for cid, _ in NOUVEAUX_CLUBS)
    op.execute(f"DELETE FROM club_roles WHERE club_id IN ({nouveaux})")
    op.execute(f"DELETE FROM clubs WHERE club_id IN ({nouveaux})")

    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.drop_column('date_creation')
        batch_op.drop_column('contact_email')
        batch_op.drop_column('type_club')
