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
# `uv run` et non `uvx` : ty a besoin du venv du projet pour résoudre les
# imports tiers, et sa version vient du lock, pas du dernier ty publié.
uv run ty check

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
# pymupdf/pymupdf4llm sont dual-licenciés (AGPL-3.0 ou Artifex Commercial) ;
# on les utilise sous leur branche AGPL, compatible avec le projet, mais
# licensecheck ne sait pas parser une mention de double licence.
uvx licensecheck --show-only-failing -0 --ignore-packages pymupdf pymupdf4llm

uv run python -m pytest tests/

echo ""
echo "======================================"
echo "✅ Tout est au vert !"
echo "======================================"
