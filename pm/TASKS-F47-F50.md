# Build tasks for F47–F50 (comments, institutions, citations, performance)

Status: proposal, waiting for user approval of F47–F50 and decisions D1–D4 (pm/FEATURES.md).

## How the work is split
- **Sonnet tasks** are small: one file (or one new file), a fixed contract written below, one self-check that must pass, under about 150 changed lines. They need no judgment about the product.
- **Opus tasks** carry judgment or risk: the reference-graph fetch, `links.py`, the filter panel in `app.html`, and every review.
- **PM** (main session) runs the spikes' gate, walks the flows, and never edits code.
- Every brief carries the PM rules: edit only the files named; never edit `pm/`; leave changes uncommitted; list every file touched; one attempt, then report.

## Rules that keep parallel agents from colliding
1. **One owner per file per wave.** Tasks in the same wave never share a file.
2. **Fetchers never write the database.** DuckDB allows one writer, so each fetcher writes chunked Parquet files to its own folder under `data/raw/<source>/` (resumable: skip ids already in a chunk). One loader (T2.1) merges them into the database.
3. **`app.html` is edited by one task at a time** (wave 4 runs in sequence). Pure logic moves to a new file `radar/filter.js` so it can be built and tested in parallel, in Node, without a browser.
4. Tests run against `data/dev.duckdb` (20k papers), never the real database, unless the task says otherwise.

## Wave 0 — spikes (parallel; facts before code)
| id | model | owns | does | done when |
|---|---|---|---|---|
| S1 | sonnet | `data/spikes/s1_comments.py` | fetch 2,000 random ids through the arXiv API `id_list` (3 s between calls); count papers with a comment, venue-like strings, code links; save 200 sample comments | `data/spikes/s1.json` with the counts and samples |
| S2 | sonnet | `data/spikes/s2_openalex.py` | look up 2,000 ids on OpenAlex by DOI `10.48550/arXiv.<id>` (key from env `OPENALEX_KEY` if set); record share with ≥ 1 institution, share with ROR, share with country, seconds per call, cost headers | `data/spikes/s2.json`; extrapolated hours and cost for 459,249 papers |
| S3 | sonnet | `data/spikes/s3_refs.py` | Semantic Scholar `/paper/batch` for 500 ids with `references.externalIds`; record the per-paper reference limit, share of references that are arXiv papers in our corpus, calls per minute | `data/spikes/s3.json`; extrapolated hours |
| S4 | sonnet | `tools/perf.cjs` (new) | headless Chrome (puppeteer-core path in env `PUPPETEER_CORE`): measures `firstDrawMs`, every request with transferred bytes and `content-encoding`, `maxFrameMs` during a 3 s pan; later also `filterApplyMs` (reads `window.__ucPerf` if present) | `data/spikes/perf-baseline.json` for the live site |

**Gate G1 (PM + user):** read S1–S4. Decide D3 from S3. If S2 coverage is under 50 %, the filter ships with a visible "partial coverage" note; under 25 %, F48 goes back to the user.

## Wave 1 — fetch and parse (parallel)
| id | model | owns | contract | self-check |
|---|---|---|---|---|
| T1.1 | sonnet | `radar/venues.py` (new) | `parse_comment(text: str \| None) -> dict` with keys `venue, kind ('accepted'\|'submitted'\|'workshop'\|None), year, code_url, pages, withdrawn`; table `VENUES` of about 80 CS venues with aliases (NeurIPS/NIPS, ACL/EMNLP/NAACL, CVPR/ICCV/ECCV, …) | `--selftest`: 40+ asserts from S1's real samples, prints `ok` |
| T1.2 | sonnet | `radar/harvest_api.py` | read `arxiv:comment`; add column `comment` (`alter table papers add column if not exists comment text`); insert it | run on 2 days into `data/dev.duckdb`; at least one non-null comment |
| T1.3 | sonnet | `radar/backfill_comments.py` (new) | ids from `papers` where `comment is null`; arXiv API `id_list` in batches of 200, 3 s apart, 5 retries; writes `data/raw/comments/NNNNN.parquet` (`id, comment`) | run on 1,000 ids; restart mid-run resumes |
| T1.4 | sonnet | `radar/institutions.py` (new) | OpenAlex by DOI, `OPENALEX_KEY` env, polite `mailto` taken from env `OPENALEX_MAILTO` only (never hard-code an email), concurrency 5, retries; writes `data/raw/openalex/NNNNN.parquet` (`id, author_pos, inst_id, ror, name, country, type`); `--since` for weekly increments | run on 500 ids; restart resumes; failed ids listed in `data/raw/openalex/failed.txt` |
| T1.5 | sonnet | `radar/enrich.py` | new flag `--refresh`: refetch `citationCount, influentialCitationCount, publicationVenue` for papers whose `fetched_at` is older than 25 days; new column `s2_venue`; embeddings are not re-fetched | on `data/dev.duckdb`, `--refresh --limit 1000` updates `fetched_at` of 1,000 rows |
| T1.6 | **opus** | `radar/refs.py` (new) | only if D3 = yes. Semantic Scholar references for every corpus paper, kept only when the cited paper is in the corpus; handles the batch reference cap found by S3 (fall back to `/paper/{id}/references` paging for papers over the cap); writes `data/raw/refs/NNNNN.parquet` (`citing_id, cited_id`) | 2,000 papers; edge count matches a hand count for 3 papers |

Notes from finished Wave 1 tasks: T1.5 — `s2_venue` is often `arXiv.org` (preprint server, not a venue); T2.1 must treat `arXiv`, `arXiv.org` and empty as no venue. A full `enrich --refresh` without a key paces 3.5 s per 500 papers, about 1.3 h. arXiv returned HTTP 429 to this machine at about 17:20 while three clients (spike S1, the T1.2 test, the T1.3 test) hit it at once; rule from now: one arXiv API client at a time, run by the PM, never in parallel agents. T1.3 is selftest-verified only; its live check runs after S1 finishes and the 429 clears. The database path for every stage comes from env `RADAR_DB` (default `data/radar.duckdb`).

## Wave 2 — load and derive (in order; each writes the database alone)
| id | model | owns | contract | self-check |
|---|---|---|---|---|
| T2.1 | sonnet | `radar/load_extra.py` (new) | merge `data/raw/*` into tables `paper_comments (id, comment, venue, venue_source, kind, year, code_url, pages, withdrawn)` (uses T1.1; S2 venue fallback), `paper_institutions (id, inst_id, author_pos)`, `institutions (inst_id, ror, name, country, type, n)`, `cite_refs (citing_id, cited_id)`; idempotent (replace); prints coverage lines | `--selftest` on a temporary copy of `data/dev.duckdb` with fixture Parquet; running twice gives the same counts |
| T2.2 | sonnet | `radar/citations.py` (new) | `paper_cites (row, cit_pct)`: percentile of citations within the submission month, average rank for ties, null stays null; `topic_signals (level, cluster, win, venue_share, code_share, cit_median_pct, top10_share, top_insts json, top_venues json)` for windows 3/6/12 | `--selftest`: percentile of a fixed 10-paper month equals hand values |
| T2.3 | **opus** | `radar/links.py` | only if D3 = yes. `topic_citelinks (level, c1, c2, month, n_cites)` from `cite_refs` + `paper_topics`; top 5 cross-pair cited papers per pair for evidence | hand count of one pair matches |
| T2.4 | sonnet | `run_all.sh`, `tools/weekly.sh` | add stages: `backfill_comments` (first run only), `institutions --since`, `enrich --refresh` (only on a new complete month), `refs` (monthly, if D3), `load_extra`, `citations`; all with `timeout` like existing stages | `bash -n` both files; `RADAR_DRY=1` prints the new stage list without touching real logs (see FRICTION note on the old dry-run) |

## Wave 3 — serve, export, filter logic (parallel)
| id | model | owns | contract | self-check |
|---|---|---|---|---|
| T3.1 | sonnet | `radar/filter.js` (new) | browser + Node module, no dependencies. `buildMask({n, flags, citpct, instRows, countryRows}, f) -> Uint8Array` (f = `{inst:[], country, venue, code, cit}`; inst lists OR, kinds AND); `countByTopic(mask, labels, k) -> Uint32Array`; `toQuery(f)` / `fromQuery(str)` | `radar/filter.test.mjs`: correctness on a 20-point fixture; on 459,249 synthetic points `buildMask` + `countByTopic` < 60 ms (`node radar/filter.test.mjs` prints `ok`) |
| T3.2 | sonnet | `radar/export.py` | new files in `site/data/`: `flags.bin` (Uint8 per row: bit0 at venue, bit1 workshop, bit2 submitted, bit3 has code, bit4 withdrawn), `citpct.bin` (Uint8, 255 = unknown), `venues.json`, `institutions.json` (`[[inst_id, name, country, n]]`, n ≥ 30), `inst/<inst_id>.bin` and `country/<cc>.bin` (sorted Uint32 rows); paper shards gain `[venue_idx, code_url, cit_pct]`; topic files gain the `topic_signals` fields; links files gain `n_cites` and evidence when present; copies `radar/filter.js` to `site/map/filter.js`; `--selftest` checks the F50 size and file-count budgets | `python -m radar.export --selftest` prints `ok` |
| T3.3 | sonnet | `radar/serve.py` | local-mode equivalents: `GET /filter.js`; `GET /institutions?q=` (top 20 by n); `GET /rows?inst=&country=` (binary Uint32 rows); `/topic` and `/paper` gain the same fields as the static files | `--selftest` extended; prints `ok` |
| T3.4 | sonnet | `tools/site-vercel.json` (new; T3.2 copies it to `site/vercel.json`) | `Cache-Control: public, max-age=3600, stale-while-revalidate=86400` on `/data/*`; no change to rewrites | `vercel deploy` preview shows the header (PM checks) |

## Wave 4 — the page (in order, one task at a time on `app.html`)
Added 2026-11-07 after the front-page review: **T4.0** (sonnet, `radar/app.html`, runs right after the F51/F52 builder): deep links `?topic=<level>-<cluster>` (select that topic at that level, inspector filled, map zoomed to it, first-visit tour suppressed) and `?link=<level>-<c1>-<c2>` (connect view, pair panel open); invalid values are ignored. T3.2 also writes `data/landing.json` (the 5 rows of each "Right now" list, < 10 KB) so the front page stops fetching the 296 KB topics file. T3.2 must also change `radar/export.py:256`, which asserts the landing page contains `src="preview.png"`; F53 removed the hero image, so assert instead that the `og:image` meta tag points at preview.png (the landing builder correctly refused to game the check). Note for T4.1 from T3.1: `buildMask` ORs every list in `data.instRows`, so pass only the selected institutions' lists, in `f.inst` order.

| id | model | owns | contract |
|---|---|---|---|
| T4.1 | **opus** | `radar/app.html` | Filter sidebar row + filter panel in the inspector (reuse the `#viewPanel` pattern): institution type-ahead (lazy `institutions.json`), country select, `At a venue` / `Has code` chips, citation control; mask from `filter.js` fed to deck.gl through one attribute with `updateTriggers` (no new object arrays per point); dimming with the existing rule; title-bar chips; URL state; empty state; `window.__ucPerf.filterApplyMs`; lazy loading so nothing new is on the first-load path. Flows F48.2, F47.2 steps 4–5, F49.2 step 4 |
| T4.2 | sonnet | `radar/app.html` | display only, after T4.1: venue pill + code link + percentile on paper lines (`plist`), topic inspector lines (venues, code, institutions, citations), Topics sheet columns `Matching`, `At a venue %`, `With code %`, `Cited`. Flows F47.2 steps 1–3, F49.2 steps 1–3 and 7 |
| T4.3 | sonnet | `radar/app.html` | only if D3 = yes, after T4.2: `Citations` column in the Links sheet, evidence line and most-cited list in the pair panel. Flow F49.2 steps 5–6 |
| T4.4 | sonnet | `radar/landing.html` | stat strip: `Affiliations for N %` and `With code N %` from `meta.json` (T3.2 adds both numbers); one sentence in "How the numbers are made" on citation percentile |

## Wave 5 — check and ship
| id | model | does |
|---|---|---|
| R1 | **opus** (plus Codex and Grok, read-only, per the review rule) | review the whole diff for correctness and the F50 budgets |
| P1 | PM | full run on the real database (network-bound fetches, no GPU: comments about 2 h, OpenAlex hours per S2, references hours per S3), export, `tools/perf.cjs`, walk F47–F50 plus a regression walk of F43/F46, update STATUS.md |
| P2 | user | approve the deploy; it goes out with `vercel deploy site --prod` as before (never `vercel promote`) |

## Order and parallelism
```
W0  S1 S2 S3 S4                    (4 sonnet, parallel)      -> gate G1 with the user
W1  T1.1 T1.2 T1.3 T1.4 T1.5 T1.6  (5 sonnet + 1 opus, parallel)
W2  T2.1 -> T2.2 -> T2.3 ; T2.4 in parallel (different files)
W3  T3.1 T3.2 T3.3 T3.4            (4 sonnet, parallel)
W4  T4.1 (opus) -> T4.2 -> T4.3 ; T4.4 in parallel
W5  R1 -> P1 -> P2
```
Total: 4 Opus tasks, 19 Sonnet tasks. The long pole is the network fetches in P1, not the coding.
