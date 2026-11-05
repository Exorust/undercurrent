#!/bin/sh
# Full pipeline after harvest. Each stage logs to data/log/<stage>.log; the script stops on the first failed stage.
# Signals and backtest come first because they need no embeddings.
cd "$(dirname "$0")"
mkdir -p data/log report
PY=.venv/bin/python
CUTOFFS="2025-01-01 2025-04-01 2025-07-01 2025-10-01"   # backtest cutoffs; keep in step with the default in radar/backtest.py
stage() {
  name=$1; shift; echo "== $name start $(date +%H:%M:%S)"
  if "$@" > "data/log/$name.log" 2>&1; then tail -50 "data/log/$name.log"; echo "== $name ok $(date +%H:%M:%S)"
  else tail -25 "data/log/$name.log"; echo "== FAILED $name"; return 1; fi
}
enrich_retry() {  # enrich is resumable; retry a bounded number of times on API trouble
  for i in 1 2 3 4 5 6; do $PY -m radar.enrich --months 40 && return 0; echo "enrich attempt $i failed, retrying in 90s"; sleep 90; done; return 1
}
stage signals  $PY -m radar.signals --top 40 || exit 1
for c in $CUTOFFS; do stage "backtest-$c" $PY -m radar.signals --top 0 --cutoff "$c" || exit 1; done
stage bteval   $PY -m radar.backtest || echo "(backtest eval failed; continuing)"
stage hn       $PY -m radar.hn --top 40 || echo "(hn check failed; continuing)"
stage enrich   enrich_retry || exit 1
stage map      $PY -m radar.map --llm-labels || exit 1
stage merge    $PY -m radar.merge --n 8 || exit 1
stage report   $PY -m radar.report || exit 1
echo "== all done $(date +%H:%M:%S)"
