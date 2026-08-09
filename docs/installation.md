# 🚀 Installation de TN-GPT

Ce guide vous explique comment installer et configurer **TN-GPT** sur votre machine locale.

## 1. Prérequis

TN-GPT utilise **[uv](https://github.com/astral-sh/uv)** pour une gestion ultra-rapide des dépendances. Assurez-vous de l'avoir installé sur votre machine.

## 2. Cloner le projet

Commencez par récupérer le code source :

```bash
git clone https://github.com/votre-repo/tn-gpt.git
cd tn-gpt
```

## 3. Synchroniser les dépendances

Installez toutes les bibliothèques requises en une seule commande :

```bash
uv sync
```

## 4. Configuration (Indispensable)

Le projet nécessite deux fichiers de configuration à la racine pour fonctionner. **Ne jamais les commiter sur Git.**

### Fichier `.env`
Créez un fichier `.env` à la racine du projet et remplissez les variables suivantes :

```env
# Clé API pour le modèle LLM (Llama 3 via Groq)
GROQ_API_KEY=gsk_your_api_key_here

# Configuration Qdrant (Base de données vectorielle)
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your_qdrant_key_if_cloud

# Google Drive (ID du dossier contenant les PDF)
DRIVE_FOLDER_ID=your_folder_id_here
```

### Fichier `service-account.json`
Pour accéder aux documents sur Google Drive, vous devez placer un fichier de compte de service Google Cloud dans le dossier `app/back`.

1. Créez un projet sur la Google Cloud Console.
2. Activez l'API Google Drive.
3. Créez une Clé de compte de service au format JSON.
4. Renommez le fichier téléchargé en `service-account.json` et placez-le dans le dossier `app/back`.

## 5. Lancement de l'application

Pour lancer l'application (Backend Flask + Frontend) :

```bash
uv run python main.py
```

L'application sera alors disponible sur [http://localhost:8501](http://localhost:8501).

## 6. Lancement de la documentation

Pour lancer la documentation Zensical en local :

```bash
uv run python docs/gen_ref_pages.py
uv run zensical serve
```

La documentation sera disponible sur [http://localhost:8000](http://localhost:8000).
