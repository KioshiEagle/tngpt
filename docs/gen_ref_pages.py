"""Generate the code reference pages and navigation."""

from pathlib import Path


def generate_reference() -> None:
    """Generate reference markdown files for Zensical."""
    root = Path(__file__).parent.parent
    src_dirs = [root / "app"]
    docs_dir = root / "docs"
    ref_dir = docs_dir / "api"

    # Create the api directory if it doesn't exist
    ref_dir.mkdir(parents=True, exist_ok=True)

    for src in src_dirs:
        for path in sorted(src.rglob("*.py")):
            if path.name in {"__init__.py", "main.py"}:
                continue

            module_path = path.relative_to(root).with_suffix("")
            doc_path = path.relative_to(root).with_suffix(".md")
            full_doc_path = ref_dir / doc_path

            parts = tuple(module_path.parts)

            if parts[-1] == "__init__":
                parts = parts[:-1]
                doc_path = doc_path.with_name("index.md")
                full_doc_path = full_doc_path.with_name("index.md")
            elif parts[-1] == "__main__":
                continue

            # Ensure the target directory exists
            full_doc_path.parent.mkdir(parents=True, exist_ok=True)

            ident = ".".join(parts)

            # Write the markdown file
            with full_doc_path.open("w", encoding="utf-8") as fd:
                fd.write(f"---\ntitle: {ident}\n---\n\n# {ident}\n\n::: {ident}\n")

    # Write the index.md file for the API root
    summary_path = ref_dir / "index.md"
    with summary_path.open("w", encoding="utf-8") as nav_file:
        nav_file.write(
            "# Référence de l'API\n\n"
            "Bienvenue dans la documentation du code source de TN-GPT.\n\n"
            "Utilisez le menu de navigation pour explorer les différents modules "
            "(comme `app.routes` ou `app.back.chunking`), où vous trouverez "
            "le détail de chaque fonction et ses explications.\n"
        )

    print(f"✅ Generated API reference in {ref_dir}")


if __name__ == "__main__":
    generate_reference()
