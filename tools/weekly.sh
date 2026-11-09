#!/bin/sh
# Weekly job (launchd fires it daily at 04:00; it works only when the last success is 6+ days old).
#   1. harvest the last 14 days of arXiv CS and embed new papers (every week)
#   1b. SOFT (a failure is logged and notified, the run continues on the last good data):
#       enrich --refresh (citation counts + venue for rows older than 6 days, every week),
#       institutions (OpenAlex affiliations for every paper not yet looked up), then load_extra (merge data/raw/* into the DB)
#       institutions and refs exit 3 when ids failed after retries: `soft` notifies, successful chunks are kept
#   2. every week: SOFT refs (only when a new month has completed since the published snapshot) (Semantic Scholar references), then full pipeline
#       (run_all.sh, which runs load_extra again after map, then links and citations), static export with ideas for the top 20 links,
#       size checks, deploy to Vercel
# A failed hard step deploys nothing: the public page keeps the last good snapshot. Failures raise a macOS notification.
# Env: UC_FORCE=1 ignores the 6-day stamp; UC_NO_DEPLOY=1 stops before `vercel deploy`; UC_ALLOW_BATTERY=1;
#      UC_DRY=1 prints the steps it would run and exits 0 (no log, no data/ access).
# OPENALEX_KEY comes from .env in the repo root (launchd has no shell profile).
cd "$(dirname "$0")/.." || exit 1

# dry run: must stay BEFORE mkdir/exec so it never writes $LOG or touches data/
if [ -n "$UC_DRY" ]; then
  since=$(date -v-14d +%F)
  echo "step: harvest: timeout 7200 radar.harvest_api --from $since"
  echo "step: embed new papers: timeout 10800 radar.enrich --months 2 (up to 3 attempts)"
  echo "step: affiliations (soft): timeout 1800 radar.institutions (papers not yet looked up)"
  echo "step: load_extra (soft): timeout 1800 radar.load_extra"
  echo "step: citation refresh (soft): timeout 10800 radar.enrich --refresh --older-days 6"
  echo "step: last-complete-month check vs published snapshot (decides refs only)"
  echo "step (new month only): refs (soft): timeout 28800 radar.refs"
  echo "step: full pipeline: timeout 10800 ./run_all.sh (includes load_extra and citations)"
  echo "step: export: timeout 3600 radar.export --out site --ideas 20"
  echo "step: preview, snapshot checks"
  echo "step: deploy: timeout 1800 vercel deploy site --prod --yes --archive=tgz (skipped when UC_NO_DEPLOY is set)"
  exit 0
fi

mkdir -p data/log
LOG="data/log/weekly-$(date +%F).log"
exec >>"$LOG" 2>&1
PY=.venv/bin/python
STAMP=data/weekly.stamp
say() { echo "== $(date '+%F %T') $*"; }
notify() { osascript -e "display notification \"$1\" with title \"undercurrent weekly\"" 2>/dev/null; }
fail() { say "FAILED: $1"; notify "Failed: $1 (log: $LOG)"; exit 1; }
# soft LABEL CMD...: a failure is logged and notified but does not stop the run
soft() { label=$1; shift; "$@" || { say "$label failed; continuing with last good data"; notify "$label failed; continuing with last good data (log: $LOG)"; }; }

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
say "affiliations (institutions, papers not yet looked up)"
soft "institutions" timeout 1800 $PY -m radar.institutions
say "load_extra"
soft "load_extra" timeout 1800 $PY -m radar.load_extra
say "refresh citation counts"
soft "enrich --refresh" timeout 10800 $PY -m radar.enrich --refresh --older-days 6

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
# the site is rebuilt and deployed every week (new papers, citation counts); the slow references download runs only in a new month

if [ "$now" != "$pub" ]; then say "new month: references"; soft "refs" timeout 28800 $PY -m radar.refs; fi
say "full pipeline"
timeout 10800 ./run_all.sh || fail "run_all.sh"
say "export"
timeout 3600 $PY -m radar.export --out site --ideas 20 || fail "export"

# front-page preview: photograph the exported map (best effort; the page has a placeholder if this fails)
say "preview"
( cd site && python3 -m http.server 8797 >/dev/null 2>&1 & echo $! > ../data/preview-http.pid )
sleep 2
PUPPETEER_CORE="${PUPPETEER_CORE:-$HOME/.claude/skills/gstack/node_modules/puppeteer-core}" \
  timeout 180 node tools/shot.cjs http://localhost:8797/map/ site/preview.png --no-measure --wait 4000 >/dev/null 2>&1 || say "preview failed (placeholder will show)"
kill "$(cat data/preview-http.pid)" 2>/dev/null; rm -f data/preview-http.pid

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
timeout 1800 vercel deploy site --prod --yes --archive=tgz || fail "vercel deploy"
touch "$STAMP"
say "done"; notify "Published the $now snapshot"
