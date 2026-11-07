#!/bin/sh
# Weekly job (launchd fires it daily at 04:00; it works only when the last success is 6+ days old).
#   1. harvest the last 14 days of arXiv CS and embed new papers (every week)
#   2. only when a new month has completed since the published snapshot: full pipeline (run_all.sh),
#      static export with ideas for the top 20 links, size checks, deploy to Vercel
# A failed step deploys nothing: the public page keeps the last good snapshot. Failures raise a macOS notification.
# Env: UC_FORCE=1 ignores the 6-day stamp; UC_NO_DEPLOY=1 stops before `vercel deploy`; UC_ALLOW_BATTERY=1.
cd "$(dirname "$0")/.." || exit 1
mkdir -p data/log
LOG="data/log/weekly-$(date +%F).log"
exec >>"$LOG" 2>&1
PY=.venv/bin/python
STAMP=data/weekly.stamp
say() { echo "== $(date '+%F %T') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"undercurrent weekly\"" 2>/dev/null; }
fail() { say "FAILED: $1"; notify "Failed: $1 (log: $LOG)"; exit 1; }

say "start"
if [ -z "$UC_FORCE" ] && [ -f "$STAMP" ] && [ -n "$(find "$STAMP" -mtime -6 2>/dev/null)" ]; then say "last success under 6 days ago; nothing to do"; exit 0; fi
if [ -z "$UC_ALLOW_BATTERY" ] && ! pmset -g batt | head -1 | grep -q "AC Power"; then say "on battery; retry tomorrow"; exit 0; fi

# one weekly job at a time (mkdir is atomic); trap removes the lock on any exit
mkdir data/weekly.lock 2>/dev/null || { say "another weekly job holds data/weekly.lock; exit"; exit 0; }
trap 'rmdir data/weekly.lock 2>/dev/null' EXIT

# the database is locked while the local server runs; retry tomorrow rather than kill it
if [ -f data/serve.pid ] && ps -p "$(head -1 data/serve.pid | tr -dc 0-9)" -o command= 2>/dev/null | grep -q radar.serve; then
  say "local server is running (data/serve.pid); retry tomorrow"; notify "Skipped: stop the local server so the weekly update can run"; exit 0
fi

say "harvest (last 14 days)"
timeout 7200 $PY -m radar.harvest_api --from "$(date -v-14d +%F)" || fail "harvest"
say "embed new papers"
ok=; for i in 1 2 3; do timeout 10800 $PY -m radar.enrich --months 2 && { ok=1; break; }; say "enrich attempt $i failed; retry in 120 s"; sleep 120; done
[ -n "$ok" ] || fail "enrich"

# last complete month now (same rule as serve.py / links.py) vs the one the published snapshot ends at
now=$($PY - <<'EOF'
import duckdb
c = duckdb.connect("data/radar.duckdb", read_only=True)
s, h = c.execute("select max(submitted), max(harvested_at) from papers").fetchall()[0]
a = lambda d: d.year * 12 + d.month - 1
e = min(a(s), a(h) - 1)
print(f"{e // 12}-{e % 12 + 1:02d}")
EOF
) || fail "last-month check"
pub=$($PY -c "import json;print(json.load(open('site/data/meta.json'))['windows']['6m']['to'])" 2>/dev/null || echo none)
say "last complete month $now; published snapshot ends $pub"
if [ "$now" = "$pub" ]; then say "no new complete month; snapshot unchanged"; touch "$STAMP"; exit 0; fi

say "new month: full pipeline"
timeout 10800 ./run_all.sh || fail "run_all.sh"
say "export"
timeout 3600 $PY -m radar.export --out site --ideas 20 || fail "export"

# sanity: point count and size before anything goes public
$PY - <<'EOF' || fail "snapshot checks"
import json, sys
m = json.load(open("site/data/meta.json")); f = json.load(open("site/data/manifest.json"))
print("points", m["n_points"], "files", f["files"], "bytes", f["bytes"])
sys.exit(0 if m["n_points"] > 400_000 and f["bytes"] < 300_000_000 else 1)
EOF

if [ -n "$UC_NO_DEPLOY" ]; then say "UC_NO_DEPLOY set; not deploying"; touch "$STAMP"; exit 0; fi
say "deploy"
mkdir -p site/.vercel && cp tools/vercel-project.json site/.vercel/project.json || fail "vercel project link (tools/vercel-project.json)"
timeout 1800 vercel deploy site --prod --yes || fail "vercel deploy"
touch "$STAMP"
say "done"; notify "Published the $now snapshot"
