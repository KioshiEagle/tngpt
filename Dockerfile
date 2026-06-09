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

CMD ["uv", "run", "python", "main.py"]