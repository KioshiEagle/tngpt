"""Conversion d'un mail exporté (.eml) en Markdown, jumeau de `pdftomd`.

Toute la pipeline converge sur `VectorStore.upload_file`, qui lit du Markdown à
frontmatter : un mail n'a donc pas besoin d'un second chemin d'ingestion, mais
d'un convertisseur qui produise cette forme-là. Chunking, embeddings, catalogue
et suivi à l'écran restent ceux des PDF.

Le document produit annonce qu'il est un mail et à quelle date il est parti.
Sans ce marquage, « le CTF commence aujourd'hui » se lit comme une information
du jour, un an après l'envoi. La règle qui exploite le marquage vit dans le
prompt de `generate` et s'accroche au titre : celui-ci doit commencer par
« Mail », les deux ne peuvent pas diverger sans que la règle devienne muette.

Les fonctions de décision sont pures et reçoivent le catalogue en argument,
comme celles de `clubs` : elles restent testables sans base ni application.
"""

import email
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from .clubs import NATURE_ASSO, NATURE_CLUB, Entite, match_entites, normalize
from .textnorm import strip_accents

logger = logging.getLogger(__name__)

# Apostrophes typographiques : indiscernables des droites à l'oeil dans le
# source, donc écrites en échappements — même parti pris que `clubs`.
_APOSTROPHES = "\u2019\u02bc\u00b4"
_APO = f"['{_APOSTROPHES}]"

# Salutations ouvrant le corps d'un mail. Ce sont elles qui bornent le résumé :
# tout ce qui les précède en est un. Les lettres finales sont étirables, la
# ponctuation du corpus étant ce qu'elle est (« Bonjouuur ! », « Holaaa »).
_SALUTATION = re.compile(
    r"^\s*(bonjou+r|bonsoi+r|salu+t|hola+|hell?o+|hey+|coucou+|yo+|plop|"
    r"cher(?:e?s)?|chere?s?|salutations|bien le bonjour)\b",
    re.IGNORECASE,
)

# Un mail du corpus s'ouvre sur « <accroche> : <résumé> », où l'accroche est une
# variation libre sur le thème de la vitesse (« En vitesse lumière », « Speedrun »,
# « Crackéclair »). Elle n'apprend rien et, répétée sur des dizaines de mails,
# rapprocherait tous ces chunks du même point de l'espace vectoriel.
_ACCROCHE_MAX = 40
_RESTE_MIN = 20
# Au-delà, le texte qui précède la salutation n'est pas un résumé mais un mail
# dont l'auteur a simplement omis de dire bonjour.
_RESUME_MAX = 400

# Intitulés de poste, cherchés dans la signature. Comparés sans accents.
_POSTE = re.compile(
    r"\b(presiden|secretai|tresori|respo|responsable|vice-|chef|capitaine|"
    r"membre du bureau)",
    re.IGNORECASE,
)
# Signature : au-delà, on ne lit plus une signature mais la fin du message.
_SIGNATURE_LIGNES = 18
_POSTES_MAX = 6

# Tags nommant la liste de diffusion, non un club : `[CETEN]` figure sur 228
# mails du corpus, le retenir d'emblée reviendrait à n'en distinguer aucun. Mais
# un mail tagué `[CETEN]` et rien d'autre émane bien du CETEN — que la base tient
# sous une seule ligne avec le BDE (`nom='CETEN'`, `slug='bde'`). D'où le repli
# de dernier rang plutôt qu'une exclusion pure.
_TAGS_LISTE = frozenset({"ceten", "infoceten", "info ceten"})
# Tags décrivant la nature du document ou une campagne, jamais une entité.
_TAGS_HORS_CLUB = _TAGS_LISTE | {"cr", "odj", "campagne", "voyage"}
_CLUBS_MAX = 3

_TAGS = re.compile(r"^\s*((?:\[[^\]]*\]\s*)+)")
_TAG = re.compile(r"\[([^\]]*)\]")
# Marqueurs de citation : rares dans ce corpus d'annonces (8 mails sur 262),
# mais un seul suffit à recopier tout un fil dans un chunk.
_CITATION = re.compile(
    rf"^\s*>|^.*\ba écrit\s*:\s*$|^-{{2,}}\s*Message d{_APO}origine",
    re.MULTILINE,
)
# Séparateur de signature normalisé (RFC 3676) : « -- » seul sur sa ligne.
_FIN_SIGNATURE = re.compile(r"^-- ?$", re.MULTILINE)
# Pied de page ajouté par Google Groups aux mails de liste (19 mails du corpus).
# Il est au mot près le même partout : indexé tel quel, il formerait un amas de
# chunks jumeaux que n'importe quelle question sur le CETEN irait percuter.
_PIED_LISTE = re.compile(
    r"^.*(?:issu du Ceten|\+unsubscribe@|retirer de la liste).*$",
    re.IGNORECASE | re.MULTILINE,
)

# Emphase produite par la conversion HTML → texte de Gmail.
_GRAS = re.compile(r"\*([^*\n]+)\*")
_ITALIQUE = re.compile(r"^\s*/(.+)/\s*$", re.MULTILINE)
# Puce isolée sur sa ligne, suivie d'une ligne blanche puis du contenu : une
# liste ainsi éclatée triple la longueur du texte et casse le découpage.
_PUCE_ECLATEE = re.compile(r"^[ \t]*([-*•])[ \t]*\n\s*\n[ \t]*", re.MULTILINE)
_LIGNES_VIDES = re.compile(r"\n{3,}")

_ADRESSE_ECOLE = re.compile(r"^([a-z]+)\.([a-z-]+)@", re.IGNORECASE)


@dataclass(frozen=True)
class Citee:
    """Une entité reconnue, et la forme sous laquelle le mail la nomme.

    Les deux diffèrent souvent : un tag `[Abso]` pour Abso'Ludique, un
    `[Inté'Galactique]` pour le club d'inté, qui se renomme chaque février. Le
    Markdown porte les deux — le nom canonique fait le lien avec la base, la
    forme du mail permet de retrouver l'entité dans le corps du message.

    La formulation reste neutre parce que la base ne distingue pas un surnom
    d'un nom révolu : les deux vivent dans la même colonne `aliases`. Écrire
    « aujourd'hui X » serait juste pour Inté'Galactique et faux pour Abso.
    """

    entite: Entite
    forme: str

    @property
    def rendu(self) -> str:
        """Nom canonique, suivi de la forme du mail quand elle en diffère."""
        if not self.forme or normalize(self.forme) == normalize(self.entite.nom):
            return self.entite.nom
        return f"{self.entite.nom} (désigné « {self.forme} » dans ce mail)"


@dataclass(frozen=True)
class Mail:
    """Un mail réduit à ce qui part dans le Markdown."""

    sujet: str
    date: str
    expediteur: str
    liste: str
    entites: tuple[Citee, ...]
    resume: str
    fonction: str
    corps: str

    @property
    def titre(self) -> str:
        """Titre du frontmatter, repris en préfixe de chaque chunk.

        Il commence par « Mail » : c'est ce mot que la règle de `generate`
        reconnaît pour ne pas lire une annonce d'il y a un an au présent.
        L'entité y figure aussi, faute de quoi les morceaux du corps — qui ne
        portent pas le bloc « Infos » — ne diraient pas de qui ils parlent.
        """
        noms = [citee.entite.nom for citee in self.entites[:2]]
        qui = ", ".join(noms) if noms else self.liste
        jour = _en_clair(self.date)
        return f"Mail {qui} du {jour} — {self.sujet}" if qui else self.sujet

    def nommees(self, nature: str) -> str:
        """Entités de la nature demandée, rendues avec la forme du mail."""
        return ", ".join(
            citee.rendu for citee in self.entites if citee.entite.nature == nature
        )


def _en_clair(iso: str) -> str:
    """Date ISO rendue en JJ/MM/AAAA, la forme lue dans les archives."""
    parts = iso.split("-")
    expected = 3
    if len(parts) != expected:
        return iso
    annee, mois, jour = parts
    return f"{jour}/{mois}/{annee}"


def _expediteur(brut: str) -> str:
    """Nom de l'expéditeur sous la forme « NOM Prénom » des archives.

    Le nom affiché dans l'en-tête est trop instable pour servir de clé : le
    corpus contient « Lucas Jordan » et « LUCAS JORDAN », « Secrétariat BDE » et
    « Secrétaire BDE ». L'adresse, elle, suit la convention de l'école
    (`prenom.nom@`) et donne la même forme pour tous. On ne retombe sur le nom
    affiché que pour les boîtes fonctionnelles, qui n'ont pas cette forme.
    """
    nom_affiche, adresse = parseaddr(brut)
    found = _ADRESSE_ECOLE.match(adresse)
    if found is None:
        return " ".join(nom_affiche.split()) or adresse
    prenom, nom = found.group(1), found.group(2)
    return f"{nom.replace('-', ' ').upper()} {prenom.capitalize()}"


def _corps_texte(msg: EmailMessage) -> str:
    """Partie `text/plain` du mail, ou chaîne vide.

    Les 262 mails du corpus en ont une : on ne convertit pas le HTML, ce qui
    évite d'embarquer un convertisseur pour un cas qui ne se présente pas.
    """
    partie = msg.get_body(preferencelist=("plain",))
    if partie is None:
        return ""
    contenu = partie.get_content()
    return contenu if isinstance(contenu, str) else ""


def _sans_citation(texte: str) -> str:
    """Coupe le texte au premier marqueur de citation."""
    found = _CITATION.search(texte)
    return texte[: found.start()] if found else texte


def _sans_pied(texte: str) -> str:
    """Coupe le pied de page de la liste de diffusion, séparateur compris."""
    found = _PIED_LISTE.search(texte)
    if found is None:
        return texte
    return texte[: found.start()].rstrip().removesuffix("--").rstrip()


def _nettoyer(texte: str) -> str:
    """Retire l'emphase et recolle les listes éclatées par la conversion Gmail."""
    texte = _GRAS.sub(r"\1", texte)
    texte = _ITALIQUE.sub(r"\1", texte)
    texte = _PUCE_ECLATEE.sub(r"\1 ", texte)
    texte = "\n".join(ligne.rstrip() for ligne in texte.splitlines())
    return _LIGNES_VIDES.sub("\n\n", texte).strip()


def _index_salutation(lignes: Sequence[str]) -> int | None:
    """Rang de la ligne de salutation, ou None s'il n'y en a pas."""
    return next((i for i, ligne in enumerate(lignes) if _SALUTATION.match(ligne)), None)


def _sans_accroche(resume: str) -> str:
    """Retire l'accroche qui précède le deux-points, quand c'en est une.

    « Vitesse x2 : rdv jeudi 16/10 » donne « rdv jeudi 16/10 ». On exige que
    l'accroche soit courte et que le reste tienne debout seul, pour ne pas
    amputer un résumé qui contiendrait légitimement un deux-points.
    """
    accroche, sep, suite = resume.partition(":")
    suite = suite.strip()
    if sep and len(accroche) <= _ACCROCHE_MAX and len(suite) >= _RESTE_MIN:
        return suite
    return resume


def _scinder(corps: str) -> tuple[str, str]:
    """Sépare le résumé d'ouverture du reste du message.

    Le corpus a formalisé le TL;DR sans le nommer : une ligne qui situe le
    message, placée avant la salutation. C'est cette position — et non un
    mot-clé — qui l'identifie, les rédacteurs inventant chacun la leur.

    Le résumé est retiré du corps parce qu'il est remonté dans le bloc
    « Infos » : l'y laisser aussi le ferait encoder deux fois, dans deux chunks
    voisins qui se disputeraient ensuite la même question.
    """
    lignes = corps.splitlines()
    index = _index_salutation(lignes)
    if index is None:
        return "", corps

    tete = [ligne.strip() for ligne in lignes[:index] if ligne.strip()]
    if not tete:
        return "", corps

    texte = " ".join(tete)
    if len(texte) > _RESUME_MAX:
        return "", corps
    return _sans_accroche(texte), "\n".join(lignes[index:]).strip()


def _fonction(corps: str) -> str:
    """Postes déclarés dans la signature, séparés par « · ».

    Quatre mails sur cinq se terminent par une signature qui énumère les
    mandats de leur auteur (« Vice-secrétaire BDE 2025 »). C'est la même
    information que `ClubRole`, datée et en libre-service : la couper — ce que
    fait d'ordinaire un nettoyage de mails — jetterait le plus utile du corpus.
    """
    found = _FIN_SIGNATURE.search(corps)
    queue = corps[found.end() :] if found else corps
    lignes = [ligne.strip(f" \t*/{_APOSTROPHES}") for ligne in queue.splitlines()]

    postes = [
        ligne
        for ligne in lignes[-_SIGNATURE_LIGNES:]
        if ligne and _POSTE.search(strip_accents(ligne))
    ]
    return " · ".join(dict.fromkeys(postes[:_POSTES_MAX]))


def _decouper_tags(sujet: str) -> tuple[list[str], str]:
    """Sépare les tags de tête du reste du sujet.

    « [CETEN] [GamingTN] Soirée gaming » donne (['CETEN', 'GamingTN'],
    'Soirée gaming').
    """
    found = _TAGS.match(sujet)
    if found is None:
        return [], sujet.strip()
    tags = [tag.strip() for tag in _TAG.findall(found.group(1)) if tag.strip()]
    return tags, sujet[found.end() :].strip()


def _par_tag_de_liste(tags: Sequence[str], catalogue: Sequence[Entite]) -> list[Citee]:
    """Entités tirées du seul tag de liste : `[CETEN]` émane du CETEN lui-même.

    Rend une liste vide si aucun tag n'est un tag de liste, ce qui rend inutile
    de tester `any(...)` avant l'appel.
    """
    return [
        Citee(entite, "")
        for tag in tags
        if normalize(tag) in _TAGS_LISTE
        for entite in match_entites(tag, catalogue)
    ]


def _entites(
    tags: Sequence[str], reste: str, catalogue: Sequence[Entite]
) -> list[Citee]:
    """Clubs et associations que le mail concerne, par ordre de fiabilité.

    Trois rangs : les tags qui nomment une entité, puis le texte du sujet
    (« Event Inté BDS : Paintball » n'a pas de tag), puis le tag de liste — un
    mail tagué `[CETEN]` sans autre indication émane du CETEN lui-même.

    La reconnaissance est exacte (nom officiel, slug, alias) : `match_flou` est
    volontairement écarté, ses résultats étant des pistes à soumettre au modèle,
    jamais une identification à inscrire dans un document qui fera ensuite foi.
    """
    trouvees: list[Citee] = []
    for tag in tags:
        if normalize(tag) in _TAGS_HORS_CLUB:
            continue
        trouvees.extend(Citee(entite, tag) for entite in match_entites(tag, catalogue))

    # Sans tag exploitable, la forme d'origine n'est pas isolable dans la phrase :
    # on ne rend alors que le nom canonique.
    if not trouvees:
        trouvees = [Citee(entite, "") for entite in match_entites(reste, catalogue)]

    if not trouvees:
        trouvees = _par_tag_de_liste(tags, catalogue)

    uniques = {citee.entite.cle: citee for citee in trouvees}
    return list(uniques.values())[:_CLUBS_MAX]


def lire(chemin: Path, catalogue: Sequence[Entite]) -> Mail:
    """Lit un `.eml` et en extrait tout ce que le Markdown affichera."""
    with chemin.open("rb") as flux:
        msg = email.message_from_binary_file(flux, policy=policy.default)

    sujet_brut = " ".join((msg.get("Subject") or chemin.stem).split())
    tags, sujet = _decouper_tags(sujet_brut)

    envoi = msg.get("Date")
    date = parsedate_to_datetime(envoi).date().isoformat() if envoi else ""

    brut = _nettoyer(_sans_pied(_sans_citation(_corps_texte(msg))))
    liste = (msg.get("List-Id") or "").strip("<>").split(".")[0]

    # La fonction se lit sur le corps entier : la signature vit après la
    # salutation, donc dans la part que `_scinder` conserve, mais la lire avant
    # le découpage évite de dépendre de cet ordre.
    fonction = _fonction(brut)
    resume, corps = _scinder(brut)

    return Mail(
        sujet=sujet or sujet_brut,
        date=date,
        expediteur=_expediteur(msg.get("From") or ""),
        liste=liste,
        entites=tuple(_entites(tags, sujet, catalogue)),
        resume=resume,
        fonction=fonction,
        corps=corps,
    )


def rendre(mail: Mail) -> str:
    """Rend le mail en Markdown à frontmatter, prêt pour `upload_file`.

    Les titres `#` et `##` ne sont pas décoratifs : ce sont les deux niveaux sur
    lesquels `get_hybrid_chunks` découpe, et chaque chunk hérite de leur libellé
    en préfixe. Une ligne dont l'information manque est omise plutôt que remplie
    d'un « Inconnu » — le prompt interdit d'inventer, un champ vide le ferait
    mentir.
    """
    envoi = _en_clair(mail.date) if mail.date else "date inconnue"
    sur = f" sur la liste {mail.liste}" if mail.liste else ""
    infos = [
        (
            f"Type : mail de diffusion envoyé le {envoi}{sur}. Les repères de "
            f"temps du message (« aujourd'hui », « ce soir », « demain ») se "
            f"comptent à partir du {envoi}."
        ),
    ]
    # Deux lignes distinctes, et jamais « club » pour une association : le
    # prompt interdit explicitement de confondre les deux natures, et le CETEN
    # — que la base tient sous la même ligne que le BDE — est une association.
    if clubs := mail.nommees(NATURE_CLUB):
        infos.append(f"Nom du club : {clubs}")
    if assos := mail.nommees(NATURE_ASSO):
        infos.append(f"Association : {assos}")
    if mail.expediteur:
        infos.append(f"Expéditeur : {mail.expediteur}")
    if mail.fonction:
        infos.append(f"Fonction de l'expéditeur : {mail.fonction}")
    if mail.resume:
        infos.append(f"Résumé : {mail.resume}")

    # `_parse_frontmatter` coupe la valeur au premier deux-points et retire les
    # guillemets qui l'entourent : un guillemet dans le titre tronquerait le sien.
    titre = mail.titre.replace('"', "'")
    return (
        f'---\ntitle: "{titre}"\ndate: "{mail.date}"\n'
        f'author: "{mail.expediteur}"\n---\n\n'
        f"# {mail.sujet}\n\n"
        f"## Infos\n" + "\n".join(infos) + "\n\n"
        f"## Corps du message\n{mail.corps}\n"
    )


def convert_file(eml_path: Path, output_dir: Path, catalogue: Sequence[Entite]) -> Path:
    """Convertit un `.eml` en Markdown et retourne le chemin du `.md`.

    Unité de travail de la conversion, appelée par le worker d'ingestion pour un
    fichier déposé. Le `.md` porte le nom du `.eml` : c'est ce que le worker
    supprime ensuite, et ce dont `upload_file` tire le `source_id`.
    """
    if eml_path.stat().st_size == 0:
        msg = f"Fichier vide : {eml_path.name}"
        raise ValueError(msg)

    mail = lire(eml_path, catalogue)
    if not mail.corps:
        msg = f"Mail sans corps exploitable : {eml_path.name}"
        raise ValueError(msg)

    logger.info(
        "EML -> MD : %s | %s | entites: %s",
        eml_path.name,
        mail.date,
        ", ".join(c.entite.nom for c in mail.entites) or "aucune",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{eml_path.stem}.md"
    md_path.write_text(rendre(mail), encoding="utf-8")
    return md_path
