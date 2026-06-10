class RAG:
    """Modèle principal du système RAG."""

    name: str

    def __init__(self, name: str = "RAG-1") -> None:
        """Initialise le système RAG.

        Args:
            name (str, optional): Nom de l'instance RAG. Par défaut "RAG-1".

        """
        self.name = name
