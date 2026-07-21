import argparse
import hashlib
import json
import optuna
import random
import statistics
import time
import logging
import math
import pickle
import numpy as np
import pandas as pd
from pathlib import Path
from collections import OrderedDict
from sentence_transformers import SentenceTransformer

from app.back.chunking import get_hybrid_chunks
from app.back.mdtoqdrant import _parse_frontmatter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 0. CACHE DISQUE (survit à un arrêt/relance du process)
# ==========================================
# Recalculer le chunking + les embeddings de 404 documents est ce qui coûte
# des minutes par trial. Le cache mémoire (OrderedDict) ci-dessous est perdu à
# chaque redémarrage du process ; ce cache disque persiste entre deux lancements
# du script, donc reprendre après un Ctrl+C ne recalcule jamais deux fois la
# même combinaison (chunk_size, overlap, modèle).
# _CACHE_VERSION est inclus dans la clé de hash : bumper cette chaîne invalide
# tout le cache d'un coup si la logique de chunking/embedding change (évite de
# servir silencieusement un résultat calculé avec l'ancienne méthode).
_CACHE_VERSION = "v2-e5prefix-metadata"
DISK_CACHE_DIR = Path(__file__).resolve().parent / "temp" / "optuna_cache"
DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _disk_cache_path(prefix: str, key: tuple, suffix: str) -> Path:
    versioned_key = (_CACHE_VERSION, *key)
    digest = hashlib.sha256(repr(versioned_key).encode()).hexdigest()[:16]
    return DISK_CACHE_DIR / f"{prefix}_{digest}.{suffix}"


# Modèles suivant la convention d'entraînement asymétrique E5 ("query: "/
# "passage: "). Répliquer cette convention est nécessaire pour comparer les
# modèles équitablement : sans elle, e5-small/e5-base sont évalués "mal
# utilisés" par rapport à la façon dont ils tournent réellement en prod
# (voir retrieval.py:62 et mdtoqdrant.py:109-111).
_E5_MODELS = {"e5-small", "e5-base"}


def _passage_text(chunk_text: str, model_name: str, date: str, title: str) -> str:
    """Réplique le préfixage de mdtoqdrant.py:109-111 (date/titre + convention e5)."""
    date_prefix = f"[Date: {date}] " if date else ""
    title_prefix = f"[Source: {title}] " if title else ""
    metadata = f"{date_prefix}{title_prefix}"
    if model_name in _E5_MODELS:
        return f"passage: {metadata}{chunk_text}"
    return f"{metadata}{chunk_text}"


def _query_text(query: str, model_name: str) -> str:
    """Réplique retrieval.py:62 (convention e5 : préfixe 'query: ')."""
    if model_name in _E5_MODELS:
        return f"query: {query}"
    return query


TRIAL_LOG_PATH = Path(__file__).resolve().parent / "optuna_retrieval_trial_log.jsonl"


def _append_trial_log(record: dict) -> None:
    """Journal détaillé (1 ligne JSON par trial), indépendant de la base Optuna.

    Garde le détail par question (score, sources retrouvées) que la base
    sqlite ne stocke pas, pour pouvoir déboguer une config précise après coup.
    """
    with TRIAL_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# Cache models
_embedding_models_cache = {}
def _get_embedding_model(model_name: str):
    model_map = {
        "miniLM": "all-MiniLM-L6-v2",
        "e5-small": "intfloat/multilingual-e5-small",
        "e5-base": "intfloat/multilingual-e5-base",
        "bge-m3": "BAAI/bge-m3"
    }
    real_name = model_map.get(model_name, model_name)
    if real_name not in _embedding_models_cache:
        logger.info(f"Chargement du modèle d'embedding: {real_name}")
        _embedding_models_cache[real_name] = SentenceTransformer(real_name)
    return _embedding_models_cache[real_name]

# ==========================================
# 1. DATASET DE RETRIEVAL (100% SANS LLM)
# ==========================================
# Questions écrites à la main à partir du vrai corpus (app/back/temp/markdowns,
# comptes-rendus BDE réels téléchargés depuis Drive). Chaque `expected_documents`
# référence l'ID Drive (nom de fichier sans extension) du/des documents source
# vérifiés manuellement. Aucune duplication artificielle : chaque question est
# unique et pèse une seule fois dans le score.
RETRIEVAL_DATASET = [
    # --- CR BDE du 03/01/2022 (1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6) ---
    {"question": "Qui a présidé la réunion du Bureau des Élèves du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"]},
    {"question": "Qui présidait le Bureau des Élèves lors de la séance du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"]},
    {"question": "Qui était secrétaire de séance à la réunion du BDE du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"]},
    {"question": "Quelles sont les dates des campagnes d'intégration 2022 annoncées lors de la réunion du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"]},
    {"question": "Contre quelle entreprise le CETEN a-t-il un procès en cours, évoqué lors de la réunion du BDE du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"]},
    {"question": "Quel délai la secrétaire du BDE 2022 annonce-t-elle pour l'envoi des comptes-rendus ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"]},

    # --- AGO du 25/01/2022 (1El47-pyPFL27hO2pdCX_XXWWfl03mIeC) ---
    {"question": "Qui a présidé l'Assemblée Générale Ordinaire du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"]},
    {"question": "À quel pourcentage le bilan moral 2021 a-t-il été accepté lors de l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"]},
    {"question": "Quel pourcentage de votes favorables a obtenu le bilan moral 2021 à l'Assemblée Générale Ordinaire du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"]},
    {"question": "À quel pourcentage le bilan financier 2021 a-t-il été accepté lors de l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"]},
    {"question": "Combien de membres étaient présents ou représentés à l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"]},
    {"question": "Qui a présenté le bilan financier 2021 lors de l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"]},

    # --- AGE du 11/01/2022 (1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z) ---
    {"question": "Combien de membres étaient présents ou représentés à l'Assemblée Générale Extraordinaire du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"]},
    {"question": "Combien de personnes avaient le droit de vote à l'AGE du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"]},
    {"question": "À quel pourcentage la modification de l'article 10.4.2 sur la composition du bureau a-t-elle été adoptée lors de l'AGE du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"]},
    {"question": "Quel était le taux d'approbation de la modification sur les modalités des élections du bureau des élèves à l'AGE du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"]},

    # --- Réunion Ouverte BDE n°01 du 23/01/2024 (1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY) ---
    {"question": "Quel jour aura lieu la soirée de désintégration selon la réunion ouverte BDE n°1 du 23 janvier 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"]},
    {"question": "Quel est le montant du chèque de caution demandé aux non-adhérents CETEN pour la désintégration 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"]},
    {"question": "Quel montant de caution est demandé à un non-adhérent CETEN pour participer à la désintégration 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"]},
    {"question": "Qui est président du club Typst'n créé lors de la réunion ouverte BDE n°1 du 23 janvier 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"]},
    {"question": "Le club Ni'TN'do a-t-il été accepté ou rejeté lors de sa création à la réunion ouverte BDE n°1 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"]},
    {"question": "Pourquoi la soirée blindtest a-t-elle été reportée d'après la réunion ouverte BDE n°1 du 23 janvier 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"]},

    # --- Réunion Ouverte BDE n°02 du 30/01/2024 (1yetct2L4YDYjngMNRF6u83MYmK-PNWUO) ---
    {"question": "Où a eu lieu la soirée de désintégration 2024 d'après la réunion ouverte BDE n°2 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"]},
    {"question": "Qui est président du club Degus'TN d'après la réunion ouverte BDE n°2 du 30 janvier 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"]},
    {"question": "Qui dirige le club Degus'TN après sa reprise en janvier 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"]},
    {"question": "Quel club a été dissous lors de la réunion ouverte BDE n°2 du 30 janvier 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"]},
    {"question": "Quelle liste a été élue pour reprendre le club intégration selon la réunion ouverte BDE n°2 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"]},
    {"question": "Quel club de listes d'intégration a repris l'organisation de la désintégration en 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"]},

    # --- Réunion Ouverte BDE n°03 du 13/02/2024 (1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M) ---
    {"question": "Pour quelle date les budgets prévisionnels annuels des clubs doivent-ils être envoyés d'après la réunion ouverte BDE n°3 du 13 février 2024 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},
    {"question": "Avant quelle heure les clubs doivent-ils envoyer leur budget prévisionnel selon la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},
    {"question": "Quels clubs sont exemptés de budget prévisionnel annuel selon la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},
    {"question": "Qu'est-ce que le Vaultwarden mis en place pour les clubs, selon la réunion ouverte BDE n°3 du 13 février 2024 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},
    {"question": "Quand a eu lieu le premier événement du club Brasserie d'après la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},
    {"question": "Quel événement du club Brasserie a eu lieu le 13 février 2024 en début de soirée ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},
    {"question": "Qui préside la proposition de club 'Bibi and Smoothie' finalement élue lors de la réunion ouverte BDE n°3 du 13 février 2024 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},
    {"question": "Quand ont lieu les journées portes ouvertes de Telecom Nancy mentionnées lors de la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"]},

    # --- Festival de Canards, mai 2022 (1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY) ---
    {"question": "Quel club a remporté le TéléCésar du meilleur nom lors de la cérémonie fictive décrite dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"]},
    {"question": "Quel club de TELECOM Nancy a remporté le prix fictif du meilleur nom dans l'édition de mai 2022 du journal ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"]},
    {"question": "Quel club a remporté le TéléCésar du meilleur logo dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"]},
    {"question": "Quel club a remporté le TéléCésar du meilleur espoir dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"]},
    {"question": "Qui a remporté le TéléCésar du meilleur logo temporaire dans le Festival de Canards ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"]},
    {"question": "Quels ingrédients sont nécessaires pour la recette des Lembas publiée dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"]},

    # --- Interview de Marc Tomczak (13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B) ---
    {"question": "En quelle année Marc Tomczak a-t-il commencé sa carrière d'enseignant-chercheur à Telecom Nancy ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"]},
    {"question": "Depuis quelle année Marc Tomczak enseigne-t-il à l'école ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"]},
    {"question": "En quelle année l'école a-t-elle déménagé dans son bâtiment actuel d'après l'interview de Marc Tomczak ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"]},
    {"question": "Quelle est la passion de Marc Tomczak qu'il évoque à plusieurs reprises dans son interview ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"]},
    {"question": "Qui, d'après son interview, a inventé les points CIPA à Telecom Nancy ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"]},

    # --- Multi-documents : deux comptes-rendus de la même réunion du 10/01/2022 ---
    # (1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc = version officielle, 1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET = version « fake CR » humoristique)
    {"question": "Quels clubs ont été dissous lors de la réunion du Bureau des Élèves du 10 janvier 2022 ?", "expected_documents": ["1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc", "1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET"]},
    {"question": "Où aura lieu la soirée de désintégration annoncée lors de la réunion du BDE du 10 janvier 2022 ?", "expected_documents": ["1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc", "1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET"]},
    {"question": "Quel a été le taux de participation à la passation des clubs annoncé lors de la réunion du BDE du 10 janvier 2022 ?", "expected_documents": ["1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc", "1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET"]},

    # --- Hors périmètre (rien dans le corpus ne doit répondre à ces questions) ---
    {"question": "Quel est le prix d'un repas à la cafétéria de Telecom Nancy ?", "expected_documents": []},
    {"question": "Combien coûte l'abonnement internet du campus ?", "expected_documents": []},
    {"question": "Quel est le numéro de téléphone du secrétariat de Telecom Nancy ?", "expected_documents": []},
    {"question": "Quels sont les horaires d'ouverture de la bibliothèque de Telecom Nancy ?", "expected_documents": []},
    {"question": "Qui est l'actuel directeur de Telecom Nancy en 2026 ?", "expected_documents": []},
    {"question": "Quel est le taux de réussite au diplôme d'ingénieur de Telecom Nancy ?", "expected_documents": []},
    {"question": "Quelle est la recette du couscous du club Marché de TELECOM ?", "expected_documents": []},
    {"question": "Quel jour de la semaine a lieu le cours de mathématiques discrètes ?", "expected_documents": []},
    {"question": "Quel est le montant des frais de scolarité annuels à Telecom Nancy ?", "expected_documents": []},
    {"question": "Combien d'élèves ingénieurs sont diplômés chaque année à Telecom Nancy ?", "expected_documents": []},

    # --- CR BDE du 01/02/2016 (1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2) — élargit la couverture aux docs anciens ---
    {"question": "Qui était président du Bureau des Élèves lors de la réunion du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"]},
    {"question": "Qui était secrétaire du BDE à la réunion du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"]},
    {"question": "Quel a été le résultat du vote pour la recréation du club Ten24 lors de la réunion du BDE du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"]},
    {"question": "De quelle marque Youri est-il devenu ambassadeur d'après le compte-rendu du BDE du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"]},
    {"question": "Combien de réponses le sondage sur le changement de logo a-t-il recueillies d'après le compte-rendu du BDE du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"]},
    {"question": "En quelle année le club Ten24 a-t-il été recréé après une première dissolution, selon les archives du BDE ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"]},

    # --- CR BDE du 01/02/2018 (1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-) ---
    {"question": "Quels clubs ont été regroupés au sein du BDA d'après le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"]},
    {"question": "Quels 5 clubs artistiques ont fusionné pour former le BDA de Telecom Nancy ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"]},
    {"question": "Qui est devenue présidente du BDA selon le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"]},
    {"question": "Qui a été élu président du club Telecome Cooking selon la réunion du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"]},
    {"question": "Qui préside le Club Jeux d'après le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"]},
    {"question": "Quel jour débutent les campagnes d'intégration selon le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"]},

    # --- AGO du 21/01/2025 (169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd) ---
    {"question": "Qui a présidé l'Assemblée Générale Ordinaire du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"]},
    {"question": "Qui dirigeait le BDE juste avant l'arrivée de Raphaël Roullet, d'après l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"]},
    {"question": "Qui a été désigné secrétaire de séance à l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"]},
    {"question": "À quel pourcentage le bilan moral 2024 a-t-il été accepté lors de l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"]},
    {"question": "Quel pourcentage de votes favorables a recueilli le bilan financier 2024 à l'assemblée générale de janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"]},
    {"question": "Qui devient président du BDE 2025 d'après l'Assemblée Générale Ordinaire du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"]},
    {"question": "Combien de membres étaient présents ou représentés à l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"]},

    # --- Réunion Ouverte BDE n°1 du 04/02/2025 (1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk) ---
    {"question": "Quelle liste d'intégration a été élue lors de la réunion ouverte BDE n°1 du 4 février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"]},
    {"question": "Avec quel pourcentage des voix l'Empire Inté'Galactique a-t-il été élu selon la réunion ouverte BDE n°1 du 4 février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"]},
    {"question": "Quelle liste a perdu l'élection intégration face à l'Empire Inté'Galactique en février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"]},
    {"question": "Le club 'Bacon Burger TN' a-t-il été accepté lors de sa création à la réunion ouverte BDE n°1 du 4 février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"]},

    # --- Réunion Ouverte BDE n°2 du 11/02/2025 (1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F) ---
    {"question": "Quel club a proposé de vendre des croquettes pour humains lors de sa création à la réunion ouverte BDE n°2 du 11 février 2025 ?", "expected_documents": ["1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"]},
    {"question": "Le club Croquet'TN a-t-il été accepté lors de la réunion ouverte BDE n°2 du 11 février 2025 ?", "expected_documents": ["1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"]},
    {"question": "Quel est le nom du club proposé par Maxence Osawa-Bourbon lors de la réunion ouverte BDE n°2 du 11 février 2025 ?", "expected_documents": ["1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"]},

    # --- Multi-documents : le TN'Event 2025 est annoncé dans la RO n°1 (bénéficiaire) ---
    # et son résultat chiffré n'apparaît que dans la RO n°2 (montant récolté) : une
    # réponse complète nécessite réellement les deux documents, contrairement aux
    # questions "même réunion, deux versions" plus haut.
    {"question": "Quelle association a bénéficié des dons du TN'Event 2025 et quel montant a été récolté ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk", "1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"]},

    # --- Réunion Ouverte BDE n°14 du 26/05/2026 (12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h) ---
    # C'est le document le PLUS RÉCENT de tout le corpus (404 docs). Question
    # volontairement difficile : ce benchmark ne modélise aucune fraîcheur
    # (contrairement à FRESHNESS_ALPHA en prod), donc une recherche purement
    # sémantique n'a aucune raison de privilégier ce document parmi la
    # quinzaine d'autres CR qui mentionnent aussi "le président du BDE". On
    # s'attend à ce que cette question score mal quelle que soit la config —
    # c'est le point : elle rend visible un angle mort du benchmark actuel.
    {"question": "Qui est le président du BDE d'après le compte-rendu le plus récent disponible dans les archives ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"]},
    {"question": "Quel club a été dissous lors de la réunion ouverte BDE n°14 du 26 mai 2026 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"]},
    {"question": "Qui est présidente du club Equi'TN créé lors de la réunion ouverte BDE n°14 du 26 mai 2026 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"]},
    {"question": "Le club 'Canada TN' a-t-il été élu lors de la réunion ouverte BDE n°14 du 26 mai 2026 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"]},
    {"question": "Pourquoi le tournoi 4 nations du 29 mai a-t-il été annulé selon la réunion ouverte BDE n°14 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"]},

    # --- Hors périmètre, version difficile : vocabulaire plausible (clubs/vie
    # associative) mais sujets absents de tout ce qui a été lu dans le corpus,
    # contrairement aux hors-périmètre "faciles" ci-dessus (frais de scolarité,
    # bibliothèque) qui n'ont aucun recouvrement lexical avec le domaine ---
    {"question": "Quel est le nom du président du club de plongée sous-marine de Telecom Nancy ?", "expected_documents": []},
    {"question": "Quel budget a été voté pour un club d'astronomie lors d'une réunion du BDE ?", "expected_documents": []},
    {"question": "Qui est le trésorier du club de photographie du CETEN ?", "expected_documents": []},
    {"question": "Quand a eu lieu la dernière réunion du club de robotique de Telecom Nancy ?", "expected_documents": []},
    {"question": "Quel est le montant de la bourse au mérite versée par le BDE aux meilleurs étudiants ?", "expected_documents": []},
    {"question": "Quelle association étudiante gère les stages en entreprise à Telecom Nancy ?", "expected_documents": []},
]

# ==========================================
# 2. RETRIEVER EN MÉMOIRE
# ==========================================
class LocalBenchmarkRetriever:
    # Cache mémoire volontairement PETIT : le cache disque rend une éviction
    # quasi gratuite à recharger (~0.3s mesuré), donc il ne sert qu'à éviter un
    # aller-retour disque pour des trials consécutifs qui répètent exactement
    # la même config. Une matrice d'embeddings bge-m3 à chunk_size=128 pèse
    # ~300 Mo : avec 64 combinaisons possibles, un cache large pourrait monter
    # à plusieurs Go de RAM pour rien. On borde donc large à quelques entrées.
    _MAX_CHUNKS_IN_MEMORY = 8
    _MAX_EMBEDDINGS_IN_MEMORY = 6
    _chunks_cache = OrderedDict()
    _embeddings_cache = OrderedDict()

    def __init__(self, markdowns_dir: Path):
        self.md_files = list(markdowns_dir.glob("*.md"))
        self.documents = []
        for file in self.md_files:
            with open(file, "r", encoding="utf-8") as f:
                raw = f.read()
            # Comme mdtoqdrant.py:97, on chunke le corps SANS le frontmatter —
            # sinon le premier chunk de chaque doc contient "---\ntitle: ...\n---"
            # brut, ce que la prod ne fait jamais.
            meta, body = _parse_frontmatter(raw)
            self.documents.append({
                "id": file.stem,
                "content": body,
                "title": meta.get("title", file.stem),
                "date": meta.get("date", ""),
            })

    def _load_or_compute_chunks(self, chunk_size: int, overlap_tokens: int) -> list:
        cache_key = (chunk_size, overlap_tokens)
        disk_path = _disk_cache_path("chunks", cache_key, "pkl")
        if disk_path.exists():
            with disk_path.open("rb") as f:
                return pickle.load(f)

        chunks = []
        for doc in self.documents:
            text_chunks = get_hybrid_chunks(doc["content"], chunk_size=chunk_size, chunk_overlap=overlap_tokens)
            for c in text_chunks:
                chunks.append({"source": doc["id"], "text": c, "date": doc["date"], "title": doc["title"]})
        with disk_path.open("wb") as f:
            pickle.dump(chunks, f)
        return chunks

    def _load_or_compute_embeddings(self, chunks: list, model_name: str, model: SentenceTransformer, cache_key: tuple) -> np.ndarray:
        disk_path = _disk_cache_path("embeddings", cache_key, "npy")
        if disk_path.exists():
            return np.load(disk_path)

        if not chunks:
            emb = np.zeros((0, model.get_sentence_embedding_dimension()), dtype=np.float32)
        else:
            # Même préfixage qu'en prod (date/titre + "passage: " pour les
            # modèles e5) : voir _passage_text, sinon la comparaison entre
            # modèles n'est pas fidèle à ce qui tourne réellement en prod.
            texts = [_passage_text(c["text"], model_name, c["date"], c["title"]) for c in chunks]
            logger.info(f"Encodage de {len(texts)} chunks avec {model_name} (chunk_size={cache_key[0]}, overlap={cache_key[1]})...")
            emb = model.encode(
                texts,
                normalize_embeddings=True,
                batch_size=128,
                show_progress_bar=True,
            )
        np.save(disk_path, emb)
        return emb

    def setup_for_trial(self, config):
        chunk_size = config["chunk_size"]
        overlap_tokens = config["overlap_tokens"]
        model_name = config["embedding_model"]

        cache_key_chunks = (chunk_size, overlap_tokens)
        if cache_key_chunks not in self._chunks_cache:
            if len(self._chunks_cache) >= self._MAX_CHUNKS_IN_MEMORY:
                self._chunks_cache.popitem(last=False)
            self._chunks_cache[cache_key_chunks] = self._load_or_compute_chunks(chunk_size, overlap_tokens)
        else:
            self._chunks_cache.move_to_end(cache_key_chunks)

        self.chunks = self._chunks_cache[cache_key_chunks]

        cache_key_embeddings = (chunk_size, overlap_tokens, model_name)
        self.model = _get_embedding_model(model_name)
        self.model_name = model_name

        if cache_key_embeddings not in self._embeddings_cache:
            if len(self._embeddings_cache) >= self._MAX_EMBEDDINGS_IN_MEMORY:
                self._embeddings_cache.popitem(last=False)
            self._embeddings_cache[cache_key_embeddings] = self._load_or_compute_embeddings(
                self.chunks, model_name, self.model, cache_key_embeddings
            )
        else:
            self._embeddings_cache.move_to_end(cache_key_embeddings)

        self.embeddings = self._embeddings_cache[cache_key_embeddings]
            
    _query_cache = OrderedDict()
    
    def search(self, query: str, top_k: int, similarity_threshold: float = 0.0):
        if len(self.chunks) == 0:
            return []

        # Même préfixe "query: " qu'en prod (retrieval.py:62) pour les modèles e5.
        query_text = _query_text(query, self.model_name)
        q_key = (str(self.model), query_text)
        if q_key not in self._query_cache:
            if len(self._query_cache) > 200:
                self._query_cache.popitem(last=False)
            self._query_cache[q_key] = self.model.encode([query_text], normalize_embeddings=True)[0]
        else:
            self._query_cache.move_to_end(q_key)
            
        q_emb = self._query_cache[q_key]
        scores = np.dot(self.embeddings, q_emb)
        
        valid_indices = [i for i, score in enumerate(scores) if score >= similarity_threshold]
        if not valid_indices:
            return []
            
        valid_scores = scores[valid_indices]
        local_top = np.argsort(valid_scores)[::-1][:top_k]
        top_indices = [valid_indices[i] for i in local_top]
        
        seen = set()
        retrieved_sources = []
        for idx in top_indices:
            src = self.chunks[idx]["source"]
            if src not in seen:
                retrieved_sources.append(src)
                seen.add(src)
        return retrieved_sources

# ==========================================
# 3. FONCTION OBJECTIF
# ==========================================
def objective(trial, retriever):
    chunk_size = trial.suggest_categorical("chunk_size", [128, 256, 512, 1024])
    # Overlap exprimé en fraction du chunk_size (pas en tokens bruts) : évite les
    # combinaisons dégénérées comme chunk_size=128 + overlap=128 (100% de
    # recouvrement, chunks quasi dupliqués — calcul gaspillé, nDCG/MRR biaisés
    # par des quasi-doublons).
    overlap_ratio = trial.suggest_categorical("overlap_ratio", [0.0, 0.15, 0.25, 0.4])
    overlap_tokens = int(chunk_size * overlap_ratio)
    embedding_model = trial.suggest_categorical("embedding_model", ["miniLM", "e5-small", "e5-base", "bge-m3"])
    top_k = trial.suggest_categorical("top_k", [1, 3, 5, 10])
    similarity_threshold = trial.suggest_float("similarity_threshold", 0.0, 0.8)

    config = {
        "chunk_size": chunk_size,
        "overlap_tokens": overlap_tokens,
        "embedding_model": embedding_model,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold
    }

    start_time = time.time()
    try:
        retriever.setup_for_trial(config)
    except Exception as e:
        logger.error(f"Échec initialisation: {e}")
        raise optuna.exceptions.TrialPruned()

    # Ordre des questions mélangé (seed = numéro du trial, donc reproductible).
    # Sans ça, RETRIEVAL_DATASET étant toujours dans le même ordre, le
    # MedianPruner compare systématiquement le même sous-ensemble biaisé de
    # questions (les 6 premières, toujours du même document) au même "step" —
    # un trial peut être coupé pour de mauvaises raisons.
    rng = random.Random(trial.number)
    question_order = list(range(len(RETRIEVAL_DATASET)))
    rng.shuffle(question_order)

    trial_scores, mrr_scores, ndcg_scores, recall_scores, precision_scores = [], [], [], [], []
    per_question_log = []
    pruned = False

    for i, dataset_idx in enumerate(question_order):
        item = RETRIEVAL_DATASET[dataset_idx]
        question = item["question"]
        expected_docs = set(item.get("expected_documents", []))

        retrieved_sources = retriever.search(question, top_k=top_k, similarity_threshold=similarity_threshold)

        mrr = ndcg = recall = precision = 0.0

        if expected_docs:
            for rank, src in enumerate(retrieved_sources, 1):
                if src in expected_docs:
                    mrr = 1.0 / rank
                    break

            intersection = expected_docs.intersection(set(retrieved_sources))
            recall = len(intersection) / len(expected_docs)
            precision = (len(intersection) / len(retrieved_sources)) if retrieved_sources else 0.0

            dcg = sum(1.0 / math.log2(rank + 1) for rank, src in enumerate(retrieved_sources, 1) if src in expected_docs)
            k = min(len(retrieved_sources), len(expected_docs)) if retrieved_sources else 1
            idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, k + 1))
            ndcg = dcg / idcg if idcg > 0 else 0.0
        elif len(retrieved_sources) == 0:
            mrr, ndcg, recall, precision = 1.0, 1.0, 1.0, 1.0
        # sinon (hors-périmètre mais quelque chose est retourné) : tout reste à 0.0

        # Poids du score composite : le MRR reste dominant (le rang du premier
        # résultat pertinent compte le plus), mais la Précision est incluse
        # pour dissuader Optuna de systématiquement choisir top_k=10 — sans
        # elle, un top_k plus large ne peut qu'améliorer Recall/MRR/nDCG, même
        # en noyant la bonne réponse dans du bruit qui coûtera des tokens (et
        # de la qualité) au LLM de génération en aval.
        score = 0.30 * mrr + 0.25 * ndcg + 0.20 * recall + 0.25 * precision
        trial_scores.append(score)
        mrr_scores.append(mrr)
        ndcg_scores.append(ndcg)
        recall_scores.append(recall)
        precision_scores.append(precision)
        per_question_log.append({
            "dataset_index": dataset_idx,
            "question": question,
            "expected_documents": sorted(expected_docs),
            "retrieved_sources": retrieved_sources,
            "mrr": mrr,
            "ndcg": ndcg,
            "recall": recall,
            "precision": precision,
            "score": score,
        })

        # Pruning précoce
        partial_score = sum(trial_scores) / len(trial_scores)
        trial.report(partial_score, i)
        if trial.should_prune():
            pruned = True
            break

    n = len(trial_scores)
    avg_score = sum(trial_scores) / n if n else 0.0
    avg_mrr = sum(mrr_scores) / n if n else 0.0
    avg_ndcg = sum(ndcg_scores) / n if n else 0.0
    avg_recall = sum(recall_scores) / n if n else 0.0
    avg_precision = sum(precision_scores) / n if n else 0.0
    # Variance : une moyenne unique cache si un trial est régulièrement moyen
    # ou juste porté par quelques questions faciles/chanceuses.
    score_std = statistics.pstdev(trial_scores) if n > 1 else 0.0
    score_min = min(trial_scores) if trial_scores else 0.0
    score_max = max(trial_scores) if trial_scores else 0.0

    # Répartition par catégorie de question : les hors-périmètre mesurent la
    # capacité à NE RIEN retourner, les autres la capacité à retrouver le(s)
    # bon(s) document(s).
    evaluated_items = [RETRIEVAL_DATASET[idx] for idx in question_order[:n]]
    in_scope = [s for s, item in zip(trial_scores, evaluated_items) if item.get("expected_documents")]
    hors_perimetre = [s for s, item in zip(trial_scores, evaluated_items) if not item.get("expected_documents")]
    avg_in_scope = sum(in_scope) / len(in_scope) if in_scope else None
    avg_hors_perimetre = sum(hors_perimetre) / len(hors_perimetre) if hors_perimetre else None

    duration_s = time.time() - start_time

    trial.set_user_attr("avg_mrr", avg_mrr)
    trial.set_user_attr("avg_ndcg", avg_ndcg)
    trial.set_user_attr("avg_recall", avg_recall)
    trial.set_user_attr("avg_precision", avg_precision)
    trial.set_user_attr("score_std", score_std)
    trial.set_user_attr("score_min", score_min)
    trial.set_user_attr("score_max", score_max)
    trial.set_user_attr("num_chunks", len(retriever.chunks))
    trial.set_user_attr("num_questions_evaluated", n)
    trial.set_user_attr("pruned_early", pruned)
    trial.set_user_attr("duration_seconds", round(duration_s, 2))
    if avg_in_scope is not None:
        trial.set_user_attr("avg_score_in_scope", avg_in_scope)
    if avg_hors_perimetre is not None:
        trial.set_user_attr("avg_score_hors_perimetre", avg_hors_perimetre)

    _append_trial_log({
        "trial_number": trial.number,
        "params": config,
        "avg_score": avg_score,
        "avg_mrr": avg_mrr,
        "avg_ndcg": avg_ndcg,
        "avg_recall": avg_recall,
        "avg_precision": avg_precision,
        "score_std": score_std,
        "score_min": score_min,
        "score_max": score_max,
        "avg_score_in_scope": avg_in_scope,
        "avg_score_hors_perimetre": avg_hors_perimetre,
        "num_chunks": len(retriever.chunks),
        "num_questions_evaluated": n,
        "pruned_early": pruned,
        "duration_seconds": round(duration_s, 2),
        "per_question": per_question_log,
    })

    if pruned:
        raise optuna.exceptions.TrialPruned()

    return avg_score

# ==========================================
# 4. MAIN
# ==========================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark retrieval-only (Optuna). Interrompre avec Ctrl+C à tout "
            "moment : chaque trial terminé est déjà commité dans la base sqlite, "
            "et le cache disque (temp/optuna_cache/) évite de recalculer les "
            "chunks/embeddings déjà vus. Relancer la commande reprend l'étude là "
            "où elle s'est arrêtée (même --study-name = même sqlite = mêmes trials)."
        )
    )
    parser.add_argument(
        "--n-trials", type=int, default=300,
        help="Nombre de NOUVEAUX trials à lancer dans cet appel (par défaut 300). "
             "S'ajoute aux trials déjà présents dans la base.",
    )
    parser.add_argument(
        "--study-name", type=str, default="retrieval_only_v2",
        help="Nom de l'étude Optuna (change de nom pour repartir d'une base vierge). "
             "v2 car le schéma de paramètres a changé (overlap_ratio au lieu "
             "d'overlap_tokens) : les 2 trials de v1 (avant corrections méthodo) "
             "restent dans la même base sqlite mais sous un nom d'étude différent.",
    )
    args = parser.parse_args()

    logger.info("Initialisation du benchmark RETRIEVAL 100% avec Optuna...")
    md_dir = Path(__file__).resolve().parent / "temp" / "markdowns"
    if not md_dir.exists() or not list(md_dir.glob("*.md")):
        msg = (
            f"Corpus introuvable dans {md_dir}. "
            "Lance `python -m app.back.ingest --step drive` puis `--step pdf` "
            "pour peupler ce dossier avec les vrais documents avant de benchmarker."
        )
        raise FileNotFoundError(msg)

    retriever = LocalBenchmarkRetriever(md_dir)

    db_url = "sqlite:///retrieval_benchmark.db"
    study = optuna.create_study(
        storage=db_url,
        direction="maximize",
        study_name=args.study_name,
        load_if_exists=True,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10, n_warmup_steps=5)
    )
    n_done_before = len(study.trials)
    logger.info(f"Étude '{args.study_name}' : {n_done_before} trial(s) déjà en base, {args.n_trials} de plus demandés.")

    try:
        logger.info(f"Lancement de l'étude (0 appel API LLM) dans : {db_url}")
        study.optimize(lambda trial: objective(trial, retriever), n_trials=args.n_trials)
    except KeyboardInterrupt:
        logger.warning(
            "Arrêt manuel : %d trial(s) complet(s) déjà sauvegardés en base. "
            "Relance la même commande pour reprendre.",
            len([t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]),
        )

    if len(study.trials) > 0:
        try:
            best_trial = study.best_trial
            logger.info(f"Meilleur score Retrieval : {best_trial.value:.4f}")
            for key, value in best_trial.params.items():
                logger.info(f"  - {key}: {value}")
            for key, value in best_trial.user_attrs.items():
                logger.info(f"  · {key}: {value}")
            df = study.trials_dataframe()
            df.to_csv("optuna_retrieval_benchmark.csv", index=False)
            logger.info("Résultats exportés dans : optuna_retrieval_benchmark.csv")
            logger.info(f"Détail par question et par trial dans : {TRIAL_LOG_PATH}")
        except ValueError:
            pass

if __name__ == "__main__":
    main()
