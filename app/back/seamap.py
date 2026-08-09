"""Carte au trésor des clubs de TELECOM Nancy.

Chemin de code distinct du chat classique. Une demande de carte déclenche un
retrieval multi-requêtes (le `TOP_K = 5` du chat ne permet pas d'énumérer les
clubs à travers ~400 archives), puis un appel Groq dont l'outil `generate_map`
est forcé : le modèle renvoie des données structurées — clubs, tutelle, icône —
et non une syntaxe de diagramme à réparer. Le dessin appartient au navigateur.
"""

import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime

from groq import Groq, Stream
from groq.types.chat import ChatCompletionChunk, ChatCompletionToolParam

from .generate import CallSpec, GenerateRequest, generate_answer
from .groqpool import acquire
from .retrieval import search
from .textnorm import strip_accents
from .types import GroqParams, MapClub, MapPayload, SearchResult

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


def wants_map(question: str) -> bool:
    """Indique si la question demande une carte des clubs plutôt qu'une réponse."""
    return bool(_MAP_PATTERNS.search(strip_accents(question)))


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


def retrieve_for_map(req: GenerateRequest) -> list[SearchResult]:
    """Agrège plusieurs recherches Qdrant pour couvrir l'ensemble des clubs.

    Chaque requête passe par `search()`, qui applique son propre reclassement par
    fraîcheur. Les résultats sont dédupliqués sur `point_id` en gardant le
    meilleur score, retriés, puis plafonnés à `MAP_MAX_CHUNKS` — garde-fou contre
    le 413 de Groq.

    Le reranker est désactivé ici : la carte veut de la couverture, pas de la
    précision, et il coûterait un appel API par requête pour un ordre que la
    déduplication et le tri qui suivent recomposent entièrement.
    """
    seen: dict[str, SearchResult] = {}
    for query in (req.question, *_MAP_QUERIES):
        for result in search(query, top_k=MAP_TOP_K, rerank_results=False):
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


# --- Icônes ------------------------------------------------------------------

# Énumération fermée : c'est elle qui garantit qu'un symbole existe toujours.
# Toute valeur produite hors de cette liste est ramenée dans le rang par
# `_resolve_icone`.
ICONES = (
    "atelier",
    "gymnase",
    "terrain",
    "theatre",
    "musique",
    "palette",
    "chapiteau",
    "taverne",
    "vignoble",
    "tour",
    "bibliotheque",
    "laboratoire",
    "navire",
    "temple",
    "jeux",
    "drapeau",
)
_ICONE_DEFAUT = "drapeau"

# Repli déterministe quand le modèle propose une icône hors énumération.
# Appliqué au nom et à la tutelle désaccentués, dans l'ordre : la première
# correspondance gagne, donc du plus spécifique au plus général.
_ICONE_MOTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(motif, re.IGNORECASE), icone)
    for motif, icone in (
        (r"crea|fablab|bricol|atelier|makers?", "atelier"),
        (r"oeno|vin|biere|brass|degustation", "vignoble"),
        (r"cirque|jongl", "chapiteau"),
        (r"impro|theatre|danse|scene", "theatre"),
        (r"musique|fanfare|chorale|\bdj\b|djing|karaoke", "musique"),
        (r"dessin|photo|peinture|art\b|arts\b|graph", "palette"),
        (r"bar\b|cuisin|cooking|taverne|resto|cafe", "taverne"),
        (r"foot|basket|hand|rugby|volley|escalade|course|tennis|sport", "terrain"),
        (r"\bbds\b|gym|muscu|fitness", "gymnase"),
        (r"tek|reseau|informatique|technique|serveur|\btns\b", "tour"),
        (r"geopolit|debat|lecture|litt|biblio|philo|langue", "bibliotheque"),
        (r"labo|robot|scien|electro|astro|chimie", "laboratoire"),
        (r"voyage|nautique|voile|integration|\binte\b", "navire"),
        (r"patrimoine|histoire|memoire|archive", "temple"),
        (r"jeu|jdr|\btgd\b|echec|game|ludo", "jeux"),
    )
)


def _resolve_icone(propose: object, nom: str, tutelle: str) -> str:
    """Ramène l'icône d'un club dans l'énumération connue.

    Le modèle choisit en premier ; hors énumération, une table de mots-clés
    tranche sur le nom ; en dernier recours, un drapeau neutre. Aucun club ne
    peut donc se retrouver sans symbole sur la carte.
    """
    if isinstance(propose, str) and propose.strip().lower() in ICONES:
        return propose.strip().lower()
    cible = strip_accents(f"{nom} {tutelle}")
    for motif, icone in _ICONE_MOTS:
        if motif.search(cible):
            return icone
    return _ICONE_DEFAUT


# --- Logos --------------------------------------------------------------------

# Logos découpés de la plaquette alpha 2026-2027 (voir `app/front/static/logos/`).
# Le modèle ne les choisit pas : un club porte le sien ou n'en porte pas, et
# c'est son nom qui trie. Les 41 clubs de l'école n'ont pas tous un logo dans la
# plaquette — les absents gardent le pictogramme dessiné.
LOGOS = (
    "absoludique",
    "algo",
    "allintn",
    "amphisuze",
    "animest",
    "astn",
    "bar",
    "baroudeurs",
    "bda",
    "bde",
    "bds",
    "brasserie",
    "bravo",
    "breizhtn",
    "cooking",
    "creatn",
    "gala",
    "gaming",
    "hackintn",
    "humanitn",
    "instantthe",
    "inte",
    "marche",
    "minitel",
    "neuratn",
    "oenologie",
    "sdf",
    "studio",
    "tektn",
    "tgd",
    "tns",
    "touristn",
    "voyage",
)

# Motifs appliqués au nom désaccentué, du plus spécifique au plus général : la
# première correspondance gagne. `baroudeurs` précède `bar` pour cette raison.
_LOGO_MOTS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(motif, re.IGNORECASE), logo)
    for motif, logo in (
        (r"baroudeur", "baroudeurs"),
        (r"gaulois|inteductible|integration|\binte\b", "inte"),
        (r"anim.?est", "animest"),
        (r"humani", "humanitn"),
        (r"\bbde\b|ceten|cercle des eleves", "bde"),
        (r"\bbda\b|bureau des arts", "bda"),
        (r"\bbds\b|bureau des sports", "bds"),
        (r"\btns\b|telecom nancy services", "tns"),
        (r"allin|poker", "allintn"),
        (r"gala", "gala"),
        (r"studio", "studio"),
        (r"abso|ludique", "absoludique"),
        (r"voyage", "voyage"),
        (r"marche", "marche"),
        (r"chok|\bbar\b", "bar"),
        (r"mini.?tel|journal", "minitel"),
        (r"gaming", "gaming"),
        (r"algo", "algo"),
        (r"hackin|hacking", "hackintn"),
        (r"cooking|cuisine", "cooking"),
        (r"oeno", "oenologie"),
        (r"brasserie|brewery", "brasserie"),
        (r"breizh|breton|bretagne", "breizhtn"),
        (r"tek.?tn|\btek\b", "tektn"),
        (r"\btgd\b|telegame|game design", "tgd"),
        (r"crea", "creatn"),
        (r"\bastn\b|admis sur titre", "astn"),
        (r"instant.?the|podcast", "instantthe"),
        (r"touris", "touristn"),
        (r"bravo", "bravo"),
        (r"\bsdf\b|degommeurs|fromage", "sdf"),
        (r"neura", "neuratn"),
        (r"suze", "amphisuze"),
    )
)


def _resolve_logo(nom: str) -> str:
    """Slug du logo du club, ou chaîne vide s'il n'en a pas dans la plaquette.

    Seul le nom décide, jamais la tutelle : sans quoi tous les clubs d'un même
    bureau hériteraient du logo de leur association mère et la carte n'afficherait
    plus qu'une poignée de symboles répétés.
    """
    cible = strip_accents(nom)
    for motif, logo in _LOGO_MOTS:
        if motif.search(cible):
            return logo
    return ""


# --- Outil Groq ---------------------------------------------------------------

MAP_TOOL_NAME = "generate_map"

# Le schéma de l'outil EST le contrat de sortie. Forcer cet outil est la seule
# façon d'imposer une structure à qwen3.6-27b en un seul aller-retour : Groq ne
# supporte `response_format={"type": "json_schema"}` que sur les modèles gpt-oss,
# et le documente incompatible avec le streaming comme avec les outils.
_MAP_TOOL: ChatCompletionToolParam = {
    "type": "function",
    "function": {
        "name": MAP_TOOL_NAME,
        "description": (
            "Dresse la carte au trésor des clubs de TELECOM Nancy à partir des "
            "archives fournies."
        ),
        "parameters": {
            "type": "object",
            "required": ["commentaire", "clubs"],
            "properties": {
                "commentaire": {
                    "type": "string",
                    "description": (
                        "Deux ou trois lignes maximum présentant la carte, ton "
                        "décontracté, sans majuscule en début de phrase."
                    ),
                },
                "clubs": {
                    "type": "array",
                    "description": (
                        "Les clubs à faire figurer. Uniquement ceux présents dans "
                        "les archives ; n'invente jamais de club."
                    ),
                    "items": {
                        "type": "object",
                        "required": ["nom", "tutelle", "icone"],
                        "properties": {
                            "nom": {
                                "type": "string",
                                "description": "Nom du club tel qu'il apparaît.",
                            },
                            "tutelle": {
                                "type": "string",
                                "description": (
                                    "Association mère : BDE, BDA, BDS, TNS, "
                                    "Humani'TN... ou 'TELECOM Nancy' si inconnue."
                                ),
                            },
                            "icone": {
                                "type": "string",
                                "enum": list(ICONES),
                                "description": (
                                    "Le symbole qui évoque le mieux l'activité du "
                                    "club sur une carte."
                                ),
                            },
                        },
                    },
                },
            },
        },
    },
}

# --- Prompt -------------------------------------------------------------------

# Le prompt ne décrit plus aucune syntaxe : le schéma de l'outil s'en charge.
# Il ne reste que les règles métier.
_MAP_PROMPT_TEMPLATE = (
    "Tu es TN-GPT, l'expert de la vie associative de TELECOM Nancy.\n"
    "On te demande de dresser la carte au trésor des clubs. Appelle l'outil "
    "generate_map avec les clubs que tu trouves dans les archives ci-dessous.\n\n"
    "Règles :\n"
    "- N'inscris que des clubs présents dans les archives. N'invente jamais un "
    "club ni une association de tutelle.\n"
    "- Rattache chaque club à son association mère (BDE, BDA, BDS, TNS, "
    "Humani'TN...). Si elle est inconnue, mets 'TELECOM Nancy'.\n"
    "- Choisis pour chaque club l'icône qui évoque le mieux son activité : un "
    "club de bricolage prend l'atelier, un club sportif le terrain, un club "
    "d'œnologie le vignoble.\n"
    "- Si la demande nomme des clubs précis, ne retiens QUE ceux-là, avec leur "
    "association de tutelle. Sinon, retiens tous les clubs trouvés.\n"
    "- Si les archives ne contiennent aucun club, appelle quand même l'outil "
    "avec une liste vide et explique-le dans le commentaire.\n\n"
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


# --- Validation de la charge utile --------------------------------------------

MAP_MAX_CLUBS = 40
_MAX_NOM = 60
_MAX_TUTELLE = 40
_MAX_COMMENTAIRE = 400
_TUTELLE_DEFAUT = "TELECOM Nancy"


def _clean_club(entry: object) -> MapClub | None:
    """Valide une entrée de club, ou None si elle est inexploitable."""
    if not isinstance(entry, dict):
        return None
    nom = str(entry.get("nom") or "").strip()[:_MAX_NOM]
    if not nom:
        return None
    tutelle = str(entry.get("tutelle") or "").strip()[:_MAX_TUTELLE] or _TUTELLE_DEFAUT
    return MapClub(
        nom=nom,
        tutelle=tutelle,
        icone=_resolve_icone(entry.get("icone"), nom, tutelle),
        logo=_resolve_logo(nom),
    )


def build_map_payload(arguments: str) -> MapPayload | None:
    """Valide les arguments de l'outil, ou None s'il n'y a pas de carte à tirer.

    Remplace l'assainissement de syntaxe mermaid : on ne répare plus un langage,
    on vérifie des types et l'appartenance à une énumération.
    """
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Arguments de generate_map illisibles (JSON tronqué ?).")
        return None
    if not isinstance(parsed, dict):
        return None

    clubs: list[MapClub] = []
    seen: set[tuple[str, str]] = set()
    for entry in parsed.get("clubs") or []:
        club = _clean_club(entry)
        if club is None:
            continue
        key = (club["nom"].lower(), club["tutelle"].lower())
        if key in seen:
            continue
        seen.add(key)
        clubs.append(club)
        if len(clubs) >= MAP_MAX_CLUBS:
            logger.info("Carte plafonnée à %d clubs.", MAP_MAX_CLUBS)
            break

    if not clubs:
        logger.warning("Aucun club exploitable : pas de carte.")
        return None
    return MapPayload(
        commentaire=_trim_commentaire(str(parsed.get("commentaire") or "")),
        clubs=clubs,
    )


def _trim_commentaire(texte: str) -> str:
    """Plafonne le commentaire sans couper au milieu d'un mot.

    Le modèle déborde régulièrement des « deux ou trois lignes » demandées ;
    une coupe brute laisserait une phrase tronquée en tête de la carte.
    """
    texte = texte.strip()
    if len(texte) <= _MAX_COMMENTAIRE:
        return texte
    coupe = texte[:_MAX_COMMENTAIRE]
    fin = max(coupe.rfind(". "), coupe.rfind(" ! "), coupe.rfind(" ? "))
    if fin > _MAX_COMMENTAIRE // 2:
        return coupe[: fin + 1]
    return coupe[: coupe.rfind(" ")].rstrip(",;:") + "…"


# --- Génération ---------------------------------------------------------------

# Langue de la fence portant la charge utile. Le canal de sortie reste du texte
# streamé : le front réutilise tel quel son découpage prose / bloc fencé.
MAP_FENCE = "tngpt-carte"

MAP_GROQ_PARAMS: GroqParams = {
    # Sur un prompt de cette taille, qwen3 consomme sinon la totalité de son
    # budget de complétion en <think> et n'appelle jamais l'outil.
    "reasoning_effort": "none",
    # Groq refuse le format de raisonnement brut dès qu'on passe des outils.
    "reasoning_format": "hidden",
    "max_completion_tokens": 1500,
    "tools": [_MAP_TOOL],
    "tool_choice": {"type": "function", "function": {"name": MAP_TOOL_NAME}},
    # Un seul appel suffit, et chaque appel parallèle coûterait des tokens de
    # sortie sur un tier déjà serré.
    "parallel_tool_calls": False,
}


def _collect_tool_arguments(completion: Stream[ChatCompletionChunk]) -> Iterator[str]:
    """Concatène les fragments d'arguments de l'outil, puis émet la charge utile.

    Compatible `generate.CompletionConsumer`. Les arguments arrivent découpés
    dans `delta.tool_calls[].function.arguments` ; rien ne peut être exploité
    avant la fin, on n'émet donc qu'une fois le flux terminé.
    """
    arguments = ""
    for chunk in completion:
        for call in chunk.choices[0].delta.tool_calls or []:
            if call.function and call.function.arguments:
                arguments += call.function.arguments

    payload = build_map_payload(arguments)
    if payload is None:
        yield "je sais pas, je trouve pas de club dans mes archives"
        return
    if payload["commentaire"]:
        yield f"{payload['commentaire']}\n"
    # Seuls les clubs partent au navigateur : le commentaire est déjà dans la prose.
    carte = json.dumps({"clubs": payload["clubs"]}, ensure_ascii=False)
    yield f"\n```{MAP_FENCE}\n{carte}\n```\n"


MAP_SPEC = CallSpec(
    build=build_map_prompt,
    params=MAP_GROQ_PARAMS,
    consume=_collect_tool_arguments,
)


def generate_map(
    req: GenerateRequest,
    results: list[SearchResult] | None = None,
    client: Groq | None = None,
) -> Iterator[str]:
    """Génère un court commentaire suivi de la carte au trésor des clubs."""
    if results is None:
        results = retrieve_for_map(req)
    if client is None:
        client, _ = acquire()
    yield from generate_answer(req, results, client=client, spec=MAP_SPEC)
