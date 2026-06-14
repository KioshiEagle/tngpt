"""Conversion PDF → Markdown avec extraction de métadonnées via LLM local."""

import contextlib
import json
import os
import re
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from urllib.error import URLError
from urllib.request import Request, urlopen

import pymupdf4llm

BASE_DIR = Path(__file__).parent.resolve()

_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")

# Borne de validité des années extraites
_YEAR_MIN = 2000
_YEAR_MAX = 2100

_MONTHS_FR: dict[str, int] = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

# Patterns du plus précis au moins précis.
# Chaque lambda reçoit m.groups() (tuple 0-indexé) et retourne (year, month, day).
_DATE_PATTERNS = [
    # ISO : 2026-06-14  → groups = (year, month, day)
    (
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
        lambda m: (int(m[0]), int(m[1]), int(m[2])),
    ),
    # Numérique FR : 14/06/2026  → groups = (day, month, year)
    (
        r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b",
        lambda m: (int(m[2]), int(m[1]), int(m[0])),
    ),
    # Littéral FR : 14 juin 2026  → groups = (day, month_name, year)
    (
        r"\b(\d{1,2})\s+(" + "|".join(_MONTHS_FR) + r")\s+(\d{4})\b",
        lambda m: (int(m[2]), _MONTHS_FR[m[1].lower()], int(m[0])),
    ),
    # Mois + année : juin 2026  → groups = (month_name, year)
    (
        r"\b(" + "|".join(_MONTHS_FR) + r")\s+(\d{4})\b",
        lambda m: (int(m[1]), _MONTHS_FR[m[0].lower()], 1),
    ),
]


def _regex_date(text: str) -> str | None:
    """Cherche la première date valide dans le texte. Retourne ISO 8601 ou None."""
    for pattern, extractor in _DATE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            with contextlib.suppress(ValueError, KeyError, IndexError):
                year, month, day = extractor(m.groups())
                if _YEAR_MIN <= year <= _YEAR_MAX:
                    return date(year, month, day).isoformat()
    return None

_ENDPOINT = "http://localhost:11434/api/generate"


def generate(prompt: str, model: str, timeout: int = 30) -> str | None:
    """Envoie un prompt au LLM local via Ollama. Retourne None si indisponible."""
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = Request(_ENDPOINT, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()).get("response", "").strip()
    except (URLError, json.JSONDecodeError, TimeoutError):
        return None


_METADATA_PROMPT = (
    "Analyse le début de ce document et extrais les métadonnées en JSON strict.\n"
    "Si une information est introuvable, utilise null.\n"
    "La date doit être en ISO 8601 (YYYY-MM-DD). "
    "Si tu as seulement mois+année, utilise le premier du mois.\n"
    "Réponds UNIQUEMENT avec du JSON valide, sans texte autour.\n\n"
    'Format : {"title": "...", "date": "YYYY-MM-DD", "author": "..."}\n\n'
    "Début du document :\n"
)


class DocumentProcessor:
    """Convertit des PDF en Markdown avec extraction de métadonnées via LLM local."""

    def _extract_metadata(self, md_content: str, filename: str) -> dict:
        """Extrait titre, date et auteur. LLM local en premier, regex en fallback."""
        meta: dict = {"title": filename, "date": None, "author": None}

        response = generate(_METADATA_PROMPT + md_content[:2000], model=_OLLAMA_MODEL)
        if response:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                with contextlib.suppress(json.JSONDecodeError):
                    meta = json.loads(match.group())
        else:
            print(f"⚠️ Ollama indisponible pour {filename}, regex en fallback")

        if not meta.get("date"):
            meta["date"] = _regex_date(md_content)
            if meta["date"]:
                print(f"   → date extraite par regex : {meta['date']}")

        return meta

    def convert_directory(self, source_dir: Path, output_dir: Path) -> None:
        """Transforme chaque PDF valide en Markdown avec frontmatter de métadonnées."""
        for pdf_path in Path(source_dir).glob("*.pdf"):
            try:
                if pdf_path.stat().st_size == 0:
                    print(f"⚠️ Fichier vide ignoré : {pdf_path.name}")
                    continue

                print(f"⚡ PDF -> MD : {pdf_path.name}")
                md_content = pymupdf4llm.to_markdown(str(pdf_path))

                meta = self._extract_metadata(md_content, pdf_path.stem)
                title = (meta.get("title") or pdf_path.stem).replace("\n", " ")
                doc_date = meta.get("date") or ""
                author = (meta.get("author") or "Inconnu").replace("\n", " ")
                print(f"   → titre: {title} | date: {doc_date} | auteur: {author}")

                frontmatter = (
                    f'---\ntitle: "{title}"\ndate: "{doc_date}"\n'
                    f'author: "{author}"\n---\n\n'
                )

                md_path = Path(output_dir) / f"{pdf_path.stem}.md"
                with md_path.open("w", encoding="utf-8") as f:
                    f.write(frontmatter + md_content)

            except (RuntimeError, ValueError, OSError) as e:
                print(f"❌ Impossible de convertir {pdf_path.name} : {e!s}")
                continue


if __name__ == "__main__":

    load_dotenv(BASE_DIR.parent.parent / ".env")

    TEMP_PDF = BASE_DIR / "temp/pdfs"
    TEMP_MD = BASE_DIR / "temp/markdowns"
    TEMP_MD.mkdir(parents=True, exist_ok=True)
    dp = DocumentProcessor()
    dp.convert_directory(TEMP_PDF, TEMP_MD)
    print("✅ PDF -> MD terminé.")
