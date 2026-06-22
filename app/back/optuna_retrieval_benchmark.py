import optuna
import time
import logging
import math
import numpy as np
import pandas as pd
from pathlib import Path
from collections import OrderedDict
from sentence_transformers import SentenceTransformer

from app.back.chunking import get_hybrid_chunks

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
RETRIEVAL_DATASET = [
    # Factuelles
    {"question": "Qui est le président du BDE actuel ?", "expected_documents": ["cr_ro_12_mars_2023"]},
    {"question": "Quel budget a été alloué au club Echecs ?", "expected_documents": ["cr_ro_12_mars_2023"]},
    {"question": "Qui organise le WEI 2023 ?", "expected_documents": ["orga_wei_2023"]},
    
    # Paraphrases
    {"question": "Qui dirige le BDE ?", "expected_documents": ["cr_ro_12_mars_2023"]},
    {"question": "Qui est à la tête du BDE ?", "expected_documents": ["cr_ro_12_mars_2023"]},
    {"question": "Quel club gère l'orga du WEI ?", "expected_documents": ["orga_wei_2023"]},
    
    # Multi-documents
    {"question": "Quels sont les clubs mentionnés dans les documents 2023 ?", "expected_documents": ["cr_ro_12_mars_2023", "orga_wei_2023"]},
    
    # Hors périmètre
    {"question": "Quelle est la taille du parking ?", "expected_documents": []},
    {"question": "Combien y a-t-il d'étudiants à Telecom Nancy ?", "expected_documents": []},
    {"question": "Quel est le numéro de téléphone du BDE ?", "expected_documents": []}
]
# On multiplie artificiellement pour avoir 50 questions dans le mock
RETRIEVAL_DATASET = RETRIEVAL_DATASET * 5

# ==========================================
# 2. RETRIEVER EN MÉMOIRE
# ==========================================
class LocalBenchmarkRetriever:
    _chunks_cache = OrderedDict()
    _embeddings_cache = OrderedDict()

    def __init__(self, markdowns_dir: Path):
        self.md_files = list(markdowns_dir.glob("*.md"))
        self.documents = []
        for file in self.md_files:
            with open(file, "r", encoding="utf-8") as f:
                self.documents.append({"id": file.stem, "content": f.read()})
                
    def setup_for_trial(self, config):
        chunk_size = config["chunk_size"]
        overlap_tokens = config["overlap_tokens"]
        model_name = config["embedding_model"]
        
        cache_key_chunks = (chunk_size, overlap_tokens)
        if cache_key_chunks not in self._chunks_cache:
            if len(self._chunks_cache) > 20:
                self._chunks_cache.popitem(last=False)
            chunks = []
            for doc in self.documents:
                text_chunks = get_hybrid_chunks(doc["content"], chunk_size=chunk_size, chunk_overlap=overlap_tokens)
                for c in text_chunks:
                    chunks.append({"source": doc["id"], "text": c})
            self._chunks_cache[cache_key_chunks] = chunks
        else:
            self._chunks_cache.move_to_end(cache_key_chunks)
            
        self.chunks = self._chunks_cache[cache_key_chunks]
        
        cache_key_embeddings = (chunk_size, overlap_tokens, model_name)
        self.model = _get_embedding_model(model_name)
        
        if cache_key_embeddings not in self._embeddings_cache:
            if len(self._embeddings_cache) > 20:
                self._embeddings_cache.popitem(last=False)
            texts = [c["text"] for c in self.chunks]
            if not texts:
                emb = np.array([])
            else:
                emb = self.model.encode(texts, normalize_embeddings=True)
            self._embeddings_cache[cache_key_embeddings] = emb
        else:
            self._embeddings_cache.move_to_end(cache_key_embeddings)
            
        self.embeddings = self._embeddings_cache[cache_key_embeddings]
            
    _query_cache = OrderedDict()
    
    def search(self, query: str, top_k: int, similarity_threshold: float = 0.0):
        if len(self.chunks) == 0:
            return []
            
        q_key = (str(self.model), query)
        if q_key not in self._query_cache:
            if len(self._query_cache) > 200:
                self._query_cache.popitem(last=False)
            self._query_cache[q_key] = self.model.encode([query], normalize_embeddings=True)[0]
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
    overlap_tokens = trial.suggest_categorical("overlap_tokens", [0, 32, 64, 128])
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
    
    try:
        retriever.setup_for_trial(config)
    except Exception as e:
        logger.error(f"Échec initialisation: {e}")
        raise optuna.exceptions.TrialPruned()
    
    trial_scores = []
    
    for i, item in enumerate(RETRIEVAL_DATASET):
        question = item["question"]
        expected_docs = set(item.get("expected_documents", []))
        
        retrieved_sources = retriever.search(question, top_k=top_k, similarity_threshold=similarity_threshold)
        
        mrr = 0.0
        ndcg = 0.0
        recall = 0.0
        
        if expected_docs:
            for rank, src in enumerate(retrieved_sources, 1):
                if src in expected_docs:
                    mrr = 1.0 / rank
                    break
            
            intersection = expected_docs.intersection(set(retrieved_sources))
            recall = len(intersection) / len(expected_docs)
            
            dcg = sum(1.0 / math.log2(rank + 1) for rank, src in enumerate(retrieved_sources, 1) if src in expected_docs)
            k = min(len(retrieved_sources), len(expected_docs)) if retrieved_sources else 1
            idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, k + 1))
            ndcg = dcg / idcg if idcg > 0 else 0.0
        else:
            if len(retrieved_sources) == 0:
                mrr, ndcg, recall = 1.0, 1.0, 1.0
            else:
                mrr, ndcg, recall = 0.0, 0.0, 0.0
                
        # Poids Optuna de l'espace exclusif Retrieval
        score = 0.5 * mrr + 0.3 * ndcg + 0.2 * recall
        trial_scores.append(score)
        
        # Pruning précoce
        partial_score = sum(trial_scores) / len(trial_scores)
        trial.report(partial_score, i)
        if trial.should_prune():
            raise optuna.exceptions.TrialPruned()
            
    return sum(trial_scores) / len(trial_scores)

# ==========================================
# 4. MAIN
# ==========================================
def main():
    logger.info("Initialisation du benchmark RETRIEVAL 100% avec Optuna...")
    md_dir = Path(__file__).resolve().parent.parent.parent / "temp" / "markdowns"
    if not md_dir.exists():
        md_dir.mkdir(parents=True, exist_ok=True)
        with open(md_dir / "cr_ro_12_mars_2023.md", "w") as f:
            f.write("# Réunion Ouverte\nSabeur Aridhi est le président du BDE actuel.\nLe club Échecs a été validé avec un budget.")
        with open(md_dir / "orga_wei_2023.md", "w") as f:
            f.write("# Organisation WEI\nC'est le club Anim qui gère toute l'organisation du WEI 2023 cette année.")
            
    retriever = LocalBenchmarkRetriever(md_dir)
    
    db_url = "sqlite:///retrieval_benchmark.db"
    study = optuna.create_study(
        storage=db_url,
        direction="maximize", 
        study_name="retrieval_only_v1",
        load_if_exists=True,
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10, n_warmup_steps=5)
    )
    
    try:
        logger.info(f"Lancement de l'étude (0 appel API LLM) dans : {db_url}")
        study.optimize(lambda trial: objective(trial, retriever), n_trials=300)
    except KeyboardInterrupt:
        logger.warning("Arrêt manuel.")
        
    if len(study.trials) > 0:
        try:
            best_trial = study.best_trial
            logger.info(f"Meilleur score Retrieval : {best_trial.value:.4f}")
            for key, value in best_trial.params.items():
                logger.info(f"  - {key}: {value}")
            df = study.trials_dataframe()
            df.to_csv("optuna_retrieval_benchmark.csv", index=False)
            logger.info("Résultats exportés dans : optuna_retrieval_benchmark.csv")
        except ValueError:
            pass

if __name__ == "__main__":
    main()
