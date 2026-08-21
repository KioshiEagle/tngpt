#!/bin/sh
# Fait tourner le banc d'essai pendant la nuit, puis l'arrête au matin.
#
# Le banc reprend où il s'est arrêté (une ligne JSONL par mesure) : le découper
# en nuits successives ne coûte rien et laisse les journées à la production.
set -eu

RACINE="/Users/tobiasnobile/dev/perso/tngpt"
DUREE="${1:-32400}"          # 9 h : de 22 h à 7 h
cd "$RACINE"

journal() { echo "$(date '+%F %T') $*" >> benchmarks/nuit.log; }

# Verrou atomique : deux instances se disputeraient le même quota Groq.
VERROU="$RACINE/benchmarks/.nuit.lock"
if ! mkdir "$VERROU" 2>/dev/null; then
    journal "déjà en cours, on ne relance pas"
    exit 0
fi
trap 'rmdir "$VERROU" 2>/dev/null || true' EXIT INT TERM

journal "démarrage pour ${DUREE}s"

# macOS n'a pas `timeout` : un chien de garde tue le banc à l'heure dite.
.venv/bin/python -m benchmarks.bench_generation \
    --questions 145 \
    --modeles openai/gpt-oss-120b \
    --sortie bench_generation.jsonl >> benchmarks/campagne.log 2>&1 &
BANC=$!

( sleep "$DUREE"; kill "$BANC" 2>/dev/null || true ) &
CHIEN=$!

wait "$BANC" 2>/dev/null || true
kill "$CHIEN" 2>/dev/null || true

journal "arrêt, $(grep -c '^{' bench_generation.jsonl 2>/dev/null || echo 0) mesures au total"
