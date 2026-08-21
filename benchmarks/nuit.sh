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

# launchd rattrape la tâche au réveil, parfois avant que le Wi-Fi soit
# remonté : la première requête mourait alors sur un DNS injoignable.
i=0
while [ "$i" -lt 60 ]; do
    ping -c1 -t2 api.groq.com >/dev/null 2>&1 && break
    i=$((i + 1))
    sleep 10
done
if [ "$i" -ge 60 ]; then
    journal "pas de réseau après 10 min, on renonce pour cette nuit"
    exit 0
fi
[ "$i" -gt 0 ] && journal "réseau revenu après $((i * 10))s d'attente"

# Borne dure à 7 h : un démarrage rattrapé tard le matin ferait sinon tourner
# le banc en pleine journée, contre la production.
FIN=$(date -j -f "%H:%M:%S" "07:00:00" "+%s")
MAINTENANT=$(date +%s)
[ "$FIN" -le "$MAINTENANT" ] && FIN=$((FIN + 86400))
RESTE=$((FIN - MAINTENANT))
[ "$RESTE" -lt "$DUREE" ] && DUREE="$RESTE"

journal "démarrage pour ${DUREE}s"

# macOS n'a pas `timeout` : un chien de garde tue le banc à l'heure dite.
# `caffeinate` porté par le banc lui-même : la machine reste éveillée tant
# qu'il travaille, et se rendort d'elle-même quand il a fini.
caffeinate -is .venv/bin/python -m benchmarks.bench_generation \
    --questions 145 \
    --modeles openai/gpt-oss-120b \
    --sortie bench_generation.jsonl >> benchmarks/campagne.log 2>&1 &
BANC=$!

( sleep "$DUREE"; kill "$BANC" 2>/dev/null || true ) &
CHIEN=$!

wait "$BANC" 2>/dev/null || true
kill "$CHIEN" 2>/dev/null || true

journal "arrêt, $(grep -c '^{' bench_generation.jsonl 2>/dev/null || echo 0) mesures au total"
