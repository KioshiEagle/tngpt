import os
from pathlib import Path
import pymupdf4llm

class DocumentProcessor:
    def __init__(self):
        # PyMuPDF4LLM n'a pas besoin d'initialisation de modèle lourd comme Docling
        pass

    def convert_directory(self, source_dir, output_dir):
        """Transforme chaque .pdf valide en .md via PyMuPDF4LLM."""
        for pdf_path in Path(source_dir).glob("*.pdf"):
            try:
                # Vérification rapide : si le fichier fait 0 octet, on passe
                if pdf_path.stat().st_size == 0:
                    print(f"⚠️ Fichier vide ignoré : {pdf_path.name}")
                    continue

                print(f"⚡ PDF -> MD (Fast) : {pdf_path.name}")
                
                # Extraction avec pymupdf4llm (beaucoup plus léger que Docling)
                # Note: Le moteur de layout est utilisé automatiquement si installé
                md_content = pymupdf4llm.to_markdown(str(pdf_path))
                
                md_path = Path(output_dir) / f"{pdf_path.stem}.md"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                    
            except Exception as e:
                # Capture les erreurs spécifiques à PyMuPDF (fichiers protégés ou malformés)
                print(f"❌ Impossible de convertir {pdf_path.name} : {str(e)}")
                continue

if __name__ == "__main__":
    from pathlib import Path
    BASE_DIR = Path(__file__).parent.resolve()
    TEMP_PDF = BASE_DIR / "temp/pdfs"
    TEMP_MD = BASE_DIR / "temp/markdowns"
    TEMP_MD.mkdir(parents=True, exist_ok=True)
    dp = DocumentProcessor()
    dp.convert_directory(TEMP_PDF, TEMP_MD)
    print("✅ PDF -> MD terminé.")