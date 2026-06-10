import os
import re
import json
from pathlib import Path
import pymupdf4llm
from groq import Groq

BASE_DIR = Path(__file__).parent.resolve()

class DocumentProcessor:
    def __init__(self):
        self.groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

    def _extract_metadata(self, md_content: str, filename: str) -> dict:
        """Extrait titre, date et auteur depuis le début du document via Groq."""
        prompt = f"""Analyse le début de ce document et extrais les métadonnées en JSON strict.
Si une information est introuvable, utilise null.
La date doit être au format ISO 8601 (YYYY-MM-DD). Si tu as seulement mois+année, utilise le premier du mois.
Réponds UNIQUEMENT avec du JSON valide, sans texte autour.

Format attendu :
{{"title": "...", "date": "YYYY-MM-DD", "author": "..."}}

Début du document :
{md_content[:2000]}"""

        try:
            completion = self.groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            raw = completion.choices[0].message.content.strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            print(f"⚠️ Extraction métadonnées échouée pour {filename} : {e}")

        return {"title": filename, "date": None, "author": None}

    def convert_directory(self, source_dir, output_dir):
        """Transforme chaque .pdf valide en .md avec frontmatter de métadonnées."""
        for pdf_path in Path(source_dir).glob("*.pdf"):
            try:
                if pdf_path.stat().st_size == 0:
                    print(f"⚠️ Fichier vide ignoré : {pdf_path.name}")
                    continue

                print(f"⚡ PDF -> MD : {pdf_path.name}")

                md_content = pymupdf4llm.to_markdown(str(pdf_path))

                meta = self._extract_metadata(md_content, pdf_path.stem)
                title = (meta.get("title") or pdf_path.stem).replace("\n", " ")
                date  = meta.get("date") or ""
                author = (meta.get("author") or "Inconnu").replace("\n", " ")
                print(f"   → titre: {title} | date: {date} | auteur: {author}")

                frontmatter = f"---\ntitle: \"{title}\"\ndate: \"{date}\"\nauthor: \"{author}\"\n---\n\n"

                md_path = Path(output_dir) / f"{pdf_path.stem}.md"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(frontmatter + md_content)

            except Exception as e:
                print(f"❌ Impossible de convertir {pdf_path.name} : {str(e)}")
                continue

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR.parent.parent / ".env")

    TEMP_PDF = BASE_DIR / "temp/pdfs"
    TEMP_MD = BASE_DIR / "temp/markdowns"
    TEMP_MD.mkdir(parents=True, exist_ok=True)
    dp = DocumentProcessor()
    dp.convert_directory(TEMP_PDF, TEMP_MD)
    print("✅ PDF -> MD terminé.")
