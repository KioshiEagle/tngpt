"""A/B de deux modèles de génération sur les vraies questions du journal.

Le contexte est récupéré une seule fois par question puis servi aux deux
modèles : on compare des générateurs, pas des pipelines de recherche.
"""

import argparse
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

# Juge d'une troisième famille : ni qwen ni gpt-oss ne peut se favoriser.
JUGE = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

TOP_K = 5
TEMPERATURE = 0.3

# 8 000 tokens/minute par modèle sur le tier gratuit, ~7 600 par appel : un
# appel par minute et par modèle, les deux quotas étant indépendants.
ATTENTE_PAR_MODELE = 62.0

_HTTP_429 = 429
_ABANDON_APRES = 3

# En deçà, la clé normalisée est trop courte pour distinguer deux questions.
_CLE_MIN = 6


@dataclass
class Cadence:
    """Dernier appel par modèle, pour espacer sans bloquer l'autre modèle."""

    dernier: dict[str, float] = field(default_factory=dict)

    def attendre(self, modele: str) -> None:
        """Dort le temps qu'il faut pour rester sous le plafond du modèle."""
        precedent = self.dernier.get(modele)
        if precedent is not None:
            reste = ATTENTE_PAR_MODELE - (time.monotonic() - precedent)
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


def generer(client: Groq, modele: str, prompt: str) -> dict[str, Any]:
    """Un appel non streamé, avec ses compteurs de tokens."""
    completion = client.chat.completions.create(
        model=modele,
        messages=[
            {"role": "system", "content": CHAT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=TEMPERATURE,
        **_params(modele),
    )
    usage = completion.usage
    details = getattr(usage, "prompt_tokens_details", None)
    return {
        "reponse": completion.choices[0].message.content or "",
        "tokens_entree": usage.prompt_tokens,
        "tokens_sortie": usage.completion_tokens,
        "tokens_caches": getattr(details, "cached_tokens", 0) or 0,
    }


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
    reponse_http = requests.post(
        url,
        headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"},
        json=charge,
        timeout=90,
    )
    reponse_http.raise_for_status()
    brut = reponse_http.json()["result"]["response"]
    trouve = re.search(r"\{.*\}", brut, re.DOTALL)
    if trouve is None:
        logger.warning("Juge illisible : %s", brut[:200])
        return {}
    return {
        cle: float(valeur)
        for cle, valeur in json.loads(trouve.group()).items()
        if isinstance(valeur, int | float)
    }


def _deja_faites(sortie: Path) -> set[str]:
    """Questions déjà traitées : une reprise ne les rejoue pas."""
    if not sortie.exists():
        return set()
    faites = set()
    for ligne in sortie.read_text(encoding="utf-8").splitlines():
        if ligne.strip():
            faites.add(json.loads(ligne)["question"])
    return faites


def _une_question(
    client: Groq, cadence: Cadence, question: str, ordre: list[str]
) -> dict[str, Any]:
    """Contexte commun, une génération par modèle, puis notation à l'aveugle."""
    contexte = contexte_de(question)
    entree: dict[str, Any] = {"question": question, "modeles": {}}

    for modele in ordre:
        cadence.attendre(modele)
        sortie = generer(client, modele, build_prompt(contexte, question))
        sortie["notes"] = juger(contexte, question, sortie["reponse"])
        entree["modeles"][modele] = sortie
        logger.info(
            "  %s : %d tokens (%d en cache), notes %s",
            modele,
            sortie["tokens_entree"],
            sortie["tokens_caches"],
            sortie["notes"],
        )
    return entree


def _boucle(args: argparse.Namespace) -> None:
    """Parcourt les questions restantes et journalise chaque comparaison."""
    # Réessais du SDK coupés : ils masqueraient les 429 qu'on veut compter.
    client = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0)
    cadence = Cadence()

    faites = _deja_faites(args.sortie)
    a_faire = [q for q in questions(args.questions) if q not in faites]
    logger.info("%d question(s) à traiter, %d déjà faites", len(a_faire), len(faites))

    quota_epuise = 0
    with args.sortie.open("a", encoding="utf-8") as flux:
        for rang, question in enumerate(a_faire, 1):
            logger.info("[%d/%d] %s", rang, len(a_faire), question[:70])
            # L'ordre alterne : le premier appelé profite d'un cache plus chaud.
            ordre = [BASELINE, CANDIDAT]
            random.shuffle(ordre)
            try:
                entree = _une_question(client, cadence, question, ordre)
            except APIStatusError as erreur:
                if erreur.status_code != _HTTP_429:
                    raise
                quota_epuise += 1
                logger.warning(
                    "429 (%d/%d) : quota probablement épuisé",
                    quota_epuise,
                    _ABANDON_APRES,
                )
                if quota_epuise >= _ABANDON_APRES:
                    logger.warning("Quota journalier atteint — reprise demain.")
                    break
                time.sleep(120)
                continue
            quota_epuise = 0
            flux.write(json.dumps(entree, ensure_ascii=False) + "\n")
            flux.flush()

    logger.info("Terminé. Résultats dans %s", args.sortie)


def main() -> None:
    """Point d'entrée : le catalogue des clubs se lit en base, donc sous Flask."""
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--questions", type=int, default=30)
    parseur.add_argument("--sortie", type=Path, default=Path("bench_generation.jsonl"))
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
