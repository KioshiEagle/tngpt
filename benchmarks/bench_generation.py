"""A/B de deux modèles de génération sur les vraies questions du journal.

Le contexte est récupéré une seule fois par question puis servi aux deux
modèles : on compare des générateurs, pas des pipelines de recherche.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import psycopg2
import requests
from dotenv import load_dotenv
from groq import APIStatusError, Groq

from app.back.clubs import lookup_context
from app.back.generate import CHAT_SYSTEM, _Contexte, build_prompt
from app.back.retrieval import search

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

BASELINE = "qwen/qwen3.6-27b"
CANDIDAT = "openai/gpt-oss-120b"

# Candidats souverains : mêmes questions, mêmes contextes, mais inférence en
# France. C'est la perte de qualité de ce déplacement qu'on cherche à chiffrer.
MISTRAL_MEDIUM = "mistral-medium-3.5"
MISTRAL_SMALL = "mistral-small-2603"


@dataclass(frozen=True)
class Route:
    """Fournisseur compatible OpenAI joint en HTTP direct, hors du pool."""

    url: str
    variable: str
    modele: str
    attente: float


# Le quota Groq appartient à la production : dès qu'une clé du fournisseur est
# fournie, le candidat part chez lui et ne dispute plus rien à personne.
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

_ROUTES: dict[str, Route] = {
    CANDIDAT: Route(CEREBRAS_URL, "CEREBRAS_API_KEY", "gpt-oss-120b", 62.0),
    MISTRAL_MEDIUM: Route(MISTRAL_URL, "MISTRAL_API_KEY", MISTRAL_MEDIUM, 2.0),
    MISTRAL_SMALL: Route(MISTRAL_URL, "MISTRAL_API_KEY", MISTRAL_SMALL, 2.0),
}

# Plafond de contexte du palier gratuit Cerebras. Le maximum mesuré sur nos
# questions est 6 837 tokens, mais une question hors norme doit être écartée
# plutôt que tronquée en silence — ce serait comparer deux contextes.
CEREBRAS_CONTEXTE_MAX = 8192

# Juge d'une troisième famille : ni qwen ni gpt-oss ne peut se favoriser.
JUGE = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

TOP_K = 5
TEMPERATURE = 0.3

# Groq décompte du quota par minute l'estimation « entrée + max_tokens » :
# sans plafond explicite, la réserve de sortie fait passer l'appel en 429.
MAX_TOKENS = 1000

# 8 000 tokens/minute par modèle sur le tier gratuit, ~7 600 par appel : un
# appel par minute et par modèle, les deux quotas étant indépendants.
ATTENTE_PAR_MODELE = 62.0

_HTTP_429 = 429
_HTTP_413 = 413
_ABANDON_APRES = 3

# En deçà, la clé normalisée est trop courte pour distinguer deux questions.
_CLE_MIN = 6

# Plafond d'une sieste : on se réveille pour reprendre la main régulièrement.
_SOMMEIL_MAX = 900.0


class QuotaEpuiseError(Exception):
    """Quota d'un fournisseur atteint, avec le délai qu'il réclame."""

    def __init__(self, modele: str, retry_after: str | None) -> None:
        """Retient le délai réclamé par le fournisseur, s'il en donne un."""
        super().__init__(f"{modele} : quota atteint")
        self.retry_after = retry_after


@dataclass
class Cadence:
    """Dernier appel par modèle, pour espacer sans bloquer l'autre modèle."""

    dernier: dict[str, float] = field(default_factory=dict)

    def attendre(self, modele: str) -> None:
        """Dort le temps qu'il faut pour rester sous le plafond du modèle.

        Le plafond est celui du fournisseur réellement appelé : la minute de
        Groq n'a pas à ralentir un candidat payant qui a ses propres limites.
        """
        precedent = self.dernier.get(modele)
        if precedent is not None:
            route = _route_de(modele)
            plafond = route.attente if route is not None else ATTENTE_PAR_MODELE
            reste = plafond - (time.monotonic() - precedent)
            if reste > 0:
                logger.info("Cadence %s : attente %.0f s", modele, reste)
                time.sleep(reste)
        self.dernier[modele] = time.monotonic()


def questions(limite: int) -> list[str]:
    """Questions distinctes du journal, dédoublonnées à la normalisation."""
    with psycopg2.connect(os.environ["DATABASE_URL"]) as cnx:
        cur = cnx.cursor()
        cur.execute(
            "SELECT question FROM queries WHERE result_count > 0 ORDER BY created_at"
        )
        lignes = [ligne[0] for ligne in cur.fetchall()]

    vus: set[str] = set()
    retenues: list[str] = []
    for brute in lignes:
        cle = re.sub(r"[^a-z0-9]+", " ", brute.lower()).strip()
        if len(cle) > _CLE_MIN and cle not in vus:
            vus.add(cle)
            retenues.append(brute.strip())
    return retenues[:limite]


def contexte_de(question: str) -> str:
    """Le bloc de contexte tel que la production le construit."""
    resultats = search(question, top_k=TOP_K)
    return _Contexte(fiches=lookup_context(question), results=resultats).rendu()


def _params(modele: str) -> dict[str, Any]:
    """Le chat n'a rien à raisonner : on coupe le budget de réflexion."""
    if modele.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    return {"reasoning_format": "hidden", "reasoning_effort": "none"}


# Une clé présente ne vaut pas un accès : un compte sans palier gratuit
# répond 402. On sonde une fois, puis on retombe sur Groq sans casser le banc.
_SONDES: dict[str, bool] = {}


def _utilisable(route: Route) -> bool:
    """Sonde l'accès au fournisseur une fois pour toutes, et retient la réponse."""
    if route.variable in _SONDES:
        return _SONDES[route.variable]
    if not os.getenv(route.variable):
        _SONDES[route.variable] = False
        return False
    reponse = requests.post(
        route.url,
        headers={"Authorization": f"Bearer {os.environ[route.variable]}"},
        json={
            "model": route.modele,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 1,
        },
        timeout=30,
    )
    _SONDES[route.variable] = reponse.ok
    if not reponse.ok:
        logger.warning(
            "%s inutilisable (HTTP %d) — le candidat repart sur Groq. %s",
            route.variable,
            reponse.status_code,
            reponse.text[:120],
        )
    return _SONDES[route.variable]


def _route_de(modele: str) -> Route | None:
    """Route directe du modèle, ou None s'il part sur le compte Groq."""
    route = _ROUTES.get(modele)
    return route if route is not None and _utilisable(route) else None


def _depouiller(donnees: dict[str, Any]) -> dict[str, Any]:
    """Extrait réponse et compteurs d'une charge au format OpenAI."""
    usage = donnees.get("usage", {})
    details = usage.get("prompt_tokens_details") or {}
    return {
        "reponse": donnees["choices"][0]["message"]["content"] or "",
        "tokens_entree": usage.get("prompt_tokens", 0),
        "tokens_sortie": usage.get("completion_tokens", 0),
        "tokens_caches": details.get("cached_tokens", 0) or 0,
    }


def _generer_http(route: Route, modele: str, prompt: str) -> dict[str, Any]:
    """Appel à une API compatible OpenAI, sans SDK supplémentaire."""
    reponse = requests.post(
        route.url,
        headers={"Authorization": f"Bearer {os.environ[route.variable]}"},
        json={
            "model": route.modele,
            "messages": [
                {"role": "system", "content": CHAT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        },
        timeout=120,
    )
    if reponse.status_code == _HTTP_429:
        raise QuotaEpuiseError(modele, reponse.headers.get("retry-after"))
    reponse.raise_for_status()
    return _depouiller(reponse.json())


def _generer_groq(client: Groq, modele: str, prompt: str) -> dict[str, Any]:
    """Appel au compte Groq — celui que se partage la production."""
    completion = client.chat.completions.create(
        model=modele,
        messages=[
            {"role": "system", "content": CHAT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        **_params(modele),
    )
    return _depouiller(completion.model_dump())


def generer(client: Groq, modele: str, prompt: str) -> dict[str, Any]:
    """Aiguille vers le fournisseur du modèle et rend ses compteurs."""
    route = _route_de(modele)
    if route is not None:
        return _generer_http(route, modele, prompt)
    return _generer_groq(client, modele, prompt)


_GRILLE = """Tu es un juge impartial. Évalue la RÉPONSE au regard du CONTEXTE.

Renvoie UNIQUEMENT un objet JSON avec ces clés, chacune un décimal de 0.0 à 1.0 :
- "fidelite" : la réponse ne dit rien que le contexte ne soutienne.
- "pertinence" : elle répond à la question posée.
- "completude" : elle exploite ce que le contexte offrait de pertinent.
- "ton" : registre d'étudiant, direct et cordial, sans jargon corporate.

CONTEXTE :
{contexte}

QUESTION : {question}

RÉPONSE À ÉVALUER :
{reponse}
"""


def juger(contexte: str, question: str, reponse: str) -> dict[str, float]:
    """Note une réponse sur Workers AI, hors des quotas Groq."""
    compte = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{compte}/ai/run/{JUGE}"
    charge = {
        "messages": [
            {
                "role": "user",
                "content": _GRILLE.format(
                    contexte=contexte[:12000], question=question, reponse=reponse
                ),
            }
        ],
        "temperature": 0.0,
        "max_tokens": 300,
    }
    # Le juge n'est pas la mesure : s'il tombe — quota de neurons épuisé,
    # réseau — on garde la génération et on note plus tard, plutôt que de
    # perdre la nuit sur une exception.
    try:
        reponse_http = requests.post(
            url,
            headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"},
            json=charge,
            timeout=90,
        )
        reponse_http.raise_for_status()
        charge_recue = reponse_http.json()["result"]
    except (requests.RequestException, KeyError, ValueError):
        logger.warning("Juge indisponible : mesure conservée sans notes.")
        return {}

    neurons = (charge_recue.get("usage") or {}).get("neurons")
    if neurons:
        logger.debug("juge : %.1f neurons", neurons)
    brut = charge_recue["response"]
    trouve = re.search(r"\{.*\}", brut, re.DOTALL)
    if trouve is None:
        logger.warning("Juge illisible : %s", brut[:200])
        return {}
    try:
        notes = json.loads(trouve.group())
    except ValueError:
        logger.warning("Juge illisible : %s", brut[:200])
        return {}
    return {
        cle: float(valeur)
        for cle, valeur in notes.items()
        if isinstance(valeur, int | float)
    }


def _deja_faites(sortie: Path) -> set[tuple[str, str]]:
    """Couples (question, modèle) déjà mesurés : une reprise ne les rejoue pas."""
    if not sortie.exists():
        return set()
    faites = set()
    for ligne in sortie.read_text(encoding="utf-8").splitlines():
        if ligne.strip():
            entree = json.loads(ligne)
            faites.add((entree["question"], entree["modele"]))
    return faites


def _pause_demandee(erreur: APIStatusError | QuotaEpuiseError) -> float:
    """Secondes à patienter, lues dans le message du 429 puis dans l'en-tête."""
    trouve = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(erreur))
    if trouve:
        minutes = float(trouve.group(1) or 0)
        return minutes * 60 + float(trouve.group(2)) + 5
    entete = (
        erreur.retry_after
        if isinstance(erreur, QuotaEpuiseError)
        else erreur.response.headers.get("retry-after")
    )
    return float(entete) + 5 if entete else 300.0


@dataclass(frozen=True)
class _Tache:
    """Une mesure à faire : la question, le modèle, et leur contexte commun."""

    question: str
    modele: str
    contexte: str


def _mesurer_ou_reporter(
    client: Groq,
    cadence: Cadence,
    tache: _Tache,
    reprise: dict[str, float],
    restantes: list[tuple[str, str]],
) -> dict[str, Any] | None:
    """Mesure la tâche, ou rend None après l'avoir reportée ou abandonnée.

    Un quota épuisé remet la tâche en file et arme la reprise du modèle ; un
    contexte trop grand l'abandonne, le banc ne pouvant pas le réduire sans
    cesser de comparer des générateurs à contexte égal.
    """
    try:
        return _mesurer(client, cadence, tache.question, tache.contexte, tache.modele)
    except (APIStatusError, QuotaEpuiseError) as erreur:
        if _contexte_trop_grand(erreur):
            logger.warning(
                "%s : contexte trop grand pour « %.40s », mesure abandonnée",
                tache.modele,
                tache.question,
            )
            return None
        if not _quota_atteint(erreur):
            raise
        delai = _pause_demandee(erreur)
        reprise[tache.modele] = time.time() + delai
        restantes.append((tache.question, tache.modele))
        logger.warning("%s à court de quota, repris dans %.0f s", tache.modele, delai)
        return None


def _consigner(
    flux: TextIO, question: str, modele: str, contexte: str, sortie: dict[str, Any]
) -> None:
    """Écrit une mesure et la vide sur disque, pour survivre à une coupure.

    L'empreinte du contexte accompagne la mesure : sans elle, rien ne dirait
    que deux modèles ont bien été jugés sur le même contexte.
    """
    empreinte = hashlib.sha256(contexte.encode()).hexdigest()[:16]
    flux.write(
        json.dumps(
            {
                "question": question,
                "modele": modele,
                "contexte_sha": empreinte,
                **sortie,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    flux.flush()


def _contexte_trop_grand(erreur: Exception) -> bool:
    """Dit si l'appel dépasse le plafond de contexte par minute du modèle.

    La production réduit alors le contexte ; le banc ne le peut pas sans
    cesser de comparer des générateurs à contexte égal, et renonce donc.
    """
    return isinstance(erreur, APIStatusError) and erreur.status_code == _HTTP_413


def _quota_atteint(erreur: Exception) -> bool:
    """Dit si l'erreur n'est qu'un quota épuisé, donc réessayable plus tard."""
    if isinstance(erreur, QuotaEpuiseError):
        return True
    return isinstance(erreur, APIStatusError) and erreur.status_code == _HTTP_429


def _mesurer(
    client: Groq, cadence: Cadence, question: str, contexte: str, modele: str
) -> dict[str, Any]:
    """Une génération et sa notation, pour un seul modèle."""
    cadence.attendre(modele)
    sortie = generer(client, modele, build_prompt(contexte, question))
    sortie["notes"] = juger(contexte, question, sortie["reponse"])
    return sortie


def _choisir(restantes: list[tuple[str, str]], reprise: dict[str, float]) -> int | None:
    """Rang de la première tâche dont le modèle n'est pas à court de quota."""
    maintenant = time.time()
    return next(
        (
            i
            for i, (_, modele) in enumerate(restantes)
            if reprise.get(modele, 0.0) <= maintenant
        ),
        None,
    )


def _patienter(restantes: list[tuple[str, str]], reprise: dict[str, float]) -> None:
    """Dort jusqu'au réveil du modèle le plus proche, par tranches bornées."""
    prochain = min(reprise[modele] for _, modele in restantes)
    delai = max(5.0, min(prochain - time.time(), _SOMMEIL_MAX))
    logger.info("Tous les modèles à court de quota — pause de %.0f s", delai)
    time.sleep(delai)


def _boucle(args: argparse.Namespace) -> None:
    """Avance modèle par modèle : celui qui a du quota continue sans l'autre."""
    # Réessais du SDK coupés : ils masqueraient les 429 qu'on veut compter.
    client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
    cadence = Cadence()

    faites = _deja_faites(args.sortie)
    voulus = [m.strip() for m in args.modeles.split(",") if m.strip()]
    restantes = [
        (question, modele)
        for question in questions(args.questions)
        for modele in voulus
        if (question, modele) not in faites
    ]
    logger.info("%d mesure(s) à faire, %d déjà en base", len(restantes), len(faites))

    # Un contexte par question, partagé par les deux modèles : sans ça on
    # comparerait des recherches et non des générateurs.
    contextes: dict[str, str] = {}
    reprise: dict[str, float] = {}

    with args.sortie.open("a", encoding="utf-8") as flux:
        while restantes:
            rang = _choisir(restantes, reprise)
            if rang is None:
                _patienter(restantes, reprise)
                continue

            question, modele = restantes.pop(rang)
            if question not in contextes:
                contextes[question] = contexte_de(question)
            contexte = contextes[question]

            sortie = _mesurer_ou_reporter(
                client, cadence, _Tache(question, modele, contexte), reprise, restantes
            )
            if sortie is None:
                continue

            _consigner(flux, question, modele, contexte, sortie)
            logger.info(
                "reste %d | %s | %s : %d tokens (%d en cache) %s",
                len(restantes),
                question[:40],
                modele,
                sortie["tokens_entree"],
                sortie["tokens_caches"],
                sortie["notes"],
            )

    logger.info("Terminé. Résultats dans %s", args.sortie)


def main() -> None:
    """Point d'entrée : le catalogue des clubs se lit en base, donc sous Flask."""
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--questions", type=int, default=30)
    parseur.add_argument("--sortie", type=Path, default=Path("bench_generation.jsonl"))
    # Le seau de quota de qwen est celui de la production : on doit pouvoir
    # mesurer le candidat seul, sans jamais réveiller la baseline.
    parseur.add_argument(
        "--modeles",
        default=f"{BASELINE},{CANDIDAT}",
        help="modèles à mesurer, séparés par des virgules",
    )
    args = parseur.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    # Importé ici et pas en tête : `main` importe le paquet qui contient ce
    # module, l'import global serait circulaire.
    from main import app  # noqa: PLC0415

    with app.app_context():
        _boucle(args)


if __name__ == "__main__":
    main()
