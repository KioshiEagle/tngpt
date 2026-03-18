import time
import pandas as pd
import numpy as np
from pathlib import Path
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from semantic_chunkers import StatisticalChunker
def run_grid_search(md_path):
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    results = []
    
    # --- DÉFINITION DE LA GRILLE (HYBRIDE) ---
    # Tailles de 200 à 2000 caractères par pas de 200
    sizes = np.linspace(200, 2000, 10, dtype=int)
    # Overlaps de 0% à 40% de la taille par pas de 10%
    overlap_rates = [0, 0.1, 0.2, 0.3, 0.4]

    print(f"🚀 Lancement du Grid Search Hybride ({len(sizes)*len(overlap_rates)} combinaisons)...")
    
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "H1"), ("##", "H2")])
    sections = md_splitter.split_text(text)

    for size in sizes:
        for rate in overlap_rates:
            overlap = int(size * rate)
            t_start = time.time()
            
            rec_splitter = RecursiveCharacterTextSplitter(chunk_size=int(size), chunk_overlap=overlap)
            chunks = []
            for s in sections:
                header = " > ".join([str(v) for v in s.metadata.values()])
                sub_chunks = rec_splitter.split_text(s.page_content)
                chunks.extend([f"[{header}] {c}" for c in sub_chunks])
            
            duration = (time.time() - t_start) * 1000
            lens = [len(c) for c in chunks]
            
            results.append({
                "Type": "Hybride",
                "Size": size,
                "Overlap_Pct": f"{int(rate*100)}%",
                "Nb_Chunks": len(chunks),
                "Moy_Len": int(np.mean(lens)),
                "Std_Dev": round(np.std(lens), 1),
                "Efficiency_Score": round(len(chunks) / (duration + 0.1), 2) # Chunks par ms
            })

    # --- TEST SÉMANTIQUE (Baseline Scientifique) ---
    print("🧠 Calcul du Sémantique (Statistical)...")
    try:
        t_start = time.time()
        encoder = SentenceTransformerEncoder(model_name="all-MiniLM-L6-v2")
        chunker = StatisticalChunker(encoder=encoder)
        sem_chunks = [c.content for c in chunker(docs=[text])[0]]
        
        duration = (time.time() - t_start) * 1000
        lens = [len(c) for c in sem_chunks]
        results.append({
            "Type": "Sémantique", "Size": "Dynamic", "Overlap_Pct": "N/A",
            "Nb_Chunks": len(sem_chunks), "Moy_Len": int(np.mean(lens)),
            "Std_Dev": round(np.std(lens), 1), "Efficiency_Score": round(len(sem_chunks) / (duration + 0.1), 4)
        })
    except Exception as e:
        print(f"❌ Erreur Sémantique : {e}")

    return pd.DataFrame(results)

if __name__ == "__main__":
    target = Path("app/back/temp/markdowns/1wPeGblsvZ9j5tFY_NI8ki9xqiZvuxGbv.md")
    if target.exists():
        df = run_grid_search(target)
        # Export pour analyse externe si besoin
        df.to_csv("app/back/chunker_grid_results.csv", index=False)
        
        print("\n" + "="*100)
        print("🏆 TOP 10 DES CONFIGURATIONS (Triées par stabilité Std_Dev)")
        print("="*100)
        print(df.sort_values("Std_Dev").head(15).to_markdown(index=False))