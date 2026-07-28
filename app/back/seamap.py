"""Carte des mers : diagramme mermaid des clubs de TELECOM Nancy.

Chemin de code distinct du chat classique. Une demande de carte déclenche un
retrieval multi-requêtes (le `TOP_K = 5` du chat ne permet pas d'énumérer les
clubs à travers ~400 archives), un prompt dédié qui impose une syntaxe mermaid
stricte, puis un assainissement serveur du bloc produit avant envoi au navigateur.
"""

import logging
import re
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime

from groq import Groq

from .generate import GenerateRequest, generate_answer
from .groqpool import acquire
from .retrieval import search
from .types import GroqParams, SearchResult

logger = logging.getLogger(__name__)

# --- Détection de l'intention -------------------------------------------------

# Formulations reconnues comme une demande de carte. Appliquées à une forme sans
# diacritiques, pour absorber « schema » aussi bien que « schéma ».
_MAP_PATTERNS = re.compile(
    r"carte\s+(?:des\s+)?(?:mers|clubs|assos|associations)"
    r"|cartographie"
    r"|(?:schema|graphe|graph|mindmap|diagramme|arbre)\s+"
    r"(?:des?\s+)?(?:clubs|assos|associations|mers)"
    r"|mermaid",
    re.IGNORECASE,
)


def _strip_accents(text: str) -> str:
    """Retire les diacritiques, pour comparer « schéma » et « schema »."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def wants_map(question: str) -> bool:
    """Indique si la question demande une carte des clubs plutôt qu'une réponse."""
    return bool(_MAP_PATTERNS.search(_strip_accents(question)))


# --- Retrieval multi-requêtes -------------------------------------------------

# Angles sous lesquels les clubs apparaissent dans les archives (RO, comptes-rendus,
# budgets). La question de l'utilisateur est interrogée en plus : c'est elle qui
# remonte les chunks des clubs explicitement nommés quand la demande est ciblée.
_MAP_QUERIES = (
    "liste des clubs et associations de TELECOM Nancy",
    "pôles artistiques et culturels du BDA",
    "creation et vote d'un club en reunion ouverte",
)

MAP_TOP_K = 10
# Plafond calibré sur le tier Groq de l'organisation (8000 tokens/minute) :
# 24 chunks pèsent ~6000 tokens de prompt, la complétion tient dans le reste.
# Au-delà, Groq renvoie 413 et `_stream_with_retries` divise le contexte par deux.
MAP_MAX_CHUNKS = 24

# Paramètres de l'appel Groq propres à la carte. `reasoning_effort="none"` est
# indispensable : sur un prompt de cette taille, qwen3 consomme la totalité de
# son budget de complétion en <think> et ne produit jamais de diagramme.
MAP_GROQ_PARAMS: GroqParams = {
    "reasoning_effort": "none",
    "max_completion_tokens": 1200,
}


def retrieve_for_map(req: GenerateRequest) -> list[SearchResult]:
    """Agrège plusieurs recherches Qdrant pour couvrir l'ensemble des clubs.

    Chaque requête passe par `search()`, qui applique son propre reclassement par
    fraîcheur. Les résultats sont dédupliqués sur `point_id` en gardant le
    meilleur score, retriés, puis plafonnés à `MAP_MAX_CHUNKS` — garde-fou contre
    le 413 de Groq.
    """
    seen: dict[str, SearchResult] = {}
    for query in (req.question, *_MAP_QUERIES):
        for result in search(query, top_k=MAP_TOP_K):
            previous = seen.get(result["point_id"])
            if previous is None or result["score"] > previous["score"]:
                seen[result["point_id"]] = result

    results = sorted(seen.values(), key=lambda r: r["score"], reverse=True)
    logger.debug(
        "Carte des mers : %d chunks uniques sur %d requêtes (plafond %d).",
        len(results),
        len(_MAP_QUERIES) + 1,
        MAP_MAX_CHUNKS,
    )
    return results[:MAP_MAX_CHUNKS]


# --- Prompt -------------------------------------------------------------------

# Les backticks de la fence figurent littéralement dans l'exemple : un modèle à
# qui l'on décrit la fence sans la lui montrer produit régulièrement un bloc non
# fencé, que le front ne sait pas reconnaître.
_MAP_PROMPT_TEMPLATE = (
    "Tu es TN-GPT, l'expert de la vie associative de TELECOM Nancy.\n"
    "On te demande de dresser la carte au trésor des clubs : une carte mentale "
    "mermaid partant de TELECOM Nancy, ramifiée vers ses associations mères "
    "(BDE, BDA, BDS, TNS, Humani'TN...) puis vers leurs clubs.\n\n"
    "Format de ta réponse, dans cet ordre exact :\n"
    "1. Deux ou trois lignes maximum, ton décontracté, sans majuscule en début "
    "de phrase. Présente-la comme tu veux, n'emploie pas de formule imposée.\n"
    "2. Un seul bloc de code mermaid, exactement comme dans l'exemple.\n\n"
    "Règles du diagramme :\n"
    "- La première ligne du bloc est `mindmap`.\n"
    "- La hiérarchie se lit UNIQUEMENT à l'indentation : 2 espaces pour la "
    "racine, 4 pour une association, 6 pour un club. Jamais de flèches.\n"
    '- Une seule racine, toujours `root(("TELECOM Nancy"))`.\n'
    "- Chaque autre nœud s'écrit identifiant + crochets + guillemets doubles : "
    'N1["Club Œnologie"]. Les identifiants sont en ASCII sans accent ni espace '
    "(N1, N2, N3...).\n"
    "- Cette forme est OBLIGATOIRE : un libellé nu comme "
    "`Club Sportif (TOSS)` perd sa première moitié au rendu, seul "
    '`N1["Club Sportif (TOSS)"]` conserve le nom entier.\n'
    "- N'utilise jamais click, style, classDef, ::icon, ni de HTML.\n"
    "- N'inscris que des clubs présents dans les archives ci-dessous. "
    "N'invente jamais un club ni une association de tutelle.\n"
    "- Si la demande nomme des clubs précis, ne dessine QUE ceux-là, avec leur "
    "association de tutelle. Sinon, dessine tous les clubs trouvés.\n"
    "- Si les archives ne contiennent aucun club, réponds uniquement "
    "'je sais pas, je trouve pas dans mes archives', sans aucun bloc mermaid.\n\n"
    "Exemple de bloc attendu :\n"
    "```mermaid\n"
    "mindmap\n"
    '  root(("TELECOM Nancy"))\n'
    '    N1["BDE"]\n'
    '      N2["Club Œnologie"]\n'
    '      N3["Tek\'TN"]\n'
    '    N4["BDA"]\n'
    '      N5["Pôle Musique"]\n'
    "```\n\n"
    "Date d'aujourd'hui : {today}\n"
    "{user_line}\n"
    "ARCHIVES SECRÈTES (CONTEXTE) :\n"
    "{context}\n\n"
    "DEMANDE :\n"
    "{question}\n\n"
)


def build_map_prompt(context: str, question: str, user_name: str | None = None) -> str:
    """Construit le prompt de carte (compatible `generate.PromptBuilder`)."""
    today = datetime.now(UTC).strftime("%d %B %Y")
    user_line = f"Utilisateur connecté : {user_name}" if user_name else ""
    return _MAP_PROMPT_TEMPLATE.format(
        today=today,
        user_line=user_line,
        context=context,
        question=question,
    )


# --- Assainissement du mermaid ------------------------------------------------

_FENCE = re.compile(r"^\s*```")
# En-tête de diagramme : celui qu'on impose, ou un reliquat d'une autre syntaxe.
_HEADER = re.compile(r"^\s*(?:mindmap|graph|flowchart)\b", re.IGNORECASE)
# Directives capables d'injecter du style ou du JavaScript dans le rendu.
_FORBIDDEN = re.compile(
    r"^\s*(?:click|style|classDef|linkStyle|class)\b|<\s*script", re.IGNORECASE
)
# Décorations mermaid qu'on ne sert pas (icônes FontAwesome, classes CSS).
_DECORATIONS = re.compile(r"::icon\([^)]*\)|:::\S+")
# Puces de liste : le modèle glisse parfois « - » devant les nœuds.
_BULLET = re.compile(r"^[-*+]\s+")
# Un nœud déjà mis en forme, sous n'importe quelle syntaxe mermaid. Le libellé
# est capturé gourmandement et ancré à la fin, pour que `N1["Club (Marché)"]`
# rende « Club (Marché) » et non « Club (Marché ».
_SHAPED = re.compile(
    r"^[A-Za-z0-9_-]*"
    r"(?:\(\((?P<circle>.*)\)\)"
    r"|\[(?P<square>.*)\]"
    r"|\{\{(?P<hexa>.*)\}\}"
    r"|\((?P<round>.*)\))$"
)

_DEFAULT_HEADER = "mindmap"
_ROOT_LABEL = "TELECOM Nancy"
# Une racine seule ne dit rien : on exige au moins une ramification.
_MIN_NODES = 2
_MAX_LABEL = 60


def _extract_block(raw: str) -> str:
    """Isole le corps du bloc mermaid, en écartant ce qui suit la fence fermante."""
    stripped = raw.lstrip()
    if not stripped.startswith("```"):
        return raw
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    end = body.find("```")
    return body if end == -1 else body[:end]


def _clean_label(text: str) -> str:
    """Extrait le libellé humain d'une ligne de nœud, quelle qu'en soit la forme.

    Une carte mentale mermaid tronque silencieusement un libellé nu contenant des
    parenthèses — « Club Sportif (TOSS) » se rend « TOSS ». On récupère donc le
    texte complet ici, pour le réémettre ensuite sous la seule forme sûre.
    """
    text = _BULLET.sub("", _DECORATIONS.sub("", text).strip()).strip()
    match = _SHAPED.match(text)
    if match:
        for group in ("circle", "square", "hexa", "round"):
            captured = match.group(group)
            if captured is not None:
                text = captured.strip()
                break
    if len(text) > 1 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    # Un guillemet interne refermerait le libellé prématurément.
    return " ".join(text.replace('"', "'").split())[:_MAX_LABEL]


def _parse_entries(body: str) -> list[tuple[int, str]]:
    """Relève les (indentation, libellé) des nœuds, dans l'ordre du document."""
    entries: list[tuple[int, str]] = []
    for original in body.splitlines():
        line = original.rstrip()
        stripped = line.strip()
        if not stripped or _FENCE.match(line) or stripped.lower() == "mermaid":
            continue
        if _HEADER.match(line) or _FORBIDDEN.search(line):
            continue
        label = _clean_label(stripped)
        if label:
            entries.append((len(line) - len(line.lstrip()), label))
    return entries


def sanitize_mermaid(raw: str) -> str | None:
    """Nettoie une carte mentale mermaid produite par le modèle, ou None si vide.

    Réécrit chaque nœud sous la forme `id["libellé"]`, la seule qui préserve les
    accents, apostrophes et parenthèses des noms de clubs, et normalise
    l'indentation qui porte à elle seule la hiérarchie. Garantit une racine
    unique : mermaid refuse net un diagramme qui en compte deux.
    """
    entries = _parse_entries(_extract_block(raw))
    if len(entries) < _MIN_NODES:
        logger.warning("Carte mentale vide ou réduite à sa racine : abandonnée.")
        return None

    # Les indentations produites par le modèle sont irrégulières : on ne garde
    # que leur ordre relatif, converti en niveaux 0, 1, 2...
    depths = {
        indent: level for level, indent in enumerate(sorted({i for i, _ in entries}))
    }

    top = [label for indent, label in entries if depths[indent] == 0]
    # Racine explicite seulement si le modèle en a produit exactement une, en tête.
    has_root = len(top) == 1 and depths[entries[0][0]] == 0
    shift = 0 if has_root else 1

    lines = [_DEFAULT_HEADER]
    if not has_root:
        lines.append(f'  root(("{_ROOT_LABEL}"))')
    for position, (indent, label) in enumerate(entries):
        level = depths[indent] + shift
        pad = "  " * (level + 1)
        if level == 0:
            lines.append(f'{pad}root(("{label}"))')
        else:
            lines.append(f'{pad}N{position}["{label}"]')
    return "\n".join(lines)


# --- Génération ---------------------------------------------------------------

# Début du diagramme : fence explicite, ou en-tête mermaid émis sans fence.
_DIAGRAM_START = re.compile(
    r"```|(?:^|\n)[ \t]*(?:mindmap\b|(?:graph|flowchart)[ \t]+[A-Za-z]{2}\b)"
)
# Recouvrement gardé en tampon pour ne pas rater un marqueur coupé entre deux chunks.
_LOOKBACK = 16


def _split_prose_and_diagram(stream: Iterator[str]) -> Iterator[str]:
    """Streame la prose, met le diagramme en tampon, puis l'émet assaini.

    Le front ne peut rendre un diagramme qu'une fois complet : le streamer
    n'apporterait rien, et c'est ce tampon qui rend possible l'assainissement
    côté serveur.
    """
    pending = ""
    diagram = ""
    in_diagram = False

    for chunk in stream:
        if in_diagram:
            diagram += chunk
            continue

        pending += chunk
        match = _DIAGRAM_START.search(pending)
        if match:
            prose = pending[: match.start()]
            if prose.strip():
                yield prose
            diagram = pending[match.start() :]
            pending = ""
            in_diagram = True
        elif len(pending) > _LOOKBACK:
            yield pending[:-_LOOKBACK]
            pending = pending[-_LOOKBACK:]

    if not in_diagram:
        if pending:
            yield pending
        return

    code = sanitize_mermaid(diagram)
    if code:
        yield f"\n\n```mermaid\n{code}\n```\n"


def generate_map(
    req: GenerateRequest,
    results: list[SearchResult] | None = None,
    client: Groq | None = None,
) -> Iterator[str]:
    """Génère une réponse courte suivie de la carte des mers des clubs."""
    if results is None:
        results = retrieve_for_map(req)
    if client is None:
        client, _ = acquire()
    yield from _split_prose_and_diagram(
        generate_answer(
            req,
            results,
            client=client,
            build=build_map_prompt,
            params=MAP_GROQ_PARAMS,
        )
    )
