import optuna
import time
import logging
import json
import pandas as pd
import numpy as np
import math
from pathlib import Path
from sentence_transformers import SentenceTransformer

# On importe les composants purement génératifs / prompts de ton projet
from app.back.generate import _get_groq_client, build_prompt, _build_context

# On utilise ta logique de chunking
from app.back.chunking import get_hybrid_chunks

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from collections import OrderedDict


# ==========================================
# 1. RETRIEVER EN MÉMOIRE POUR LE BENCHMARK
# ==========================================
class LocalBenchmarkRetriever:
    """
    Retriever local qui court-circuite Qdrant pour le benchmark.
    À chaque trial, il découpe et encode localement avec les paramètres choisis.
    C'est la seule façon d'optimiser efficacement chunk_size et embedding_model !
    """

    _chunks_cache = OrderedDict()
    _embeddings_cache = OrderedDict()

    def __init__(self, markdowns_dir: Path):
        self.md_files = list(markdowns_dir.glob("*.md"))
        self.documents = []
        for file in self.md_files:
            with open(file, "r", encoding="utf-8") as f:
                self.documents.append({"id": file.stem, "content": f.read()})

    def setup_for_trial(self, config):
        """Découpe et encode les documents avec la configuration actuelle, avec CACHE."""
        chunk_size = config["chunk_size"]
        overlap_tokens = config["overlap_tokens"]
        model_name = config["embedding_model"]

        # 1. Cache pour le Chunking
        cache_key_chunks = (chunk_size, overlap_tokens)
        if cache_key_chunks not in self._chunks_cache:
            if len(self._chunks_cache) > 20:
                self._chunks_cache.popitem(last=False)
            chunks = []
            for doc in self.documents:
                text_chunks = get_hybrid_chunks(
                    doc["content"], chunk_size=chunk_size, chunk_overlap=overlap_tokens
                )
                for c in text_chunks:
                    chunks.append({"source": doc["id"], "text": c})
            self._chunks_cache[cache_key_chunks] = chunks
        else:
            self._chunks_cache.move_to_end(cache_key_chunks)

        self.chunks = self._chunks_cache[cache_key_chunks]

        # 2. Cache pour les Embeddings
        cache_key_embeddings = (chunk_size, overlap_tokens, model_name)
        self.model = _get_embedding_model(model_name)

        if cache_key_embeddings not in self._embeddings_cache:
            if len(self._embeddings_cache) > 20:
                self._embeddings_cache.popitem(last=False)
            texts = [c["text"] for c in self.chunks]
            if not texts:
                emb = np.array([])
            else:
                # IMPORTANT: Normalize embeddings for fast numpy dot product
                emb = self.model.encode(texts, normalize_embeddings=True)
            self._embeddings_cache[cache_key_embeddings] = emb
        else:
            self._embeddings_cache.move_to_end(cache_key_embeddings)

        self.embeddings = self._embeddings_cache[cache_key_embeddings]

    _query_cache = OrderedDict()

    def search(self, query: str, top_k: int, similarity_threshold: float = 0.0):
        if len(self.chunks) == 0:
            return []

        # IMPORTANT: Normalize query embedding avec Cache
        q_key = (str(self.model), query)
        if q_key not in self._query_cache:
            if len(self._query_cache) > 100:
                self._query_cache.popitem(last=False)
            self._query_cache[q_key] = self.model.encode(
                [query], normalize_embeddings=True
            )[0]
        else:
            self._query_cache.move_to_end(q_key)

        q_emb = self._query_cache[q_key]

        # Si les embeddings sont normalisés, le produit scalaire (dot product) est exactement la cosine similarity !
        scores = np.dot(self.embeddings, q_emb)

        # Filtrage par similarity_threshold
        valid_indices = [
            i for i, score in enumerate(scores) if score >= similarity_threshold
        ]
        if not valid_indices:
            return []

        valid_scores = scores[valid_indices]
        local_top = np.argsort(valid_scores)[::-1][:top_k]
        top_indices = [valid_indices[i] for i in local_top]

        results = []
        for idx in top_indices:
            # On recrée l'objet SearchResult attendu par build_context
            results.append(
                {
                    "content": self.chunks[idx]["text"],
                    "metadata": {"source": self.chunks[idx]["source"]},
                    "score": float(scores[idx]),
                    "semantic_score": float(scores[idx]),
                    "freshness_score": 1.0,  # Ignoré pour ce test pur sémantique
                }
            )
        return results


# Cache global des modèles HF pour éviter de les télécharger à chaque essai
_embedding_models_cache = {}


def _get_embedding_model(model_name: str):
    model_map = {
        "miniLM": "all-MiniLM-L6-v2",
        "e5-small": "intfloat/multilingual-e5-small",
    }
    real_name = model_map.get(model_name, "all-MiniLM-L6-v2")
    if real_name not in _embedding_models_cache:
        logger.info(f"Chargement du modèle d'embedding: {real_name}")
        _embedding_models_cache[real_name] = SentenceTransformer(real_name)
    return _embedding_models_cache[real_name]


# ==========================================
# 2. GOLDEN DATASET (AVEC GROUND TRUTH & EXPECTED DOCS)
# ==========================================
GOLDEN_DATASET = [
    {
        "question": "Qui est le président du BDE actuel ?",
        "ground_truth": "Sabeur Aridhi",
        "expected_documents": ["cr_ro_12_mars_2023"],  # IDs attendus
    },
    {
        "question": "Quel club a été validé à la Réunion Ouverte du 12 mars ?",
        "ground_truth": "Le club Échecs",
        "expected_documents": ["cr_ro_12_mars_2023"],
    },
    {
        "question": "Combien de membres compte le bureau des sports ?",
        "ground_truth": "Le contexte ne donne pas cette information.",
        "expected_documents": [],  # Hors périmètre / Piège
    },
    {
        "question": "Qui organise le WEI ?",
        "ground_truth": "Le club Anim",
        "expected_documents": ["orga_wei_2023"],  # Document fictif attendu
    },
    {
        "question": "Où est la machine à café du bâtiment E ?",
        "ground_truth": "L'information n'est pas présente dans les archives.",
        "expected_documents": [],
    },
]


# ==========================================
# 3. PIPELINE RAG MOCKÉE (SANS QDRANT)
# ==========================================
def run_rag_pipeline(config, question, retriever):
    top_k = int(config["top_k"])
    similarity_threshold = float(config.get("similarity_threshold", 0.0))
    results = retriever.search(
        question, top_k=top_k, similarity_threshold=similarity_threshold
    )
    context = _build_context(results)

    # On isole les sources réellement remontées (en gardant l'ordre original !)
    seen = set()
    retrieved_sources = []
    for r in results:
        src = r["metadata"]["source"]
        if src not in seen:
            retrieved_sources.append(src)
            seen.add(src)

    prompt = build_prompt(context, question)
    client = _get_groq_client()
    model_name = config["generation_llm"]
    prompt_style = config.get("generation_prompt", "persona")

    # Injection dynamique du style optimisé
    if prompt_style == "strict":
        sys_msg = "Tu es un assistant strict, factuel et neutre. Ne réponds qu'avec les informations du contexte."
    elif prompt_style == "concise":
        sys_msg = "Tu es un assistant ultra-concis. Ta réponse doit tenir en une seule phrase."
    elif prompt_style == "citation":
        sys_msg = "Tu es un assistant académique. Cite toujours la source du contexte exact à la fin de tes phrases."
    else:
        sys_msg = "Tu es un étudiant de Telecom Nancy décontracté. Tu utilises l'humour étudiant."

    generation_temperature = config.get("generation_temperature", 0.7)

    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=generation_temperature,
        )
        answer = completion.choices[0].message.content
        return answer, context, retrieved_sources
    except Exception as e:
        # Filtrage et Pruning si le LLM n'existe pas ou limite atteinte
        err_msg = str(e).lower()
        if (
            "does not exist" in err_msg
            or "not found" in err_msg
            or "invalid" in err_msg
        ):
            logger.warning(
                f"Modèle {model_name} invalide sur Groq, PRUNING de l'essai."
            )
            raise optuna.exceptions.TrialPruned()
        raise e


# ==========================================
# 4. LLM AS A JUDGE (MULTI-JUGEMENTS & SÉPARÉS)
# ==========================================
def judge_rag_quality(context, question, response, evaluate_persona=True):
    client = _get_groq_client()

    keys_instruction = '"faithfulness", "answer_relevance", "safety"'
    if evaluate_persona:
        keys_instruction += ', "persona_compliance"'

    persona_rule = (
        "- 'persona_compliance' : 1.0 si le ton est bien celui d'un étudiant décontracté, sinon 0.0."
        if evaluate_persona
        else ""
    )

    judge_prompt = f"""
    En tant que juge expert, évalue la qualité de la réponse générée pour une question, en fonction du contexte fourni.
    Renvoie UNIQUEMENT un objet JSON valide avec ces clés exactes : 
    {keys_instruction}.
    Chaque valeur doit être un nombre décimal (float) entre 0.0 et 1.0.
    
    Critères spécifiques :
    - 'faithfulness' : La réponse est-elle fidèle au contexte (sans hallucination) ?
    - 'answer_relevance' : La réponse adresse-t-elle directement la question ?
    {persona_rule}
    - 'safety' : 1.0 si le contenu est bienveillant, 0.0 s'il est offensant.

    Question: {question}
    Contexte: {context}
    Réponse à évaluer: {response}
    """

    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": judge_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        logger.error(f"Erreur d'un juge RAG Quality: {e}")
        return {}


def judge_correctness(question, response, ground_truth):
    client = _get_groq_client()
    judge_prompt = f"""
    En tant que juge factuel strict, détermine si la réponse donnée correspond aux faits attendus (Ground Truth).
    Ignore les détails additionnels ou le style, vérifie juste si l'information clé est présente et correcte.
    Renvoie UNIQUEMENT un objet JSON valide avec cette clé exacte : "answer_correctness".
    Valeur attendue : décimal entre 0.0 et 1.0.

    Question: {question}
    Réponse à évaluer: {response}
    Ground Truth (Fait attendu): {ground_truth}
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": judge_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        scores = json.loads(completion.choices[0].message.content)
        return float(scores.get("answer_correctness", 0.0))
    except Exception as e:
        logger.error(f"Erreur du juge Correctness: {e}")
        return 0.0


def evaluate_response(
    context, question, response, ground_truth="", num_judgments=1, evaluate_persona=True
):
    # Juge Principal (Qualité)
    accumulated_scores = {
        k: 0.0
        for k in [
            "faithfulness",
            "answer_relevance",
            "persona_compliance",
            "safety",
            "answer_correctness",
        ]
    }
    valid_judgments = 0

    for _ in range(num_judgments):
        scores = judge_rag_quality(context, question, response, evaluate_persona)
        if scores:
            accumulated_scores["faithfulness"] += float(scores.get("faithfulness", 0.0))
            accumulated_scores["answer_relevance"] += float(
                scores.get("answer_relevance", 0.0)
            )
            if evaluate_persona:
                accumulated_scores["persona_compliance"] += float(
                    scores.get("persona_compliance", 0.0)
                )
            accumulated_scores["safety"] += float(scores.get("safety", 1.0))
            valid_judgments += 1

    if valid_judgments > 0:
        for k in ["faithfulness", "answer_relevance", "persona_compliance", "safety"]:
            accumulated_scores[k] /= valid_judgments

    # Juge Correctness Séparé + RapidFuzz
    if ground_truth:
        import rapidfuzz

        exact_match_score = rapidfuzz.fuzz.token_set_ratio(
            ground_truth.lower(), response.lower()
        )
        if exact_match_score >= 90:
            accumulated_scores["answer_correctness"] = 1.0
        else:
            accumulated_scores["answer_correctness"] = judge_correctness(
                question, response, ground_truth
            )

    return accumulated_scores


# ==========================================
# 5. FONCTION OBJECTIF & CALCUL DU SCORE
# ==========================================
def objective(trial, retriever):
    chunk_size = trial.suggest_categorical("chunk_size", [150, 300, 600, 1000])
    # Overlap en tokens directement, décorrélé du chunk_size !
    overlap_tokens = trial.suggest_categorical("overlap_tokens", [0, 50, 100, 200])
    embedding_model = trial.suggest_categorical(
        "embedding_model", ["miniLM", "e5-small"]
    )
    top_k = trial.suggest_categorical("top_k", [2, 3, 5, 8])
    similarity_threshold = trial.suggest_float("similarity_threshold", 0.2, 0.8)
    generation_prompt = trial.suggest_categorical(
        "generation_prompt", ["strict", "concise", "persona", "citation"]
    )
    generation_temperature = trial.suggest_categorical(
        "generation_temperature", [0.0, 0.2, 0.5, 0.7]
    )
    generation_llm = trial.suggest_categorical(
        "generation_llm",
        ["llama-3.1-8b-instant", "gemma2-9b-it", "mixtral-8x7b-32768", "qwen-2.5-32b"],
    )

    config = {
        "chunk_size": chunk_size,
        "overlap_tokens": overlap_tokens,
        "embedding_model": embedding_model,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,
        "generation_prompt": generation_prompt,
        "generation_temperature": generation_temperature,
        "generation_llm": generation_llm,
    }

    # 1. On configure le retriever en mémoire pour cette configuration
    try:
        retriever.setup_for_trial(config)
    except Exception as e:
        logger.error(f"Échec de l'initialisation du Retriever: {e}")
        raise optuna.exceptions.TrialPruned()

    trial_scores = []
    metrics_sum = {
        "faithfulness": 0.0,
        "answer_relevance": 0.0,
        "persona_compliance": 0.0,
        "safety": 0.0,
        "answer_correctness": 0.0,
        "retrieval_mrr": 0.0,
        "retrieval_ndcg": 0.0,
    }

    for i, item in enumerate(GOLDEN_DATASET):
        question = item["question"]
        ground_truth = item.get("ground_truth", "")
        expected_docs = set(item.get("expected_documents", []))

        success = False
        retries = 3

        while not success and retries > 0:
            try:
                start_time = time.time()
                response, context_extrait, retrieved_sources = run_rag_pipeline(
                    config, question, retriever
                )
                latency = time.time() - start_time
                metrics_sum["latency"] = metrics_sum.get("latency", 0.0) + latency

                # A. Calcul Déterministe du MRR et nDCG (Retrieval)
                if expected_docs:
                    mrr = 0.0
                    for rank, src in enumerate(retrieved_sources, 1):
                        if src in expected_docs:
                            mrr = 1.0 / rank
                            break

                    dcg = sum(
                        1.0 / math.log2(i + 1)
                        for i, src in enumerate(retrieved_sources, 1)
                        if src in expected_docs
                    )
                    k = (
                        min(len(retrieved_sources), len(expected_docs))
                        if retrieved_sources
                        else 1
                    )
                    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, k + 1))
                    ndcg = dcg / idcg if idcg > 0 else 0.0
                else:
                    # S'il ne faut rien retourner, MRR et nDCG sont 1.0 SEULEMENT si on n'a rien ramené (sinon 0.0)
                    mrr = 1.0 if len(retrieved_sources) == 0 else 0.0
                    ndcg = 1.0 if len(retrieved_sources) == 0 else 0.0

                metrics_sum["retrieval_mrr"] += mrr
                metrics_sum["retrieval_ndcg"] += ndcg

                # B. Évaluation LLM-as-a-Judge (num_judgments=1 avec temp=0 pour sauver des coûts)
                has_persona = config["generation_prompt"] == "persona"
                scores = evaluate_response(
                    context_extrait,
                    question,
                    response,
                    ground_truth,
                    num_judgments=1,
                    evaluate_persona=has_persona,
                )

                if not has_persona:
                    scores["persona_compliance"] = 1.0

                for k in scores:
                    metrics_sum[k] += scores[k]

                # C. Calcul de la Moyenne Pondérée Constante (Biais neutralisé)
                w_f = 0.30
                w_r = 0.35
                w_c = 0.25 if ground_truth else 0.0
                w_p = 0.05
                w_s = 0.05

                total_w = w_f + w_r + w_c + w_p + w_s

                weighted_score = (
                    (w_f * scores.get("faithfulness", 0.0))
                    + (w_r * ndcg)
                    + (w_c * scores.get("answer_correctness", 0.0))
                    + (w_p * scores.get("persona_compliance", 1.0))
                    + (w_s * scores.get("safety", 1.0))
                ) / total_w

                trial_scores.append(weighted_score)
                success = True

                # D. Optuna Pruning
                partial_score = sum(trial_scores) / len(trial_scores)
                trial.report(partial_score, i)
                if trial.should_prune():
                    raise optuna.exceptions.TrialPruned()

            except optuna.exceptions.TrialPruned:
                raise  # On laisse remonter pour pruner le trial
            except Exception as e:
                logger.error(f"Erreur d'API Q{i + 1}: {e}")
                time.sleep(30)
                retries -= 1
                if retries == 0:
                    trial_scores.append(0.0)

    num_questions = len(trial_scores) if trial_scores else 1
    avg_latency = metrics_sum.get("latency", 0.0) / num_questions
    trial.set_user_attr("latency_seconds", avg_latency)

    for k, total in metrics_sum.items():
        if k == "latency":
            continue
        if total == 0.0 and k in [
            "answer_correctness",
            "retrieval_mrr",
            "retrieval_ndcg",
            "persona_compliance",
        ]:
            continue
        trial.set_user_attr(k, total / num_questions)

    base_score = sum(trial_scores) / len(trial_scores) if trial_scores else 0.0
    return base_score, avg_latency


# ==========================================
# 6. MAIN & DB
# ==========================================
def main():
    logger.info("Initialisation du benchmark RAG avec Optuna...")

    # Trouver le dossier des markdowns
    md_dir = Path(__file__).resolve().parent.parent.parent / "temp" / "markdowns"
    if not md_dir.exists():
        logger.warning(f"Dossier {md_dir} introuvable. Création d'un mock.")
        md_dir.mkdir(parents=True, exist_ok=True)
        with open(md_dir / "cr_ro_12_mars_2023.md", "w") as f:
            f.write(
                "# Réunion Ouverte\nSabeur Aridhi est le président du BDE actuel.\nLe club Échecs a été validé à la réunion."
            )
        with open(md_dir / "orga_wei_2023.md", "w") as f:
            f.write(
                "# Organisation WEI\nC'est le club Anim qui gère toute l'organisation du WEI cette année."
            )

    retriever = LocalBenchmarkRetriever(md_dir)

    db_url = "sqlite:///rag_benchmark.db"
    study = optuna.create_study(
        storage=db_url,
        directions=["maximize", "minimize"],
        study_name="rag_tn_pareto",
        load_if_exists=True,
    )

    try:
        logger.info(f"Sauvegarde résiliente dans : {db_url}")
        # PHASE 1 : On peut réduire n_trials pour tester
        study.optimize(lambda trial: objective(trial, retriever), n_trials=100)
    except KeyboardInterrupt:
        logger.warning("Optimisation arrêtée manuellement.")

    logger.info("\n" + "=" * 50)
    logger.info("OPTIMISATION TERMINÉE")
    logger.info("=" * 50)

    if len(study.trials) > 0:
        try:
            best_trials = study.best_trials
            logger.info(f"Trouvé {len(best_trials)} essais sur le Front de Pareto.")
            for i, trial in enumerate(best_trials, 1):
                logger.info(f"--- Pareto Optimal #{i} ---")
                logger.info(
                    f"  Qualité : {trial.values[0]:.4f} | Latence : {trial.values[1]:.2f}s"
                )
                for key, value in trial.params.items():
                    logger.info(f"    - {key}: {value}")
            df = study.trials_dataframe()
            df.to_csv("optuna_rag_benchmark.csv", index=False)
            logger.info("Exporté dans : optuna_rag_benchmark.csv")
        except ValueError:
            pass


if __name__ == "__main__":
    main()
