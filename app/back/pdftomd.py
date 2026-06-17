import contextlib
import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import pymupdf4llm
from dotenv import load_dotenv
from groq import APIConnectionError, APIStatusError, APITimeoutError, Groq

BASE_DIR = Path(__file__).parent.resolve()

_GROQ_MODEL = os.getenv("GROQ_METADATA_MODEL", "llama-3.1-8b-instant")

# Borne de validité des années extraites
_YEAR_MIN = 1990
_YEAR_MAX = 2027
_YEAR_2DIGIT_CUTOFF = 27

_MONTHS_FR: dict[str, int] = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

# Patterns du plus précis au moins précis.
# Chaque lambda reçoit m.groups() (tuple 0-indexé) et retourne (year, month, day).
_DATE_PATTERNS = [
    # ISO : 2026-06-14  → groups = (year, month, day)
    (
        r"\b(\d{4})-(\d{2})-(\d{2})\b",
        lambda m: (int(m[0]), int(m[1]), int(m[2])),
    ),
    # Numérique FR avec ou sans espaces : 14/06/2026 ou 14 / 06 / 2026
    (
        r"\b(\d{1,2})\s*[/\-]\s*(\d{1,2})\s*[/\-]\s*(\d{4})\b",
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
    # Numérique FR année 2 chiffres : 17/12/18 → DD/MM/YY (≤27 → 20xx, sinon 19xx)
    (
        r"\b(\d{1,2})\s*[/\-]\s*(\d{1,2})\s*[/\-]\s*(\d{2})\b",
        lambda m: (
            2000 + int(m[2]) if int(m[2]) <= _YEAR_2DIGIT_CUTOFF else 1900 + int(m[2]),
            int(m[1]),
            int(m[0]),
        ),
    ),
]


def _collapse_spaced_digits(text: str) -> str:
    """Collapse character-spaced headers: '0 7 / 0 4 / 2 0 2 6' → '07 / 04 / 2026'."""
    return re.sub(r"(\d)(?: \d)+", lambda m: m.group().replace(" ", ""), text)


def _regex_date(text: str) -> str | None:
    """Cherche la première date valide dans le texte. Retourne ISO 8601 ou None."""
    for t in (text, _collapse_spaced_digits(text)):
        for pattern, extractor in _DATE_PATTERNS:
            for m in re.finditer(pattern, t, re.IGNORECASE):
                with contextlib.suppress(ValueError, KeyError, IndexError):
                    year, month, day = extractor(m.groups())
                    if _YEAR_MIN <= year <= _YEAR_MAX:
                        return date(year, month, day).isoformat()
    return None


_METADATA_SYSTEM = (
    "Tu es un assistant qui extrait des métadonnées de documents. "
    "Réponds UNIQUEMENT avec du JSON valide, sans texte autour, sans balises markdown."
)

_METADATA_USER = (
    "Analyse le début de ce document et extrais les métadonnées en JSON strict.\n"
    "Si une information est introuvable, utilise null.\n"
    "La date doit être en ISO 8601 (YYYY-MM-DD). "
    "Si tu as seulement mois+année, utilise le premier du mois.\n"
    "Pour l'auteur : cherche un nom de personne, "
    "l'auteur est généralement le secrétaire de séance "
    "ou le ou les rédacteurs.\n"
    "Si tu ne trouves pas d'auteur, indique null\n"
    "Si tu trouves plusieurs noms, joins-les avec ' & '.\n"
    'Format : {"title": "...", "date": "YYYY-MM-DD", "author": "..."}\n\n'
    "Début du document :\n"
)


def _groq_extract(content: str) -> str | None:
    """Appelle Groq pour extraire les métadonnées. Retourne None si indisponible."""
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    try:
        resp = client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=[
                {"role": "system", "content": _METADATA_SYSTEM},
                {"role": "user", "content": _METADATA_USER + content},
            ],
            temperature=0,
            max_tokens=200,
        )
        return resp.choices[0].message.content or None
    except (APITimeoutError, APIConnectionError, APIStatusError):
        return None


class DocumentProcessor:
    """Convertit des PDF en Markdown avec extraction de métadonnées via Groq."""

    def _extract_metadata(self, md_content: str, filename: str) -> dict:
        """Extrait titre, date et auteur via Groq, regex en fallback pour la date."""
        meta: dict = {"title": filename, "date": None, "author": None}

        response = _groq_extract(md_content[:2000])
        if response:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                with contextlib.suppress(json.JSONDecodeError):
                    meta = json.loads(match.group())
        else:
            print(f"⚠️ Groq indisponible pour {filename}, regex en fallback")

        if not meta.get("date"):
            # Filename d'abord (ex: RO_10_2026-04-07, 2022_04_25), content ensuite
            clean_stem = filename.replace("_", "-")
            meta["date"] = _regex_date(clean_stem) or _regex_date(md_content)
            if meta["date"]:
                print(f"   → date extraite par regex : {meta['date']}")

        return meta

    def convert_directory(
        self, source_dir: Path, output_dir: Path, log_file: Path
    ) -> None:
        """Transforme chaque PDF valide en Markdown avec frontmatter de métadonnées."""
        raw = json.loads(log_file.read_text()) if log_file.exists() else {}
        processed = dict.fromkeys(raw) if isinstance(raw, list) else raw

        for pdf_path in Path(source_dir).glob("*.pdf"):
            try:
                if pdf_path.stat().st_size == 0:
                    print(f"⚠️ Fichier vide ignoré : {pdf_path.name}")
                    continue

                current_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
                md_path = Path(output_dir) / f"{pdf_path.stem}.md"
                if processed.get(pdf_path.stem) == current_hash and md_path.exists():
                    print(f"⏭️  Déjà converti : {pdf_path.name}")
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

                with md_path.open("w", encoding="utf-8") as f:
                    f.write(frontmatter + md_content)

                processed[pdf_path.stem] = current_hash

            except (RuntimeError, ValueError, OSError) as e:
                print(f"❌ Impossible de convertir {pdf_path.name} : {e!s}")
                continue

        with log_file.open("w") as f:
            json.dump(processed, f)


if __name__ == "__main__":
    load_dotenv(BASE_DIR.parent.parent / ".env")

    TEMP_PDF = BASE_DIR / "temp/pdfs"
    TEMP_MD = BASE_DIR / "temp/markdowns"
    TEMP_MD.mkdir(parents=True, exist_ok=True)
    logfile = BASE_DIR / "processed_pdfs.json"
    dp = DocumentProcessor()
    dp.convert_directory(TEMP_PDF, TEMP_MD, logfile)
    print("✅ PDF -> MD terminé.")
