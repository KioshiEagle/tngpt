import os
import time
import psutil
import pandas as pd
from pathlib import Path

# Imports des extracteurs
import fitz  # PyMuPDF
import pymupdf4llm
from markitdown import MarkItDown
from docling.document_converter import DocumentConverter
import pdfplumber
from pypdf import PdfReader

def get_mem():
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)

def run_master_benchmark(pdf_path):
    path_str = str(pdf_path)
    file_size_mo = os.path.getsize(pdf_path) / (1024 * 1024)
    
    doc_info = fitz.open(path_str)
    nb_pages = len(doc_info)
    doc_info.close()
    
    results = []
    detailed_contents = {}

    extractors = [
        ("PyMuPDF_Raw", lambda p: "".join([page.get_text() for page in fitz.open(p)])),
        ("PyMuPDF4LLM", lambda p: pymupdf4llm.to_markdown(p)),
        ("pypdf", lambda p: "".join([page.extract_text() for page in PdfReader(p).pages])),
        ("pdfplumber", lambda p: "".join([page.extract_text() or "" for page in pdfplumber.open(p).pages])),
        ("MarkItDown", lambda p: MarkItDown().convert(p).text_content),
        ("Docling", lambda p: DocumentConverter().convert(p).document.export_to_markdown())
    ]

    for name, func in extractors:
        print(f"🔄 Test de {name}...")
        try:
            m_start = get_mem()
            t_start = time.time()
            
            content = func(path_str)
            
            duration = time.time() - t_start
            mem_peak = get_mem() - m_start
            
            # Sauvegarde pour le rapport détaillé
            detailed_contents[name] = content
            
            results.append({
                "Outil": name,
                "Vitesse (pp/s)": round(nb_pages / duration, 2),
                "RAM/Mo PDF": round(mem_peak / file_size_mo, 2),
                "Complétude (char/p)": round(len(content) / nb_pages, 0),
                "Temps (s)": round(duration, 2),
                "Peak RAM (Mo)": round(mem_peak, 1)
            })
        except Exception as e:
            print(f"❌ Erreur {name}: {e}")

    df = pd.DataFrame(results)
    return df, detailed_contents, nb_pages, file_size_mo

def generate_markdown_report(df, contents, filename, nb_pages, size):
    report_path = Path("app/back/Results_extractors.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Rapport de Benchmark Extraction PDF\n\n")
        f.write(f"**Fichier testé :** {filename}  \n")
        f.write(f"**Pages :** {nb_pages} | **Taille :** {size:.2f} Mo\n\n")
        
        f.write("## 📊 Tableau Comparatif des KPIs\n\n")
        f.write(df.sort_values("Vitesse (pp/s)", ascending=False).to_markdown(index=False))
        f.write("\n\n---\n\n")
        
        f.write("## 📝 Extraits Longs (Analyse de structure)\n\n")
        f.write("> Ces extraits permettent de vérifier si les colonnes ou les tableaux sont respectés.\n\n")
        
        for name, text in contents.items():
            f.write(f"### 🔹 {name}\n")
            f.write("```markdown\n")
            # Extrait de 2000 caractères pour bien voir la structure
            f.write(text[:2000] + "\n...")
            f.write("\n```\n\n")
            f.write("---\n\n")
            
    print(f"✅ Rapport généré : {report_path}")

if __name__ == "__main__":
    target = Path("app/back/temp/pdfs/1wPeGblsvZ9j5tFY_NI8ki9xqiZvuxGbv.pdf")
    if target.exists():
        df_results, all_contents, pages, size = run_master_benchmark(target)
        generate_markdown_report(df_results, all_contents, target.name, pages, size)