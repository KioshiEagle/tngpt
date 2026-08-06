"""description des clubs et peuplement depuis la plaquette alpha 2026-2027

Revision ID: c4e91a72d508
Revises: b3d7c1a9e042
Create Date: 2026-08-06 15:02:41.117903

Les descriptions sont reprises telles quelles de la plaquette alpha 2026-2027
(club Studio). Aucun club n'est décrit de mémoire : ceux que la plaquette
mentionne sans les présenter (un logo en dernière page, pas de texte) gardent
une description NULL.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e91a72d508'
down_revision = 'b3d7c1a9e042'
branch_labels = None
depends_on = None


# Anim'Est manquait : la plaquette compte cinq associations (CETEN, BDS, TNS,
# Humani'TN, Anim'Est) et la décrit comme « une association étudiante ».
ASSOS = [
    {'asso_id': 2, 'asso_name': "Anim'Est"},
]

# Les cinq associations figurent AUSSI comme clubs : `club_roles` ne pointe que
# vers `clubs`, une association sans ligne ici ne pourrait donc pas avoir de
# bureau — et « qui est trésorier de TNS » resterait sans réponse.
CLUBS = [
    (1, 'CETEN', 'bde', 0,
     "Le Cercle des Élèves de TELECOM Nancy (CETEN) est le BDE de l'école : "
     "l'association qui gère les 41 clubs animant la vie associative, veille à "
     "ce qu'ils disposent de ce dont ils ont besoin et organise des évènements "
     "plus généraux tout au long de l'année."),
    (2, "Humani'TN", 'humanitn', 1,
     "Association engagée pour promouvoir des valeurs humaines et solidaires à "
     "travers des évènements qu'elle organise ou auxquels elle participe : "
     "Octobre Rose, TN'Event, Course de la Jonquille."),
    (3, "Anim'Est", 'animest', 2,
     "Association étudiante qui promeut la culture japonaise à l'échelle du "
     "Grand-Est au travers d'une convention organisée chaque année à Nancy un "
     "week-end de novembre, rassemblant plus de 8000 visiteurs et près de 200 "
     "bénévoles. Au programme : spectacles, artistes, activités et Maid Café."),
    (4, 'BDS', 'bds', 4,
     "Le Bureau Des Sports propose des entraînements en volley, foot, basket, "
     "handball, badminton et course à pied, et participe à plusieurs tournois "
     "majeurs inter-écoles d'ingénieurs."),
    (5, 'Telecom Nancy Services', 'tns', 5,
     "TNS est la junior-entreprise de l'école : elle prospecte des missions "
     "auprès d'entreprises et les transmet aux étudiants, qui les réalisent "
     "contre rémunération. On peut y devenir intervenant ou membre actif "
     "(ressources humaines, développement commercial, gestion de projet, "
     "trésorerie, secrétariat, systèmes d'information)."),

    (6, 'Les Intéductibles Gaulois', 'inte', 0,
     "Le club intégration accueille les nouveaux étudiants : exploration de "
     "Nancy, barathon, défis et surprises. Seize membres pour l'édition 2026."),
    (7, 'Bureau Des Arts', 'bda', 0,
     "Le BDA réunit musiciens, dessinateurs, comédiens, danseurs, cinéastes et "
     "amateurs de karaoké, organisés en pôles : Musique (local rempli "
     "d'instruments), Théâtre (improvisation ou texte), Danse (rock et salsa), "
     "Cinéma, Karaoké et Dessin."),
    (8, 'Studio', 'studio', 0,
     "Le club photo et vidéo réalise les projets créatifs des autres clubs — "
     "logos, vidéos, affiches, la plaquette elle-même — couvre les évènements "
     "et soirées, produit le yearbook et initie à Photoshop, Illustrator et au "
     "montage vidéo."),
    (9, "Chok'Bar", 'bar', 0,
     "Le bar de l'école propose boissons et friandises à prix modique, des "
     "viennoiseries chaque matin et des plats le midi à prix réduit. Se porter "
     "volontaire pour préparer les sandwichs donne droit au sien gratuitement."),
    (10, 'ASTN', 'astn', 0,
     "ASTN accompagne les étudiants admis sur titre avant même leur arrivée : "
     "préparation aux matières clés, intégration sociale et informations "
     "essentielles tout au long de l'année."),
    (11, 'Gala', 'gala', 0,
     "La soirée de fin de cursus réunit plus de 200 convives en tenue élégante "
     "autour d'un repas gastronomique, d'une ambiance sublimée par le BDA et "
     "d'un DJ set. Édition prévue le 21 novembre 2026."),
    (12, 'Club Voyage', 'voyage', 0,
     "Le club organise des voyages à prix réduit : Europa Park en septembre, "
     "Prague en novembre, une semaine au ski en janvier, et des destinations "
     "comme Amsterdam, la Croatie ou la Chine."),
    (13, "Abso'Ludique", 'absoludique', 0,
     "Club de jeux de société, deux fois par semaine le mercredi et le vendredi "
     "soir, avec une collection de plus de 100 jeux : Blood on the Clocktower, "
     "jeux de rôle, Magic, échecs. Aucune expérience requise."),
    (14, "Tek'TN", 'tektn', 0,
     "Le club bricolage de TELECOM Nancy : robotique, électronique, impression "
     "3D et DIY, avec un local et des outils. Première participation à la Coupe "
     "de France de Robotique."),
    (15, "Hackin'TN", 'hackintn', 0,
     "Club de cybersécurité et de hacking informatique : séances pédagogiques "
     "sur des thèmes variés et organisation ou participation à de nombreux CTF."),
    (16, 'TéléGameDesign', 'tgd', 0,
     "Le club de création de jeux vidéo : séances hebdomadaires, game design, "
     "conception artistique, entraide et tutorats Unity et Godot."),
    (17, 'Club Marché', 'marche', 0,
     "Le club propose chaque semaine légumes, pain et fruits BIO et locaux "
     "issus d'une AMAP, pour soutenir l'agriculture locale et de saison."),
    (18, 'Club Cooking', 'cooking', 0,
     "On y cuisine ensemble des plats autour d'un thème, parfois en "
     "collaboration avec d'autres clubs (œnologie, marché) ou avec un vrai "
     "chef, puis on déguste ensemble."),
    (19, 'GamingTN', 'gaming', 0,
     "Le club organise des évènements sur différents jeux, pour la découverte "
     "comme pour la compétition."),
    (20, 'Les Supers Dégommeurs de Fromage', 'sdf', 0,
     "Le club des passionnés de fromage de TELECOM Nancy : on y fait déguster "
     "les fromages de sa région et on repart avec des paniers d'autres "
     "contrées."),
    (21, 'Club Bravo', 'bravo', 0,
     "Le Club Bravo transmet la joie, la bonne humeur et la félicité, notamment "
     "par des compliments sincères et précis et des gestes gratifiants."),
    (22, 'Algo', 'algo', 0,
     "Club de programmation et de résolution de problèmes : compétitions "
     "hebdomadaires (Codeforces, Codechef, Prologin, SWERC, LeetCode, Google "
     "Hash Code), challenges en groupe ou en solo et présentations "
     "d'algorithmes, des novices aux mordus."),
    (23, "Neura'TN", 'neuratn', 0,
     "Club d'intelligence artificielle : séances hebdomadaires et projets de "
     "groupe sur les réseaux de neurones, la computer vision et le natural "
     "language processing, avec des participations fréquentes à des hackathons "
     "d'IA."),
    (24, "Fashion'TN", None, 0,
     "Club de mode ouvert à tous : sorties en friperie et à Emmaüs, dress code "
     "d'une journée chaque mois et vente de vêtements."),
    (25, 'BricksTN', None, 0,
     "Le club des enthousiastes de la brique LEGO : on se retrouve pour "
     "construire ensemble et on partage ses constructions sur un serveur "
     "Discord dédié."),
    (26, 'Club Œnologie', 'oenologie', 0,
     "Club de dégustation de vins, pour connaisseurs comme pour néophytes, lors "
     "de moments conviviaux parfois animés par des experts."),
    (27, 'Club Brasserie', 'brasserie', 0,
     "Club convivial où les passionnés de bière se retrouvent pour des soirées "
     "animées, des dégustations de bières locales et internationales et des "
     "rencontres avec des brasseurs."),
    (28, 'Cafélécom', None, 0,
     "Le club des amateurs de café de TELECOM Nancy : paniers de café proposés "
     "tout au long de l'année et moments de dégustation pour échanger "
     "techniques et conseils."),
    (29, "Elsass'TN", None, 0,
     "Le club fait découvrir la culture alsacienne à travers plusieurs "
     "événements dans l'année : Saint-Nicolas, tartes flambées, bretzels."),
    (30, "L'Instant Thé", 'instantthe', 0,
     "Le rendez-vous podcast de l'école : depuis un studio équipé, des lives "
     "Twitch pour discuter de la vie associative et de l'actualité, autour d'un "
     "bon thé."),
    (31, "Mini Tel'", 'minitel', 0,
     "Le journal de l'école, publié tous les trois mois : mini-jeux, articles "
     "écrits par les élèves et les profs, interviews et pages de citations "
     "proposées via un bot Discord. Le club assiste à chaque réunion ouverte du "
     "BDE pour en proposer un compte-rendu alternatif."),
    (32, "Créa'TN", 'creatn', 0,
     "Club de crochet, tricot, point de croix et cosplay : on y manie le fil, "
     "l'aiguille et le crochet, et on apprend ou enseigne sa passion aux autres "
     "membres."),
    (33, 'Amphi Suze', 'amphisuze', 0,
     "Club de dégustation et de découverte des spiritueux : création de son "
     "propre digestif, dégustations et découverte de leur histoire et de leurs "
     "secrets de fabrication."),
    (34, 'AllinTN', 'allintn', 0,
     "Le club de poker de TELECOM Nancy, ouvert à tous les niveaux : soirées "
     "poker chaque semaine et organisation du NSPC, grand tournoi réunissant "
     "les étudiants de tout Nancy."),
    (35, 'Les Baroudeurs', 'baroudeurs', 0,
     "Des mèmes, des pranks, du brainrot et la revue de bar."),
    (36, "Touris'TN", 'touristn', 0,
     "Le club accompagne les étudiants dans leur découverte de Nancy tout au "
     "long de l'année, à travers des sorties dans des lieux culturels et "
     "ludiques."),
    # La plaquette ne montre que le logo de ce club, sans page de présentation :
    # description laissée à NULL plutôt qu'inventée.
    (37, "Breizh'TN", 'breizhtn', 0, None),
]


def upgrade():
    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))

    op.bulk_insert(
        sa.table('assos',
                 sa.column('asso_id', sa.Integer),
                 sa.column('asso_name', sa.String)),
        ASSOS,
    )
    op.bulk_insert(
        sa.table('clubs',
                 sa.column('club_id', sa.Integer),
                 sa.column('club_name', sa.String),
                 sa.column('slug', sa.String),
                 sa.column('asso_id', sa.Integer),
                 sa.column('description', sa.Text)),
        [
            {
                'club_id': club_id,
                'club_name': name,
                'slug': slug,
                'asso_id': asso_id,
                'description': description,
            }
            for club_id, name, slug, asso_id, description in CLUBS
        ],
    )


def downgrade():
    clubs = sa.table('clubs', sa.column('club_id', sa.Integer))
    op.execute(
        clubs.delete().where(
            clubs.c.club_id.in_([club_id for club_id, *_ in CLUBS])
        )
    )
    assos = sa.table('assos', sa.column('asso_id', sa.Integer))
    op.execute(
        assos.delete().where(
            assos.c.asso_id.in_([row['asso_id'] for row in ASSOS])
        )
    )
    with op.batch_alter_table('clubs', schema=None) as batch_op:
        batch_op.drop_column('description')
