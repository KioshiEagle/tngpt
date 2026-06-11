import argparse
import sys
from pathlib import Path

import pymupdf4llm


def main():
    """
    main
    PDF → Markdown structuré par extraction de texte, pour ingestion RAG.

    Usage :
        uv run python parse_plaquette_alpha.py mon_document.pdf
        uv run python parse_plaquette_alpha.py mon_document.pdf -o sortie.md
    """
    parser = argparse.ArgumentParser(description="PDF → Markdown structuré par extraction de texte")
    parser.add_argument("pdf", type=Path, help="Fichier PDF source")
    parser.add_argument("-o", "--output", type=Path, help="Fichier .md de sortie")
    args = parser.parse_args()

    if not args.pdf.exists():
        sys.exit(f"Fichier introuvable : {args.pdf}")

    default_out = Path(__file__).parent / "temp/markdowns" / args.pdf.with_suffix(".md").name
    default_out.parent.mkdir(parents=True, exist_ok=True)
    output = args.output or default_out

    print(f"[1/2] Extraction du texte de {args.pdf.name}…")
    md_text = pymupdf4llm.to_markdown(str(args.pdf), page_chunks=True)

    print(f"[2/2] Écriture → {output}")
    parts = [f"# Document : {args.pdf.name}\n"]
    for chunk in md_text:
        page_num = chunk["metadata"]["page_number"]
        content = chunk["text"].strip()
        if content:
            parts.append(f"\n<!-- page: {page_num} -->\n\n{content}\n\n---\n")

    output.write_text("\n".join(parts), encoding="utf-8")
    print(f"Terminé. {len(md_text)} page(s) extraite(s).")


if __name__ == "__main__":
    main()
