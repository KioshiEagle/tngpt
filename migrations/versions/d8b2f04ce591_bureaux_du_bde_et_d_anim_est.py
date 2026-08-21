"""bureaux 2026-2027 du BDE (CETEN) et d'Anim'Est

Revision ID: d8b2f04ce591
Revises: c6a4e1b83f27
Create Date: 2026-08-07 12:18:44.913027

Premier remplissage d'`asso_roles`. Les rôles 5 à 13 avaient été saisis à la
main sur la base d'origine, donc aucune migration ne les créait : une base
neuve s'arrêtait ici sur une violation de clé étrangère. Ils sont désormais
insérés avec leur orthographe de l'époque (« évènements », « partenariats »,
« billeterie ») — f1a6de29c704 corrige billetterie plus loin dans la chaîne.

Les personnes extérieures à l'école — Anim'Est en recrute beaucoup — portent le
suffixe « (exté) » dans `personne`, faute d'une colonne dédiée. C'est une
information que la plaquette confirme (près de 200 bénévoles venant au-delà de
TELECOM Nancy) et qu'il aurait été dommage de perdre.

Trois personnes figuraient déjà dans `club_roles` sous une autre graphie ; les
lignes existantes sont alignées sur la forme retenue ici.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd8b2f04ce591'
down_revision = 'c6a4e1b83f27'
branch_labels = None
depends_on = None


CETEN, ANIMEST = 0, 2
MANDAT = "2026-2027"

# Rôles saisis à la main sur la base d'origine, jamais portés en migration.
# Insérés sans écraser l'existant : la base d'origine les a déjà.
ROLES_SAISIS_A_LA_MAIN = [
    (5, "Responsable logistique"),
    (6, "Responsable évènements"),
    (7, "Vice-trésorier"),
    (8, "Vice-secrétaire"),
    (9, "Responsable prévention"),
    (10, "Responsable partenariats"),
    (11, "Responsable RSE"),
    (12, "Responsable billeterie"),
    (13, "Responsable sécurité"),
]

# Rôles absents de la table. Les identifiants prennent la suite des 5..13.
NOUVEAUX_ROLES = [
    (14, "Responsable passation"),
    (15, "Responsable boutique"),
    (16, "Responsable buvette et emplettes"),
    (17, "Responsable chorégraphie"),
    (18, "Responsable créateurs"),
    (19, "Responsable fil rouge"),
    (20, "Responsable graphisme"),
    (21, "Responsable informatique"),
    (22, "Responsable infrastructure"),
    (23, "Responsable maid café"),
]

# (asso_id, role_id, personne)
BUREAUX = [
    # --- BDE / CETEN ---
    (CETEN, 0, "BELTRAN Loan"),
    (CETEN, 1, "DOUILLET Lucas"),
    (CETEN, 2, "VIRELY Florent"),
    (CETEN, 7, "GILBERT Ewen"),
    (CETEN, 3, "TRINDER Sébastien"),
    (CETEN, 8, "BRAVO Quentin"),
    (CETEN, 6, "NOBILE Tobias"),
    (CETEN, 6, "LUKUMUENA Aïcha"),
    (CETEN, 6, "HARI Théo"),
    (CETEN, 5, "WENDLING--LOMBROSO Lucie"),
    (CETEN, 5, "WOZNY Sylvain"),
    (CETEN, 10, "PETIT Robin"),
    (CETEN, 11, "PEYNON Éléa"),
    (CETEN, 14, "ROULLET Raphaël"),
    (CETEN, 4, "JORDAN Lucas"),
    (CETEN, 4, "MACQUART Jarod"),
    # --- Anim'Est ---
    (ANIMEST, 0, "LECLERE-MOINAUX Loan"),
    (ANIMEST, 0, "BUI Kévin"),
    (ANIMEST, 1, "BRILLAUD Timothée"),
    (ANIMEST, 1, "THOMAS Eléa-Rose"),
    (ANIMEST, 2, "GODARD Alexis"),
    (ANIMEST, 3, "GAIFFE Félix"),
    (ANIMEST, 3, "NEGRO Eric"),
    (ANIMEST, 12, "CHEVEREAU Edwin"),
    (ANIMEST, 15, "VERET Salif"),
    (ANIMEST, 15, "OSAWA-BOURBON Maxence"),
    (ANIMEST, 16, "PIGASSOU Clément"),
    (ANIMEST, 16, "DOLE Dylan"),
    (ANIMEST, 16, "PEYNON Éléa"),
    (ANIMEST, 17, "Amélie Usya (exté)"),
    (ANIMEST, 4, "HARI Théo"),
    (ANIMEST, 4, "BENDALI Yassine"),
    (ANIMEST, 4, "BRAUDEL Angélique (exté)"),
    (ANIMEST, 18, "CHALOT Hugo"),
    (ANIMEST, 18, "GIOVANELLA Camille"),
    (ANIMEST, 6, "DA CUNHA Eliot (exté)"),
    (ANIMEST, 6, "SLILA Oscar (exté)"),
    (ANIMEST, 6, "MUNIER-KAPLANIAN Timothé (exté)"),
    (ANIMEST, 19, "NIAMKE Emma"),
    (ANIMEST, 19, "PERNIN Marine (exté)"),
    (ANIMEST, 20, "NANCHEN Emma"),
    (ANIMEST, 20, "POURRET Tom"),
    (ANIMEST, 21, "DOUELLE Cyprien"),
    (ANIMEST, 21, "CHEVALLET Jules"),
    (ANIMEST, 22, "COULON Louis"),
    (ANIMEST, 5, "LEHEUP Charles"),
    (ANIMEST, 5, "MESNARD-LE-RESTE Julien"),
    (ANIMEST, 5, "HATEAU Mathias"),
    (ANIMEST, 23, "INVERNIZZI Mei (exté)"),
    (ANIMEST, 23, "BRAVO Quentin"),
    (ANIMEST, 13, "DI GALLO Clément"),
    (ANIMEST, 13, "VILETTE Léo (exté)"),
]

# Graphies déjà présentes dans `club_roles`, alignées sur celles retenues ici :
# (ancienne, retenue).
UNIFICATIONS = [
    ("WENDLING LOMBROSO Lucie", "WENDLING--LOMBROSO Lucie"),
    ("PEYNON Eléa", "PEYNON Éléa"),
]


def upgrade():
    for rid, nom in ROLES_SAISIS_A_LA_MAIN:
        op.execute(
            sa.text(
                "INSERT INTO roles (role_id, role_name) VALUES (:rid, :nom) "
                "ON CONFLICT DO NOTHING"
            ).bindparams(rid=rid, nom=nom)
        )
    op.bulk_insert(
        sa.table('roles',
                 sa.column('role_id', sa.Integer),
                 sa.column('role_name', sa.String)),
        [{'role_id': rid, 'role_name': nom} for rid, nom in NOUVEAUX_ROLES],
    )
    op.bulk_insert(
        sa.table('asso_roles',
                 sa.column('role_id', sa.Integer),
                 sa.column('asso_id', sa.Integer),
                 sa.column('mandat', sa.String),
                 sa.column('personne', sa.String)),
        [{'role_id': r, 'asso_id': a, 'mandat': MANDAT, 'personne': p}
         for a, r, p in BUREAUX],
    )
    for avant, apres in UNIFICATIONS:
        op.execute(
            sa.text(
                "UPDATE club_roles SET personne = :apres WHERE personne = :avant"
            ).bindparams(apres=apres, avant=avant)
        )


def downgrade():
    for avant, apres in UNIFICATIONS:
        op.execute(
            sa.text(
                "UPDATE club_roles SET personne = :avant WHERE personne = :apres"
            ).bindparams(apres=apres, avant=avant)
        )
    op.execute(
        sa.text("DELETE FROM asso_roles WHERE mandat = :m").bindparams(m=MANDAT)
    )
    # ROLES_SAISIS_A_LA_MAIN survit au downgrade : sur la base d'origine ces
    # rôles préexistent à cette révision, les retirer serait une perte.
    ids = ", ".join(str(rid) for rid, _ in NOUVEAUX_ROLES)
    op.execute(f"DELETE FROM roles WHERE role_id IN ({ids})")
