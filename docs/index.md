# Documentation de TN-GPT

Bienvenue dans la documentation officielle de **TN-GPT**, l'intelligence artificielle optimisée pour TELECOM Nancy.

## Qu'est-ce que TN-GPT ?

TN-GPT est un assistant intelligent basé sur l'architecture RAG (Retrieval-Augmented Generation). Il est conçu pour analyser, indexer et interagir avec diverses sources de données internes (PDFs, documents textes, bases de données) afin de fournir des réponses précises et contextualisées aux questions des utilisateurs.

### Fonctionnalités principales

* **Ingestion de données** : Traitement robuste de documents complexes via PyMuPDF.
* **Vectorisation** : Intégration transparente avec Qdrant pour le stockage de vecteurs à haute dimension.
* **Génération** : Modèles d'IA performants pour formuler des réponses naturelles.
* **API REST** : Backend Flask léger et extensible pour interagir avec le modèle.

## Navigation

* [**Installation**](installation.md) : Découvrez comment installer et lancer TN-GPT localement.
* [**Architecture**](architecture.md) : Le trajet d'une question, de l'ingestion à la réponse.
* [**Rapports de benchmark**](rapports.md) : Les campagnes Optuna qui ont réglé le retrieval, et leurs limites.
* [**Référence API**](api/) : Explorez le code source et l'architecture interne de notre solution.
