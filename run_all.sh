#!/bin/sh
# Full pipeline after harvest. Each stage logs to data/log/<stage>.log; the script stops on the first failed stage.
# Signals and backtest come first because they need no embeddings.
# Stages in order: signals, backtest-<cutoff> x4, bteval (soft), hn (soft), enrich, map, links,
#   load_extra (merges data/raw/* Parquet into the DB), citations (citation percentiles and per-topic signals; needs
#   paper_topics from map, so it runs after links), merge, report.
# Lock (arch §5.3): data/run.lock holds this shell's pid, removed on exit; refuses while data/serve.pid names a live radar.serve.
# RADAR_DRY=1 replaces every stage with `sleep 1` (lock tests only; touches no database).
cd "$(dirname "$0")"
mkdir -p data/log report
PY=.venv/bin/python
CUTOFFS="2025-01-01 2025-04-01 2025-07-01 2025-10-01"   # backtest cutoffs; keep in step with the default in radar/backtest.py

# check_pid FILE TAG: 0 = names a live process whose command contains TAG; otherwise report and remove the stale file
check_pid() {
  [ -f "$1" ] || return 1
  p=$(head -1 "$1" | tr -dc 0-9)
  cmd=$( [ -n "$p" ] && ps -p "$p" -o command= 2>/dev/null )
  if [ -z "$cmd" ]; then echo "removed stale lock $1 (pid ${p:-?} not running)" >&2
  elif echo "$cmd" | grep -q "$2"; then return 0
  else echo "removed stale lock $1 (pid $p is $(basename "$(ps -p "$p" -o comm=)"), not $2)" >&2; fi
  rm -f "$1"; return 1
}
take_lock() { ( set -C; echo $$ > data/run.lock ) 2>/dev/null; }   # set -C: create only if missing (atomic)
if ! take_lock; then
  if check_pid data/run.lock run_all.sh; then echo "data/run.lock names a running pipeline (pid $p). Wait for it." >&2; exit 1; fi
  take_lock || { echo "cannot create data/run.lock" >&2; exit 1; }
fi
child=
trap 'rm -f data/run.lock; [ -n "$child" ] && kill $child 2>/dev/null' EXIT
trap 'exit 143' TERM; trap 'exit 130' INT     # sh skips the EXIT trap on an untrapped signal; route signals through exit
# own lock first, then the server's: if both start at once both refuse, never both run (arch §5.3)
if check_pid data/serve.pid radar.serve; then
  echo "data/serve.pid names a running server (pid $p, radar.serve). Stop it first." >&2; exit 1
fi

stage() {
  name=$1; shift; echo "== $name start $(date +%H:%M:%S)"
  [ -n "$RADAR_DRY" ] && set -- sleep 1
  # in the background + wait, so a SIGTERM to this shell is handled now, not after the stage ends
  "$@" > "data/log/$name.log" 2>&1 & child=$!
  if wait $child; then child=; tail -50 "data/log/$name.log"; echo "== $name ok $(date +%H:%M:%S)"
  else child=; tail -25 "data/log/$name.log"; echo "== FAILED $name"; return 1; fi
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
stage links    $PY -m radar.links || exit 1
stage load_extra timeout 1800 $PY -m radar.load_extra || exit 1
stage citations  timeout 1800 $PY -m radar.citations || exit 1
stage merge    $PY -m radar.merge --n 8 || exit 1
stage report   $PY -m radar.report || exit 1
echo "== all done $(date +%H:%M:%S)"
