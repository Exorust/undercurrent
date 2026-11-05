#!/bin/sh
# Full pipeline after harvest. Each stage logs to data/log/<stage>.log; the script stops on the first failed stage.
# Signals and backtest come first because they need no embeddings.
cd "$(dirname "$0")"
mkdir -p data/log report
PY=.venv/bin/python
N=${EMBED_N:-60000}   # sample size that gets embeddings for the map (see enrich.py)
stage() {
  name=$1; shift; echo "== $name start $(date +%H:%M:%S)"
  if "$@" > "data/log/$name.log" 2>&1; then tail -50 "data/log/$name.log"; echo "== $name ok $(date +%H:%M:%S)"
  else tail -25 "data/log/$name.log"; echo "== FAILED $name"; return 1; fi
}
enrich_retry() {  # enrich is resumable; retry a bounded number of times on API trouble
  for i in 1 2 3 4 5 6; do $PY -m radar.enrich --limit "$N" && return 0; echo "enrich attempt $i failed, retrying in 90s"; sleep 90; done; return 1
}
stage signals  $PY -m radar.signals --top 40 || exit 1
stage backtest $PY -m radar.signals --top 40 --cutoff 2025-04-01 || exit 1
stage bteval   $PY -m radar.backtest --cutoff 2025-04-01 || echo "(backtest eval failed; continuing)"
stage hn       $PY -m radar.hn --top 40 || echo "(hn check failed; continuing)"
stage enrich   enrich_retry || exit 1
stage map      $PY -m radar.map --llm-labels || exit 1
stage merge    $PY -m radar.merge --n 8 || exit 1
stage report   $PY -m radar.report || exit 1
echo "== all done $(date +%H:%M:%S)"
