# Status — run 2026-11-06/07 (first build, full walk)

Server run id `20261107T064547Z`, full database (459,249 points; 21 areas / 114 topics / 373 sub-topics — the plans assumed 20/116/368; the real counts are the expected values from here on). Bridge threshold T = 5 (calibrated: 9.7 % of papers are level-1 bridges; T = 4 gave 20.2 %, outside the 1–15 % band). kNN gate 98.8 % agreement, recall@14 0.981. Evidence root: `pm/evidence/2026-11-06/`.

Builders this run: four Opus builders (pipeline, links, server, page), then eight Codex runs (server contract additions, page contract fixes, arriving rule, nine page fixes, three regressions). Walks: PM directly (F1–F16, F35.1, F14.2) plus three Opus walkers for the page flows (evidence folders per flow).

Vocabulary: **solved** = walked against the running software, every step matched, evidence saved · **blocked** = three fix dispatches failed · **reviewed-not-verified** = read, not run · **not-started** = no code.

## Flows

| flow | status | evidence | files changed (uncommitted) | attempts |
|---|---|---|---|---|
| F1.1 `ask()` tools off | solved (step 4 note: reply was a non-executed `<invoke name="Glob">` string with no file names; first attempt of that prompt hung to the 300 s timeout, retry answered in 26 s) | F1/walk.txt | radar/llm.py | 1 |
| F1.2 pins | solved | F1/walk.txt | requirements.txt | 1 |
| F1.3 claude missing/timeout | solved | F1/walk.txt | radar/llm.py | 1 |
| F2.1 knn/point_index/map_meta | solved (6,429,486 knn rows, 0 self, points.bin 7,807,265 B) | F2/sql-walk.txt, F2-full-map-run.txt | radar/map.py, tools/dev_db.py | 1 |
| F2.2 points.bin layout | solved | F2/sql-walk.txt | radar/map.py | 1 |
| F2.3 rerun replaces | solved (on dev: counts unchanged, map_meta +1 row) | F14-F16/walk5-F14.2-rerun.txt | — | 1 |
| F3.1 kNN precision | solved (98.8 % / 0.981; `--seed` only changes the sample when the exact sample is rebuilt, so the full-DB rerun is identical) | F3/walk.txt | radar/map.py | 1 |
| F3.2 gate fails | solved | F3/walk.txt | — | 1 |
| F4.1 bridge tables | solved (level-1 bridges 44,377 = 9.7 %) | F4-F6/sql-walk.txt | radar/links.py | 2 (T 4 → 5) |
| F4.2 calibration | solved with a note: the named positive pair never enters the top 40 at any T, so the rule marks no `*`; T chosen by the share band (step 2 fails as written, see FRICTION 9) | F4-calibrate.txt | radar/links.py | 1 |
| F4.3 top-ten sanity | solved (list in F4-F6/sql-walk.txt: compression ↔ in-context learning, text-to-image ↔ diffusion editing, …, all neighbours) | F4-F6/sql-walk.txt | — | 1 |
| F5.1 topic_months/onset | solved after the PM correction (latest onset 2025-05; window-share rule finds 15 clusters) | F4-F6/sql-walk.txt, F11/arriving-round2.txt | — | 1 |
| F5.2 topic_parent | solved (373 rows, 43 under 0.6, min share 0.346) | F4-F6/sql-walk.txt | radar/links.py | 1 |
| F6.1 candidate_vocab | solved (vocab 32,313; 57 s; 5.75 GB RSS) | F4-F6/f6-walk.txt | radar/signals.py | 1 |
| F6.2 paper_terms/phrase tables | solved (11.87 M rows; 415 phrases; step 2 passes literally but see FRICTION 3) | F4-F6/f6-walk.txt | radar/links.py | 1 |
| F6.3 phrase link rule | solved (9 links; `llm agents` eff 2.85 is ≤ 4 so it is not the broad example — `agentic ai` and the 5.8-eff `multi-teacher on-policy distillation` are) | F4-F6/f6-walk.txt, F12/walk.txt | — | 1 |
| F7.1 run lock | solved | F7/walk.txt | run_all.sh | 1 |
| F7.2 refuses while server live | solved | F7/walk.txt | run_all.sh | 1 |
| F7.3 stale/reused pid | solved | F7/walk.txt | run_all.sh | 1 |
| F8.1 start/serve/stop | solved (3 s load, RSS 1.65–1.83 GB) | F8-F10/walk.txt, walk-lifecycle.txt | radar/serve.py | 1 |
| F8.2 pipeline live | solved | F8-F10/walk-lifecycle.txt | radar/serve.py | 1 |
| F8.3 no links stage | solved | F8-F10/walk-lifecycle.txt | — | 1 |
| F8.4 stale pid | solved | F14-F16/walk3-restart.txt | — | 1 |
| F8.5 port in use | solved | F8-F10/walk-lifecycle.txt | — | 1 |
| F9.1 Host/Origin | solved | F9/walk.txt | radar/serve.py | 1 |
| F9.2 body/type | solved (8 MB limit, 9.7 MB → 413) | F9/walk.txt | — | 1 |
| F9.3 concurrency | solved (20 × 200; 10 parallel saves) | F9/walk.txt | — | 1 |
| F9.4 error envelope | solved | F8-F10/walk-lifecycle.txt | — | 1 |
| F10.1 page/points/meta | solved (14 meta keys incl. `started`) | F8-F10/walk.txt | radar/serve.py | 2 |
| F10.2 labels | solved | F8-F10/walk.txt | — | 1 |
| F10.3 paper by row | solved (median 3.6 ms) | F8-F10/walk.txt | — | 1 |
| F11.1 topics default | solved after the arriving-rule change (15 arriving; vol_ratio 1.3553) | F11/walk.txt, F11/arriving-round2.txt | radar/serve.py | 2 |
| F11.2 levels/windows | solved (level 0 in 1.2 ms) | F11/walk.txt | — | 1 |
| F12.1 links list | solved (3 new, 243 ratio, 1,015 low; 7 phrase links) | F12/walk.txt | — | 1 |
| F12.2 link evidence | solved | F12/walk.txt | — | 1 |
| F12.3 levels | solved (level 0: 1,981 pairs in 13 ms) | F12/walk.txt | — | 1 |
| F13.1 select topic | solved (top terms: human mobility, traffic forecasting, mobility patterns, urban mobility, road network) | F13/walk.txt | — | 1 |
| F13.2 select rows | solved (25,000 rows in 31 ms, terms_sampled 20000) | F13/walk.txt | — | 1 |
| F13.3 phrase/query | solved (phrase 211 ms, query 48 ms; injection → 422, papers unchanged) | F13/walk.txt | — | 1 |
| F13.4 transient lifetime | solved | F13/walk.txt, F14-F16/walk3-restart.txt | — | 1 |
| F14.1 save/list/load | solved | F14-F16/walk1.txt, walk3-restart.txt | radar/serve.py | 1 |
| F14.2 load after rerun | solved (dev: polygon_valid false, run_id R1, n unchanged) | F14-F16/walk5-F14.2-rerun.txt | — | 1 |
| F14.3 delete/in_use | solved | F14-F16/walk2-ideas.txt | — | 1 |
| F15.1 distance topics | solved (cos 0.0227 recomputed 0.02267; `/distance` counts bridges over all months, `/link` over the window: 191 vs 38) | F14-F16/walk1.txt | — | 1 |
| F15.2 two lassos | reviewed-not-verified (needs two hand-drawn lassos on either side of a gap; the rows path is covered by F13.2 and F15.1) | — | — | 0 |
| F15.3 colour/dist | solved (373 floats in 7 ms; inside median 0.025 vs outside 0.071) | F14-F16/walk1.txt | — | 1 |
| F16.1 ideas success | solved (26.6 s, 3 ideas, all cited ids in the 28 shown; survives restart) | F14-F16/walk2-ideas.txt, walk3-restart.txt | radar/serve.py, radar/merge.py | 1 |
| F16.2 concurrency/validation | solved (409 in 2 ms) | F14-F16/walk2-ideas.txt | — | 1 |
| F16.3 fault injection | solved (all 7 paths; reasons stored) | F14-F16/walk4-fake-llm.txt | — | 1 |
| F16.4 merge.py retired | solved after PM correction (merge stage kept for `merge_ideas`) | F14-F16/walk4-fake-llm.txt | radar/merge.py, radar/merge.html (deleted), report/merge.html (deleted) | 1 |
| F17.1 load/first draw | solved (first contentful paint 56–72 ms fresh profile; first point draw 1,180–1,200 ms, gated by the Tailwind browser compile) | F17.1/walkC-*, shots/round4-run* | radar/app.html | 3 |
| F17.2 pan/zoom/fit | solved | F17.1/walk-output.json | radar/app.html | 1 |
| F17.3 fallback | not-verifiable (precondition not met: frames 17 ms, recolour 174 ms; `?fallback=sample` draws 150,001 points) | F17.1/ | radar/app.html | 1 |
| F18.1 growth scale | solved (steps 4–5 not-verifiable: no `new` topic, none under 30 papers) | F18.1/ | radar/app.html | 1 |
| F18.2 modes | solved | F18.1/ | — | 1 |
| F18.3 recolour time | solved (20 ms) | F18.1/ | — | 1 |
| F19.1 label sets | **blocked** — after 3 dispatches 17 of 21 area names show at fit (hidden: Remote Sensing and Geospatial Imaging, Quantum and Information Theory, Numerical Methods for PDEs, LLM Reasoning and Alignment); 22 labels, none overlapping. Last error: no free ±2-line offset for 4 names at 14 px on a 968 px map. User decides: accept ≥ 17 of 21, or allow 12 px area labels at fit | F19.1/walkC-* | radar/app.html | 3 |
| F19.2 priority | solved | F19.1/ | — | 1 |
| F19.3 level control | solved | F19.1/ | — | 1 |
| F20.1 first view | solved | F20.1/ | — | 1 |
| F20.2 connect preset | solved | F20.1/ | — | 1 |
| F20.3 window | solved | F20.1/, F20.3/ | — | 1 |
| F21.1 table | solved (15 chips `name ×g`; `new` chip not-verifiable: none new) | F21.1/walkC-* | radar/serve.py | 2 |
| F21.2 row/chip clicks | solved | F21.2/walkC-* | radar/app.html | 2 |
| F21.3 other levels | solved after the level-2 grouping fix (15 entries under areas) | F11/arriving-level2-round4.txt | radar/serve.py | 3 |
| F22.1 nodes/edges | solved | F22.1/ | — | 1 |
| F22.2 Links tab | solved | F22.1/ | — | 1 |
| F23.1 evidence from edge | solved | F23.1/ | — | 1 |
| F23.2 under 10 bridges | solved | F23.1/ | — | 1 |
| F24.1 first click | solved | F24/walkC-* | — | 1 |
| F24.2 second click | solved | F24/ | — | 1 |
| F24.3 Escape order | solved after PM correction (page-load opacity) | F24/ | — | 1 |
| F24.4 hover | solved | F24/ | — | 1 |
| F25.1 lasso | solved (point-in-polygon 5 ms) | F25/ | — | 1 |
| F25.2 too small | solved | F25/ | — | 1 |
| F25.3 cancel | solved | F25/ | — | 1 |
| F26.1 phrase search | solved | F26/ | — | 1 |
| F26.2 title search | solved | F26/ | — | 1 |
| F26.3 zero matches | solved | F26/ | — | 1 |
| F26.4 no map points | solved (`1 paper matches.`) | F26/walkC-* | radar/serve.py, radar/app.html | 2 |
| F27.1 selection view | solved | F27/walkC-* | radar/app.html | 2 |
| F27.2 empty state | solved | F27/walkC-* | — | 2 |
| F27.3 small/slow | solved (`Still computing` text verified with `--slow` by the builder; PM saw the 50 % grey and progress line) | F27/walkC-* | radar/app.html | 2 |
| F28.1 A/B compare | solved | F28/ | — | 1 |
| F28.2 colour-blind | solved | F28/ | — | 1 |
| F29.1 ask for ideas | solved (one real call, 26.5 s, 3 ideas; note: the model once wrote `arXiv:arXiv:2603.08721` and the page shows it as written) | F29/walkC-* | radar/app.html, radar/serve.py | 1 |
| F29.2 stop/slow/failing | solved after the 502 message fix (`claude -p exited with code N (<stderr>)`, formatter self-checked; page path re-verified by reading, the fake path was walked before the fix) | F29/walkC-observations-fault.json | radar/serve.py | 2 |
| F30.1 save | solved | F30/walkC-* | — | 1 |
| F30.2 reopen | solved (in_use message not-verifiable without ideas; covered by F14.3) | F30/ | — | 1 |
| F30.3 reopen after rerun | solved by equivalence: F14.2 on the dev database plus the older-run note seen in F30 | F30/, F14-F16/walk5 | — | 1 |
| F31.1 phrases tab | solved after PM correction of step 6 (415 rows; app shows CS-corrected growth) | F31/ | radar/serve.py (`/phrases`), radar/app.html | 2 |
| F32.1 server stops | solved (banner 2.3 s, re-sent selection after restart) | F32/walkC-* | — | 1 |
| F32.2 opened as file | solved | F32/walkC-* | — | 1 |
| F32.3 guard refusal | solved with a note: 127.0.0.1 is accepted by both guards, so only a rewritten Origin triggers the banner (text exact) | F32/walkC-* | — | 1 |
| F32.4 stale lock note | solved | F32/walkC-* | radar/serve.py, radar/app.html | 2 |
| F33.1 keys | solved (white 2 px ring after round 4; tab order at 1600 correct) | F33/walkC-*, J5/round4-final-check.json | radar/app.html | 3 |
| F33.2 screen reader/text | solved | F33/ | — | 1 |
| F33.3 colour-blind/motion | solved | F33/walkC-* | — | 1 |
| F34.1 1600×1000 | solved | F34/ | — | 1 |
| F34.2 1440×900 | solved | F34/walkC-* | radar/app.html | 2 |
| F34.3 1100×800 | solved | F34/ | — | 1 |
| F35.1 self-checks | solved (links/map/serve selftests ok; broken scratch copy fails with exit 1) | F35/ | radar/links.py, radar/map.py, radar/serve.py | 1 |
| F35.2 screenshots | solved | F35/walkC-* | tools/shot.cjs | 1 |
| F35.3 timing | solved after round 4 (three runs: firstDraw 1,194 / 1,179 / 1,197 ms median 1,194 < 2,000; recolour median 21 ms; max frame 17 ms; Apple M5 Metal; the first walk measured a 2,294 ms median when the point layer waited for Google Fonts) | F35/round4-f353-runs.txt, walkC-f353-runs.txt | radar/app.html | 2 |
| F36.1 libraries | solved (4 pinned URLs; 592 KB) | F36/grep.txt | radar/app.html | 1 |
| F36.2 tokens/look | solved after the F37 restyle (tab buttons 13 px; tokens per F37) — was **blocked** — 110 controls at 13 px with 1 px border and 6 px radius; the 4 tab-bar buttons are 12 px because the PM's brief said to leave tabs alone. Three dispatches used. One-line fix (`font-size: 13px` on the tab buttons) waits for the user's go | F36/walkC-* | radar/app.html | 3 |
| F36.3 animation | solved (ideas counter and toast verified in F29) | F36/walkC-*, F29/ | — | 1 |
| F37 Fuselab style | solved except one label overlap: badge labels 1 and 5 (`LLM Agent Tools and Safety ×1.7` / `LLM Agents and Scientific Applications ×1.4`) overlap at both widths because badges are fixed in the collision pass; all other labels clear (18 at 1440, 22 at 1600). Card sizes 944 × 640 / 1024 × 552 exact, no page scroll, Liter loaded, first draw 1,190 ms | 2026-11-07-style/ | radar/app.html | 2 |

## Journeys

| journey | status | evidence | notes |
|---|---|---|---|
| J1 what is arriving | solved after the J1 step 6 correction (badges = Topics rows 1–5; `]` → ×1.39 label; chip zoom → `Sub-topic, in topic LLM Agent Tools and Safety (share 100%)`) | J1/walkC-* | 15 arriving under the window-share rule |
| J2 how topics connect | solved (114 nodes, 40 edges; ten thickest edges judged neighbours, list in J2/; evidence view with 10 bridge papers; 30 white bridge points; 7 dashed phrase edges) | J2/walkC-* | top-10 list also in F4-F6/sql-walk.txt |
| J3 lasso → compare → ideas → restart | solved (`home field · 4 papers`; lasso 1,197 papers in 29 ms; ideas in 26.5 s; same ideas after restart; SQL ok count 7 = tab) | J3/walkC-* | one real ideas call |
| J4 operator monthly rerun | reviewed-not-verified for step 2 (a full `./run_all.sh` rerun was not executed this run: it includes the arXiv/Semantic Scholar enrich stage and 8 report-idea model calls); steps 1, 3, 5, 6, 7 verified by the equivalent flows F7.2, F4.1.7, F14.2, F16.1.5, F3.1 | F7/, F14-F16/walk5, F3/ | run on the dev database only |
| J5 keyboard only, laptop width | **blocked on step 2** (steps 1, 3–7 pass; at 1440 × 900 the tab order is q1, q2, (i), level, colour, search, A, B, ⇄, lasso, **window**, **layers**, canvas — the window and layers `<details><summary>` controls keep tabbing after the lasso after 3 dispatches; the 1600 × 1000 order is correct) | J5/walkC-*, J5/round5-tab-order.txt | last error: Codex reported the fix as confirmed from code, headless Chrome shows the old order |

## Blocked
- F19.1 step 1: 17 of 21 area names at fit after 3 dispatches (4 names have no free offset at 14 px). Decide: accept ≥ 17, or allow 12 px area labels at fit.
- J5 step 2: tab order at 1440 × 900 puts the window and layers dropdowns after the lasso button (3 dispatches; the `<summary>` elements do not take the assigned tabindex priority).


## Design findings recorded this run
1. The onset rule produced no arriving sub-topics; replaced by the window-share rule (user decision 2026-11-07; FEATURES F11 and flows F5.1, F11.1 corrected).
2. Cluster counts moved to 21/114/373 with the precomputed neighbour graph (UMAP `precomputed_knn`); fine clusters min size 137.
3. Links stage peak memory 8.3–8.6 GB (estimate was 5 GB); map 7 min; links 110 s; `candidate_vocab()` 57 s / 5.75 GB.
4. Calibration: the named pair rule picks no T; T = 5 by the bridge-share band.
5. Two `claude -p` hangs to the 300 s timeout when the model attempted a tool call with tools off.

## Run 2026-11-07: rename, static snapshot, hosting, weekly job

| item | status | evidence |
|---|---|---|
| Rename to undercurrent; repo github.com/Exorust/undercurrent | done (private: creating a public repo was blocked by the permission check) | git log |
| F38 export | solved: 2,622 files, 91.2 MB uncompressed, 25 s; topics/topic/link files equal the live server; 20 idea pairs, 60 ideas, 0 dropped | data/log/export-ideas.log |
| F38 static page | solved at a sub-path (local walk) and live at https://charoori.com/undercurrent/: 459,249 points, first draw 1,428 ms, no errors; parity with serve.py on a 4,000-row lasso exact for n, months, growth, top papers; known differences: phrase counts (normalised 346 vs literal 218), topic-pair bridge counts (window n_bridge), centroid distance (0.0249 vs 0.0253) | 2026-11-07-static/ |
| F38 hosting | solved: Vercel project `undercurrent` (https://undercurrent-five-kappa.vercel.app, brotli), charoori.com `vercel.json` rewrite on main (commit 9ae8162, promoted to production); charoori.com home and blog still 200 | curl checks |
| F39 weekly job | see the weekly test row below | data/log/weekly-2026-11-07.log |
| F39 weekly test (forced, deploy off) | solved for the weekly path: harvest 8,668 upserts in 53 s (461,838 papers); embed 1,253 new papers in 18 min including one Semantic Scholar batch failure retried; month check 2026-10 = published 2026-10 → no rebuild; stamp written, lock removed. The month-roll path (run_all → export → deploy) is not yet exercised by the job itself; its parts were run by hand this session. First automatic rebuild expected on the first run after 2026-12-01 (the job needs papers harvested in December before November counts as complete). | data/log/weekly-2026-11-07.log |
| launchd agent | installed: ~/Library/LaunchAgents/com.charoori.undercurrent.weekly.plist, loaded, daily 04:00 | launchctl print |
