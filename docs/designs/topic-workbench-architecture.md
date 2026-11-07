# Architecture plan: Topic workbench

Written 2026-11-06 for the approved design `docs/designs/topic-workbench.md`. This plan says what to build, where, in what order, and how to check it. It resolves the engineering questions in the design's "Reviewer Concerns" (R3-1 to R3-14) and records every choice it made in section 10.

Conventions used below:

- "Verified" means checked on 2026-11-06 against the code, the installed packages or a read-only query on `data/radar.duckdb`. Every other number is marked "estimate".
- "kNN" means k-nearest neighbours. "Row" means a paper's position in the fixed row order defined in section 3. "Window" means a run of complete calendar months.
- Level 2 = coarse (20 clusters), level 1 = medium, called "topics" (116), level 0 = fine (368). Verified with `select level, count(*) from topics`.

## 0. Facts checked before planning

| fact | value | how checked |
|---|---|---|
| umap-learn version | 0.5.12 | `.venv/bin/python -c "import umap; print(umap.__version__)"` |
| pynndescent version | 0.7.0 | same, `pynndescent.__version__` |
| other pins | duckdb 2.0.0, numpy 2.5.4, pyarrow 26.0.1, hdbscan 0.8.44, datamapplot 0.7.3, scikit-learn 1.9.1 | `pip freeze` |
| requirements file | none exists; nothing is pinned | `ls` of the project root |
| embedded papers | 459,249 of 460,585 (1,336 without a vector) | count queries |
| `submitted` | month precision (`YYYY-MM-01`, derived from the arXiv id); 37 distinct months, 2023-11 to 2026-11 | query |
| partial month | 2026-11 has 3,231 papers; 2026-10 has 22,883 | query |
| ids | all new-style, all 10 characters (`YYMM.NNNNN`) | query |
| primary categories | 149 distinct | query |
| fine cluster size | min 142, max 11,987 (design says 137 minimum; small drift) | query |
| level nesting | levels are separate HDBSCAN runs, not nested. Mean share of a fine cluster's papers in its majority topic is 0.90; 37 of 368 fine clusters have under 60%, the worst 31%. Topic to coarse: mean 0.92, 5 of 116 under 60% | query |
| S3 rows in the latest live run | 415 phrases (2026-11-05) | query |
| s2_meta | 407,301 papers have a TLDR summary; 307,708 have at least one citation | query |
| text size | title + abstract average 1,395 characters | query |
| `claude -p` tools | `radar/llm.py` passes no tool flag, so tools are on today. The design says they are off. | `radar/llm.py:7` |

### How the installed umap-learn exposes its neighbour graph

- `radar/map.py:65` calls `umap.UMAP(n_neighbors=15, metric="cosine", random_state=42, low_memory=True).fit_transform(V)`. With more than 4,096 points UMAP calls `nearest_neighbors()` (`umap/umap_.py:257`), which builds `pynndescent.NNDescent(X, n_neighbors=15, metric="cosine", random_state=..., n_trees=min(64, 5 + round(sqrt(n) / 20)), n_iters=max(5, round(log2(n))), max_candidates=60, low_memory=True, compressed=False)` and reads `knn_search_index.neighbor_graph` (`umap_.py:328-345`). For n = 459,249 that is 39 trees and 19 iterations.
- The result is stored on the fitted object as the private attributes `_knn_indices`, `_knn_dists` and `_knn_search_index` (`umap_.py:2626-2634`). `graph_` is the fuzzy symmetric graph built from them, not the raw kNN.
- Self at rank 0: yes, by construction. `NNDescent.neighbor_graph` in pynndescent 0.7.0 (`pynndescent_.py:2413-2444`) states "Each point is listed as its own first neighbor (at distance 0)". Internally it holds 15 real neighbours, then shifts them right by one, drops the 15th, and writes the point's own index at position 0. So the public graph has 14 real neighbours per paper, and the self entry is at rank 0 even when duplicate vectors exist. Rows can be padded with `-1` if fewer neighbours were found.
- Disconnection: UMAP sets neighbours at distance ≥ 2 (the cosine maximum, `DISCONNECTION_DISTANCES["cosine"] = 2`) to `-1`, in place, on the arrays it was given (`umap_.py:2652-2654`). In practice this never triggers for cosine, but the code must tolerate `-1`.
- Public reuse path: `UMAP(precomputed_knn=(knn_indices, knn_dists))` is supported (`umap_.py:1648`, `1995-2055`). A 2-tuple gives a warning ("transform will be unavailable"), which is harmless because `map.py` never calls `transform`.
- With `random_state` set, UMAP forces `n_jobs=1` (`umap_.py:1950-1951`). This is why the map stage uses one core.

## 1. Module layout

```
                          run_all.sh  (writes data/run.lock, refuses if data/serve.pid is live)
                              |
 harvest -> enrich -> signals -> backtests -> hn -> map -> links -> merge -> report
                                                    |       |
                         radar.duckdb tables:  point_index  bridges, topic_links, topic_months,
                                               knn          topic_onset, topic_parent, terms,
                                               knn_exact_   paper_terms, phrase_papers,
                                                 sample     phrase_topics, (view) paper_topics
                                               map_meta
                         files:  report/points.bin, report/points.ids
                              |
                              v
           radar/serve.py  (reads radar.duckdb read-only; writes only data/workbench.duckdb)
                              |  JSON + binary over http://127.0.0.1:8765
                              v
           radar/app.html  (deck.gl from a CDN; one file, no build step)
```

| file | new or changed | owns | lines (estimate) | reuses |
|---|---|---|---|---|
| `radar/map.py` | changed | Builds the kNN once with `pynndescent.NNDescent` and passes it to UMAP through `precomputed_knn`; writes `point_index`, `knn`, `knn_exact_sample`, `map_meta`; writes `report/points.bin` and `report/points.ids`; wraps its table writes in one transaction | +70 | `fetch_vecs` (`harvest.py:30`), existing UMAP and HDBSCAN code |
| `radar/links.py` | new | Stage `links`: bridges, topic_links, topic_months, topic_onset, topic_parent, terms, paper_terms, phrase_papers, phrase_topics, paper_topics view; kNN precision check; `--calibrate` report for the bridge threshold and onset share | 220 | `signals.KW`, `signals.stem`, `signals.NAMES`, a new `signals.candidate_vocab()` (below), `harvest.DB` |
| `radar/signals.py` | changed | Extract the candidate-vocabulary block of `terms()` (`signals.py:99-117`) into `candidate_vocab(df, recent, min_recent=30) -> list[str]`; `terms()` calls it | ±15 (net about 0) | itself |
| `radar/serve.py` | new | `ThreadingHTTPServer` on 127.0.0.1:8765; Host and Origin guard; pid lock; in-memory arrays; all endpoints in section 5; `data/workbench.duckdb` | 380 | `fetch_vecs`, `llm.ask`, `merge.top_papers` pattern |
| `radar/app.html` | new | The page: three columns, deck.gl `ScatterplotLayer` and `LineLayer`, lasso, inspector, tabs | 700 (HTML + JS + CSS) | `radar/merge.html` styling |
| `radar/llm.py` | changed | `ask()` runs `claude -p` with all tools off, no MCP servers, no session file, in an empty temp directory | +6 | itself |
| `radar/merge.py` | changed in step 6 | Stops writing `report/merge.html`; keeps writing `merge_ideas` for the report; the idea instructions move to a module constant shared with `serve.py` | -25 | itself |
| `run_all.sh` | changed | Run lock with `trap`, serve-pid check, new `links` stage after `map` | +15 | existing `stage()` helper |
| `requirements.txt` | new | Pins for the packages whose private or version-specific behaviour this plan relies on | 9 | none |

Shared code check (ponytail rule: extract only with two real callers): `candidate_vocab()` has two callers, `signals.terms()` (`signals.py:99-117`, existing) and `links.paper_terms()` (proposed). Both need the same tokenisation (`KW`), the same chunked candidate search and the same name filter, and a drift between the two would make distinctive terms disagree with the rising-phrase list. Lines removed from `signals.py`: about 12; added there: about 14 (function header and call); `links.py` saves about 12 lines it would otherwise copy. The idea-prompt constant has two callers (`merge.py` and `serve.py`). Nothing else is extracted.

## 2. Data model

All new pipeline tables live in `data/radar.duckdb`. Months are stored as `smallint` month indices: 0 = the first month in `papers` (2023-11, stored in `map_meta.month0`). Clusters are `smallint` cluster ids from the current map run.

### 2.1 Written by `radar/map.py`

```sql
-- one row per embedded paper; row order = arXiv id ascending (section 3)
create or replace table point_index (
  row      integer primary key,
  id       text not null unique,
  month    smallint not null,      -- month index
  cat      smallint not null,      -- index into map_meta 'categories' (JSON list), 255 = unknown
  l0 smallint not null, l1 smallint not null, l2 smallint not null,   -- cluster per level
  x real not null, y real not null);

-- 14 real neighbours per paper (self removed by row, not by rank position)
-- no primary key: an ART index on 6.4M rows costs build time and memory; uniqueness is asserted by the self-check
create or replace table knn (row integer not null, nbr integer not null, rank tinyint not null, dist real not null);

-- exact cosine neighbours of 1,000 random rows (seed 0), for the precision check in links.py
create or replace table knn_exact_sample (row integer not null, nbr integer not null, rank tinyint not null,
                                          primary key (row, rank));

create or replace table map_meta (key text primary key, value text not null);
-- keys: run_id (e.g. 20261106T183000Z), run_unix, n_points, month0 (2023-11), n_months, categories (JSON),
--       umap_version, pynndescent_version, knn_recall14 (written by links.py)
```

`point_index` is the design's `point_index(row, id)` plus the per-paper columns the page and server need. It replaces nothing: `topic_members` stays as is, because `merge.py` and `report.py` read it.

### 2.2 Written by `radar/links.py`

```sql
create or replace view paper_topics as            -- design table, kept as a view (decision D6)
  select k.row, l.level,
         case l.level when 0 then p.l0 when 1 then p.l1 else p.l2 end as cluster,
         count(*)::double / 14 as nbr_share
  from knn k join point_index p on p.row = k.nbr cross join (values (0), (1), (2)) l(level)
  group by all;

create or replace table bridges (
  row integer not null, level tinyint not null,
  c1 smallint not null, c2 smallint not null,     -- c1 < c2
  k1 tinyint not null, k2 tinyint not null,       -- neighbour counts in c1 and c2 (out of 14)
  month smallint not null,
  primary key (level, row), check (c1 < c2));

create or replace table topic_links (
  level tinyint, c1 smallint, c2 smallint, month smallint, n_bridge integer not null,
  primary key (level, c1, c2, month), check (c1 < c2));

create or replace table topic_months (level tinyint, cluster smallint, month smallint, n integer not null,
                                      primary key (level, cluster, month));

create or replace table topic_onset (level tinyint, cluster smallint, onset_month smallint not null,
                                     onset_share real not null,   -- the share used (default 0.10)
                                     primary key (level, cluster));

-- not in the design: maps fine clusters to topics and topics to coarse clusters by majority (levels are not nested, section 0)
create or replace table topic_parent (level tinyint, cluster smallint, parent_level tinyint, parent smallint not null,
                                      share real not null, primary key (level, cluster, parent_level));

create or replace table terms (term_id integer primary key, term text not null unique, n_all integer not null);
-- no primary key (size); rows inserted ordered by term_id, row so DuckDB zone maps prune
create or replace table paper_terms (row integer not null, term_id integer not null);

-- not in the design: the papers behind each S3 phrase, so phrase selections and phrase_topics share one match
create or replace table phrase_papers (phrase text not null, row integer not null);

create or replace table phrase_topics (phrase text, level tinyint, cluster smallint, n integer not null, share real not null,
                                       primary key (phrase, level, cluster));
```

### 2.3 `data/workbench.duckdb` (owned by `radar/serve.py`, never opened by the pipeline)

```sql
create table if not exists selections (
  sel_id text primary key,          -- 12 hex characters from secrets.token_hex(6)
  name text,                        -- user label; null = auto label
  kind text not null,               -- topic | lasso | click | phrase | query
  spec json,                        -- {"level":1,"cluster":63} | {"polygon":[[x,y],...]} | {"phrase":"..."} | {"query":"..."}
  ids text[] not null,              -- arXiv ids, never rows or cluster ids
  n integer not null,
  created timestamp not null default now(),
  run_id text not null);            -- map run at save time, for display only

create table if not exists ideas (
  idea_id text primary key,
  created timestamp not null default now(),
  a_sel text not null, b_sel text not null,    -- saved selection ids
  a_name text, b_name text,                    -- label snapshot at generation time
  title text, text text not null,
  cited text[] not null,                        -- arXiv ids parsed from text
  shown text[] not null,                        -- the ids the model saw
  status text not null check (status in ('ok', 'regenerated', 'dropped')),
  reason text,                                  -- why regenerated or dropped
  model text not null,
  run_id text not null);
```

These two tables are only ever created if missing and appended to. Later schema changes use `alter table ... add column`. Nothing drops them.

### 2.4 Row counts, sizes, rebuild policy

| table | rows (estimate unless verified) | on-disk size (estimate) | rebuilt |
|---|---|---|---|
| point_index | 459,249 (verified count of embedded papers) | 15 MB | every map run: `create or replace` inside one transaction with `knn`, `knn_exact_sample`, `map_meta`, `topics`, `topic_members` |
| knn | 6,429,486 (459,249 × 14) | 40-60 MB | same |
| knn_exact_sample | 14,000 | < 1 MB | same |
| map_meta | about 12 | tiny | same |
| bridges | 50k-120k per level, up to 350k total | 3-6 MB | every links run: all links tables `create or replace` inside one transaction |
| topic_links | level 1: up to about 75k; level 0: up to about 400k | 2-5 MB | same |
| topic_months | at most 504 × 37 = 18,648 | tiny | same |
| topic_onset | 504 (verified cluster count) | tiny | same |
| topic_parent | 368 × 2 + 116 = 852 | tiny | same |
| terms | 20,000-60,000 (the design's "about 20,000" is not verified; `signals.py` never stores the vocabulary) | 1-3 MB | same |
| paper_terms | 5M-15M | 40-120 MB | same |
| phrase_papers | 50k-300k | 2-6 MB | same |
| phrase_topics | about 30k | < 1 MB | same |
| selections, ideas | grows by user action; a 100k-paper selection is about 1.1 MB of ids | small | never rebuilt |

Consistency between stages: `map.py` writes `map_meta.run_id`. `links.py` writes `map_meta.links_run_id = run_id` as the last statement of its transaction. `serve.py` refuses to start, with a message naming the stage to rerun, when the two differ (a map run whose links stage failed would otherwise pair new rows and clusters with old bridges).

`map.py` today deletes and reinserts `topics` and `topic_members` level by level without a transaction (`map.py:69-87`); a crash in the middle leaves a mixed state. The change wraps all of the map stage's table writes in `begin` / `commit`, after UMAP and HDBSCAN have finished.

## 3. `report/points.bin` and `report/points.ids`

Row order contract: rows are the embedded papers sorted by arXiv id ascending (`order by id`). The same order is used in `point_index`, `knn`, `points.bin`, `points.ids` and the server's in-memory arrays. Because ids sort by submission month, rows are also roughly in time order. Sorting by id makes the order the same across runs when the paper set is the same, and lets the server map ids to rows with `numpy.searchsorted` on the sorted id array, with no dictionary.

`points.bin`, little-endian, columnar, so each column is a TypedArray view with no copy:

| offset (bytes) | type | content |
|---|---|---|
| 0 | uint32 | magic `0x54505246` (ASCII "FRPT" read little-endian) |
| 4 | uint32 | format version = 1 |
| 8 | uint32 | n (number of rows) |
| 12 | uint32 | month0 as YYYYMM (202311) |
| 16 | uint16 | number of months |
| 18 | uint16 | number of categories |
| 20 | uint32 | run_unix (map run time, seconds) |
| 24 | 8 bytes | zero |
| 32 | float32 × 2n | x, y interleaved (x0, y0, x1, y1, ...), ready for deck.gl `getPosition` |
| 32 + 8n | uint16 × n | month index |
| 32 + 10n | int16 × n | level 0 cluster |
| 32 + 12n | int16 × n | level 1 cluster |
| 32 + 14n | int16 × n | level 2 cluster |
| 32 + 16n | uint8 × n | primary category index (255 = unknown) |

Size = 32 + 17n = 7,807,265 bytes for n = 459,249 (verified arithmetic; the design's "about 8 MB" holds). Every block starts on an offset that is a multiple of its element size (32 and 8n are multiples of 4; all later offsets are even), which TypedArray views require.

Browser decode:

```js
const buf = await (await fetch('/points.bin')).arrayBuffer();
const dv = new DataView(buf);
if (dv.getUint32(0, true) !== 0x54505246 || new Uint32Array(buf, 0, 1)[0] !== 0x54505246)
  throw new Error('points.bin: bad magic or big-endian platform');   // TypedArrays use platform byte order
const n = dv.getUint32(8, true);
const xy = new Float32Array(buf, 32, 2 * n);
const month = new Uint16Array(buf, 32 + 8 * n, n);
const lvl = [0, 1, 2].map(k => new Int16Array(buf, 32 + (10 + 2 * k) * n, n));
const cat = new Uint8Array(buf, 32 + 16 * n, n);
```

Writer (in `map.py`): `numpy` arrays with explicit dtypes `<f4`, `<u2`, `<i2`, `u1`, written with `tofile` to `points.bin.tmp`, then `os.replace` to the final name so a crash never leaves half a file.

`points.ids`: UTF-8, one arXiv id per line in row order, LF line ends, final LF. Size 459,249 × 11 = 5,051,739 bytes (verified arithmetic; the design's 4.6 MB is low). The page does not load it at start (hover shows topic, month and category from `points.bin`; a click fetches `/paper?row=`). It stays as a served file for debugging and scripts.

Both files are served with `ETag: <run_id>` and `Cache-Control: no-cache`, so the browser revalidates and never mixes a new `points.bin` with an old page state.

## 4. kNN and bridges

### 4.1 Getting the graph (change to `map.py`)

```python
from pynndescent import NNDescent
n = len(V)
nnd = NNDescent(V, n_neighbors=15, metric="cosine", random_state=42, n_jobs=1, low_memory=True, compressed=False,
                n_trees=min(64, 5 + int(round(n ** 0.5 / 20))), n_iters=max(5, int(round(np.log2(n)))), max_candidates=60)
idx, dist = nnd.neighbor_graph                   # (n, 15); self at column 0 by pynndescent 0.7.0 contract
xy = umap.UMAP(n_neighbors=15, min_dist=0.05, metric="cosine", random_state=42, low_memory=True,
               precomputed_knn=(idx.copy(), dist.copy())).fit_transform(V)   # copies: UMAP writes -1 into its arrays
```

The parameters copy what UMAP passes internally (section 0), so compute time and graph quality match today's run. Using the public `neighbor_graph` and `precomputed_knn` instead of the private `_knn_indices` removes the dependency on UMAP internals that R3-11 raised. The graph stored is, by construction, the graph UMAP used.

Self removal by row (R3-11): for each row `i`, drop entries where `idx[i, j] == i` or `idx[i, j] == -1`, then keep the first 14 remaining. If a row has no self entry (never expected), the first 14 are kept and a counter is printed. Rows with fewer than 14 valid neighbours are kept with what they have; `paper_topics` and the bridge rule divide by 14 regardless, so a short row can only lose bridge status, never gain it. The self-check asserts that at least 99.9% of rows have exactly 14 (estimate of what will be seen).

`requirements.txt` pins `umap-learn==0.5.12` and `pynndescent==0.7.0` (plus duckdb, numpy, pyarrow, hdbscan, datamapplot, scikit-learn, pandas at their installed versions). `map.py` writes both versions into `map_meta` so a later run can be compared.

### 4.2 Precision check against an exact search (design Open Question 4)

Done in `map.py` while `V` is still in memory (the vectors are freed after UMAP today), about 10-30 seconds (estimate; depends on which BLAS numpy uses, which `numpy.show_config()` did not name):

1. Draw 1,000 rows with `numpy.random.default_rng(0).choice(n, 1000, replace=False)`.
2. Normalise the 1,000 query vectors to unit length (`Q`, 1000 × 768).
3. Stream the full matrix in chunks of 50,000 rows: normalise the chunk, compute `chunk @ Q.T` (50,000 × 1,000 floats, 200 MB), keep a running top 15 per query with `argpartition`.
4. Remove each query's own row, keep the top 14, write them to `knn_exact_sample`.

`links.py` then computes, on those 1,000 rows:

- recall@14 = mean over queries of |approximate ∩ exact| / 14;
- bridge agreement at level 1 and level 0 = share of the 1,000 rows whose bridge decision (bridge or not, and the same pair) is identical with exact and approximate neighbours;
- mean absolute difference in the neighbour count of the second cluster.

Acceptance: bridge agreement ≥ 0.95 at level 1. Recall@14 is reported and stored in `map_meta.knn_recall14`; a recall below 0.90 with agreement ≥ 0.95 is accepted and noted, because bridges are what the graph is used for. If agreement is below 0.95: first rerun `NNDescent` with `n_iters` doubled and `max_candidates=100` (estimate: +2 to +4 minutes); if still below, an exact kNN for all papers is the fallback (estimate 10-30 minutes at full CPU and several GB in chunks), which needs the user's go-ahead under the project rule on heavy runs.

### 4.3 Bridge rule

For each paper `i` and level `L`, count its 14 neighbours per cluster (`numpy.bincount` over `labels_L[knn[i]]`, vectorised over all rows with a row-offset trick). Let `a` and `b` be the two clusters with the most neighbours (ties broken in favour of the paper's own cluster, then the lower id). Paper `i` is a bridge between `min(a, b)` and `max(a, b)` when `count(a) ≥ T` and `count(b) ≥ T`. With 14 neighbours and `T = 4`, at most three clusters can reach `T`; only the top two count.

This differs slightly from the design text ("at least 4 of 14 neighbours in a second cluster"). Requiring `T` neighbours in both clusters stops a paper that HDBSCAN placed in the wrong 2-D cluster (0 neighbours in its own cluster, 12 in another) from counting as a bridge. The paper's own cluster is usually `a`. Decision D3.

`topic_links` = count of bridge papers per `(level, c1, c2, month)`, month = the bridge paper's month.

### 4.4 Normalisation

Over a window `W` (section 6): `strength(c1, c2) = B_W / sqrt(n1_W * n2_W)`, where `B_W` is the sum of `n_bridge` over the window and `n1_W`, `n2_W` the clusters' paper counts in the window from `topic_months`. Undefined (null, not drawn) when either count is 0. The raw `B_W` is shown next to every edge.

### 4.5 Calibration (`python -m radar.links --calibrate`, read-only after the stage)

Prints, for `T` in {3, 4, 5, 6}, at level 1, window = last 12 complete months:

1. Share of papers that are bridges, and mean neighbour purity (share of a paper's neighbours in its own cluster) per level.
2. The top 20 edges by strength with labels, `B_W`, and both clusters' sizes.
3. Named check pairs, found by label search on the current run (cluster ids change between runs, so labels are matched with `ilike`). Current labels (verified): positive pair "Robot Manipulation Learning" (1/63) with "Diffusion and Flow Generation" (1/31); negative pair "Graph Algorithms and Complexity" (1/71) with "Medical Image Analysis" (1/34). Add one more positive pair after reading the first top-20 list (expected candidates: "Production Agentic AI Workflows" with a code-generation topic).

Rule for choosing `T`: the smallest `T` for which (a) no negative pair is in the top 10, (b) every top-10 edge has `B_W ≥ 5`, and (c) the positive pair has rank ≤ 30 by strength. Smallest because it keeps the most evidence. The chosen `T` is a module constant in `links.py` with the date and the printed numbers in a comment, and is stored in `map_meta.bridge_T` so the page can state it.

Level 0 links are computed but only shown in the overlay if mean neighbour purity at level 0 is ≥ 0.5 (estimate threshold). Below that, most fine-level "bridges" mean only that the 2-D clustering cut a 768-d neighbourhood. Top edges will mostly be clusters that touch on the map; that is expected, not a fault.

## 5. Server (`radar/serve.py`)

### 5.1 Process and threads

- `http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)`, default port 8765, `--port` flag. Bind to `127.0.0.1`, not `localhost`, so it never listens on IPv6 `::1` by surprise.
- Start-up order: take the pid lock (5.3); open `radar.duckdb` with `duckdb.connect(DB, read_only=True, config={"memory_limit": "2GB"})`; check `map_meta.run_id == links_run_id`; load in-memory state; open `workbench.duckdb`; print the URL.
- Each request takes `cur = con.cursor()` and closes it in `finally`. A DuckDB connection must not be shared across threads; cursors are separate connections to the same database.
- `workbench.duckdb`: one connection, used only while holding `wb_lock = threading.Lock()`, for reads and writes alike (R3-14). The workbench holds a few hundred rows, so serialising it costs nothing.
- One language model call at a time: `ideas_lock`, taken with `acquire(blocking=False)`; a second `POST /ideas` while one runs gets 409. The model call runs with no cursor open and without `wb_lock`.
- Transient selections live in memory: an `OrderedDict` of `sel_id -> (rows int32 array, kind, spec)`, capped at 64 entries (least recently used dropped). Only saved selections go to `workbench.duckdb`.

### 5.2 Memory (estimates, except the vector size)

| item | size |
|---|---|
| vectors, 459,249 × 768 float32, in row order | 1.41 GB (verified arithmetic) |
| knn as int32 (n × 14) | 26 MB |
| ids (`numpy` `<U10`), months, categories, 3 label arrays, citations, x, y | about 30 MB |
| fine-cluster centroids (368 × 768) | 1 MB |
| DuckDB read connection | capped at 2 GB, typically a few hundred MB |
| peak while loading vectors (Arrow table plus numpy copy) | about 2.9 GB, then 1.5 GB steady |

Vectors are loaded with `fetch_vecs(con, "select p.row, e.vec from point_index p join embeddings e using(id) order by p.row")`. Estimated start-up 10-20 seconds. If it is over 30 seconds when measured, `map.py` can also write `data/vectors.f32` (1.41 GB, row order) for `numpy.memmap`; not built unless needed (decision D10).

### 5.3 Pid files and stale detection (R3-1, R3-2)

The pipeline never opens `data/workbench.duckdb`, and the server opens `data/radar.duckdb` read-only.

`serve.py`:

```python
def pid_alive(pidfile, tag):
    """True when pidfile names a running process whose command line contains tag."""
    try: pid = int(pathlib.Path(pidfile).read_text().split()[0])
    except (FileNotFoundError, ValueError, IndexError): return False
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: pass                     # exists, owned by someone else
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return tag in cmd                                # a reused pid after a reboot is not a live lock
```

1. Create `data/serve.pid` with `os.open(path, O_CREAT | O_EXCL | O_WRONLY)` and write the pid. On `FileExistsError`: if `pid_alive(serve.pid, "radar.serve")`, exit with "server already running (pid N)"; otherwise print "removing stale data/serve.pid (pid N not running)", remove it, retry once.
2. Then check `pid_alive("data/run.lock", "run_all.sh")`. If live: remove our own pid file and exit with "pipeline is running (pid N); start the server after it finishes". If the file exists but is stale: warn, remove, continue.
3. Remove `data/serve.pid` in an `atexit` handler and on SIGTERM and SIGINT (signal handler calls `sys.exit`, which runs `atexit`).

`run_all.sh` (top of script):

```sh
alive() { [ -f "$1" ] && p=$(head -1 "$1") && ps -p "$p" -o command= 2>/dev/null | grep -q "$2"; }
if ! ( set -C; echo $$ > data/run.lock ) 2>/dev/null; then          # set -C: create only if missing (atomic)
  if alive data/run.lock run_all.sh; then echo "another run is active (data/run.lock)"; exit 1; fi
  echo "removing stale data/run.lock"; rm -f data/run.lock; ( set -C; echo $$ > data/run.lock ) || exit 1
fi
trap 'rm -f data/run.lock' EXIT INT TERM
if alive data/serve.pid radar.serve; then echo "server is running (data/serve.pid); stop it first"; exit 1; fi
[ -f data/serve.pid ] && { echo "removing stale data/serve.pid"; rm -f data/serve.pid; }
```

Each side creates its own file first and then checks the other's. If both start at the same moment, both may refuse; neither can run alongside the other. That is the safe failure. The manual fix for any stuck lock is to delete the file. Backstop (estimate, to be confirmed once): DuckDB itself refuses a read-write open while another process holds the file read-only, and the reverse, so a stage started by hand (`python -m radar.map`) while the server runs fails with DuckDB's lock error rather than corrupting anything.

### 5.4 Request guard

- Every request: the `Host` header must equal `localhost:<port>` or `127.0.0.1:<port>`, else 403. This blocks DNS-rebinding pages.
- Every POST: `Origin` must equal `http://localhost:<port>` or `http://127.0.0.1:<port>`, else 403 (a missing `Origin` is also 403; current Chrome and Safari send it on every `fetch` POST). `Content-Type` must be `application/json`, else 415, so a plain HTML form from another site cannot post.
- Body limit 8 MB (413 above). A full-map lasso of 459,249 row numbers is about 3.2 MB of JSON (estimate).
- Every JSON response carries `run_id`. The page sends `X-Run-Id`; a mismatch returns 409 `stale_run` and the page reloads itself. This covers a page left open across a pipeline run and a server restart.

### 5.5 Endpoints

Common objects:

- `window`: `{"from": "2026-05", "to": "2026-10", "prior_from": "2025-11", "prior_to": "2026-04", "last_complete": "2026-10", "vol_recent": 123456, "vol_prior": 101234, "vol_ratio": 1.22}`
- `paper`: `{"row": 12, "id": "2510.01234", "title": "...", "month": "2025-10", "cat": "cs.LG", "citations": 41, "summary": "TLDR or first 400 characters of the abstract"}`

Requests with `from`/`to` default to the last 6 complete months. `level` defaults to 1.

| method, path | request | response (JSON unless noted) |
|---|---|---|
| GET `/` | none | `radar/app.html` |
| GET `/meta` | none | `{run_id, n_points, months: ["2023-11", ...], last_complete, categories: [...], levels: {"2": 20, "1": 116, "0": 368}, bridge_T, onset_share, knn_recall14, backtest_line: "top-50 phrases grew 1.5x faster than CS in 36% of cases vs 27% baseline (pooled, 4 cutoffs)", level0_links: bool}` |
| GET `/points.bin`, `/points.ids` | none | binary / text, section 3 |
| GET `/labels?level=` | level | `{level, clusters: [{c, label, n, cx, cy, parent, parent_share}]}` (parent from `topic_parent`) |
| GET `/topics?level=&from=&to=` | level, window | `{window, topics: [{c, label, n_total, recent, prior, growth, is_new, arriving: [{c, label, recent, prior, growth, is_new, onset}]}]}`; `growth` is null when `is_new`; `arriving` lists level-0 clusters whose majority parent at the requested level is this topic |
| GET `/links?level=&from=&to=` | level, window | `{window, rule: {k: 14, T, strength: "B/sqrt(n1*n2)", share: "B/(n1+n2)"}, bridge_links: [{c1, c2, strength, n_bridge, share_now, share_prior, growth, status}], phrase_links: [{c1, c2, phrases: [...]}]}`; `status` is `new`, `ratio` or `low` (< 10 bridges); sorted `new` first (by `n_bridge`), then `ratio` by `growth` descending, then `low` by `n_bridge`. Phrase links ignore the window (stated in the response as `"phrase_window": "whole corpus"`) |
| GET `/link?level=&c1=&c2=&from=&to=` | pair, window | `{c1, c2, n_bridge, bridges: [paper + {k1, k2}] (up to 30, by citations, inside the window), phrases: [{phrase, n1, n2, share1, share2}]}` |
| POST `/select` | exactly one of `{"rows": [int]}`, `{"topic": {"level": 1, "c": 63}}`, `{"phrase": "..."}`, `{"query": "..."}`, `{"sel_id": "..."}`; optional `from`, `to` | `{sel_id, kind, n, n_unembedded, missing, rows (omitted for kind rows and topic), months: [int per month], growth: {recent, prior, growth, is_new, window}, top_cited: [paper × 10], newest: [paper × 10], terms: [{term, k, share_sel, share_all, delta, z}] (20), topics: [{level, c, label, own_share, nbr_share}] (top 10 per level)}` |
| GET `/paper?row=` | row | `paper + {abstract, authors (first 5), url}` |
| GET `/distance?a=&b=` | two sel_ids | `{cos_dist, n_a, n_b, overlap, n_bridges, bridges: [paper] (up to 20)}` |
| GET `/colour/dist?a=&level=0` | sel_id | `{level, dist: [float per cluster]}` (cosine distance of each cluster centroid to A's centroid) |
| GET `/selections` | none | `[{sel_id, name, kind, n, created, run_id}]` |
| POST `/selections` | `{sel_id, name}` | `{sel_id}`: copies a transient selection to `workbench.duckdb` as ids |
| POST `/selections/delete` | `{sel_id}` | `{deleted: true}`; refused with 409 if ideas reference it |
| POST `/ideas` | `{a, b, n}` (n from 1 to 5, default 3) | `{ideas: [{idea_id, title, text, cited, status}], dropped, shown: [paper + {side: "A" | "B" | "bridge"}]}` |
| GET `/ideas?a=&b=` | optional filters | `[{idea_id, created, a_name, b_name, title, text, cited, status}]` (dropped rows excluded) |

Selection rules:

- Selections contain embedded papers only. `{query}` can match some of the 1,336 unembedded papers; they are counted in `n_unembedded` and left out of all statistics.
- `{rows}`: integers in `[0, n)`, deduplicated with `numpy.unique`; anything else is 400.
- `{query}`: `title ilike '%' || ? || '%' escape '\'` with `%`, `_` and `\` in the user text escaped. Parameterised, never formatted into SQL.
- `{phrase}`: if the phrase is an S3 phrase, rows come from `phrase_papers`; otherwise `contains(lower(title || ' ' || abstract), lower(?))` over `papers` joined to `point_index` (a full scan of about 640 MB of text, estimate 0.5-2 seconds).
- `{sel_id}` of a saved selection: ids are mapped to rows with `searchsorted` on the sorted id array; ids absent from the current run are counted in `missing`.
- Monthly counts, growth, top cited and newest are computed in numpy on the in-memory arrays. Distinctive terms are one DuckDB query on `paper_terms` with the selection's rows registered as an Arrow table on the request's cursor (formula in 6.6).
- Bridges between two selections A and B (for `/distance` and `/ideas`): with boolean masks `mA`, `mB` over all rows, `kA = mA[knn].sum(1)`, `kB = mB[knn].sum(1)`; a paper is a bridge when `kA ≥ T` and `kB ≥ T`. Papers outside both selections can be bridges (the gap between two lassos).

Errors: always `{"error": code, "message": text, "run_id": ...}`.

| status | code | when |
|---|---|---|
| 400 | `bad_request` | missing or invalid parameter, window outside the data, rows out of range, more than one selector |
| 403 | `forbidden` | Host or Origin check failed |
| 404 | `not_found` | unknown path or unknown or expired `sel_id` (the page then re-sends the selection) |
| 409 | `stale_run`, `ideas_busy`, `in_use` | run id mismatch; another ideas call running; deleting a selection that ideas reference |
| 413 | `too_large` | body over 8 MB |
| 415 | `bad_type` | POST without `application/json` |
| 422 | `empty_selection` | selection has no embedded papers |
| 500 | `internal` | unexpected exception; traceback to stderr and `data/log/serve.log` |
| 502 | `llm_failed` | `claude -p` returned non-zero |
| 504 | `llm_timeout` | `claude -p` ran longer than 300 seconds |

### 5.6 `POST /ideas` and `claude -p`

Shown set: up to 8 bridge papers between A and B (by citations), then the 5 most cited and 5 newest of A and of B that are not already shown: at most 28 papers, each with arXiv id, title and a 400-character summary (about 6,000 tokens, estimate). If either selection is transient, it is saved first (unnamed), so stored ideas always point at saved selections and survive a restart and a pipeline rerun.

Prompt: the instruction paragraph from `radar/merge.py:89` (concrete method, problem, why plausible, first experiment, cite inline as `arXiv:ID`, plain language), adapted to "between A and B", asking for `n` ideas as a JSON array of `{"title", "idea"}`. The parse takes the text between the first `[` and the last `]` (the same approach as `map.llm_names`). A parse failure re-runs the whole call once; a second failure returns 502.

Citation check per idea (R3-9): parse `arXiv:(\d{4}\.\d{4,5})(v\d+)?` from the text, strip versions. The idea passes when every cited id is in the shown set, and either it cites at least one A-side paper and at least one B-side paper, or it cites at least one bridge paper. An idea that cites nothing fails. A failing idea is regenerated once on its own: one more `claude -p` call that asks for a single idea, with the reason it failed. If that fails too, the idea is stored with `status = 'dropped'` and its reason, logged to stderr, and not shown. The server checks; the model is only asked.

`radar/llm.py` change (R3-8):

```python
def ask(prompt, model="sonnet", timeout=300):
    argv = ["claude", "-p", "--model", model, "--output-format", "text",
            "--tools", "",                      # no built-in tools: the prompt carries untrusted paper text
            "--strict-mcp-config",              # with no --mcp-config: no MCP servers either
            "--no-session-persistence"]
    with tempfile.TemporaryDirectory() as d:    # empty working directory, defence in depth
        r = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=d)
```

The flags were checked against `claude --help` on 2026-11-06 (`--tools ""` disables all built-in tools; `--strict-mcp-config` uses only servers from `--mcp-config`). The change also applies to the existing callers `map.llm_names` and `merge.py`, which also send paper titles. Whether user-level hooks still run under `-p` is an open question (section 10.2); with no tools, tool hooks have nothing to fire on.

## 6. Windows and growth

Notation: months are indices; `E` = last complete month; a window of `N` months is `R = [E-N+1, E]`, its prior window `P = [E-2N+1, E-N]`.

### 6.1 Last complete month

`E = min(month(max(submitted)), month(max(harvested_at)) - 1)`. Today: `max(submitted)` is 2026-11, so `E` = 2026-10. The second term handles a harvest on the 1st of a month with no new-month papers yet, where the latest month with papers is complete. This differs from `signals.windows()` (`signals.py:43-49`), which always drops `month(max(submitted))`; in that one edge case the two disagree by one month (decision D12, documented in the page's window label).

With 36 complete months, `N ≤ 18` (the prior window must start at or after month 0). The page offers 3, 6, 12 and 18 months and any custom `from`/`to` of equal-length recent and prior windows inside the data.

### 6.2 CS-corrected growth (topics and fine clusters)

`vol = V_R / V_P`, with `V` = number of embedded papers in the window. For a cluster with `r` papers in `R` and `p` in `P`:

`growth = (r + 1) / (p · vol + 1)`

This is the same form as `signals.pair_rows` and `signals.terms` (`signals.py:78-79`, `144-145`), so a topic's growth and a phrase's growth mean the same thing. `merge.py:41` uses an uncorrected ratio; the workbench does not reuse it.

### 6.3 Link growth with pseudo-counts

For a pair over `R` and `P`: `B_R`, `B_P` bridge counts; `n1`, `n2` paper counts.

- `share_now = B_R / (n1_R + n2_R)`
- `share_prior = (B_P + 1) / (n1_P + n2_P + 1)`
- if `B_R < 10`: `status = low`, `growth = null`
- else if `B_P < 3`: `status = new`, `growth = null`
- else `status = ratio`, `growth = share_now / share_prior`

No CS correction is needed: both shares are already ratios to the pair's own volume.

### 6.4 "New" and "arriving" for fine clusters (R3-4)

`onset_month` = the first complete month `m` where the cluster's cumulative count over complete months reaches `onset_share × total` (default 0.10). Computed per cluster over `topic_months` with the partial month excluded.

A fine cluster is arriving in window `R` when all hold:

1. `onset_month ∈ R`;
2. `r ≥ 30`;
3. `growth ≥ 2` (formula 6.2).

"New" is a display label for an arriving cluster with `p < 5`: the ratio is replaced by "new". Condition 1 still applies. A cluster with `r ≥ 30` and `p < 5` that fails condition 1 is not shown as arriving.

The same "new" label applies to topics (`r ≥ 30`, `p < 5`) in the Topics tab, without the onset condition, because topics are not flagged as arriving.

Calibration of `onset_share` (in `--calibrate`): find the fine clusters whose labels or top terms match "world action model" and "agent harness" (by label `ilike` and by `phrase_topics` share). For window 6 and 12 months, print their onset months, `r`, `p`, growth and whether they are flagged, for `onset_share` in {0.05, 0.10, 0.20}. Keep 0.10 unless both are missed at 0.10 and caught at another value. If no fine cluster corresponds to these phrases (they may be inside larger clusters), record that, and this is the case for the design's Open Question 3 (a finer level).

### 6.5 Phrase links (R3-10)

From `phrase_topics` at the requested level, for each S3 phrase: `Q` = clusters with `n ≥ 10` and `share ≥ 0.20`. The phrase's spread is `eff = exp(-Σ s_c · ln s_c)` over all clusters with `s_c = share` (the effective number of clusters). The phrase links every pair in `Q` when `|Q| ≥ 2` and `eff ≤ 4`. Phrases with `eff > 4` are listed in the Phrases tab and draw no edge.

The design's cap of 4 clusters at the 20% level only excluded phrases split exactly five ways, because at most five clusters can each hold 20%. The effective number measures the whole spread, so a phrase like "llm agents" spread thinly over 30 clusters is excluded even if two clusters pass 20%. Phrase links are whole-corpus; the page says so next to the dashed-edge legend.

Phrase matching for `phrase_papers` (in `links.py`): normalise text and phrase the same way (`lower`, hyphens to spaces, other non-alphanumerics to spaces, collapse spaces), then match `\b<phrase>s?\b`. Run as one DuckDB query with `contains()` as a prefilter and `regexp_matches()` to confirm, over the 415 phrases (estimate 1-3 minutes). S3 keys that were counted without stop words ("chain thought") rarely occur word for word; they will have few or no papers, and `phrase_topics` simply has no rows for them. Their count is printed.

### 6.6 Distinctive-term score (R3-5)

For a selection S of `n_s` papers and a term w: `k_s` = selection papers containing w; `k_b` = papers outside S containing w; `n_b = N - n_s`; `π_w = n_all(w) / N` (the term's corpus document share).

Each side gets a Beta prior centred on the corpus share with total weight `α0` papers: `α_w = α0 · π_w`, `β_w = α0 · (1 - π_w)`.

```
ω_s = ln((k_s + α_w) / (n_s - k_s + β_w))
ω_b = ln((k_b + α_w) / (n_b - k_b + β_w))
δ   = ω_s - ω_b
var = 1/(k_s + α_w) + 1/(n_s - k_s + β_w) + 1/(k_b + α_w) + 1/(n_b - k_b + β_w)
z   = δ / sqrt(var)
```

Rank by `δ` among terms with `k_s ≥ 3`, `k_s ≥ 0.01 · n_s` and `z ≥ 3`. This is the log-odds ratio with an informative Dirichlet prior (Monroe, Colaresi and Quinn, 2008, "Fightin' Words").

Why this fixes the reviewer's point: the old fixed pseudo-share 0.001 equals about 460 papers of background for every term, larger than most terms' real counts. Here the pseudo-count is proportional to each term's own corpus share, so a term in 50 papers gets about 0.1 pseudo-papers with `α0 = 1000`, and its background share is still estimated from its own counts. Ranking by effect size with a significance gate keeps very common phrases from filling the list by count alone.

`α0` default 1,000 (estimate). Tune in step 4 on three known lassos (the robot-manipulation topic, the gap between the agent-security and production-agent topics, one fine cluster): pick the value whose top 10 terms a reader would call specific to the region. The same SQL runs for any selection size. The design's 20,000-paper sampling is kept only as a fallback if the query is over 1.5 seconds on a 100k selection (estimate: under 1 second, since it is a hash join of a registered row list with `paper_terms`).

```sql
with s as (select row from sel),                                   -- sel = registered Arrow table of rows
k as (select pt.term_id, count(*) k_s from paper_terms pt join s using(row) group by 1 having count(*) >= greatest(3, 0.01 * $n_s))
select t.term, k.k_s, t.n_all - k.k_s as k_b, t.n_all::double / $N as pi
from k join terms t using(term_id)
```

The formula is then applied in numpy on the few hundred to few thousand rows returned.

## 7. Failure modes and edge cases

| case | behaviour |
|---|---|
| Empty selection (a lasso over empty space, a query with no match) | 422 `empty_selection`; the inspector says "no papers in this selection" and keeps the previous one |
| Selection with no embedded papers (title query matching only unembedded papers) | 422 with `n_unembedded` in the message |
| Selection of 1 or 2 papers | Stats returned; growth shown as counts only (`r`, `p`), no ratio when `r < 10` (same minimum-support idea as links); terms list usually empty because of `k_s ≥ 3` |
| Pair with zero bridges in the window | `strength = 0`, `status = low`; no edge drawn; evidence panel says "no bridge papers in this window" and still lists shared phrases |
| Cluster with 0 papers in the window | `strength` null, edge not drawn; topic growth uses the `+1` smoothing and shows counts |
| Pipeline run while the server is up | Prevented: `run_all.sh` refuses (5.3). A stage run by hand hits DuckDB's file lock (backstop, to be confirmed). |
| Server started after a run, page still open from before | 409 `stale_run` on the next request; the page reloads `points.bin` and labels |
| Map stage succeeded, links stage failed | `links_run_id ≠ run_id`; server refuses to start with "rerun: python -m radar.links" |
| Stale pid file (crash, reboot) | Command-name check; warning naming the file; removed; start continues |
| Pid reused by an unrelated process | Command-name check fails, file treated as stale |
| Huge lasso (100k to 459k rows) | Browser point-in-polygon with a bounding-box prefilter over the `xy` array (estimate 20-100 ms); JSON body up to about 3.2 MB; server work is numpy over boolean masks plus one join; target under 3 seconds, measured in step 4 |
| Partial month | Excluded from every window and from onset; the label names the last complete month |
| Window outside the data (prior window before 2023-11) | 400 with the largest allowed `N` |
| Cluster ids change between runs | Saved selections and ideas store arXiv ids and label snapshots, never cluster ids. A saved "topic" selection reloads as its ids; its auto label is recomputed as the current topic holding most of its papers, with that share |
| Saved selection ids missing from the new run | Counted in `missing` and shown ("12 papers from the saved set are no longer embedded") |
| Idea cites an id outside the shown set, or none, or only one side | Regenerated once alone; then stored as `dropped` with the reason, logged, not shown |
| `claude -p` fails or times out | 502 or 504; nothing stored; the page shows the message and a retry button |
| Two ideas requests at once | Second gets 409 `ideas_busy` |
| Two browser tabs | Both work; writes are serialised by `wb_lock`; the in-memory selection cache is shared |
| Disk full while writing `points.bin` | Temp file plus `os.replace`; the old file stays; the stage fails |
| `-1` neighbours from pynndescent | Ignored; the row keeps fewer than 14 and can only lose bridge status |
| A fine cluster with no clear parent (37 have under 60% in their majority topic) | Shown under its majority topic with its share ("62% in this topic"), so a split cluster is visible rather than hidden |

## 8. Test plan

### 8.1 One runnable self-check per module (ponytail style)

Each check is a few `assert` lines at module level on tiny inputs, like `signals.py:36`, so it runs on every import and costs microseconds. Larger checks run on real data as part of the stage and print one line.

| module | check | what it asserts | the regression it catches |
|---|---|---|---|
| `map.py` | `encode_points()` round trip on 5 fake rows | decoding the bytes with the section 3 offsets returns the same x, y, month, labels and categories; header magic, n and total length 32 + 17n | an offset or dtype change that would make the page draw garbage |
| `map.py` | `drop_self()` on a 3 × 15 array with self at column 0, self at column 3 (duplicate case) and a `-1` | each row keeps 14 valid neighbours, none equals its own row | self-neighbour kept, or a real neighbour dropped by position |
| `map.py` (run time) | after the real run | `knn` row count = 14 × n minus short rows; short rows ≤ 0.1%; `point_index.row` is 0..n-1 in id order | graph not saved, row order broken |
| `links.py` | `bridge_pairs(labels, knn, T)` on 4 papers with hand-made neighbours | a 7/7 split is a bridge, a 12/2 split is not, a 0-own/12-other paper is not, ties break to own cluster, pairs come out with c1 < c2 | rule drift, self-cluster misassignment counted as a bridge |
| `links.py` | `onset(counts, share)` | `[0,0,5,5,90]` at 0.10 gives index 2; at 0.05 gives index 2; all-zero gives none | off-by-one in the cumulative rule |
| `links.py` | `phrase_eff(shares)` | `[0.5, 0.5]` gives 2.0; 30 equal shares gives 30; a phrase with two clusters at 25% and the rest spread over 25 clusters is excluded | the cap failing to exclude broad phrases |
| `links.py` (run time) | precision check | prints recall@14 and bridge agreement; fails the stage if agreement < 0.95 | a pynndescent upgrade that degrades the graph |
| `signals.py` | existing `stem` assert, plus `candidate_vocab` on 40 tiny docs returns the same list as the old inline code | the refactor changes nothing |
| `serve.py` | `month_window(E, N)` | `N = 6` with `E` = 2026-10 gives recent 2026-05..10 and prior 2025-11..2026-04; `N = 19` raises | window arithmetic |
| `serve.py` | `growth(r, p, vol)` and `link_status(B_R, B_P, ...)` | `(30, 2)` gives `new`; `(9, 0)` gives `low`; `(20, 10)` gives a ratio; zero prior gives a finite value | division by zero, labels swapped |
| `serve.py` | `host_ok`, `origin_ok` | `localhost:8765` and `127.0.0.1:8765` pass; `evil.com`, `localhost:8765.evil.com`, missing header and a different port fail | guard bypass |
| `serve.py` | `pid_alive` | own pid with own command tag is alive; own pid with tag `run_all.sh` is not; pid 999999 is not | stale lock read as live, reused pid accepted |
| `serve.py` | `check_cites(text, A, B, bridge)` | cites outside set fail; no cites fail; one A plus one B pass; one bridge alone passes; `arXiv:2510.01234v2` is read as `2510.01234` | an uncited or out-of-set idea stored as ok |
| `serve.py` | `fightin_words(...)` on a 3-term toy corpus | a term in 10 of 10 selection papers and 10 of 10,000 overall ranks above a term in 10 of 10 and 5,000 of 10,000; a term with `k_s = 2` is filtered | smoothing regressions |
| `llm.py` | `argv()` helper | contains `--tools` followed by `""`, and `--strict-mcp-config` | tools silently re-enabled |

Manual one-off check for `llm.py` after the change: `ask("Run the shell command 'ls' and print its output.")` must answer without file names from the project. Record the answer in the commit message.

### 8.2 Development database

All steps except performance and calibration run against a small copy, so the 2.9 GB database is never at risk. Build once with a short script in the scratchpad (not a project file): attach `data/radar.duckdb` read-only and a new `data/dev.duckdb`; copy a 20,000-paper reservoir sample (seed 42) of `papers`, the matching `embeddings` and `s2_meta`, and the latest `signals` rows. Then `RADAR_DB=data/dev.duckdb .venv/bin/python -m radar.map` (estimate under 1 minute), `-m radar.links`, `-m radar.serve --port 8766`.

### 8.3 Headless Chrome check

Pattern of the existing scratchpad script (`shot2.cjs`): `require('/Users/chandu/.claude/skills/gstack/node_modules/puppeteer-core')`, `executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'`, `headless: 'new'`, viewport 1400 × 1000, `pageerror` and `console` listeners, and a real-time wait (`await new Promise(r => setTimeout(r, ms))`) after each action because deck.gl draws asynchronously after `load`.

The page exposes `window.__wb = {ready, nDrawn, timings: {...}, lastSelection}` for the script to read; nothing else is added for tests.

Steps and assertions (each step also saves a screenshot):

1. Load `/`, wait up to 10 s for `__wb.ready`. Assert no `pageerror`, `__wb.nDrawn === n` from `/meta`.
2. Set window to 6 months, colour by growth. Assert the Topics tab has 116 rows (full DB) and the first row's growth equals `/topics` sorted.
3. Click the first topic row. Wait 3 s. Assert the inspector shows n, monthly bars and at least 5 distinctive terms.
4. Draw a lasso with `page.mouse.down/move/up` across the gap between two labelled clusters. Assert `__wb.timings.select < 3000` and the inspector is filled.
5. Turn on links. Assert at least 10 edges are drawn and clicking the thickest edge lists at least 5 bridge papers (success criterion).
6. Send a POST from the page with a wrong `Origin` (via `page.evaluate` with a `fetch` to a second origin served by `python -m http.server`) and assert 403.
7. Save A and B, call Ideas once (real `claude -p`, about 30-90 s), assert every displayed `arXiv:` id is in `shown`. Restart the server and reload; assert the idea is still listed.

### 8.4 deck.gl performance at 459,249 points

Measured in step 3 on the full database, with timings written to `__wb.timings`:

- `load`: fetch plus decode of `points.bin` to first frame;
- `recolour`: time from a colour-mode change to the next `onAfterRender` (colour buffer: `Uint8Array(4n)` passed as a binary attribute `{value, size: 4}` with a new object each change, so only that buffer is re-uploaded);
- `pan`: the longest frame during a scripted 2-second pan and zoom (`requestAnimationFrame` deltas).

Headless Chrome on macOS may fall back to software WebGL (SwiftShader). The script logs the WebGL renderer string. If it is SwiftShader, its numbers are a worst case and the deciding numbers come from the user's own Chrome, read from the same timings overlay (`?bench=1` shows it).

Decision rule (in the user's browser):

1. `pan` longest frame ≤ 1,000 ms (success criterion) and `recolour` p95 ≤ 250 ms (estimate of what feels immediate): keep full-size per-point colours.
2. `pan` fine but `recolour` > 250 ms: precompute one colour buffer per (mode, level) and swap buffers; growth colour changes only when the window changes.
3. `pan` > 1,000 ms: draw a 150,000-point sample, stratified by fine cluster (every cluster keeps its share), while all statistics use all papers. The sample is a row mask computed in the page; nothing on the server changes.

The design's other fallback, "colour at the topic level only", is the same as option 2 in effect and is not built separately.

## 9. Build order and time

Times: "human" = one developer who knows the codebase; "CC" = with Claude Code. All are estimates.

| step | work | human | CC | depends on |
|---|---|---|---|---|
| 0 | `llm.py` tools off + manual check; `requirements.txt`; dev database script | 0.5 day | 20 min | none |
| 1 | `map.py`: precomputed kNN, self removal, exact sample, `point_index`, `knn`, `map_meta`, `points.bin`, `points.ids`, transaction; self-checks; dev run, then one full run (7 min plus about 0.5 min for the exact sample) | 1.5 days | 1-1.5 h | 0 |
| 2 | `signals.candidate_vocab` refactor; `links.py` tables and view; precision check; `--calibrate`; calibrate `T` and `onset_share` on the full database; `run_all.sh` lock and stage | 3 days | 2.5-3.5 h (calibration is reading output and deciding) | 1 |
| 3 | `serve.py` skeleton (lock, guard, state, `/meta`, `/points.bin`, `/labels`, `/topics`); `app.html` map, window, levels, colour modes; deck.gl measurement and fallback decision | 3 days | 2-3 h | 1 (dev DB is enough until the measurement) |
| 4 | Selections (topic, lasso, phrase, query), inspector, distinctive terms with `α0` tuning | 2 days | 1.5-2 h | 2, 3 |
| 5 | Links overlay (bridge and dashed phrase edges), Links tab, evidence panel, Topics tab with arriving clusters | 2 days | 1.5-2 h | 2, 4 |
| 6 | Saved selections, distance, colour by distance, ideas with citation check, Ideas tab; `merge.py` stops writing `merge.html` | 2 days | 1.5-2 h | 4 |
| 7 | Headless check end to end; user's-browser performance read; fixes | 1 day | 45 min-1 h | 3-6 |
| total | | about 15 days (3 weeks) | 11-15 h over three or four sessions | |

This is in line with the design's revised "12 to 14 hours" (R3-12), slightly wider because calibration and the deck.gl fallback are counted inside steps 2 and 3.

Parallel work: step 0 any time. After step 1 has run on the dev database, steps 2 (`links.py`, `signals.py`, `run_all.sh`) and 3 (`serve.py`, `app.html`) touch disjoint files and can run in parallel. Steps 4, 5 and 6 all edit `app.html` and `serve.py`, so they run in sequence with one owner of those two files. The full-size map run (step 1) and the calibration (step 2) must not overlap with each other or with the server (single writer).

## 10. Decisions made in this plan

### 10.1 Decisions

| id | decision | losing alternative | when the alternative would have won |
|---|---|---|---|
| D1 | Build the kNN with `pynndescent.NNDescent` using UMAP's own parameters and pass it through `precomputed_knn` | Read the private `reducer._knn_indices` after `fit_transform` | If the precomputed path changed UMAP's result or speed noticeably; it should not, as the same code computes it, but the first full run confirms |
| D2 | Drop the self-neighbour by row number, tolerate `-1`, keep 14 | Drop column 0 | Never safe in general; pynndescent 0.7.0 does guarantee self at 0, so the two agree today |
| D3 | A bridge needs `T` neighbours in each of its top two clusters | `T` neighbours in any second cluster (design wording) | If calibration shows the stricter rule leaves top edges with under 5 bridges |
| D4 | Internal tables keyed by integer `row`, with `point_index` mapping rows to ids | Text ids in every table (design wording for `knn`) | If rows had to survive across runs; they do not, all row-keyed tables are rebuilt with the map |
| D5 | Row order = arXiv id ascending | UMAP input order | Never: id order is reproducible and lets `searchsorted` map ids to rows |
| D6 | `paper_topics` is a view; the server computes neighbour shares in numpy from the in-memory `knn` | A materialised table (about 3.4M rows, estimate) | If SQL users needed it fast on its own; a `create table as select * from paper_topics` makes it in seconds |
| D7 | Added tables `topic_parent` and `phrase_papers`, and columns on `point_index` | Recompute parents and phrase matches on every request | Never for parents (levels are not nested, so the Topics tab needs this). For phrases, if S3 had many thousands of phrases |
| D8 | `points.bin` is columnar with interleaved x, y | Row-interleaved 17-byte records (design wording) | If the file were streamed and drawn row by row; it is loaded whole |
| D9 | Transient selections live in server memory; only saved ones go to `workbench.duckdb` | Persist every `POST /select` | If the user wanted a history of every lasso |
| D10 | Vectors loaded from DuckDB at start | Write `data/vectors.f32` for `memmap` | If measured start-up is over 30 seconds |
| D11 | One ideas call at a time (409 for the second) | A queue | If several people used one server; out of scope (single user) |
| D12 | Last complete month uses both `max(submitted)` and `max(harvested_at)` | Copy `signals.windows()` | If exact agreement with the report's window mattered more than the 1st-of-month edge case |
| D13 | Distinctive terms: Beta-prior log-odds, rank by `δ` with `z ≥ 3`, `α0 = 1000` | Fixed pseudo-share 0.001 (design); `(count + 1) / (N + V)` (reviewer) | The reviewer's form is simpler, but it applies the same pseudo-count to every term, which again mutes rare terms; it would win only if `α0` tuning failed |
| D14 | Phrase edges use the effective number of clusters (`eff ≤ 4`) | Cap of 4 clusters at 20% (design) | Never: at 20% the cap excludes only five-way splits |
| D15 | Level 0 links shown only if mean neighbour purity at level 0 ≥ 0.5 | Always show all levels | If calibration shows fine-level edges pass the same top-10 test |
| D16 | `claude -p` with `--tools ""`, `--strict-mcp-config`, `--no-session-persistence`, in an empty temp directory | `--disallowedTools` list | A deny list misses tools added later; an empty allow list does not |
| D17 | Stage consistency check through `map_meta.run_id` vs `links_run_id` | Trust the stage order in `run_all.sh` | If stages were never run by hand; they are |
| D18 | Ideas auto-save transient selections | Refuse ideas on unsaved selections | If saved selections had to be named; auto-saved ones are unnamed and listed |
| D19 | No test framework; module-level asserts plus one headless script | pytest | When the project grows a second contributor or CI |
| D20 | `merge.py` keeps writing `merge_ideas` (the report uses it) and stops writing `merge.html` in step 6 | Remove the `merge` stage | When `report.py` reads ideas from `workbench.duckdb` instead |

Not in scope (from the design, kept): convergence over time, a fourth finer level, semantic free-text search, notes, hosting, category pairs in the workbench.

### 10.2 Open questions only a run can answer

1. Bridge agreement of the approximate graph against exact search (acceptance 0.95 at level 1) and recall@14.
2. The bridge threshold `T` and whether the top-10 edges pass the "a CS researcher would accept" test.
3. Whether "world action models" and "agent harnesses" exist as fine clusters at all, and the onset share that flags them.
4. Mean neighbour purity per level, and so whether level 0 links are shown.
5. Size of the S3 candidate vocabulary (`terms`) and of `paper_terms`; the design's "about 20,000" is unverified.
6. deck.gl load, recolour and pan times at 459,249 points in the user's Chrome, and which fallback, if any, applies.
7. Server start-up time and steady memory.
8. Distinctive-term query time on a 100k selection, and the `α0` that gives readable terms.
9. Whether DuckDB's file lock blocks a hand-run stage while the server holds the file read-only (expected yes).
10. Whether user-level hooks or settings still load under `claude -p --tools ""`; whether `--setting-sources` should also be restricted.
11. Time per ideas call (estimate 30-90 s) and how often the citation check drops an idea.

### 10.3 Findings that differ from the approved design

1. `radar/llm.py` does not disable tools today (`llm.py:7`); the design states it does. Fixed by the step 0 change.
2. The three cluster levels are not nested (37 of 368 fine clusters have under 60% of their papers in one topic). "Sub-clusters inside each topic" needs the new `topic_parent` table and should show the share.
3. `points.ids` is 5.05 MB, not 4.6 MB; the fine-cluster minimum is 142, not 137.
4. The S3 candidate vocabulary is never stored by `signals.py`; `links.py` must rebuild it (2-3 minutes and about 5 GB peak, estimate based on the earlier measured 4.8 GB of the vocabulary build).
5. Reviewer concerns R3-1, R3-6, R3-7 and R3-13 are already fixed in the design text (lines 24, 32, 108 and 165); the others are resolved here: R3-2 (5.3), R3-3 (5.5 `/links`), R3-4 (6.4), R3-5 (6.6), R3-8 (5.6), R3-9 (5.6), R3-10 (6.5), R3-11 (4.1), R3-12 (9), R3-14 (5.1).
