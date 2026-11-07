# field-radar: design

Started 2026-11-04. Scope: computer science only.

## 1. Purpose

Two jobs, one pipeline:

1. **Radar.** Once a month, find CS research topics that are new or growing fast, pairs of CS fields that are starting to connect, and merge ideas that link a rising topic to the user's home field.
2. **Thinking map.** A zoomable 2D map of papers with topic labels at several zoom levels, so a large body of information can be processed by reading 10 labels, then 100, then 1,000, instead of 20,000 abstracts.

The pipeline is general (documents → embeddings → map → labels → change over time → ask). arXiv CS is the first input. Other document sets (HN threads, the user's own notes) can be added later as new inputs without changing the pipeline.

## 2. Decisions taken

| Decision | Choice | Alternative that lost, and when it would win |
|---|---|---|
| Scope | CS only (arXiv `cs.*` plus cross-lists into CS) | All of science via OpenAlex; wins if biology or physics becomes a target |
| Paper source | arXiv OAI-PMH bulk API, `set=cs` | Kaggle snapshot; wins if full history to 1990s is wanted in one download |
| Embeddings | Semantic Scholar SPECTER2 (768-d) via `/paper/batch`, no GPU | Local embedding model; wins if S2 coverage of the harvested papers is below ~80% |
| Topic detection | Term emergence (2–4 word phrases, burst detection) plus map-region labels | Embedding clusters per quarter; wins if the backtest shows topics that rise without a shared phrase |
| Main view | Zoomable point map (datamapplot) | Full topic graph; wins only at the ~100-topic zoom level |
| Distance math | Always in full 768-d space; the 2D map is display only | Distances read off the map; never correct, UMAP distorts |
| Time window | 3 years (Nov 2023 → now) | Longer; needed only for a deeper backtest |
| User's own docs on map | Not in v1; planned v2 layer reading `~/myproj/*/SYNTHESIS.md`, never `respond/perspectives/` | |
| Grants, OpenAlex, Reddit, X | Dropped | |

## 3. Data

| Source | Fields used | Access |
|---|---|---|
| arXiv OAI-PMH (`oaipmh.arxiv.org/oai`, `metadataPrefix=arXiv`, `set=cs`) | id, created, updated, title, abstract, categories (primary + cross-lists), authors | Free, no key, ~1,000 records per page, resumption tokens |
| Semantic Scholar Graph API `/paper/batch` | `embedding.specter_v2`, `tldr`, `citationCount`, `externalIds` | Free; 500 ids per call; request a key before bulk runs |
| HN Algolia API | mention counts per term per month | Free; checked only for flagged terms |
| Hugging Face Daily Papers | upvotes per arXiv id | Free; v2 |

Notes from the first harvest (2026-11-04):
- OAI-PMH `from`/`until` filter on the record datestamp (last update), not on submission, so updated old papers appear too.
- The `created` field is not the first-submission date (a 2022 paper showed `created=2026-11-02`). Submission month is derived from the id (`YYMM.NNNNN`) into `papers.submitted`; only ids at or after the window's first month are kept. Old-style ids (`math/0501001`) have no month and are dropped; none exist in a 2023+ window.
- A single 3-year request never returned its first page in 6 minutes; one calendar month answers in under 1 s. The harvester loops month by month and marks each finished month with a `data/raw/cs_<month>_done` file, so it is resumable.
- November 2023 alone: 8,635 records seen, 5,117 kept, 7 pages, ~45 s.

Storage: one DuckDB file `data/radar.duckdb`. Tables: `papers`, `paper_categories`, `embeddings`, `s2_meta`, `hn_counts`, `topics`, `topic_members`, `signals`, `notes`. Raw OAI pages are kept as gzipped XML in `data/raw/` so the database can be rebuilt.

## 4. Signals

Each signal answers one question. A candidate topic must pass all four scoring filters (growth, age, breadth, acceleration) to appear in the report.

| Signal | Question | Method | Cost |
|---|---|---|---|
| S1 Topic growth | Which map regions are growing? | Member count per region per month, slope and second difference | Low |
| S2 Field convergence | Which category pairs are starting to share papers? | Co-occurrence count per pair per month, ranked by growth with a minimum size | Low |
| S3 Term emergence | Which new phrases are spreading? | 2–4 word phrases from titles and abstracts, first-seen date, Kleinberg burst detection, number of distinct author groups | Medium, CPU |
| S4 Practitioner check | Is HN talking about it? | HN mention counts for flagged terms; flag "HN before papers" when HN count rises while paper count is still small | Low |

Scoring filters:
- **Growth**: rate of increase with a minimum size (default: at least 30 papers in the latest quarter).
- **Age**: first appearance date; younger scores higher.
- **Breadth**: number of distinct first-author surnames or affiliations; a single-lab term is not a field (default: at least 5 groups).
- **Acceleration**: positive second difference of the monthly count.

## 5. Merge ideas

1. Describe the home field: user-supplied keywords plus a set of seed papers; represent it as the mean SPECTER2 vector of the seeds.
2. For each rising topic, compute cosine distance from the home vector in 768-d.
3. Keep topics in the middle band (default 0.25–0.55 cosine distance; tune on feedback). Too close is already the home field; too far has no practical link.
4. An LLM reads the topic summary, 3 top papers, and the home description, and writes: "Method X from topic A could address problem Y in the home field", with the 3 papers cited. Output is stored in `signals` with the model name and date.

## 6. Map

- datamapplot interactive HTML from UMAP of SPECTER2 vectors.
- Labels at three levels (~10 / ~100 / ~1,000 clusters) from HDBSCAN at three minimum cluster sizes; labels written by an LLM from top terms plus 5 titles.
- Layers: time slider (month of `created`), growth colour (S1/S3), convergence lines (S2, drawn between region centroids), home marker, user notes (pins stored in `notes`).
- Click region → summary, top 5 papers, growth chart, HN counts.
- Select two regions → LLM answers: what connects them, what is missing between them, one combined idea, with cited papers.
- Ask a question → embed the question with SPECTER2 through S2 (or a local small model if unavailable), highlight nearest region.
- Second view: local graph of a selected topic and its ~20 nearest topics.
- Size risk: datamapplot is untested at 300k+ points. Fallback: show last 12 months plus a random sample of older papers; full data stays in DuckDB.

## 7. Backtest (before trusting any signal)

Run the pipeline on data truncated at 2022-01-01 (requires widening the harvest to 2021 for this step only), then:
- Hit rate: does it flag RAG, Gaussian splatting, test-time compute, mechanistic interpretability, flow matching before their paper counts took off?
- False-alarm rate: of everything flagged in Jan 2022, how many topics had fewer than 200 papers by 2024?
- Record both numbers in `BACKTEST.md`. Tune the four filter thresholds on this, not on the live run.

## 8. Build order

Each step ends with a stop to show results.

1. Project setup, OAI-PMH harvest for `cs` from 2023-11-01, load into DuckDB, report counts by month and category. No GPU.
2. Semantic Scholar enrichment (embeddings, tldr, citations). Request an API key first. Report coverage %.
3. S2 field convergence and S3 term emergence; first ranked lists.
4. Map v1: UMAP + HDBSCAN + datamapplot, three label levels, time slider.
5. Backtest (section 7). Tune thresholds.
6. Merge ideas + monthly report (`report/YYYY-MM.md`), HN check.
7. Map v2: notes, two-region question, home marker, user docs layer.

## 9. Layout

```
field-radar/
  DESIGN.md
  radar/
    harvest.py      # OAI-PMH → data/raw/*.xml.gz → DuckDB papers
    enrich.py       # Semantic Scholar batch → embeddings, s2_meta
    signals.py      # S1–S4
    map.py          # UMAP, HDBSCAN, datamapplot HTML
    report.py       # monthly markdown
  data/             # gitignored; radar.duckdb, raw/
  report/
  .venv/
```

Run everything with `.venv/bin/python -m radar.<module>`. Monthly run is a single `make monthly` later; not needed for v1.

## 10. State after the first full run (2026-11-04)

Run with `./run_all.sh`; each stage logs to `data/log/<stage>.log`. Outputs: `report/2026-11.md`, `report/map.html`, `BACKTEST.md`.

What differs from the plan above:

- **Harvest has two sources.** arXiv's bulk endpoint (OAI-PMH) stopped answering uncached requests on 2026-11-04, at datestamp April 2026. `radar/harvest_api.py` uses the arXiv search API, which filters on the true first-submission date, and covered 2025-07-01 to today. Total 452,360 papers. Gap: papers submitted before July 2025 and revised after April 2026 are missing until `radar/harvest.py` can finish (it resumes from the marker files in `data/raw/`).
- **Embeddings cover a sample, not everything.** Semantic Scholar allows about 1,500 papers per minute without an API key, so `radar/enrich.py --limit 60000` fetches a fixed sample of the last 18 months, ordered by `md5(id)` so a larger limit extends the same set. 59,753 papers have vectors. Set `S2_API_KEY` to go about 3 times faster.
- **S1 (topic growth on map clusters) is only used inside merge ideas**, on the 60,000-paper sample. It is not a separate table in the report.
- **S3 needed two filters that were not in the plan.** A phrase must appear in titles (at least 3 titles and 5% of its recent papers), and its mix of primary categories must differ from the average recent paper (KL divergence at least 0.5). Without them the list was writing-style phrases such as "central finding". Ranking uses a rough significance value `z = (recent - expected) / sqrt(recent + expected + 1)` multiplied by `log(growth)`.
- **Backtest was run from 2025-04-01, not 2022**, because the data starts in November 2023. Result, corrected for the 1.26x growth of all CS: of the top 50 flagged phrases 42% grew at least 1.5x faster than the field, 40% kept pace, 18% faded (median 1.41). All 156 candidates: 35% / 48% / 17% (median 1.25). So the filters select phrases that beat the field and the ranking adds a small lift. Product and model names fade ("openai o1", "deepseek r1"); method names grow. S2 has no numerical backtest yet.
- **Map clustering.** HDBSCAN leaves 34% to 39% of points unassigned; each gets the majority label of its 15 nearest assigned points. The coarse level uses leaf cluster selection (27 clusters); the default selection put 88% of papers into one cluster.
- **Merge distance band is relative.** Topic distances from the home field were all between 0.04 and 0.09, so the fixed band in section 5 selected nothing. `radar/merge.py` takes topics between the 25th and 75th percentile of distance.

Not built yet: notes on the map, the two-region question, the home-field marker, growth colouring, the user-documents layer, a product-name filter for S3, a check of the `math.PR` pairs in S2 (seven rise together, which may be a reclassification by arXiv).

## 11. Second pass (2026-11-05): data fixes, signal clean-up, pooled backtest

- **Harvest gap fixed.** The search API query `cat:cs.*` does not match papers whose only CS category is an alias: `cs.NA` is indexed as `math.NA` and `cs.SY` as `eess.SY`. About 600 papers a month were missing from April 2026 on. `radar/harvest_api.py` now queries `cs.*,math.NA,eess.SY` by default; a one-off run for the two alias categories from 2025-07-01 added 8,225 papers (460,585 total). The API also lists only one name of an alias pair where the bulk endpoint lists both; the `ALIAS` map in `radar/signals.py` already merges them.
- **S3 filters added.** (1) A phrase must occur word for word in at least half of its recent papers; this removes phrases that exist only because stop words are removed before counting ("models write"). It also removes real names that contain a stop word ("chain of thought"); storing the most common surface form would keep them. (2) If one neighbour word follows or precedes a phrase in at least 60% of its occurrences, the phrase is extended ("frozen language" becomes "frozen language model") and its counts are scaled by that share. (3) Phrases with a product or model name (hand list `NAMES`, or any token with a digit) are dropped. (4) Spelling variants (hyphen, plural) become one row.
- **Backtest is pooled over four cutoffs** (2025-01, 04, 07, 10) because one cutoff gives only 50 phrases. Result for S3: 36% of the top 50 grew at least 1.5x faster than the field, 22% faded; baseline 27% and 22%. With the old rules: 34% and 24%; baseline 26% and 25%. So the new filters clean the list but change its predictive value very little, and the single-cutoff figure in section 10 (42%) was the best of the four cutoffs.
- **S2 does not predict.** First numerical backtest: 42% of flagged pairs faded against 28% of all pairs with at least 30 papers; only the pairs with `cs.MA` (multi-agent systems) kept growing. Stricter variants (larger pairs, growth in two consecutive windows, growth beyond each member category) were no better. The minimum pair size is now 30 and the report presents S2 as a description of the last 6 months. A signal for field convergence should come from the embedding map (topics that move toward each other), which is not built.
- **Why category pairs are unstable.** The average number of categories per paper fell from 1.92 to 1.72 between January 2025 and October 2026, and single categories change in steps: `eess.IV` cross-lists halved in September 2025, `eess.AS` fell in November 2025. The `math.PR` rise flagged in the first report is of this kind: the share of CS papers that carry `math.PR` went from about 0.5% to about 0.75% in July 2026 and stayed there, spread over 486 first authors and many primary categories, with no common topic. It is one category-level change counted as seven pairs.
- **All papers embedded; map runs at full size.** The Semantic Scholar fetch without an API key covered 459,249 of 460,585 papers (99.7%) in about 3 hours with no failed attempt. The map stage on all of them took 7 minutes and 8.1 GB of memory at peak (UMAP on one core), and gives 20 / 116 / 368 clusters. `report/map.html` is 36 MB; in headless Chrome with software rendering it loads in 3 seconds, draws all points and zooms. The fallback in section 6 (last 12 months plus a sample) was not needed. A missing line break had put the map title inside a comment; fixed.
- **Plural rule.** `stem()` in `radar/signals.py` now maps "harnesses" to "harness", so "agent harness" and "agent harnesses" are one row. It is not a real stemmer: "policies" and "policy" stay apart.
- **Merge view (`report/merge.html`).** `python -m radar.merge` now also writes a small page (260 KB, plain SVG, no library) with the 116 medium topics twice: left on the map coordinates with the home-field marker and lines to the topics that got an idea, right as growth against 768-d cosine distance to the home field with the band shaded. Click a topic for its numbers, the 5 most-cited papers and the idea text. `--view` rewrites the page from stored ideas without LLM calls (about 10 s). The alternative, colouring the big map by growth, lost because it would replace the cluster colours and cost another 36 MB file; it would win if one needs growth at the paper level rather than the topic level.
