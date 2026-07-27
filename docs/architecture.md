# Architecture de TN-GPT

Vue d'ensemble technique du projet : les composants, le trajet d'une question,
et la chaîne d'ingestion des documents.

Le principe qui structure tout : **PostgreSQL est le plan de contrôle**
(utilisateurs, journal d'usage, quotas, catalogue), **Qdrant est le plan de
données** (les chunks et leurs vecteurs). Qdrant n'enregistre aucun usage : tout
le monitoring vient du journal en base.

> **Architecture cible.** Ce document intègre deux évolutions en cours : le
> retrait de l'accès API entrant (TN-GPT n'est pas exposé en tant qu'API) et
> l'ajout d'un **pool de clés Groq** réparti pour éviter qu'une seule clé sature.

## Composants et flux

```mermaid
flowchart TB
    subgraph client["Client"]
        Browser["Navigateur<br/>chat web + panel admin"]
    end

    subgraph flask["Application Flask — main.py"]
        AuthBP["auth_bp /auth<br/>connexion OAuth"]
        ChatBP["bp /chat<br/>chat web (streaming)"]
        AdminBP["admin_bp /admin<br/>panel admin"]
        Ext["Extensions<br/>login · limiter · CSRF · migrate"]
    end

    subgraph logic["Logique métier — app/back"]
        Perms["permissions.py<br/>bitmask de droits"]
        Retrieval["retrieval.py<br/>recherche hybride"]
        Generate["generate.py<br/>prompt + réponse"]
        Usage["usage.py<br/>journal + quotas"]
        Catalog["catalog.py<br/>catalogue + ingestion"]
        GroqPool["pool de clés Groq<br/>choix + suivi d'usage"]
    end

    subgraph pipeline["Pipeline d'ingestion"]
        Drive["drivetolocal.py"]
        Pdf["pdftomd.py<br/>PDF → Markdown"]
        Vector["mdtoqdrant.py<br/>chunking + embeddings Gemini"]
    end

    subgraph stores["Stockage"]
        PG[("PostgreSQL — plan de contrôle<br/>users · conversations · queries<br/>retrieval_events · documents · groq_keys")]
        QD[("Qdrant cloud — plan de données<br/>chunks + vecteurs")]
    end

    subgraph external["Services externes"]
        Groq["Groq · LLM<br/>qwen3 / llama"]
        OAuth["Google OAuth"]
        GDrive["Google Drive<br/>PDF sources"]
        Gemini["Google Gemini<br/>gemini-embedding-001"]
    end

    Browser --> AuthBP & ChatBP & AdminBP
    AuthBP --> OAuth
    AuthBP --> PG

    ChatBP --> Retrieval --> Generate
    Retrieval --> QD
    Retrieval --> Gemini
    ChatBP --> Usage --> PG
    Generate --> GroqPool --> Groq
    GroqPool --> PG

    AdminBP --> Perms & Catalog & Usage
    AdminBP --> PG
    Catalog --> QD
    Catalog --> Vector

    Drive --> GDrive
    Drive --> Pdf --> Vector
    Pdf --> GroqPool
    Vector --> Gemini
    Vector --> QD

    Ext -.-> PG
```

Le pool de clés Groq est traversé aux **deux** endroits où le projet appelle
Groq : la génération des réponses ([generate.py](../app/back/generate.py)) et
l'extraction de métadonnées pendant l'ingestion
([pdftomd.py](../app/back/pdftomd.py)).

## Trajet d'une question (chat web)

```mermaid
sequenceDiagram
    actor U as Utilisateur
    participant F as Flask /chat
    participant R as retrieval.py
    participant Q as Qdrant
    participant P as PostgreSQL
    participant G as pool Groq
    participant L as Groq (LLM)

    U->>F: message
    F->>R: retrieve(question)
    R->>Q: query_points (vecteur Gemini)
    Q-->>R: chunks pertinents
    F->>P: log_retrieval (queries + retrieval_events)
    F->>G: demande de génération
    G->>P: clé la moins chargée + incrément d'usage
    G->>L: chat completion (streaming)
    L-->>F: réponse en flux
    F-->>U: réponse affichée au fil de l'eau
```

La recherche et la journalisation ont lieu **avant** le streaming : l'usage est
enregistré même si le client se déconnecte pendant la réponse.

## Pipeline d'ingestion

```mermaid
flowchart LR
    G["Dépôt admin<br/>(glisser-déposer)"] --> C
    A["Google Drive<br/>PDF sources"] --> B["drivetolocal.py<br/>téléchargement"]
    B --> C["pdftomd.py<br/>PDF → Markdown<br/>métadonnées via Groq"]
    C --> D["mdtoqdrant.py<br/>chunking + embeddings Gemini"]
    D --> E[("Qdrant<br/>chunks + vecteurs")]
    D --> F[("PostgreSQL<br/>catalogue documents")]
```

Deux entrées alimentent la même chaîne : la pipeline automatique depuis Google
Drive, et le dépôt manuel par glisser-déposer dans le panel admin.

## Schéma de la base (plan de contrôle)

```mermaid
erDiagram
    users ||--o{ conversations : possède
    users ||--o{ queries : pose
    queries ||--o{ retrieval_events : produit
    users {
        int user_id PK
        string user_mail
        int user_permissions
        string status
        int quota_daily
    }
    conversations {
        int conversation_id PK
        int user_id FK
        json messages
    }
    queries {
        int query_id PK
        int user_id FK
        string question
        int result_count
        datetime created_at
    }
    retrieval_events {
        int event_id PK
        int query_id FK
        string point_id
        string source_id
        float score
    }
    documents {
        string source_id PK
        string title
        int chunk_count
        string status
    }
```

La table `groq_keys` (pool de clés et compteurs d'usage) rejoindra ce schéma
avec la fonctionnalité de répartition Groq.
