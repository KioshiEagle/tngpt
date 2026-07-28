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
    "On te demande de dessiner la « carte des mers » des clubs : un diagramme "
    "mermaid reliant TELECOM Nancy à ses associations mères (BDE, BDA, BDS, "
    "TNS, et Humani'TN) puis à leurs clubs.\n\n"
    "Format de ta réponse, dans cet ordre exact :\n"
    "1. Deux ou trois lignes maximum, ton décontracté, sans majuscule en début "
    "de phrase.\n"
    "2. Un seul bloc de code mermaid, exactement comme dans l'exemple.\n\n"
    "Règles du diagramme :\n"
    "- La première ligne du bloc est `graph LR`.\n"
    "- Les identifiants de nœuds sont en ASCII, sans accent ni espace "
    "(TN, BDE, C1, C2...).\n"
    "- Les libellés sont TOUJOURS entre guillemets doubles : "
    'C1["Club Œnologie"]. Sans guillemets, les accents, apostrophes et '
    "parenthèses des noms de clubs cassent le rendu.\n"
    "- N'utilise jamais click, style, classDef, linkStyle, ni de HTML.\n"
    "- N'inscris que des clubs présents dans les archives ci-dessous. "
    "N'invente jamais un club ni une association de tutelle.\n"
    "- Si la demande nomme des clubs précis, ne dessine QUE ceux-là, avec leur "
    "association de tutelle. Sinon, dessine tous les clubs trouvés.\n"
    "- Si les archives ne contiennent aucun club, réponds uniquement "
    "'je sais pas, je trouve pas dans mes archives', sans aucun bloc mermaid.\n\n"
    "Exemple de bloc attendu :\n"
    "```mermaid\n"
    "graph LR\n"
    '  TN(("TELECOM Nancy"))\n'
    '  TN --> BDE["BDE"]\n'
    '  TN --> BDA["BDA"]\n'
    '  BDE --> C1["Club Œnologie"]\n'
    '  BDA --> C2["Club Impro"]\n'
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
_HEADER = re.compile(r"^\s*(?:graph|flowchart)\b", re.IGNORECASE)
# Directives capables d'injecter du style ou du JavaScript dans le rendu.
_FORBIDDEN = re.compile(
    r"^\s*(?:click|style|classDef|linkStyle|class)\b|<\s*script", re.IGNORECASE
)
_EDGE = re.compile(r"-{2,}>|-\.-+>|={2,}>|-{3,}(?!>)")

# Un nœud et son libellé, toutes formes confondues. Les alternatives sont
# ordonnées du délimiteur le plus enveloppant au plus simple — `((` avant `(` —
# et la substitution se fait en UNE passe, sans réexaminer le texte déjà
# remplacé : un libellé déjà entre guillemets qui contient des parenthèses
# n'est donc jamais réécrit.
_NODE = re.compile(
    r"(?P<id>\b[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:"
    r"\(\((?P<circle>[^()]*)\)\)"
    r"|\[(?P<square>[^\[\]]*)\]"
    r"|\{(?P<brace>[^{}]*)\}"
    r"|\((?P<round>[^()]*)\)"
    r")"
)
_SHAPES = (
    ("circle", "((", "))"),
    ("square", "[", "]"),
    ("brace", "{", "}"),
    ("round", "(", ")"),
)

_DEFAULT_HEADER = "graph LR"


def _quote_node(match: re.Match[str]) -> str:
    """Réécrit un nœud avec son libellé entre guillemets doubles."""
    for group, opener, closer in _SHAPES:
        label = match.group(group)
        if label is None:
            continue
        label = label.strip()
        if len(label) > 1 and label.startswith('"') and label.endswith('"'):
            return f"{match.group('id')}{opener}{label}{closer}"
        # Un guillemet interne refermerait le libellé prématurément.
        safe = label.replace('"', "'")
        return f'{match.group("id")}{opener}"{safe}"{closer}'
    return match.group(0)


def _extract_block(raw: str) -> str:
    """Isole le corps du bloc mermaid, en écartant ce qui suit la fence fermante."""
    stripped = raw.lstrip()
    if not stripped.startswith("```"):
        return raw
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    end = body.find("```")
    return body if end == -1 else body[:end]


def sanitize_mermaid(raw: str) -> str | None:
    """Nettoie un bloc mermaid produit par le modèle, ou None s'il est inexploitable.

    Retire fences et directives interdites, force `graph LR` en tête, met les
    libellés entre guillemets et déduplique les lignes. Renvoie None quand il ne
    reste aucune arête : mieux vaut pas de carte qu'une carte cassée.
    """
    lines: list[str] = []
    seen: set[str] = set()

    for original in _extract_block(raw).splitlines():
        line = original.rstrip()
        if not line.strip() or _FENCE.match(line):
            continue
        if line.strip().lower() == "mermaid":
            continue
        if _FORBIDDEN.search(line):
            continue
        # L'en-tête est réécrit une seule fois, en tête du bloc final.
        if _HEADER.match(line):
            continue
        cleaned = _NODE.sub(_quote_node, line)
        key = cleaned.strip()
        if key in seen:
            continue
        seen.add(key)
        lines.append(cleaned)

    if not any(_EDGE.search(line) for line in lines):
        logger.warning("Bloc mermaid sans arête exploitable : carte abandonnée.")
        return None

    return "\n".join([_DEFAULT_HEADER, *lines])


# --- Génération ---------------------------------------------------------------

# Début du diagramme : fence explicite, ou en-tête mermaid émis sans fence.
_DIAGRAM_START = re.compile(r"```|(?:^|\n)[ \t]*(?:graph|flowchart)[ \t]+[A-Za-z]{2}\b")
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
