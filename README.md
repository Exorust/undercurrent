# undercurrent

A map of every computer-science paper on arXiv since November 2023 (459,249 papers), built to answer two questions:

1. **What is arriving?** Which topics and sub-topics are growing faster than computer science as a whole over the last 3, 6 or 12 complete months.
2. **How do topics connect?** Which pairs of topics share papers whose nearest neighbours sit in both, and what those bridge papers are.

Public snapshot: <https://charoori.com/undercurrent> (refreshed when a month completes).

## How it works

```
harvest (arXiv) -> enrich (SPECTER2 embeddings via Semantic Scholar) -> signals (rising phrases)
  -> map (15-neighbour graph, UMAP, HDBSCAN at three levels) -> links (bridges, monthly counts, phrases)
  -> serve (local workbench, http://localhost:8765)   or   export (static snapshot in site/)
```

- **Growth** is the change in a topic's paper count between the window and the window before it, divided by the growth of all computer science in the same period.
- **Arriving sub-topic**: at least 30 papers in the window, and a share of its own papers inside the window at least 1.6 times the corpus share.
- **Bridge paper**: at least 5 of its 14 nearest neighbours fall in each of two topics. Link strength = bridges / sqrt(n1 × n2).

## Run it locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run_all.sh                       # full pipeline into data/radar.duckdb (about 20 min after harvest; 8.6 GB peak memory)
.venv/bin/python -m radar.serve    # workbench at http://localhost:8765
```

The local workbench adds what the static snapshot cannot: ideas between two selections (via `claude -p`), saved selections, and search over full abstracts.

## Static snapshot and the weekly job

```sh
.venv/bin/python -m radar.export   # writes site/ (the page plus pre-computed JSON for every view)
tools/weekly.sh                    # harvest + embed; on a new complete month: map, links, export, deploy
```

`tools/weekly.sh` runs from a macOS launchd agent every Sunday at 04:00 (see `tools/com.charoori.undercurrent.weekly.plist`).

## Repository map

| path | what |
|---|---|
| `radar/` | pipeline stages, the server (`serve.py`), the page (`app.html`), the static export |
| `tools/` | dev database builder, headless-Chrome check (`shot.cjs`), weekly job |
| `docs/designs/` | design, architecture and UI plans |
| `pm/` | features, acceptance flows and journeys used to build and check the tool |
| `DESIGN.md`, `BACKTEST.md` | the original radar design and the backtest of the rising-phrase signal |
