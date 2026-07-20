FROM ghcr.io/astral-sh/uv:0.11.19-python3.13-trixie

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8501

ENV FLASK_APP=main.py

# Les migrations sont appliquées avant le démarrage : le schéma n'est plus créé
# par l'application elle-même. Sûr ici car un seul worker (-w 1) ; passer à
# plusieurs workers imposerait de sortir `db upgrade` dans une étape dédiée.
CMD ["sh", "-c", "uv run flask db upgrade && uv run gunicorn -w 1 -b 0.0.0.0:8501 main:app"]