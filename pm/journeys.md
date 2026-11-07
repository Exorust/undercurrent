# Journeys

Each journey crosses several features from first contact to the user's real goal. Every step has an observable expected result. Walk with headless Chrome (puppeteer script) plus `curl` and SQL where noted.

## J1 — Researcher: "What is arriving this half-year?" (the first question)
Crosses: F8, F10, F11, F17, F18, F20, F21, F24, F27
| step | action | expected result (observable) |
|---|---|---|
| 1 | `.venv/bin/python -m radar.serve`, open the printed URL | page loads; header shows `What is arriving` active and the window label `May to Oct 2026 vs Nov 2025 to Apr 2026 · corrected for CS growth (×1.36) · last complete month Oct 2026` |
| 2 | wait for points | 459,249 points coloured by growth; 5 numbered badges on the map |
| 3 | read the inspector without clicking | `This window` summary with CS totals, the 5 badge topics with growth, up to 10 arriving sub-topics across CS |
| 4 | write down the five badge topics and their chips | the same five names appear in rows 1–5 of the Topics tab with the same chips (success criterion: named without reading the report) |
| 5 | click badge `1` | the topic becomes A; inspector shows `×g after correction (raw ×r; all CS grew ×1.36)`, monthly bars, arriving sub-topic chips |
| 6 | click an arriving chip | the map zooms to the sub-topic; inspector reads `Sub-topic, in topic <parent> (share NN%)`; its window share is at least 1.6 × the corpus share (the tooltip or inspector line shows the share or the ×g growth; onset is informational only — PM correction 2026-11-07, window-share rule) |
| 7 | press `]` (12 months) | the badge set changes or stays; the header label changes to `Nov 2025 to Oct 2026 vs …`; the inspector numbers re-request and update |
| 8 | press `[` (back to 6) | the original five badges return |

## J2 — Researcher: "How do topics connect, and what is the evidence?" (the second question)
Crosses: F4, F12, F20, F22, F23, F28
| step | action | expected result (observable) |
|---|---|---|
| 1 | from the default view press `2` | points grey; 116 nodes; 40 edges; Links tab active |
| 2 | read the five badge edges | the five pairs match the top five rows of the Links tab (all `new` first, then by link growth) |
| 3 | judge the ten thickest edges | all ten pairs are between fields a CS researcher accepts as neighbours (list pasted into evidence); none joins unrelated areas (success criterion) |
| 4 | hover the thickest edge | tooltip with bridge count, shares and link growth equal to `GET /links` |
| 5 | click it | evidence view: status tag, numbers block, at least 5 bridge papers listed with neighbour splits (success criterion), shared phrases or `no shared rising phrases` |
| 6 | press `Show bridge papers on map` | white 3 px points appear between the two topics |
| 7 | press `Set as A and B` | A and B outlines; A vs B block shows distance, overlap, bridge count equal to `GET /distance` |
| 8 | toggle `Phrase links (all 36 months)` | dashed edges appear; hovering one lists phrases with per-side counts; the label states the 36-month base |
| 9 | press `1` | preset 1 restored; A and B still set |

## J3 — Researcher: lasso a gap, compare it with the home field, ask for ideas, keep them
Crosses: F13, F14, F15, F16, F25, F26, F28, F29, F30
| step | action | expected result (observable) |
|---|---|---|
| 1 | search phrase `metal kernel`, Enter, press `A`, `Save` as `home field` | saved list shows `home field · N papers · <date>`; A outline on the scattered matches |
| 2 | press `L`, lasso the gap between two robot-learning and diffusion clusters | label `Lasso, 1,204 papers, mostly <topic> (62%)` in under 3 s (success criterion); inspector shows stats, top papers, distinctive phrases |
| 3 | press `B` | A vs B block: cosine distance, overlap, bridge papers between the home field and the lasso |
| 4 | choose colour `distance to A` | greys by distance to the home field; the lasso region's shade matches its `cos_dist` rank |
| 5 | press `Ideas between A and B`, read the confirm text, press `Send` | counter runs; within 120 s ideas appear; every cited `arXiv:ID` is a link tagged `A`, `B` or `bridge` and belongs to the 28 shown papers (checked by comparing with the `shown` list in the response) |
| 6 | restart the server, reload the page | Ideas tab lists the row; clicking it shows the same ideas (success criterion: visible after a restart) |
| 7 | `select count(*) from ideas where status='ok'` in `data/workbench.duckdb` | equals the ideas shown |

## J4 — Operator: monthly rerun without losing saved work
Crosses: F1, F2, F3, F4, F5, F6, F7, F8, F14, F30
| step | action | expected result (observable) |
|---|---|---|
| 1 | with the server running, `./run_all.sh` | refused: `data/serve.pid names a running server (pid N, radar.serve). Stop it first.`; exit 1 |
| 2 | stop the server (`kill -TERM`), `./run_all.sh` (on the dev database for the walk) | all stages run; stderr shows `knn … 14 neighbours`, `T = <int>`, `knn check … agrees NN%`, `links_run_id = <new run id>`; `data/run.lock` absent afterwards |
| 3 | `select run_id, links_run_id from map_meta order by created desc limit 1` | both equal the new run id; the previous row still present |
| 4 | start the server, open the page | header shows the new last complete month if the month rolled; stale-lock note absent |
| 5 | click the saved `home field` | restored by paper ids; note `The map layout changed since you saved this on <date>, so the old lasso outline is not shown` for lasso saves; `Restored N of M papers` if any missing |
| 6 | Ideas tab | the ideas from J3 are still listed with their A and B names |
| 7 | `GET /meta` | `knn_bridge_agree >= 95` or STATUS records the measured value and the decision taken |

## J5 — Researcher on the laptop screen, keyboard only
Crosses: F20, F21, F24, F33, F34
| step | action | expected result (observable) |
|---|---|---|
| 1 | open the page at 1440 × 900 | 2-row header; A/B/Saved row at the top of the inspector; lasso button on the map; no clipped text |
| 2 | Tab through the header controls to the Topics table | the focus ring is visible on each control in the documented order |
| 3 | ↓ ↓ ↓ Enter | the third topic is A; the inspector fills |
| 4 | `2`, Tab to the Links table, ↓ Enter | the top link's evidence view opens |
| 5 | Esc | back to the A selection view |
| 6 | Esc, Esc | A cleared; inspector shows the window summary |
| 7 | resize to 1100 × 800, click a topic | the inspector opens as a drawer; Esc closes it |
