#!/usr/bin/env bash

set -euo pipefail

# Vérifie que tous les outils sont installés
command -v uv >/dev/null || { echo "uv est absent"; exit 1; }
command -v gitleaks >/dev/null || { echo "gitleaks est absent"; exit 1; }

echo "======================================"
echo "🚀 Lancement des pipelines CI locaux"
echo "======================================"

echo ""
echo "[1/3] Vérification du projet"
echo "--------------------------------------"

uv lock --check
uv sync
uv check

echo ""
echo "[2/3] Qualité du code"
echo "--------------------------------------"

uvx ruff check . --statistics
uvx ruff format . --check
uvx complexipy .
uvx pydoclint .
gitleaks git . --verbose
echo ""
echo "[3/3] Dépendances & Tests"
echo "--------------------------------------"

uv audit
uvx licensecheck --show-only-failing -0

uv run python -m pytest tests/

echo ""
echo "======================================"
echo "✅ Tout est au vert !"
echo "======================================"
