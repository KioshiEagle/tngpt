TN-GPT 🦆
TN-GPT est l'assistant intelligent dédié aux étudiants de TELECOM Nancy.
Que ce soit pour réviser des points de cours complexes ou déterrer les anecdotes les plus sombres du lore de l'école (merci le Mini Tel'), TN-GPT retrouve l'info et te répond comme un pote de promo.

## 🧱 Prérequis

- [uv](https://docs.astral.sh/uv/) (gestion des dépendances Python)
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose (base PostgreSQL)
- Un compte [Qdrant Cloud](https://qdrant.tech/) (ou une instance locale) pour la base vectorielle
- Un projet [Google Cloud Console](https://console.cloud.google.com/) pour l'authentification OAuth (et, en option, l'accès à Google Drive pour l'ingestion automatique)
- Une clé API [Groq](https://console.groq.com/) pour la génération des réponses

## 🚀 Installation

1. Cloner le projet

```bash
git clone <url-du-repo>
cd tn-gpt
```

2. Synchroniser les dépendances

```bash
uv sync
```

## ⚙️ Configuration

### 1. Fichier `.env`

Copie `.env.example` en `.env` à la racine du projet, puis remplis les valeurs :

```bash
cp .env.example .env
```

Détail des variables :

| Variable | Description |
|---|---|
| `FLASK_SECRET_KEY` | Clé de signature des sessions/cookies. Génère la tienne : `python -c "import secrets; print(secrets.token_hex(32))"` |
| `FLASK_DEBUG` | `True` en local (active aussi `OAUTHLIB_INSECURE_TRANSPORT` pour l'OAuth en http), `False` en production |
| `DEFAULT_DAILY_QUOTA` | Quota de questions par jour par défaut (les administrateurs n'y sont pas soumis) |
| `DATABASE_URL` | Connexion PostgreSQL, obligatoire — l'app refuse de démarrer sans elle (pas de repli SQLite) |
| `POSTGRES_PASSWORD` | Mot de passe Postgres utilisé par `docker-compose.yml` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Identifiants OAuth Google (voir ci-dessous) |
| `GROQ_API_KEY` | Clé Groq de repli, utilisée si le pool de clés en base (panel admin) est vide |
| `GROQ_CHAT_MODEL` | Modèle utilisé pour générer les réponses du chat (défaut `qwen/qwen3.6-27b`) |
| `GROQ_METADATA_MODEL` | Modèle utilisé pour l'extraction de métadonnées à l'ingestion (défaut `llama-3.1-8b-instant`) |
| `QDRANT_URL` / `QDRANT_API_KEY` | Connexion à la base vectorielle Qdrant |
| `DRIVE_FOLDER_IDS` | IDs des dossiers Google Drive contenant les PDF sources, séparés par des virgules (optionnel, pipeline d'ingestion automatique uniquement) |

### 2. Authentification Google OAuth (obligatoire)

L'accès à l'application est restreint aux comptes `@telecomnancy.net`.

1. Sur la [Google Cloud Console](https://console.cloud.google.com/), crée un projet (ou réutilise-en un).
2. Va dans *APIs & Services > Identifiants* et crée un *ID client OAuth 2.0* de type **Application Web**.
3. Ajoute l'URI de redirection autorisée : `http://localhost:8501/auth/callback` (adapte le domaine en production).
4. Reporte `client_id` et `client_secret` dans `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

### 3. Fichier `service-account.json` (optionnel — ingestion automatique Drive)

Nécessaire uniquement pour l'ingestion automatique de PDF depuis Google Drive (`app/back/ingest.py`). Le panel admin permet aussi un dépôt manuel par glisser-déposer, qui n'en a pas besoin.

1. Sur la même Google Cloud Console, active l'API **Google Drive**.
2. Crée une clé de compte de service (*Identifiants > Créer des identifiants > Compte de service*), format JSON.
3. Renomme le fichier téléchargé en `service-account.json` et place-le dans `app/back/`.
4. Partage le(s) dossier(s) Drive source avec l'adresse e-mail du compte de service, puis renseigne leurs IDs dans `DRIVE_FOLDER_IDS`.

### 4. Base de données PostgreSQL

Lance uniquement la base via Docker Compose (le service `db`) :

```bash
docker compose up -d db
```

Applique ensuite le schéma avec les migrations Alembic :

```bash
uv run flask db upgrade
```

### 5. Base vectorielle Qdrant

Utilise une instance [Qdrant Cloud](https://qdrant.tech/) (renseigne `QDRANT_URL`/`QDRANT_API_KEY`), ou lance une instance locale :

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Dans ce cas, laisse `QDRANT_API_KEY` vide dans `.env`.

### 6. Premier administrateur

Aucun utilisateur n'a de droits admin à la création. Connecte-toi une première fois via Google OAuth (étape suivante) pour créer ton compte, puis élève-le :

```bash
uv run flask make-admin ton.adresse@telecomnancy.net
```

Les administrateurs suivants peuvent être désignés depuis le panel admin (`/admin`). Une clé Groq peut aussi y être ajoutée au pool — sans cela, `GROQ_API_KEY` sert de repli.

## 🛠️ Lancement

```bash
uv run python main.py
```

L'application est disponible sur http://localhost:8501.

## 📥 Ingestion des documents

Deux façons d'alimenter la base de connaissances :

- **Glisser-déposer** dans le panel admin (`/admin/catalog`) — pas de configuration Drive nécessaire.
- **Pipeline automatique** depuis Google Drive (nécessite `service-account.json` et `DRIVE_FOLDER_IDS`) :

```bash
uv run python app/back/ingest.py
```

Les PDF sont convertis en Markdown, découpés en chunks et indexés dans Qdrant ; leurs métadonnées sont extraites via Groq et le catalogue est tenu à jour dans PostgreSQL.

## ✅ Vérifications avant de commit

Le script [run_ci.sh](run_ci.sh) rejoue en local les vérifications de la pipeline CI (lint, sécurité, tests) :

```bash
./run_ci.sh
```

Utilise l'extension Ruff pour VS Code pour appliquer directement les bonnes pratiques du projet (`.vscode/settings.json` ou paramètres utilisateur) :

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

## 🏗️ Architecture

Voir [docs/architecture.md](docs/architecture.md) pour le détail des composants, du trajet d'une question et de la pipeline d'ingestion.

- **Authentification** : OAuth Google restreint aux comptes `@telecomnancy.net`.
- **Retrieval** : recherche sémantique via embeddings Cloudflare Workers AI dans Qdrant.
- **Génération** : Groq (modèle configurable via `GROQ_CHAT_MODEL`), avec un pool de clés API réparties.
- **Contrôle** : PostgreSQL porte les utilisateurs, permissions, quotas, journal d'usage et catalogue de documents.