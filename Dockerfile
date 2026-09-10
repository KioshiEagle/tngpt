FROM ghcr.io/astral-sh/uv:0.12.12-python3.13-trixie

WORKDIR /app

COPY pyproject.toml uv.lock ./

ENV UV_COMPILE_BYTECODE=1

RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8501

ENV FLASK_APP=main.py

ARG APP_VERSION=dev
ARG APP_REVISION=""
ENV APP_VERSION=$APP_VERSION
ENV APP_REVISION=$APP_REVISION

# Les migrations sont appliquées avant le démarrage : le schéma n'est plus créé
# par l'application elle-même. Sûr ici car un seul worker (-w 1) ; passer à
# plusieurs workers imposerait de sortir `db upgrade` dans une étape dédiée.
CMD ["sh", "-c", "uv run flask db upgrade && uv run gunicorn -w 1 -b 0.0.0.0:8501 main:app"]