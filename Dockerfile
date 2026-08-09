FROM ghcr.io/astral-sh/uv:0.11.19-python3.13-trixie

WORKDIR /app

# Plus aucune dépendance système : libgl1 et libglib2.0-0 servaient à l'inférence
# locale (commit df03ee6, « Docker image using only CPU for inference »). Tout
# tourne désormais sur API — Groq pour la génération, Workers AI pour les
# embeddings — et PyMuPDF embarque ses propres binaires.

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8501

ENV FLASK_APP=main.py

# Les migrations sont appliquées avant le démarrage : le schéma n'est plus créé
# par l'application elle-même. Sûr ici car un seul worker (-w 1) ; passer à
# plusieurs workers imposerait de sortir `db upgrade` dans une étape dédiée.
CMD ["sh", "-c", "uv run flask db upgrade && uv run gunicorn -w 1 -b 0.0.0.0:8501 main:app"]