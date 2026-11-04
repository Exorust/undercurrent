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
