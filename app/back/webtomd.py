"""Conversion d'une page web en Markdown, troisième jumeau de `pdftomd`.

Même principe que `mailtomd` : la pipeline converge sur `upload_file`, qui lit
du Markdown à frontmatter, donc une source nouvelle n'a besoin que d'un
convertisseur vers cette forme.

Le site de l'école est du WordPress rendu côté serveur, dont l'API REST est
fermée (401) : on passe donc par le HTML, et par le sitemap plutôt que par un
parcours de liens — il donne la liste exhaustive sans deviner la structure.

Extraire le contenu principal n'est pas optionnel. Le menu, le pied de page et
le bandeau cookies pèsent l'essentiel des 150 Ko de chaque page ; recopiés dans
431 documents, ils formeraient un amas de chunks jumeaux que n'importe quelle
question irait percuter. C'est le rôle de trafilatura.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura

logger = logging.getLogger(__name__)

# Le titre commence par ce marqueur, comme celui d'un mail commence par « Mail ».
# `upload_file` le recopie en préfixe de chaque chunk : c'est ce qui dit au
# modèle qu'il lit le site officiel de l'école et non une archive étudiante.
_MARQUEUR = "Site TELECOM Nancy"

# En deçà, l'extraction n'a rien ramené d'exploitable : page de redirection,
# galerie d'images, formulaire seul. Mieux vaut aucun document qu'un document
# vide qui occuperait une ligne au catalogue.
_CORPS_MIN = 200

# Identifiant de document : `Document.source_id` est un VARCHAR(128).
_MAX_SOURCE_ID = 128

_NON_SLUG = re.compile(r"[^a-z0-9]+")
# Séparateurs possibles devant le suffixe du site, en échappement : le
# demi-cadratin est indiscernable du trait d'union à l'oeil dans le source.
_TIRETS = "-\u2013|"
_SUFFIXE_SITE = re.compile(rf"\s*[{_TIRETS}]\s*TELECOM NANCY\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Page:
    """Une page web réduite à ce qui part dans le Markdown."""

    url: str
    titre: str
    date: str
    auteur: str
    corps: str

    @property
    def source_id(self) -> str:
        """Identifiant stable du document, dérivé du chemin de l'URL.

        Dérivé de l'URL et non du titre : deux pages peuvent porter le même
        titre, jamais la même adresse. Un re-crawl retombe donc sur le même
        identifiant et remplace le document au lieu de le dupliquer.
        """
        chemin = _NON_SLUG.sub("-", urlparse(self.url).path.lower()).strip("-")
        return f"web-{chemin or 'accueil'}"[:_MAX_SOURCE_ID]

    @property
    def titre_complet(self) -> str:
        """Titre du frontmatter, repris en préfixe de chaque chunk."""
        return f"{_MARQUEUR} — {self.titre}"


def _nettoyer_titre(brut: str | None, url: str) -> str:
    """Titre sans le suffixe du site, qui serait répété sur 431 documents."""
    titre = _SUFFIXE_SITE.sub("", (brut or "").strip())
    if titre:
        return titre
    # Sans titre exploitable, le dernier segment d'URL vaut mieux que rien.
    segments = [s for s in urlparse(url).path.split("/") if s]
    return segments[-1].replace("-", " ") if segments else url


def lire(url: str, html: str) -> Page | None:
    """Extrait le contenu principal d'une page. None si rien d'exploitable.

    Renvoie None sur les pages de listing : trafilatura y prend le premier
    article de la liste pour le contenu principal, et produit un document
    attribué à une autre URL que celle demandée. La divergence entre l'URL
    canonique extraite et l'URL crawlée est ce qui les trahit — sans ce
    contrôle, « /vie-etudiante/ » entrerait en base sous le titre d'un article
    de 2023 sur le club poker.
    """
    corps = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=False,
        include_images=False,
        include_tables=True,
        include_comments=False,
    )
    if not corps or len(corps.strip()) < _CORPS_MIN:
        return None

    meta = trafilatura.extract_metadata(html)
    canonique = (meta.url if meta else None) or url
    if canonique.rstrip("/") != url.rstrip("/"):
        logger.debug("Page de listing ignorée : %s -> %s", url, canonique)
        return None

    return Page(
        url=url,
        titre=_nettoyer_titre(meta.title if meta else None, url),
        date=(meta.date if meta else None) or "",
        auteur=(meta.author if meta else None) or "",
        corps=corps.strip(),
    )


def rendre(page: Page) -> str:
    """Rend la page en Markdown à frontmatter, prêt pour `upload_file`.

    Le bloc « Infos » porte l'URL et la nature de la source. L'URL sert de
    citation vérifiable ; la nature dit au modèle que cette page fait autorité
    sur l'institutionnel — cursus, admissions, contacts — là où les archives
    étudiantes n'engagent que leurs auteurs.
    """
    infos = [
        (
            f"Type : page du site officiel de TELECOM Nancy, {page.url}. "
            f"Fait autorité sur l'information institutionnelle (formations, "
            f"admissions, organisation de l'école)."
        ),
    ]
    if page.date:
        infos.append(f"Dernière mise à jour : {page.date}")
    if page.auteur:
        infos.append(f"Auteur : {page.auteur}")

    # `_parse_frontmatter` coupe au premier deux-points et retire les guillemets
    # encadrants : un guillemet dans le titre tronquerait le sien.
    titre = page.titre_complet.replace('"', "'")
    return (
        f'---\ntitle: "{titre}"\ndate: "{page.date}"\n'
        f'author: "{page.auteur or "TELECOM Nancy"}"\n---\n\n'
        f"# {page.titre}\n\n"
        f"## Infos\n" + "\n".join(infos) + "\n\n"
        f"## Contenu\n{page.corps}\n"
    )


def lister_urls(client: httpx.Client, index: str) -> list[str]:
    """Toutes les URLs du sitemap, index de sitemaps compris.

    Le sitemap donne la liste exhaustive que publie le site lui-même : pas de
    parcours de liens à écrire, pas de piège à robots, et rien qui échappe.
    """
    trouvees = client.get(index).text
    sitemaps = re.findall(r"<loc>([^<]+sitemap[^<]*)</loc>", trouvees)

    urls: list[str] = []
    for sitemap in sitemaps or [index]:
        contenu = client.get(sitemap).text
        urls.extend(
            u for u in re.findall(r"<loc>([^<]+)</loc>", contenu) if "sitemap" not in u
        )
    return list(dict.fromkeys(urls))


def convert_url(url: str, html: str, output_dir: Path) -> Path | None:
    """Convertit une page en Markdown et retourne le chemin du `.md`.

    None quand la page n'a rien d'exploitable : l'appelant la compte comme
    ignorée, ce n'est pas une erreur.
    """
    page = lire(url, html)
    if page is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{page.source_id}.md"
    md_path.write_text(rendre(page), encoding="utf-8")
    return md_path
