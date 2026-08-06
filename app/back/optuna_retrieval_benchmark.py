import argparse
import hashlib
import json
import optuna
import os
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
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.back.chunking import get_hybrid_chunks
from app.back.mdtoqdrant import _parse_frontmatter
# Réutilisation directe des constantes/fonction de fraîcheur de la prod
# (plutôt que de les dupliquer) : garantit que le benchmark ne peut jamais
# diverger silencieusement de ce qui tourne réellement en prod. Note : cet
# import charge e5-small en mémoire dès l'import de ce module (retrieval.py
# instancie son SentenceTransformer au niveau module) — coût one-shot
# négligeable (modèle "small", quelques secondes), mais explicite ici pour
# ne pas surprendre.
from app.back.retrieval import CANDIDATE_MULTIPLIER, FRESHNESS_ALPHA, _freshness_score

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


def _atomic_write(path: Path, write_fn) -> None:
    """Écrit dans un fichier temporaire puis renomme atomiquement (os.replace).

    Nécessaire si plusieurs process tournent en parallèle sur la même machine
    (ex: un par GPU) et peuvent tomber sur la même combinaison chunk_size/
    overlap/modèle en même temps : sans ça, un process pourrait lire un
    fichier de cache à moitié écrit par l'autre.
    """
    tmp_path = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    with tmp_path.open("wb") as f:
        write_fn(f)
    os.replace(tmp_path, path)


# Modèles suivant la convention d'entraînement asymétrique E5 ("query: "/
# "passage: "). Répliquer cette convention est nécessaire pour comparer les
# modèles équitablement : sans elle, e5-small/e5-base sont évalués "mal
# utilisés" par rapport à la façon dont ils tournent réellement en prod
# (voir retrieval.py:62 et mdtoqdrant.py:109-111).
_E5_MODELS = {"e5-small", "e5-base", "e5-large"}
# arctic-l (Snowflake/snowflake-arctic-embed-l-v2.0) préfixe UNIQUEMENT la
# requête ("query: "), jamais le passage — convention différente d'e5
# (vérifié dans la doc du modèle, recherche du 22/07/2026). Le confondre avec
# _E5_MODELS ajouterait un préfixe "passage: " que le modèle n'attend pas :
# pas un crash, juste une comparaison faussée en silence — d'où un set séparé.
_QUERY_ONLY_PREFIX_MODELS = {"arctic-l"}


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
    if model_name in _E5_MODELS or model_name in _QUERY_ONLY_PREFIX_MODELS:
        return f"query: {query}"
    return query


# ==========================================
# 0bis. SCORING NIVEAU CHUNK (ce qui compte : le bon passage, pas le bon doc)
# ==========================================
# `answer_snippets` sur chaque question du dataset est une liste de "groupes" :
# chaque groupe est une liste de formulations alternatives (une seule suffit,
# OR) pour UN fait requis ; il faut satisfaire TOUS les groupes (AND) pour un
# recall de 1.0. Ça permet de représenter aussi bien "un seul fait à trouver,
# plusieurs formulations possibles" que "plusieurs faits distincts requis"
# (ex: la question multi-documents sur le TN'Event qui a besoin à la fois du
# nom de l'association ET du montant récolté).
def _chunk_is_relevant(chunk: dict, answer_snippets: list, expected_docs: set) -> bool:
    """Un chunk compte comme pertinent seulement s'il vient d'un des documents
    attendus ET contient un snippet requis. Sans la vérification de la
    source, une formulation générique répétée ailleurs dans le corpus (ex:
    "n'est pas élu", un pourcentage de vote comme "91%") pourrait être créditée
    depuis un chunk totalement hors-sujet qui la contient par coïncidence —
    c'est le bug trouvé après la première version de ce scoring : un chunk qui
    "a l'air bon" textuellement mais vient du mauvais document ne doit jamais
    compter comme un succès de retrieval."""
    if chunk["source"] not in expected_docs:
        return False
    text_lower = chunk["text"].lower()
    return any(alt.lower() in text_lower for group in answer_snippets for alt in group)


def _chunk_metrics(retrieved: list, answer_snippets: list, expected_docs: set) -> tuple:
    """MRR/nDCG/Recall/Precision calculés sur le CONTENU des chunks retournés
    par search() (pas sur les documents source), avec vérification de la
    source (voir _chunk_is_relevant). `retrieved` est la liste ordonnée de
    dicts {"source", "text"} telle que renvoyée par search(), SANS
    déduplication (comme la prod)."""
    if not answer_snippets:
        # Question hors-périmètre : le bon comportement est de ne rien retourner.
        if not retrieved:
            return 1.0, 1.0, 1.0, 1.0
        return 0.0, 0.0, 0.0, 0.0

    relevance_flags = [_chunk_is_relevant(r, answer_snippets, expected_docs) for r in retrieved]

    mrr = 0.0
    for rank, rel in enumerate(relevance_flags, 1):
        if rel:
            mrr = 1.0 / rank
            break

    # nDCG toujours calculé et journalisé (diagnostic utile pour les questions
    # multi-faits), mais retiré du score composite optimisé par Optuna : pour
    # les questions à un seul fait requis (78 des 98 questions du dataset),
    # l'idéal (idcg) se calcule avec k=1, donc nDCG se réduit quasiment au même
    # signal que MRR (rang du premier hit) — les deux ensemble comptaient deux
    # fois la même information dans le score composite.
    dcg = sum(1.0 / math.log2(rank + 1) for rank, rel in enumerate(relevance_flags, 1) if rel)
    k = min(len(retrieved), len(answer_snippets)) if retrieved else 1
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, k + 1)) if k > 0 else 1.0
    ndcg = dcg / idcg if idcg > 0 else 0.0

    # Le recall ne compte que le texte des chunks dont la source est valide,
    # pour la même raison que _chunk_is_relevant : un chunk hors-sujet ne doit
    # pas pouvoir "satisfaire" un groupe de snippets par coïncidence.
    combined = " ".join(r["text"] for r in retrieved if r["source"] in expected_docs).lower()
    satisfied_groups = sum(1 for group in answer_snippets if any(alt.lower() in combined for alt in group))
    recall = satisfied_groups / len(answer_snippets)

    precision = (sum(relevance_flags) / len(retrieved)) if retrieved else 0.0

    return mrr, ndcg, recall, precision


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
        "e5-large": "intfloat/multilingual-e5-large",
        "bge-m3": "BAAI/bge-m3",
        # Architecture XLM-RoBERTa STOCK (pas de trust_remote_code), Apache
        # 2.0, lignée d'entraînement différente d'e5/bge — ajouté après
        # l'incident gte-multi en vérifiant explicitement (recherche menée
        # le 22/07/2026) qu'il n'utilise pas de code de modélisation custom.
        "arctic-l": "Snowflake/snowflake-arctic-embed-l-v2.0",
        # "gte-multi": "Alibaba-NLP/gte-multilingual-base" — DÉSACTIVÉ.
        # Son code custom (trust_remote_code) a déclenché une assertion CUDA
        # "index out of bounds" (IndexKernel.cu) dès le premier batch
        # d'encodage sur le serveur GPU (run_benchmark2.log, 22/07/2026) ;
        # cette assertion corrompt le contexte CUDA pour tout le reste du
        # PROCESSUS (30 trials suivants tous échoués, quel que soit le
        # modèle demandé). Retiré du grid dans objective() — l'entrée reste
        # ici en commentaire pour référence si quelqu'un veut creuser
        # (version transformers/torch, revision du modèle sur le Hub) avant
        # de le réactiver.
    }
    real_name = model_map.get(model_name, model_name)
    if real_name not in _embedding_models_cache:
        logger.info(f"Chargement du modèle d'embedding: {real_name}")
        # trust_remote_code=True : sans effet sur les modèles actuels
        # (architectures stock) ; laissé en place pour le jour où gte-multi
        # (ou un autre modèle à code custom) serait réactivé ci-dessus.
        _embedding_models_cache[real_name] = SentenceTransformer(real_name, trust_remote_code=True)
        # SentenceTransformer choisit cuda automatiquement si torch.cuda est
        # disponible — ce log rend explicite si l'encodage tourne sur CPU
        # (lent) ou GPU (rapide), sans avoir à deviner depuis les logs génériques.
        logger.info(f"  -> device utilisé : {_embedding_models_cache[real_name].device}")
    return _embedding_models_cache[real_name]


# Reranker cross-encoder : capacité CANDIDATE (absente de la prod actuelle),
# pas une réplique — voir use_reranker dans objective(). Un seul modèle fixe
# (pas de choix multiple) pour contenir la taille de la grille : la question
# qu'Optuna tranche est "un reranker vaut-il le coût ?", pas "lequel choisir".
RERANK_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
# Borne indépendante de top_k (max 10) : reranker le pool complet
# top_k*CANDIDATE_MULTIPLIER (jusqu'à 200) à chaque question et chaque trial
# serait hors de prix ; 20 est déjà large marge pour réordonner utilement.
RERANK_POOL_SIZE = 20

_reranker_models_cache = {}
def _get_reranker_model():
    if RERANK_MODEL_NAME not in _reranker_models_cache:
        logger.info(f"Chargement du reranker: {RERANK_MODEL_NAME}")
        _reranker_models_cache[RERANK_MODEL_NAME] = CrossEncoder(RERANK_MODEL_NAME, trust_remote_code=True)
        logger.info(f"  -> device utilisé : {_reranker_models_cache[RERANK_MODEL_NAME].model.device}")
    return _reranker_models_cache[RERANK_MODEL_NAME]

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
    {"question": "Qui a présidé la réunion du Bureau des Élèves du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"], "answer_snippets": [["Quentin ROUSSEY"]]},
    {"question": "Qui présidait le Bureau des Élèves lors de la séance du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"], "answer_snippets": [["Quentin ROUSSEY"]]},
    {"question": "Qui était secrétaire de séance à la réunion du BDE du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"], "answer_snippets": [["Rachel BACZYNSKI"]]},
    {"question": "Quelles sont les dates des campagnes d'intégration 2022 annoncées lors de la réunion du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"], "answer_snippets": [["24 janvier au 4 février 2022"]]},
    {"question": "Contre quelle entreprise le CETEN a-t-il un procès en cours, évoqué lors de la réunion du BDE du 3 janvier 2022 ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"], "answer_snippets": [["GOLDEN Voyages", "WEI and GO"]]},
    {"question": "Quel délai la secrétaire du BDE 2022 annonce-t-elle pour l'envoi des comptes-rendus ?", "expected_documents": ["1Fx6hkwnMyEqELbhTxmVUuvKH9r_2CEi6"], "answer_snippets": [["48h"]]},

    # --- AGO du 25/01/2022 (1El47-pyPFL27hO2pdCX_XXWWfl03mIeC) ---
    {"question": "Qui a présidé l'Assemblée Générale Ordinaire du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"], "answer_snippets": [["Julien TEISSIER"]]},
    {"question": "À quel pourcentage le bilan moral 2021 a-t-il été accepté lors de l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"], "answer_snippets": [["91%"]]},
    {"question": "Quel pourcentage de votes favorables a obtenu le bilan moral 2021 à l'Assemblée Générale Ordinaire du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"], "answer_snippets": [["91%"]]},
    {"question": "À quel pourcentage le bilan financier 2021 a-t-il été accepté lors de l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"], "answer_snippets": [["93%"]]},
    {"question": "Combien de membres étaient présents ou représentés à l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"], "answer_snippets": [["au nombre de 44"]]},
    {"question": "Qui a présenté le bilan financier 2021 lors de l'AGO du 25 janvier 2022 ?", "expected_documents": ["1El47-pyPFL27hO2pdCX_XXWWfl03mIeC"], "answer_snippets": [["Tom CABARAT"]]},

    # --- AGE du 11/01/2022 (1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z) ---
    {"question": "Combien de membres étaient présents ou représentés à l'Assemblée Générale Extraordinaire du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"], "answer_snippets": [["au nombre de 102"]]},
    {"question": "Combien de personnes avaient le droit de vote à l'AGE du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"], "answer_snippets": [["au nombre de 102"]]},
    {"question": "À quel pourcentage la modification de l'article 10.4.2 sur la composition du bureau a-t-elle été adoptée lors de l'AGE du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"], "answer_snippets": [["92,1%"]]},
    {"question": "Quel était le taux d'approbation de la modification sur les modalités des élections du bureau des élèves à l'AGE du 11 janvier 2022 ?", "expected_documents": ["1Fa0mwG-UqhqsdzO4ohIS2N_YvPg94c0Z"], "answer_snippets": [["89,2%"]]},

    # --- Réunion Ouverte BDE n°01 du 23/01/2024 (1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY) — OCR corrompu (accents), snippets vérifiés par grep ---
    {"question": "Quel jour aura lieu la soirée de désintégration selon la réunion ouverte BDE n°1 du 23 janvier 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"], "answer_snippets": [["mercredi 31 janvier"]]},
    {"question": "Quel est le montant du chèque de caution demandé aux non-adhérents CETEN pour la désintégration 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"], "answer_snippets": [["200€"]]},
    {"question": "Quel montant de caution est demandé à un non-adhérent CETEN pour participer à la désintégration 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"], "answer_snippets": [["200€"]]},
    {"question": "Qui est président du club Typst'n créé lors de la réunion ouverte BDE n°1 du 23 janvier 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"], "answer_snippets": [["VESSE"]]},
    {"question": "Le club Ni'TN'do a-t-il été accepté ou rejeté lors de sa création à la réunion ouverte BDE n°1 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"], "answer_snippets": [["Ni’TN’do"]]},
    {"question": "Pourquoi la soirée blindtest a-t-elle été reportée d'après la réunion ouverte BDE n°1 du 23 janvier 2024 ?", "expected_documents": ["1XlvCqSEe77B7k8fYqWBe0g4pWknvbmSY"], "answer_snippets": [["vendredi 19 janvier"]]},

    # --- Réunion Ouverte BDE n°02 du 30/01/2024 (1yetct2L4YDYjngMNRF6u83MYmK-PNWUO) — OCR corrompu ---
    {"question": "Où a eu lieu la soirée de désintégration 2024 d'après la réunion ouverte BDE n°2 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"], "answer_snippets": [["Carri`ere", "Max´eville"]]},
    {"question": "Qui est président du club Degus'TN d'après la réunion ouverte BDE n°2 du 30 janvier 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"], "answer_snippets": [["Hippolyte COSSERAT"]]},
    {"question": "Qui dirige le club Degus'TN après sa reprise en janvier 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"], "answer_snippets": [["Hippolyte COSSERAT"]]},
    {"question": "Quel club a été dissous lors de la réunion ouverte BDE n°2 du 30 janvier 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"], "answer_snippets": [["Chaussette Soli’TN"]]},
    {"question": "Quelle liste a été élue pour reprendre le club intégration selon la réunion ouverte BDE n°2 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"], "answer_snippets": [["N’int´e’ndo Bleue"]]},
    {"question": "Quel club de listes d'intégration a repris l'organisation de la désintégration en 2024 ?", "expected_documents": ["1yetct2L4YDYjngMNRF6u83MYmK-PNWUO"], "answer_snippets": [["N’int´e’ndo Bleue"]]},

    # --- Réunion Ouverte BDE n°03 du 13/02/2024 (1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M) — OCR corrompu ---
    {"question": "Pour quelle date les budgets prévisionnels annuels des clubs doivent-ils être envoyés d'après la réunion ouverte BDE n°3 du 13 février 2024 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["15 f´evrier"]]},
    {"question": "Avant quelle heure les clubs doivent-ils envoyer leur budget prévisionnel selon la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["15 f´evrier"]]},
    {"question": "Quels clubs sont exemptés de budget prévisionnel annuel selon la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["Gala, Voyage, Int´e"]]},
    {"question": "Qu'est-ce que le Vaultwarden mis en place pour les clubs, selon la réunion ouverte BDE n°3 du 13 février 2024 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["Vaultwarden"]]},
    {"question": "Quand a eu lieu le premier événement du club Brasserie d'après la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["13 f´evrier"]]},
    {"question": "Quel événement du club Brasserie a eu lieu le 13 février 2024 en début de soirée ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["13 f´evrier"]]},
    {"question": "Qui préside la proposition de club 'Bibi and Smoothie' finalement élue lors de la réunion ouverte BDE n°3 du 13 février 2024 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["Mathieu BREIT"]]},
    {"question": "Quand ont lieu les journées portes ouvertes de Telecom Nancy mentionnées lors de la réunion ouverte BDE n°3 ?", "expected_documents": ["1BqtdD-nbi8dr4zk2Ho0aBhpL0BQuR_8M"], "answer_snippets": [["17 f´evrier"]]},

    # --- Festival de Canards, mai 2022 (1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY) ---
    {"question": "Quel club a remporté le TéléCésar du meilleur nom lors de la cérémonie fictive décrite dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"], "answer_snippets": [["HackInTn"]]},
    {"question": "Quel club de TELECOM Nancy a remporté le prix fictif du meilleur nom dans l'édition de mai 2022 du journal ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"], "answer_snippets": [["HackInTn"]]},
    {"question": "Quel club a remporté le TéléCésar du meilleur logo dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"], "answer_snippets": [["club Voyage"]]},
    {"question": "Quel club a remporté le TéléCésar du meilleur espoir dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"], "answer_snippets": [["Créa’TN"]]},
    {"question": "Qui a remporté le TéléCésar du meilleur logo temporaire dans le Festival de Canards ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"], "answer_snippets": [["Art’emis"]]},
    {"question": "Quels ingrédients sont nécessaires pour la recette des Lembas publiée dans le Festival de Canards de mai 2022 ?", "expected_documents": ["1ve8jJIoZCahmL64sTxv6zLXLbIdAHrMY"], "answer_snippets": [["farine"], ["poudre d’amande"]]},

    # --- Interview de Marc Tomczak (13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B) ---
    {"question": "En quelle année Marc Tomczak a-t-il commencé sa carrière d'enseignant-chercheur à Telecom Nancy ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"], "answer_snippets": [["en 1990"]]},
    {"question": "Depuis quelle année Marc Tomczak enseigne-t-il à l'école ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"], "answer_snippets": [["en 1990"]]},
    {"question": "En quelle année l'école a-t-elle déménagé dans son bâtiment actuel d'après l'interview de Marc Tomczak ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"], "answer_snippets": [["en 2007"]]},
    {"question": "Quelle est la passion de Marc Tomczak qu'il évoque à plusieurs reprises dans son interview ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"], "answer_snippets": [["mycologie"]]},
    {"question": "Qui, d'après son interview, a inventé les points CIPA à Telecom Nancy ?", "expected_documents": ["13CYrBeKHH0SVSrWDuBNzcH7yyc8x-99B"], "answer_snippets": [["inventé les points CIPA"]]},

    # --- Multi-documents : deux comptes-rendus de la même réunion du 10/01/2022 ---
    # (1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc = version « fake CR » humoristique,
    #  1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET = version officielle avec tableaux de vote)
    # — labels corrigés : ils étaient inversés dans une version précédente de ce commentaire.
    {"question": "Quels clubs ont été dissous lors de la réunion du Bureau des Élèves du 10 janvier 2022 ?", "expected_documents": ["1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc", "1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET"], "answer_snippets": [["Conférences"], ["Fantasy League"]]},
    {"question": "Où aura lieu la soirée de désintégration annoncée lors de la réunion du BDE du 10 janvier 2022 ?", "expected_documents": ["1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc", "1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET"], "answer_snippets": [["Fort Pélissier"]]},
    {"question": "Quel a été le taux de participation à la passation des clubs annoncé lors de la réunion du BDE du 10 janvier 2022 ?", "expected_documents": ["1h80-iKn60PL1bXphFZ2JjJmT_spYWZDc", "1Eyo-9BB5bGrhdrQkEgg_1Bt9xCzW9RET"], "answer_snippets": [["32.48%"]]},

    # --- Hors périmètre (rien dans le corpus ne doit répondre à ces questions) ---
    {"question": "Quel est le prix d'un repas à la cafétéria de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien coûte l'abonnement internet du campus ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le numéro de téléphone du secrétariat de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quels sont les horaires d'ouverture de la bibliothèque de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Qui est l'actuel directeur de Telecom Nancy en 2026 ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le taux de réussite au diplôme d'ingénieur de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quelle est la recette du couscous du club Marché de TELECOM ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel jour de la semaine a lieu le cours de mathématiques discrètes ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le montant des frais de scolarité annuels à Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien d'élèves ingénieurs sont diplômés chaque année à Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},

    # --- CR BDE du 01/02/2016 (1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2) — élargit la couverture aux docs anciens ---
    {"question": "Qui était président du Bureau des Élèves lors de la réunion du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"], "answer_snippets": [["Guillaume HABEN"]]},
    {"question": "Qui était secrétaire du BDE à la réunion du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"], "answer_snippets": [["Yoni LEVY"]]},
    {"question": "Quel a été le résultat du vote pour la recréation du club Ten24 lors de la réunion du BDE du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"], "answer_snippets": [["Le club est donc créé"]]},
    {"question": "De quelle marque Youri est-il devenu ambassadeur d'après le compte-rendu du BDE du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"], "answer_snippets": [["Lipton"]]},
    {"question": "Combien de réponses le sondage sur le changement de logo a-t-il recueillies d'après le compte-rendu du BDE du 1er février 2016 ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"], "answer_snippets": [["132 réponses"]]},
    {"question": "En quelle année le club Ten24 a-t-il été recréé après une première dissolution, selon les archives du BDE ?", "expected_documents": ["1e3DpNOs6YPw3lyWHeZpqHNyrD8fYd2g2"], "answer_snippets": [["Le club est donc créé"]]},

    # --- CR BDE du 01/02/2018 (1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-) ---
    {"question": "Quels clubs ont été regroupés au sein du BDA d'après le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"], "answer_snippets": [["Chorale, Jonglerie, Télécom to Live, Cinéma et Musique"]]},
    {"question": "Quels 5 clubs artistiques ont fusionné pour former le BDA de Telecom Nancy ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"], "answer_snippets": [["Chorale, Jonglerie, Télécom to Live, Cinéma et Musique"]]},
    {"question": "Qui est devenue présidente du BDA selon le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"], "answer_snippets": [["Valentine ROULETTE"]]},
    {"question": "Qui a été élu président du club Telecome Cooking selon la réunion du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"], "answer_snippets": [["Julien ZHAN"]]},
    {"question": "Qui préside le Club Jeux d'après le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"], "answer_snippets": [["Benoit SCHULER"]]},
    {"question": "Quel jour débutent les campagnes d'intégration selon le compte-rendu du BDE du 1er février 2018 ?", "expected_documents": ["1OdQLE0XRxcSzatMxMRpRDp_DRBryLZu-"], "answer_snippets": [["mercredi 7 février"]]},

    # --- AGO du 21/01/2025 (169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd) ---
    {"question": "Qui a présidé l'Assemblée Générale Ordinaire du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"], "answer_snippets": [["Killian Thuillier", "Killian THUILLIER"]]},
    {"question": "Qui dirigeait le BDE juste avant l'arrivée de Raphaël Roullet, d'après l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"], "answer_snippets": [["Killian Thuillier", "Killian THUILLIER"]]},
    {"question": "Qui a été désigné secrétaire de séance à l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"], "answer_snippets": [["Baptiste SIBELLAS"]]},
    {"question": "À quel pourcentage le bilan moral 2024 a-t-il été accepté lors de l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"], "answer_snippets": [["89, 16%"]]},
    {"question": "Quel pourcentage de votes favorables a recueilli le bilan financier 2024 à l'assemblée générale de janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"], "answer_snippets": [["85, 54%"]]},
    {"question": "Qui devient président du BDE 2025 d'après l'Assemblée Générale Ordinaire du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"], "answer_snippets": [["Raphaël ROULLET"]]},
    {"question": "Combien de membres étaient présents ou représentés à l'AGO du 21 janvier 2025 ?", "expected_documents": ["169VCit-Etci6Qtt-mA8GG7c2TVr7cjyd"], "answer_snippets": [["au nombre de 83"]]},

    # --- Réunion Ouverte BDE n°1 du 04/02/2025 (1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk) ---
    {"question": "Quelle liste d'intégration a été élue lors de la réunion ouverte BDE n°1 du 4 février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"], "answer_snippets": [["Empire Inté’Galactique"]]},
    {"question": "Avec quel pourcentage des voix l'Empire Inté'Galactique a-t-il été élu selon la réunion ouverte BDE n°1 du 4 février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"], "answer_snippets": [["56.67%"]]},
    {"question": "Quelle liste a perdu l'élection intégration face à l'Empire Inté'Galactique en février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"], "answer_snippets": [["Inte’mporel"]]},
    {"question": "Le club 'Bacon Burger TN' a-t-il été accepté lors de sa création à la réunion ouverte BDE n°1 du 4 février 2025 ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk"], "answer_snippets": [["la charolaise"]]},

    # --- Réunion Ouverte BDE n°2 du 11/02/2025 (1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F) ---
    {"question": "Quel club a proposé de vendre des croquettes pour humains lors de sa création à la réunion ouverte BDE n°2 du 11 février 2025 ?", "expected_documents": ["1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"], "answer_snippets": [["Croquet'TN"]]},
    {"question": "Le club Croquet'TN a-t-il été accepté lors de la réunion ouverte BDE n°2 du 11 février 2025 ?", "expected_documents": ["1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"], "answer_snippets": [["blind test de croquettes"]]},
    {"question": "Quel est le nom du club proposé par Maxence Osawa-Bourbon lors de la réunion ouverte BDE n°2 du 11 février 2025 ?", "expected_documents": ["1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"], "answer_snippets": [["Bacon Burger Club"]]},

    # --- Multi-documents : le TN'Event 2025 est annoncé dans la RO n°1 (bénéficiaire) ---
    # et son résultat chiffré n'apparaît que dans la RO n°2 (montant récolté) : une
    # réponse complète nécessite réellement les deux documents, contrairement aux
    # questions "même réunion, deux versions" plus haut.
    {"question": "Quelle association a bénéficié des dons du TN'Event 2025 et quel montant a été récolté ?", "expected_documents": ["1FzdeZaGZYIXe0AbeOES0PlbbHKxAU7uk", "1s16O-CvOLd-P1PsTiqPGsWjgRMZ68e3F"], "answer_snippets": [["AEIM"], ["7766,79"]]},

    # --- Réunion Ouverte BDE n°14 du 26/05/2026 (12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h) ---
    # C'est le document le PLUS RÉCENT de tout le corpus (404 docs). Jusqu'à
    # v3, ce benchmark ne modélisait aucune fraîcheur (contrairement à
    # FRESHNESS_ALPHA en prod), donc une recherche purement sémantique
    # n'avait aucune raison de privilégier ce document parmi la quinzaine
    # d'autres CR qui mentionnent aussi "le président du BDE" — la question
    # était volontairement gardée pour rendre visible cet angle mort. Depuis
    # v4, LocalBenchmarkRetriever.search() réplique le re-classement par
    # fraîcheur de retrieval.py (voir FRESHNESS_ALPHA/_freshness_score
    # importés en tête de fichier), donc cette question redevient un test
    # légitime de la capacité réelle du système à privilégier le document
    # récent — plus un angle mort connu et accepté.
    {"question": "Qui est le président du BDE d'après le compte-rendu le plus récent disponible dans les archives ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"], "answer_snippets": [["NOBILE Tobias"]]},
    {"question": "Quel club a été dissous lors de la réunion ouverte BDE n°14 du 26 mai 2026 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"], "answer_snippets": [["les bonnes miches"]]},
    {"question": "Qui est présidente du club Equi'TN créé lors de la réunion ouverte BDE n°14 du 26 mai 2026 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"], "answer_snippets": [["PEYNON Eléa"]]},
    {"question": "Le club 'Canada TN' a-t-il été élu lors de la réunion ouverte BDE n°14 du 26 mai 2026 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"], "answer_snippets": [["n'est pas élu"]]},
    {"question": "Pourquoi le tournoi 4 nations du 29 mai a-t-il été annulé selon la réunion ouverte BDE n°14 ?", "expected_documents": ["12grcvbrk2mzOpOSVNEr0VdzA2x6p3v3h"], "answer_snippets": [["assez de personnes qui se sont manifestées"]]},

    # --- Hors périmètre, version difficile : vocabulaire plausible (clubs/vie
    # associative) mais sujets absents de tout ce qui a été lu dans le corpus,
    # contrairement aux hors-périmètre "faciles" ci-dessus (frais de scolarité,
    # bibliothèque) qui n'ont aucun recouvrement lexical avec le domaine ---
    {"question": "Quel est le nom du président du club de plongée sous-marine de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel budget a été voté pour un club d'astronomie lors d'une réunion du BDE ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Qui est le trésorier du club de photographie du CETEN ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quand a eu lieu la dernière réunion du club de robotique de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le montant de la bourse au mérite versée par le BDE aux meilleurs étudiants ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quelle association étudiante gère les stages en entreprise à Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},

    # --- Hors périmètre, extension v4 : le v3 n'avait que 16 questions
    # hors-périmètre (16% du dataset), ce qui rend avg_score_hors_perimetre
    # extrêmement bruité (chaque question pèse 6,25 points sur cette
    # sous-métrique). Chaque entrée ci-dessous a été vérifiée par grep exact
    # sur le corpus réel (app/back/temp/markdowns/*.md, 404 fichiers) pour
    # écarter toute question qui aurait accidentellement une vraie réponse
    # (le corpus BDE est riche en clubs éclectiques réels : oenologie,
    # échecs, babyfoot, voile, couture, escalade... tous vérifiés présents
    # et donc explicitement évités ici).
    # -- Faciles : infrastructure/administratif, aucun recouvrement lexical avec le corpus (comptes-rendus BDE) --
    {"question": "Quel est le tarif de la carte multi-services de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Où se trouve le parking vélo de l'école ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel logiciel de messagerie interne utilise l'administration de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le montant de la caution pour un badge d'accès perdu ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel bus dessert le campus de Telecom Nancy depuis la gare ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le prix d'une place de parking étudiant à l'année ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le montant de la bourse CROUS moyenne versée aux élèves de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quels sont les horaires d'ouverture du secrétariat pédagogique ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le débit du réseau wifi du campus de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le nom du fournisseur de restauration de la cafétéria ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien de jours de congés maladie un stagiaire peut-il poser ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quelle est la surface totale du campus de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le nom du logiciel de gestion des notes utilisé par l'administration ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Où se trouve la laverie du campus ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Où se trouve la salle de méditation du campus ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien de places compte la salle de coworking de l'école ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quelle assurance responsabilité civile est recommandée aux stagiaires de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le tarif étudiant de l'abonnement PASS Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel est le numéro d'urgence à contacter en cas d'incident sur le campus ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quelle mutuelle étudiante l'école recommande-t-elle ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien de places compte le local à vélos couvert du campus ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien de distributeurs de boissons sont installés dans le bâtiment principal ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quelles salles de Telecom Nancy sont climatisées ?", "expected_documents": [], "answer_snippets": []},
    # -- Difficiles : vocabulaire plausible (clubs/vie associative) mais entités vérifiées absentes du corpus --
    {"question": "Qui dirige le club d'apiculture de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Qui a été élu président du club randonnée lors d'une réunion ouverte du BDE ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quelle liste a remporté l'élection du bureau du club international ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien de bénévoles le club environnement a-t-il mobilisés pour un nettoyage de campus ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Combien de membres compte le club de musique électronique de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel montant a été récolté lors d'une vente de gâteaux organisée par un club ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel club a réalisé une fresque de street art dans les locaux de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Qui préside le club de poterie de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quel budget a été voté pour le club de philatélie lors d'une réunion du BDE ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Quand a eu lieu la dernière réunion du club de généalogie de Telecom Nancy ?", "expected_documents": [], "answer_snippets": []},
    {"question": "Qui est trésorier du club d'aquariophilie du CETEN ?", "expected_documents": [], "answer_snippets": []},
]

# Ordre des questions mélangé UNE SEULE FOIS pour toute l'étude (seed fixe),
# PAS par trial. Sans ça, le MedianPruner compare le score partiel de deux
# trials "au même step" en supposant qu'ils portent sur les mêmes questions —
# si chaque trial a son propre ordre aléatoire (ex: seed=trial.number), le
# step 5 du trial A et le step 5 du trial B portent sur des questions
# différentes, et la comparaison du pruner n'a plus de sens. Un ordre fixe
# (mais mélangé, pas dans l'ordre du dataset) donne une comparaison honnête
# tout en évitant le biais "toujours les mêmes 6 premières questions".
_QUESTION_ORDER_SEED = 42
QUESTION_ORDER = list(range(len(RETRIEVAL_DATASET)))
random.Random(_QUESTION_ORDER_SEED).shuffle(QUESTION_ORDER)

# ==========================================
# 2. RETRIEVER EN MÉMOIRE
# ==========================================
class LocalBenchmarkRetriever:
    # Cache mémoire volontairement PETIT : le cache disque rend une éviction
    # quasi gratuite à recharger (~0.3s mesuré), donc il ne sert qu'à éviter un
    # aller-retour disque pour des trials consécutifs qui répètent exactement
    # la même config. Une matrice d'embeddings bge-m3 pèse ~300 Mo : avec 4
    # chunk_size × 5 overlap_ratio × 6 modèles = 120 combinaisons possibles
    # (v4, contre 64 en v3), un cache large pourrait monter à plusieurs Go de
    # RAM pour rien. On borde donc large à quelques entrées.
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

        def _write(f):
            pickle.dump(chunks, f)

        _atomic_write(disk_path, _write)
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

        def _write(f):
            np.save(f, emb)

        _atomic_write(disk_path, _write)
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
    
    def search(self, query: str, top_k: int, similarity_threshold: float = 0.0, use_reranker: bool = False):
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

        # Étage 1 — réplique retrieval.py:63-70 : pool de candidats classé par
        # score SÉMANTIQUE pur, borné à top_k*CANDIDATE_MULTIPLIER comme en
        # prod (Qdrant `limit=top_k*20`). Évite de calculer la fraîcheur sur
        # toute la base à chaque requête, et garde `similarity_threshold`
        # comme filtre pré-fraîcheur — exactement l'ordre de `SCORE_THRESHOLD`
        # en prod (filtre sur le sémantique, jamais sur l'hybride).
        valid_scores = scores[valid_indices]
        pool_size = min(len(valid_indices), top_k * CANDIDATE_MULTIPLIER)
        pool_local = np.argsort(valid_scores)[::-1][:pool_size]
        pool_indices = [valid_indices[i] for i in pool_local]

        # Étage 2 — réplique retrieval.py:74-90 : re-classement par score
        # hybride sémantique+fraîcheur, mêmes constantes que la prod
        # (FRESHNESS_ALPHA, _freshness_score importés directement de
        # retrieval.py pour ne jamais diverger silencieusement). Sans cet
        # étage, le benchmark évaluait un système qui ne se comporte pas
        # comme celui réellement déployé.
        hybrid_ranked = sorted(
            pool_indices,
            key=lambda idx: FRESHNESS_ALPHA * scores[idx] + (1 - FRESHNESS_ALPHA) * _freshness_score(self.chunks[idx]["date"]),
            reverse=True,
        )

        if use_reranker:
            # Étage 3 (optionnel, absent de la prod actuelle — voir
            # RERANK_POOL_SIZE) : cross-encoder sur un short-list bornée,
            # indépendamment de top_k, pour contenir le coût.
            shortlist = hybrid_ranked[:RERANK_POOL_SIZE]
            if shortlist:
                pairs = [(query, self.chunks[idx]["text"]) for idx in shortlist]
                rerank_scores = _get_reranker_model().predict(pairs)
                shortlist = [idx for _, idx in sorted(zip(rerank_scores, shortlist), key=lambda x: x[0], reverse=True)]
            final_indices = shortlist[:top_k]
        else:
            final_indices = hybrid_ranked[:top_k]

        # Pas de déduplication par document : la vraie prod (retrieval.py:58-91)
        # ne déduplique jamais non plus — les top_k résultats renvoyés au LLM
        # de génération peuvent tout à fait venir 3 fois du même document. Une
        # dédup ici fausserait le sens de `top_k` par rapport à la prod, et
        # empêcherait d'évaluer si le CHUNK exact contenant la réponse est
        # remonté (ce qui compte davantage que "le bon document, n'importe où
        # dedans").
        return [
            {"source": self.chunks[idx]["source"], "text": self.chunks[idx]["text"]}
            for idx in final_indices
        ]

# ==========================================
# 3. FONCTION OBJECTIF
# ==========================================
# v3 optimisait un score composite unique = 0.40*MRR + 0.30*Recall +
# 0.30*Precision moyenné sur TOUTES les questions (in-scope ET
# hors-périmètre confondues, 82/16 questions). Analyse post-mortem de la
# study v3 (257 trials complétés) : corrélation entre ce score et
# avg_score_in_scope = 0.97, mais corrélation avec avg_score_hors_perimetre
# = -0.22 (négative). Preuve concrète : le trial qui filtrait 100% du
# hors-périmètre tombait au rang 250/257 du classement composite. Le score
# unique décourageait donc structurellement le refus de répondre, car les 82
# questions in-scope écrasent mécaniquement les 16 (v3) puis 50 (v4)
# questions hors-périmètre dans une moyenne unique.
# v4 sépare les deux : l'objectif optimisé devient PURE in-scope quality
# (avg_score_in_scope_macro, voir plus bas), et le hors-périmètre devient une
# CONTRAINTE de faisabilité (Optuna constrained TPE, voir sampler dans
# main()) — un trial qui ne filtre pas au moins HORS_PERIMETRE_MIN_SCORE des
# questions hors-périmètre est traité comme non-faisable, quel que soit son
# score in-scope, au lieu d'être autorisé à "acheter" du rappel avec de
# l'hallucination.
HORS_PERIMETRE_MIN_SCORE = 0.5


def _constraints_func(trial):
    """Callback pour optuna.samplers.TPESampler(constraints_func=...).

    Convention Optuna : contrainte satisfaite quand la valeur retournée est
    <= 0. On la définit dans objective() via trial.set_user_attr("constraint",
    (valeur,)) car c'est le seul endroit où avg_score_hors_perimetre est
    calculé ; ce callback ne fait que relire cet attribut.

    TPESampler.after_trial() appelle CE callback après CHAQUE trial, y
    compris les pruné/échoués — un KeyError ici plante l'étude ENTIÈRE, pas
    juste le trial en cours (vécu : un échec d'initialisation GPU sur un
    trial a fait planter tout le run car "constraint" n'était pas encore
    posé à ce stade). .get() avec un défaut "non-faisable" est donc une
    protection nécessaire, en plus de celle posée explicitement dans
    objective() pour le cas d'échec d'initialisation connu.
    """
    return trial.user_attrs.get("constraint", (HORS_PERIMETRE_MIN_SCORE,))


# Poids (w_mrr, w_recall, w_precision) du score composite par question,
# recalibrés à partir d'une mesure empirique sur les 257 trials complétés de
# l'étude v3 (21074 observations de questions in-scope) :
#   - corr(MRR, Recall) = 0.875, et pour les 79/82 questions in-scope
#     mono-fait : recall>0 <=> mrr>0 EXACTEMENT (0 désaccord sur 20303
#     observations). Recall est donc une version binarisée du même événement
#     que MRR pour l'écrasante majorité du dataset — même redondance que
#     celle qui avait justifié de retirer nDCG du score en v3. Recall garde
#     un rôle réel mais mineur : les 3 questions multi-faits + 4
#     multi-documents où corr(MRR,Recall) tombe à 0.59 (une partie des faits
#     peut être trouvée sans que tous le soient).
#   - corr(MRR, Precision) = 0.75 et corr(Recall, Precision) = 0.75 : plus
#     corrélées entre elles qu'on ne le supposait (mécaniquement, un top_k
#     petit avec un bon rang tend à avoir peu de bruit), mais SANS
#     l'équivalence logique stricte de MRR/Recall — Precision capture un
#     échec que MRR ne voit jamais (bon chunk trouvé mais noyé dans du bruit
#     avec un top_k large), donc reste le seul second axe justifié.
# v4 réduit donc le poids de Recall (redondant) au profit de MRR (le signal
# le plus informatif : sensible au rang, pas juste binaire), Precision
# inchangée. Ce choix reste un jugement motivé par la structure du dataset,
# PAS calibré contre une vraie qualité de génération (LLM-as-judge) — d'où
# les variantes de poids loguées en diagnostic ci-dessous, pour permettre une
# ré-analyse a posteriori sans tout relancer si ce choix se révèle discutable.
SCORE_WEIGHTS = {
    "v4": (0.55, 0.15, 0.30),          # optimisé par Optuna à partir de v4
    "v3_legacy": (0.40, 0.30, 0.30),   # ancien poids v3, diagnostic (comparaison historique)
    "equal": (1 / 3, 1 / 3, 1 / 3),    # diagnostic : poids naïf égal
    "mrr_only": (1.0, 0.0, 0.0),       # diagnostic : et si on ignorait recall/precision ?
}


def _weighted_score(mrr, recall, precision, weights):
    w_mrr, w_recall, w_precision = weights
    return w_mrr * mrr + w_recall * recall + w_precision * precision


def _micro_macro_mean(values_by_doc):
    """values_by_doc : dict clé-document -> liste de floats (une entrée par
    question évaluée pour ce document). Retourne (micro, macro) :
      - micro : moyenne brute sur TOUTES les observations (une question =
        un poids égal, sur-pondère les documents les plus questionnés).
      - macro : moyenne des moyennes par document (chaque document pèse
        1/n_docs, indépendamment du nombre de questions écrites pour lui).
    Voir le commentaire sur avg_score_in_scope_macro plus bas pour le
    pourquoi de cette distinction (16 documents, 3 à 8 questions chacun).
    """
    all_values = [v for vs in values_by_doc.values() for v in vs]
    micro = sum(all_values) / len(all_values) if all_values else 0.0
    doc_means = [sum(vs) / len(vs) for vs in values_by_doc.values()]
    macro = sum(doc_means) / len(doc_means) if doc_means else 0.0
    return micro, macro


def objective(trial, retriever):
    # 128 retiré (v3 : mean systématiquement la pire config pour TOUS les
    # modèles testés, 257 trials) ; 800 ajouté car c'est la valeur RÉELLE de
    # prod (mdtoqdrant.py) — jamais testée par le grid v3 alors que c'est la
    # config effectivement déployée.
    chunk_size = trial.suggest_categorical("chunk_size", [256, 512, 800, 1024])
    # Overlap exprimé en fraction du chunk_size (pas en tokens bruts) : évite les
    # combinaisons dégénérées comme chunk_size=128 + overlap=128 (100% de
    # recouvrement, chunks quasi dupliqués — calcul gaspillé, nDCG/MRR biaisés
    # par des quasi-doublons). 0.3 ajouté : avec chunk_size=800, ça donne
    # overlap_tokens=240, exactement la config de prod (mdtoqdrant.py).
    overlap_ratio = trial.suggest_categorical("overlap_ratio", [0.0, 0.15, 0.25, 0.3, 0.4])
    overlap_tokens = int(chunk_size * overlap_ratio)
    # e5-large et arctic-l ajoutés (v5) pour élargir la comparaison au-delà de
    # miniLM/e5-small/e5-base/bge-m3 (v3 a montré que le modèle explique 90%
    # de la variance de score — c'est de loin le levier le plus rentable).
    # arctic-l (Snowflake/snowflake-arctic-embed-l-v2.0) choisi après
    # l'incident gte-multi en vérifiant explicitement : architecture
    # XLM-RoBERTa stock (pas de trust_remote_code), Apache 2.0.
    # gte-multi RETIRÉ (voir _get_embedding_model, model_map) : son code
    # custom (trust_remote_code) déclenche une assertion CUDA "index out of
    # bounds" (IndexKernel.cu) dès le premier batch d'encodage sur ce
    # serveur — une fois déclenchée, l'assertion corrompt le contexte CUDA
    # pour TOUT LE RESTE DU PROCESSUS : 30 trials consécutifs ont échoué
    # après ce seul incident (run_benchmark2.log, 22/07/2026), quel que soit
    # le modèle réellement demandé par chaque trial. Pas un problème
    # d'hyperparamètre — un problème d'environnement (version
    # torch/transformers/driver) spécifique au code custom de ce modèle.
    # jina-embeddings-v3 envisagé puis écarté : licence CC-BY-NC-4.0 (usage
    # commercial restreint) ET même besoin de trust_remote_code que gte-multi.
    embedding_model = trial.suggest_categorical(
        "embedding_model", ["miniLM", "e5-small", "e5-base", "e5-large", "bge-m3", "arctic-l"]
    )
    # 2 et 4 ajoutés pour affiner autour de l'optimum trouvé en v3 (top_k=3
    # gagnant, top_k=1 et top_k=10 nettement pires) sans élargir la grille au-delà.
    top_k = trial.suggest_categorical("top_k", [1, 2, 3, 4, 5, 10])
    similarity_threshold = trial.suggest_float("similarity_threshold", 0.0, 0.8)
    # Nouveau (v4) : reranker cross-encoder optionnel après le retrieval
    # dense+fraîcheur (voir RERANK_POOL_SIZE et LocalBenchmarkRetriever.search).
    # Absent de la prod actuelle — c'est une capacité candidate, pas une
    # réplication ; Optuna décide si le coût en vaut la peine.
    use_reranker = trial.suggest_categorical("use_reranker", [False, True])

    config = {
        "chunk_size": chunk_size,
        "overlap_tokens": overlap_tokens,
        "embedding_model": embedding_model,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,
        "use_reranker": use_reranker,
    }

    start_time = time.time()
    try:
        retriever.setup_for_trial(config)
    except Exception as e:
        logger.error(f"Échec initialisation: {e}")
        # Le TPESampler contraint (constraints_func, voir plus haut) appelle
        # _constraints_func APRÈS chaque trial, y compris les prunés — sans
        # cet attribut, KeyError non rattrapée dans le sampler et ÉTUDE
        # ENTIÈRE plantée (pas juste ce trial). Constraint > 0 = non-faisable
        # par défaut : un trial qui n'a même pas pu s'initialiser (OOM GPU,
        # échec de chargement modèle, etc.) ne doit jamais être considéré
        # comme respectant la contrainte hors-périmètre.
        trial.set_user_attr("constraint", (HORS_PERIMETRE_MIN_SCORE,))
        raise optuna.exceptions.TrialPruned()

    # Métriques CHUNK (primaires, pilotent le score optimisé par Optuna) et
    # DOCUMENT (secondaires, gardées en diagnostic pour comparaison — voir
    # avg_*_doc_level dans les user_attrs, mais elles ne comptent plus dans
    # ce qu'Optuna maximise : trouver le bon document sans le bon passage
    # dedans ne sert à rien pour la génération).
    trial_scores, mrr_scores, ndcg_scores, recall_scores, precision_scores = [], [], [], [], []
    doc_mrr_scores, doc_recall_scores = [], []
    in_scope_running = []  # signal de pruning : reflète l'objectif réellement optimisé (in-scope), pas le blend v3
    per_question_log = []
    pruned = False

    for i, dataset_idx in enumerate(QUESTION_ORDER):
        item = RETRIEVAL_DATASET[dataset_idx]
        question = item["question"]
        expected_docs = set(item.get("expected_documents", []))
        answer_snippets = item.get("answer_snippets", [])

        retrieved = retriever.search(
            question, top_k=top_k, similarity_threshold=similarity_threshold, use_reranker=use_reranker
        )

        mrr, ndcg, recall, precision = _chunk_metrics(retrieved, answer_snippets, expected_docs)

        # Diagnostic document-level (non optimisé) : dédup en préservant l'ordre.
        # Uniquement pour les questions in-scope — pour le hors-périmètre,
        # "a-t-on trouvé le bon document" n'a pas de sens (il n'y en a pas) et
        # ce signal est déjà capturé par avg_score_hors_perimetre ; le
        # mélanger ici comme le faisait v3 aurait contaminé la moyenne
        # doc-level de la même façon que les métriques chunk-level l'étaient.
        seen = set()
        retrieved_sources_unique = []
        for r in retrieved:
            if r["source"] not in seen:
                retrieved_sources_unique.append(r["source"])
                seen.add(r["source"])
        doc_mrr = doc_recall = None
        if expected_docs:
            doc_mrr = 0.0
            for rank, src in enumerate(retrieved_sources_unique, 1):
                if src in expected_docs:
                    doc_mrr = 1.0 / rank
                    break
            doc_recall = len(expected_docs.intersection(retrieved_sources_unique)) / len(expected_docs)
            doc_mrr_scores.append(doc_mrr)
            doc_recall_scores.append(doc_recall)

        # Score PAR QUESTION (poids SCORE_WEIGHTS["v4"], voir le commentaire
        # détaillé au-dessus de SCORE_WEIGHTS pour la justification empirique
        # du passage de 0.40/0.30/0.30 à 0.55/0.15/0.30 — nDCG reste retiré,
        # toujours pour la même raison de redondance avec MRR). Ce champ
        # "score" reste utilisé comme diagnostic par-question (per_question_log)
        # et pour le signal de pruning ; l'agrégation finale (macro par
        # document, in-scope séparé du hors-périmètre) est calculée plus bas.
        score = _weighted_score(mrr, recall, precision, SCORE_WEIGHTS["v4"])
        trial_scores.append(score)
        mrr_scores.append(mrr)
        ndcg_scores.append(ndcg)
        recall_scores.append(recall)
        precision_scores.append(precision)
        if expected_docs:
            in_scope_running.append(score)
        per_question_log.append({
            "dataset_index": dataset_idx,
            "question": question,
            "expected_documents": sorted(expected_docs),
            "retrieved_sources": [r["source"] for r in retrieved],
            "mrr": mrr,
            "ndcg": ndcg,
            "recall": recall,
            "precision": precision,
            "score": score,
            "doc_mrr": doc_mrr,
            "doc_recall": doc_recall,
        })

        # Pruning précoce : piloté par l'in-scope UNIQUEMENT (l'objectif
        # réellement optimisé en v4, voir HORS_PERIMETRE_MIN_SCORE plus haut).
        # v3 mélangeait hors-périmètre et in-scope dans le signal de pruning
        # alors que le hors-périmètre n'est même plus dans le score final ici
        # — le garder aurait pu faire élaguer trop tôt un trial qui démarre
        # sur une série de questions hors-périmètre ratées dans l'ordre fixe.
        partial_score = sum(in_scope_running) / len(in_scope_running) if in_scope_running else 0.0
        trial.report(partial_score, i)
        if trial.should_prune():
            pruned = True
            break

    n = len(trial_scores)
    duration_s = time.time() - start_time

    # Répartition par catégorie de question : les hors-périmètre mesurent la
    # capacité à NE RIEN retourner, les autres la capacité à retrouver le bon
    # PASSAGE (pas juste le bon document).
    evaluated_items = [RETRIEVAL_DATASET[idx] for idx in QUESTION_ORDER[:n]]
    hors_perimetre_scores = [s for s, item in zip(trial_scores, evaluated_items) if not item.get("expected_documents")]
    avg_hors_perimetre = sum(hors_perimetre_scores) / len(hors_perimetre_scores) if hors_perimetre_scores else 0.0

    # Structure unique regroupant TOUTES les métriques par-question in-scope,
    # groupées par document (ou combo de documents pour les 4 questions
    # multi-documents) : sert de base à la fois aux moyennes par métrique
    # brute (MRR/nDCG/Recall/Precision, doc-level) et aux variantes de score
    # composite (SCORE_WEIGHTS), en micro (moyenne brute par question) ET
    # macro (moyenne des moyennes par document — voir _micro_macro_mean).
    # Recommandation issue de l'analyse post-mortem v3 : les 82 questions
    # in-scope ne couvrent que 16 documents distincts sur les 404 du corpus,
    # avec entre 3 et 8 questions par document — une moyenne brute par
    # question sur-pondère mécaniquement les documents les plus questionnés
    # (ex: la Réunion Ouverte BDE n°3 du 13/02/2024, 8 questions, pèserait
    # 2,7x plus que l'AGE du 11/01/2022, 3 questions). C'est la version MACRO
    # du poids "v4" qui est retournée à Optuna (voir `return` plus bas) ;
    # toutes les autres variantes ci-dessous sont des diagnostics.
    in_scope_by_doc = {}
    for item, mrr_v, ndcg_v, recall_v, precision_v in zip(evaluated_items, mrr_scores, ndcg_scores, recall_scores, precision_scores):
        docs = item.get("expected_documents")
        if not docs:
            continue
        key = tuple(sorted(docs))
        in_scope_by_doc.setdefault(key, []).append({
            "mrr": mrr_v, "ndcg": ndcg_v, "recall": recall_v, "precision": precision_v,
        })
    num_docs_in_scope_evaluated = len(in_scope_by_doc)

    def _by_doc(field):
        return {k: [r[field] for r in v] for k, v in in_scope_by_doc.items()}

    avg_mrr_in_scope_micro, avg_mrr_in_scope_macro = _micro_macro_mean(_by_doc("mrr"))
    avg_ndcg_in_scope_micro, avg_ndcg_in_scope_macro = _micro_macro_mean(_by_doc("ndcg"))
    avg_recall_in_scope_micro, avg_recall_in_scope_macro = _micro_macro_mean(_by_doc("recall"))
    avg_precision_in_scope_micro, avg_precision_in_scope_macro = _micro_macro_mean(_by_doc("precision"))

    # Doc-level (a-t-on retrouvé le bon DOCUMENT, dédupliqué, indépendamment
    # du chunk précis) : diagnostic non optimisé, scopé in-scope uniquement
    # (voir la boucle plus haut, doc_mrr_scores/doc_recall_scores ne sont
    # remplies que pour les questions in-scope désormais).
    doc_level_by_doc = {}
    in_scope_items_ordered = [item for item in evaluated_items if item.get("expected_documents")]
    for item, dm, dr in zip(in_scope_items_ordered, doc_mrr_scores, doc_recall_scores):
        key = tuple(sorted(item["expected_documents"]))
        doc_level_by_doc.setdefault(key, []).append({"doc_mrr": dm, "doc_recall": dr})
    avg_doc_mrr_micro, avg_doc_mrr_macro = _micro_macro_mean({k: [r["doc_mrr"] for r in v] for k, v in doc_level_by_doc.items()})
    avg_doc_recall_micro, avg_doc_recall_macro = _micro_macro_mean({k: [r["doc_recall"] for r in v] for k, v in doc_level_by_doc.items()})

    # Variantes de score composite : la SEULE optimisée par Optuna est
    # "v4"/macro (voir `return`) ; les autres (v3_legacy, equal, mrr_only, et
    # les versions micro) sont des diagnostics purs, recalculés gratuitement à
    # partir des mêmes MRR/Recall/Precision déjà mesurés — pas d'appel
    # retrieval supplémentaire. Objectif : pouvoir vérifier après coup si le
    # "gagnant" de l'étude dépend fortement du choix de poids, sans devoir
    # tout relancer (voir SCORE_WEIGHTS pour la justification du choix v4).
    score_variants = {}
    for variant_name, weights in SCORE_WEIGHTS.items():
        values_by_doc = {
            k: [_weighted_score(r["mrr"], r["recall"], r["precision"], weights) for r in v]
            for k, v in in_scope_by_doc.items()
        }
        micro, macro = _micro_macro_mean(values_by_doc)
        score_variants[variant_name] = {"micro": micro, "macro": macro}
    avg_score_in_scope_macro = score_variants["v4"]["macro"]

    # Réplique EXACTE du score v3 (moyenne brute sur TOUTES les questions,
    # in-scope ET hors-périmètre confondues, poids 0.40/0.30/0.30) : gardée
    # en diagnostic pour comparaison historique directe avec les 257 trials
    # de l'étude v3 — c'est ce score qui s'est révélé anti-corrélé avec le
    # filtrage hors-périmètre (corr=-0.22) et n'est plus optimisé depuis v4.
    v3_replica_scores = [_weighted_score(m, r, p, SCORE_WEIGHTS["v3_legacy"]) for m, r, p in zip(mrr_scores, recall_scores, precision_scores)]
    avg_score_v3_replica = sum(v3_replica_scores) / n if n else 0.0

    # Variance : une moyenne unique cache si un trial est régulièrement moyen
    # ou juste porté par quelques questions faciles/chanceuses. Calculée sur
    # le score "v4" par-question, in-scope uniquement (mélanger le
    # hors-périmètre binaire 0/1 comme le faisait v3 aurait à nouveau
    # contaminé cette mesure de dispersion).
    in_scope_v4_scores = [r["mrr"] * SCORE_WEIGHTS["v4"][0] + r["recall"] * SCORE_WEIGHTS["v4"][1] + r["precision"] * SCORE_WEIGHTS["v4"][2]
                           for v in in_scope_by_doc.values() for r in v]
    score_std = statistics.pstdev(in_scope_v4_scores) if len(in_scope_v4_scores) > 1 else 0.0
    score_min = min(in_scope_v4_scores) if in_scope_v4_scores else 0.0
    score_max = max(in_scope_v4_scores) if in_scope_v4_scores else 0.0

    # Contrainte hors-périmètre (constrained optimization, voir sampler dans
    # main()) : convention Optuna, satisfaite quand la valeur est <= 0. Un
    # trial qui ne filtre pas au moins HORS_PERIMETRE_MIN_SCORE des questions
    # hors-périmètre est traité comme non-faisable, quel que soit son score
    # in-scope — voir le commentaire au-dessus de la définition de
    # HORS_PERIMETRE_MIN_SCORE pour la justification empirique de ce choix.
    constraint_value = HORS_PERIMETRE_MIN_SCORE - avg_hors_perimetre
    trial.set_user_attr("constraint", (constraint_value,))
    constraint_satisfied = constraint_value <= 0.0

    log_record = {
        "trial_number": trial.number,
        "params": config,
        # -- Objectif optimisé --
        "avg_score_in_scope_macro": avg_score_in_scope_macro,
        "hors_perimetre_constraint_satisfied": constraint_satisfied,
        "hors_perimetre_min_required": HORS_PERIMETRE_MIN_SCORE,
        # -- Métriques brutes in-scope, micro (par question) et macro (par document) --
        "avg_mrr_in_scope_micro": avg_mrr_in_scope_micro,
        "avg_mrr_in_scope_macro": avg_mrr_in_scope_macro,
        "avg_ndcg_in_scope_micro": avg_ndcg_in_scope_micro,
        "avg_ndcg_in_scope_macro": avg_ndcg_in_scope_macro,
        "avg_recall_in_scope_micro": avg_recall_in_scope_micro,
        "avg_recall_in_scope_macro": avg_recall_in_scope_macro,
        "avg_precision_in_scope_micro": avg_precision_in_scope_micro,
        "avg_precision_in_scope_macro": avg_precision_in_scope_macro,
        "avg_doc_mrr_in_scope_micro": avg_doc_mrr_micro,
        "avg_doc_mrr_in_scope_macro": avg_doc_mrr_macro,
        "avg_doc_recall_in_scope_micro": avg_doc_recall_micro,
        "avg_doc_recall_in_scope_macro": avg_doc_recall_macro,
        # -- Variantes de poids du score composite, diagnostic (voir SCORE_WEIGHTS) --
        "score_variants": score_variants,
        "avg_score_v3_replica": avg_score_v3_replica,
        # -- Hors-périmètre --
        "avg_score_hors_perimetre": avg_hors_perimetre,
        # -- Dispersion (sur le score v4 in-scope uniquement) --
        "score_std": score_std,
        "score_min": score_min,
        "score_max": score_max,
        # -- Contexte / coût --
        "num_chunks": len(retriever.chunks),
        "embedding_dim": int(retriever.embeddings.shape[1]) if len(retriever.embeddings) else 0,
        "num_questions_evaluated": n,
        "num_docs_in_scope_evaluated": num_docs_in_scope_evaluated,
        "pruned_early": pruned,
        "duration_seconds": round(duration_s, 2),
        "per_question": per_question_log,
    }

    for key, value in log_record.items():
        if key not in ("params", "per_question"):
            trial.set_user_attr(key, value)

    _append_trial_log(log_record)

    if pruned:
        raise optuna.exceptions.TrialPruned()

    return avg_score_in_scope_macro

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
            "où elle s'est arrêtée (même --study-name = même sqlite = mêmes trials). "
            "v4/v5 ajoutent e5-large, arctic-l et un reranker cross-encoder optionnel "
            "(use_reranker) (gte-multi retiré : assertion CUDA reproductible, voir "
            "model_map dans _get_embedding_model) : le premier trial pour "
            "chaque nouvelle combinaison (chunk_size, overlap, modèle) télécharge/encode "
            "à froid — durée totale par trial nettement plus variable qu'en v3."
        )
    )
    parser.add_argument(
        "--n-trials", type=int, default=300,
        help="Nombre de NOUVEAUX trials à lancer dans cet appel (par défaut 300). "
             "S'ajoute aux trials déjà présents dans la base.",
    )
    parser.add_argument(
        "--study-name", type=str, default="retrieval_only_v5",
        help="Nom de l'étude Optuna (change de nom pour repartir d'une base vierge). "
             "v4 avait changé le sens du score optimisé (in-scope macro sous "
             "contrainte hors-périmètre, voir HORS_PERIMETRE_MIN_SCORE) mais sa "
             "base sqlite contenait des trials avec embedding_model='gte-multi' "
             "(modèle retiré depuis, voir _get_embedding_model) : Optuna refuse "
             "de sampler sur une étude dont l'historique référence une valeur "
             "catégorielle qui n'existe plus dans la distribution actuelle "
             "(ValueError 'gte-multi' not in (...) — le run plantait à chaque "
             "relance). v5 repart d'une base vierge pour ce même motif ; le "
             "cache disque (temp/optuna_cache/) reste valide et partagé entre "
             "toutes les études, aucun calcul déjà fait n'est perdu. Les trials "
             "v1/v2/v3/v4 restent dans la même base sqlite mais leurs valeurs "
             "ne sont pas comparables à celles de v5.",
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
    # TPESampler avec constraints_func : sampling constraint-aware (Optuna
    # priorise les trials faisables et, parmi eux, ceux qui maximisent
    # l'objectif retourné par objective() — avg_score_in_scope_macro). Voir
    # HORS_PERIMETRE_MIN_SCORE et _constraints_func plus haut pour le
    # pourquoi de ce choix face au score composite unique de v3.
    sampler = optuna.samplers.TPESampler(constraints_func=_constraints_func, n_startup_trials=10)
    study = optuna.create_study(
        storage=db_url,
        direction="maximize",
        study_name=args.study_name,
        load_if_exists=True,
        sampler=sampler,
        # n_warmup_steps=20 (~15% des 132 questions, contre 98 en v3) : le
        # dataset hors-périmètre est passé de 16 à 50 questions pour rendre
        # avg_score_hors_perimetre statistiquement moins bruité (chaque
        # question pesait 6,25 points sur cette sous-métrique avec 16
        # questions ; 2 points avec 50).
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10, n_warmup_steps=20)
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

    complete_trials = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    # study.best_trial ne filtre PAS par faisabilité pour une étude
    # mono-objectif contrainte (limitation connue d'Optuna) : il faut exclure
    # nous-mêmes les trials qui violent HORS_PERIMETRE_MIN_SCORE avant de
    # choisir un "meilleur" trial, sinon on retomberait exactement dans le
    # travers de v3 (un trial avec un bon in-scope mais un hors-périmètre
    # non-filtré serait à nouveau élu "meilleur").
    feasible_trials = [t for t in complete_trials if t.user_attrs.get("hors_perimetre_constraint_satisfied")]
    logger.info(
        f"{len(complete_trials)} trial(s) complet(s), dont {len(feasible_trials)} respectant la "
        f"contrainte hors-périmètre (>= {HORS_PERIMETRE_MIN_SCORE:.0%}) et "
        f"{len(complete_trials) - len(feasible_trials)} rejeté(s) pour ce motif."
    )

    if feasible_trials:
        best_trial = max(feasible_trials, key=lambda t: t.value)
        logger.info(f"Meilleur score in-scope (macro, sous contrainte hors-périmètre) : {best_trial.value:.4f}")
        for key, value in best_trial.params.items():
            logger.info(f"  - {key}: {value}")
        for key, value in best_trial.user_attrs.items():
            logger.info(f"  · {key}: {value}")

        # Robustesse : score_std/sqrt(n) donne l'erreur standard du score sur
        # ~132 questions. Sur la study v3, cette erreur standard (~0.04) était
        # 15x plus grande que l'écart entre le trial classé 1er et le 20e
        # (~0.007) — annoncer UN gagnant à la 4e décimale n'avait aucun sens
        # statistique. On rapporte donc plutôt le "plateau" des trials
        # indiscernables du meilleur (à moins d'un écart-type), et le nombre
        # de configs (chunk_size, overlap, modèle, top_k) distinctes qu'il
        # contient.
        best_se = best_trial.user_attrs.get("score_std", 0.0) / math.sqrt(max(best_trial.user_attrs.get("num_questions_evaluated", 1), 1))
        plateau = [t for t in feasible_trials if (best_trial.value - t.value) <= best_se]
        distinct_configs = {
            (
                t.params.get("chunk_size"), t.params.get("overlap_ratio"), t.params.get("embedding_model"),
                t.params.get("top_k"), t.params.get("use_reranker"),
            )
            for t in plateau
        }
        logger.info(
            f"Plateau à ±1 écart-type du meilleur (SE≈{best_se:.4f}) : {len(plateau)} trial(s), "
            f"{len(distinct_configs)} config(s) (chunk_size/overlap/modèle/top_k/reranker) distincte(s) : {sorted(distinct_configs, key=lambda c: str(c))}"
        )
    else:
        logger.warning(
            "Aucun trial ne respecte la contrainte hors-périmètre — augmenter --n-trials, "
            "ou revoir HORS_PERIMETRE_MIN_SCORE si la contrainte est trop stricte pour ce corpus."
        )

    if len(study.trials) > 0:
        try:
            df = study.trials_dataframe()
            df.to_csv("optuna_retrieval_benchmark.csv", index=False)
            logger.info("Résultats exportés dans : optuna_retrieval_benchmark.csv")
            logger.info(f"Détail par question et par trial dans : {TRIAL_LOG_PATH}")
        except ValueError:
            pass

if __name__ == "__main__":
    main()
