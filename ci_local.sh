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
# `uv check` (expérimental) réclame un uv récent et télécharge le ty du jour :
# `uvx ty check` fait la même vérification sans dépendre de la version d'uv.
uvx ty check

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
