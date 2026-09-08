FROM ghcr.io/astral-sh/uv:0.11.19-python3.13-trixie

WORKDIR /app

# Plus aucune dépendance d'inférence : libgl1 et libglib2.0-0 servaient au local
# (commit df03ee6, « Docker image using only CPU for inference »). Tout tourne
# désormais sur API — Groq pour la génération, Workers AI pour les embeddings —
# et PyMuPDF embarque ses propres binaires.
#
# Reste `pg_dump`, que le panel admin lance pour servir une sauvegarde de la
# base. Sans lui, le bouton échoue proprement mais ne sauvegarde rien.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8501

ENV FLASK_APP=main.py

# Gravées au build par la CI (`--build-arg`) et affichées dans le pied du panel
# admin : seule l'image sait quelle version elle porte. Sans elles, le panel
# affiche « dev » — rien d'autre n'en dépend.
ARG APP_VERSION=dev
ARG APP_REVISION=""
ENV APP_VERSION=$APP_VERSION
ENV APP_REVISION=$APP_REVISION

# Les migrations sont appliquées avant le démarrage : le schéma n'est plus créé
# par l'application elle-même. Sûr ici car un seul worker (-w 1) ; passer à
# plusieurs workers imposerait de sortir `db upgrade` dans une étape dédiée.
CMD ["sh", "-c", "uv run flask db upgrade && uv run gunicorn -w 1 -b 0.0.0.0:8501 main:app"]