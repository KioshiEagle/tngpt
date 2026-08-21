# 🚀 Installation de TN-GPT

Ce guide déroule l'installation complète en local. Pour juste cloner et lancer,
le démarrage rapide du [README](https://github.com/KioshiEagle/tngpt) suffit.

## 1. Prérequis

- [uv](https://docs.astral.sh/uv/) — gestion des dépendances Python
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose — base PostgreSQL
- Un compte [Qdrant Cloud](https://qdrant.tech/) (ou une instance locale) pour la base vectorielle
- Un projet [Google Cloud Console](https://console.cloud.google.com/) pour l'authentification OAuth, et en option l'accès Drive pour l'ingestion automatique
- Une clé API [Groq](https://console.groq.com/) pour la génération des réponses
- Un compte [Cloudflare](https://dash.cloudflare.com/) pour les embeddings et le reclassement Workers AI

## 2. Cloner et synchroniser

```bash
git clone <url-du-repo>
cd tn-gpt
uv sync
```

## 3. Fichier `.env`

Copie le gabarit fourni, puis remplis les valeurs :

```bash
cp env.example .env
```

| Variable | Description |
|---|---|
| `FLASK_SECRET_KEY` | Clé de signature des sessions/cookies. Génère la tienne : `python -c "import secrets; print(secrets.token_hex(32))"` |
| `OAUTH_ALLOW_HTTP` | `true` en local uniquement. Autorise oauthlib à échanger le jeton hors https, sans quoi la connexion sur `http://localhost` est refusée. À laisser vide en production |
| `LOG_LEVEL` | Niveau de journalisation, `INFO` par défaut. `DEBUG` déverse les chunks Qdrant de chaque question dans les logs |
| `DEFAULT_DAILY_QUOTA` | Quota de questions par jour par défaut (les administrateurs n'y sont pas soumis) |
| `DATABASE_URL` | Connexion PostgreSQL, obligatoire — l'app refuse de démarrer sans elle (pas de repli SQLite) |
| `POSTGRES_PASSWORD` | Mot de passe Postgres utilisé par `docker-compose.yml` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Identifiants OAuth Google (voir §4) |
| `GROQ_API_KEY` | Clé Groq de repli, utilisée si le pool de clés en base (panel admin) est vide |
| `GROQ_CHAT_MODEL` | Modèle utilisé pour générer les réponses du chat (défaut `qwen/qwen3.6-27b`) |
| `GROQ_METADATA_MODEL` | Modèle utilisé pour l'extraction de métadonnées à l'ingestion (défaut `llama-3.1-8b-instant`) |
| `QDRANT_URL` / `QDRANT_API_KEY` | Connexion à la base vectorielle Qdrant |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` | Accès Workers AI, pour les embeddings et le reranker |
| `RERANK_ENABLED` / `RERANK_MODEL` / `RERANK_POOL_SIZE` | Reclassement des résultats : activation, modèle, et taille du vivier repassé au reranker |
| `DRIVE_FOLDER_IDS` | IDs des dossiers Google Drive contenant les PDF sources, séparés par des virgules (optionnel, pipeline d'ingestion automatique uniquement) |

!!! danger "Ne jamais commiter `.env` ni `service-account.json`."

## 4. Authentification Google OAuth (obligatoire)

L'accès à l'application est restreint aux comptes `@telecomnancy.net`.

1. Sur la [Google Cloud Console](https://console.cloud.google.com/), crée un projet (ou réutilise-en un).
2. Va dans *APIs & Services > Identifiants* et crée un *ID client OAuth 2.0* de type **Application Web**.
3. Ajoute l'URI de redirection autorisée : `http://localhost:8501/auth/callback` (adapte le domaine en production).
4. Reporte `client_id` et `client_secret` dans `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

## 5. Fichier `service-account.json` (optionnel)

Nécessaire uniquement pour l'ingestion automatique de PDF depuis Google Drive
(`app/back/ingest.py`). Le panel admin permet aussi un dépôt manuel par
glisser-déposer, qui n'en a pas besoin.

1. Sur la même Google Cloud Console, active l'API **Google Drive**.
2. Crée une clé de compte de service (*Identifiants > Créer des identifiants > Compte de service*), format JSON.
3. Renomme le fichier téléchargé en `service-account.json` et place-le dans `app/back/`.
4. Partage le(s) dossier(s) Drive source avec l'adresse e-mail du compte de service, puis renseigne leurs IDs dans `DRIVE_FOLDER_IDS`.

## 6. Base de données PostgreSQL

Lance uniquement la base via Docker Compose (le service `db`), puis applique le
schéma avec les migrations Alembic :

```bash
docker compose up -d db
uv run flask db upgrade
```

## 7. Base vectorielle Qdrant

Utilise une instance [Qdrant Cloud](https://qdrant.tech/) (renseigne
`QDRANT_URL` / `QDRANT_API_KEY`), ou lance-en une en local :

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Dans ce cas, laisse `QDRANT_API_KEY` vide dans `.env`.

## 8. Lancement et premier administrateur

L'application tourne sous gunicorn, en local comme en production — il n'y a
pas de serveur de développement :

```bash
uv run gunicorn -w 1 -b 127.0.0.1:8501 main:app
```

L'application est disponible sur http://localhost:8501.

!!! tip "Itérer sans relancer à la main"
    `--reload` fait redémarrer gunicorn à chaque modification d'un fichier
    Python. Les templates Jinja, eux, sont compilés une fois pour la vie du
    process : une modification de `index.html` demande un redémarrage, ou un
    `--reload-extra-file app/front/templates/index.html`.

!!! bug "macOS : worker tué en boucle au démarrage"
    Sur macOS, un worker peut mourir dès son démarrage avec
    `+[NSCharacterSet initialize] may have been in progress in another thread
    when fork() was called`, suivi d'un `SIGKILL` et d'un redémarrage sans fin.
    Le runtime Objective-C refuse qu'un `+initialize` entamé avant un `fork()`
    se poursuive dans l'enfant, et gunicorn fabrique ses workers par `fork()`.

    ```bash
    OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run gunicorn -w 1 --reload -b 127.0.0.1:8501 main:app
    ```

    Strictement local : l'image Docker tourne sous Linux, qui n'a pas de
    runtime Objective-C, et n'est donc pas concernée.

!!! warning "Un seul worker"
    `-w 1` n'est pas arbitraire : les migrations Alembic sont appliquées au
    démarrage du conteneur, ce qui ne serait pas sûr avec plusieurs workers
    concurrents.

Aucun utilisateur n'a de droits admin à la création. Connecte-toi une première
fois via Google OAuth pour créer ton compte, puis élève-le :

```bash
uv run flask make-admin ton.adresse@telecomnancy.net
```

Les administrateurs suivants se désignent depuis le panel admin (`/admin`). Une
clé Groq peut aussi y être ajoutée au pool — sans cela, `GROQ_API_KEY` sert de
repli.

## 9. Ingestion des documents

Deux façons d'alimenter la base de connaissances :

- **Glisser-déposer** dans le panel admin (`/admin/catalog`) — pas de configuration Drive nécessaire.
- **Pipeline automatique** depuis Google Drive (nécessite `service-account.json` et `DRIVE_FOLDER_IDS`) :

```bash
uv run python app/back/ingest.py
```

Les PDF sont convertis en Markdown, découpés en chunks et indexés dans Qdrant ;
leurs métadonnées sont extraites via Groq et le catalogue est tenu à jour dans
PostgreSQL.

## 10. Vérifications avant de commit

Le script `ci_local.sh` rejoue en local les vérifications de la pipeline CI —
lint, format, complexité, docstrings, secrets, audit de dépendances et tests :

```bash
./ci_local.sh
```

Utilise l'extension Ruff pour VS Code pour appliquer directement les bonnes
pratiques du projet (`.vscode/settings.json` ou paramètres utilisateur) :

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
