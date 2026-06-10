from pathlib import Path

import pymupdf4llm


class DocumentProcessor:
    def __init__(self) -> None:
        # PyMuPDF4LLM n'a pas besoin d'initialisation de modèle lourd comme Docling
        pass

    def convert_directory(self, source_dir: Path, output_dir: Path) -> None:
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
                md_path.write_text(md_content, encoding="utf-8")

            except Exception as e:
                # Capture les erreurs spécifiques à PyMuPDF
                print(f"❌ Impossible de convertir {pdf_path.name} : {e!s}")
                continue
