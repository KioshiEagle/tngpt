TN-GPT 🦆
TN-GPT est l'assistant intelligent dédié aux étudiants de TELECOM Nancy.
Que ce soit pour réviser des points de cours complexes ou déterrer les anecdotes les plus sombres du lore de l'école (merci le Mini Tel'), TN-GPT retrouve l'info et te répond comme un pote de promo.

🚀 Installation
Le projet utilise uv pour une gestion ultra-rapide des dépendances.

Cloner le projet

Bash
git clone https://github.com/votre-repo/tn-gpt.git
cd tn-gpt
Synchroniser les dépendances

Bash
uv sync
⚙️ Configuration (Indispensable)
Le projet nécessite deux fichiers de configuration à la racine pour fonctionner. Ne jamais les commit sur Git.

Utiliser l'extension ruff pour vscode pour appliquer directemnent les bonnes pratiques et configurations settings.json :
```json
"editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.fixAll.ruff": "always",
        "source.organizeImports.ruff": "always"
    },

    "files.trimTrailingWhitespace": true,
    "files.insertFinalNewline": true,
    "files.trimFinalNewlines": true,

    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true
    },

    "ruff.nativeServer": "on",
    "ruff.organizeImports": true,
    "ruff.fixAll": true
```
1. Fichier .env
Crée un fichier .env à la racine du projet et remplis les variables suivantes :

Code snippet
# Clé API pour le modèle LLM (Llama 3 via Groq)
GROQ_API_KEY=gsk_your_api_key_here

# Configuration Qdrant (Base de données vectorielle)
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your_qdrant_key_if_cloud

# Google Drive (ID du dossier contenant les PDF)
DRIVE_FOLDER_ID=your_folder_id_here
2. Fichier service-account.json
Pour accéder aux documents sur Google Drive, tu dois placer un fichier de compte de service Google Cloud dans le dossier back.

Crée un projet sur la Google Cloud Console.

Active l'API Google Drive.

Crée une Clé de compte de service au format JSON.

Renomme le fichier téléchargé en service-account.json.

🛠️ Lancement
Pour lancer l'application (Backend Flask + Frontend) :

Bash
uv run python main.py
L'application sera disponible sur http://localhost:8501.

🏗️ Architecture
Ingestion : Les PDF sont récupérés sur Drive, découpés en chunks (chunking adaptatif) et indexés dans Qdrant.

Bash
uv run python app/back/ingest.py

Retrieval : Recherche sémantique via fastembed.

Génération : Traitement par Llama 3.3 70B sur Groq pour une réponse fluide et taquine.
