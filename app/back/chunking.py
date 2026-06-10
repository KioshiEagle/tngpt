from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)


def get_hybrid_chunks(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 240,
) -> list[str]:
    """Méthode Championne : Découpe le Markdown par Header (H1/H2).

    Puis affine par RecursiveCharacterSplitter avec injection de contexte.
    """
    headers_to_split_on = [
        ("#", "Header_1"),
        ("##", "Header_2"),
    ]

    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,  # On garde les headers dans le texte pour plus de clarté
    )

    sections = md_splitter.split_text(text)

    # Configuration du découpage fin (Granularité RAG)
    # 840 / 240 -> 30% d'overlap
    rec_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ".", "!", "?", " ", ""],
        add_start_index=True,
    )

    final_chunks = []

    for section in sections:
        # Extraction sécurisée des métadonnées (ex: Nom du Prof ou Nom de la Rubrique)
        # On crée une étiquette de contexte comme [Édito] ou [Citations > Sabeur Aridhi]
        metadata_values = [str(v) for v in section.metadata.values()]
        header_context = " > ".join(metadata_values) if metadata_values else "Général"
        prefix = f"[{header_context}] "

        # On découpe le contenu de la section en petits morceaux
        sub_chunks = rec_splitter.split_text(section.page_content)

        for content in sub_chunks:
            # On vérifie que le chunk n'est pas un micro-fragment inutile
            cleaned_content = content.strip()
            min_chunk_size = 60

            if len(cleaned_content) > min_chunk_size:
                # CRUCIAL : On injecte le préfixe au début de chaque chunk
                final_chunks.append(prefix + cleaned_content)

    return final_chunks


# Alias pour garder la compatibilité avec tes anciens scripts si nécessaire
def recursive_chunking(
    text: str,
    max_chunk_size: int = 800,
    chunk_overlap: int = 200,
) -> list[str]:
    """Alias pour garder la compatibilité avec tes anciens scripts si nécessaire."""
    return get_hybrid_chunks(text, max_chunk_size, chunk_overlap)
