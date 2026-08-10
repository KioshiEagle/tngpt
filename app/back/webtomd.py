"""Conversion d'une page web en Markdown, troisième jumeau de `pdftomd`.

WordPress à l'API REST fermée : on passe par le HTML et le sitemap. Extraire le
contenu principal est obligatoire, menu et pied de page pesant l'essentiel.
"""

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura

logger = logging.getLogger(__name__)

# Marqueur de titre, recopié en préfixe de chaque chunk : il dit au modèle
# qu'il lit le site officiel de l'école et non une archive étudiante.
_MARQUEUR = "Site TELECOM Nancy"

# En deçà, l'extraction n'a rien ramené d'exploitable : mieux vaut aucun
# document qu'un document vide au catalogue.
_CORPS_MIN = 200

# Identifiant de document : `Document.source_id` est un VARCHAR(128).
_MAX_SOURCE_ID = 128

# Archives WordPress : des listes d'amorces, sans contenu propre.
_SEGMENTS_ARCHIVE = ("/category/", "/tag/", "/author/")

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

        De l'URL et non du titre, que deux pages peuvent partager : un re-crawl
        remplace donc le document au lieu de le dupliquer.
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


def _hors_perimetre(url: str) -> bool:
    """Écarte les URLs sans contenu propre à indexer.

    Les doublons du sitemap, qui se réduisent au même `source_id` ; et les
    archives, sans contenu propre et à fraîcheur perpétuellement maximale.
    """
    parts = urlparse(url)
    return bool(parts.query) or any(
        segment in parts.path for segment in _SEGMENTS_ARCHIVE
    )


def lire(url: str, html: str) -> Page | None:
    """Extrait le contenu principal d'une page. None si rien d'exploitable.

    None aussi sur les pages de listing, que trahit la divergence entre URL
    canonique et URL crawlée : trafilatura y prend le premier article.
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

    Le bloc « Infos » porte l'URL, citation vérifiable, et la nature de la
    source, qui fait autorité sur l'institutionnel.
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


SITEMAP_ECOLE = "https://telecomnancy.univ-lorraine.fr/sitemap_index.xml"

# Le crawl s'annonce : un assistant qui aspire 431 pages doit être identifiable
# par l'administrateur du site, et joignable s'il pose problème.
_USER_AGENT = "TN-GPT/1.0 (assistant etudiant TELECOM Nancy)"
# Le site est celui de l'école, pas une cible : une pause entre deux requêtes
# évite de lui infliger une rafale pour un contenu qui bouge rarement.
_PAUSE = 0.3
_TIMEOUT = 30.0


def crawl(
    sitemap: str = SITEMAP_ECOLE,
    limite: int | None = None,
    *,
    simulation: bool = False,
) -> dict[str, int]:
    """Crawle un sitemap, convertit chaque page et l'ingère dans Qdrant.

    Les imports du catalogue sont faits ici et non en tête de module : `catalog`
    importe déjà `webtomd`, l'inverse au niveau module fermerait le cycle.
    """
    from flask import current_app  # noqa: PLC0415

    from .catalog import _ingest_worker  # noqa: PLC0415
    from .models import DOC_INDEXING, DOC_ORIGIN_WEB, Document, db  # noqa: PLC0415

    app = current_app._get_current_object()  # noqa: SLF001  # ty: ignore[unresolved-attribute]
    depot = Path(app.config["UPLOAD_DIR"])
    depot.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(
        timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    )
    urls = [u for u in lister_urls(client, sitemap) if not _hors_perimetre(u)][:limite]
    stats = {"total": len(urls), "converties": 0, "ignorees": 0, "echecs": 0}

    for rang, url in enumerate(urls, start=1):
        try:
            html = client.get(url).text
            md_path = convert_url(url, html, depot)
        except (httpx.HTTPError, OSError):
            logger.warning("Page injoignable : %s", url)
            stats["echecs"] += 1
            continue

        if md_path is None:
            stats["ignorees"] += 1
            continue
        stats["converties"] += 1

        if simulation:
            md_path.unlink(missing_ok=True)
        else:
            source_id = md_path.stem
            document = db.session.get(Document, source_id) or Document(
                source_id=source_id
            )
            db.session.add(document)
            document.status = DOC_INDEXING
            document.origin = DOC_ORIGIN_WEB
            document.error = None
            db.session.commit()
            _ingest_worker(app, source_id, md_path)

        if rang % 50 == 0:
            logger.info("Crawl : %d/%d", rang, len(urls))
        time.sleep(_PAUSE)

    return stats
