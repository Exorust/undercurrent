# UI design: Topic workbench page (`radar/app.html`)

Companion to the approved design `docs/designs/topic-workbench.md` (called "the design doc" below). That document defines the data, the server and the rules. This document defines what the page shows, where, and how the user moves through it. It does not change any pipeline rule.

Written 2026-11-06. Wireframe: `docs/designs/wireframe/sketch.html`, rendered to `docs/designs/wireframe/sketch.png` (first viewport) and `docs/designs/wireframe/sketch-click.png` (full page, both frames). Numbers in the wireframe are placeholders except the phrase counts, the topic names and the CS growth factor, which come from `data/radar.duckdb` on 2026-11-06.

## Terms used here

- **Window**: a run of complete months. "Last 6 months" on 2026-11-06 means May to October 2026. The **prior window** is the same number of months just before it (November 2025 to April 2026). The current partial month (November 2026) is never counted.
- **CS correction**: every growth ratio is divided by the growth of all CS papers between the prior window and the window. For the default window this factor is 113,241 / 83,358 = ×1.36 (counted from `papers` on 2026-11-06). A topic that grew ×1.36 in raw counts shows ×1.00, "flat".
- **Growth ratio (g)**: `((n_now + 1) / (n_prior + 1)) / CS factor`, as in the design doc.
- **Level**: one of the three cluster levels. The page calls them "areas" (20 coarse clusters), "topics" (116 medium clusters) and "sub-topics" (368 fine clusters). Same words everywhere on the page.
- **Bridge paper**: a paper with at least 4 of its 14 nearest neighbours (768-number vectors) in a second cluster at the same level.
- **Strength**: `n_bridge / sqrt(n1 * n2)` over the window. **Link growth**: bridge share `n_bridge / (n1 + n2)` in the window divided by the smoothed share in the prior window.
- **Phrase link**: a rising phrase with at least 10 papers and at least 20% of its papers in each of two clusters, spread over at most 4 clusters. Counted over all 36 months, not over the window.
- **Selection**: a set of paper ids. A topic, a lasso region, a phrase match, a title search and a saved selection are all selections.
- **A and B**: the two selection slots used for comparison and for ideas.

## a. The two questions as screens

The header holds a two-button switch: **What is arriving** and **How topics connect**. Each button is a preset: it sets the map layers, the colour mode and the open tab in one click. The user can change any control afterwards; the switch then shows neither button as active, and clicking a button again restores its preset. The switch is a shortcut, not a mode lock: every control works in both.

### Screen 1: "What is arriving" (the page opens here)

First view on load, with no saved state:

| control | default | why |
|---|---|---|
| question | What is arriving | The first question in the user's statement. It also needs nothing selected to be useful. |
| window | last 6 complete months (May to Oct 2026) vs the 6 before | Matches the design doc's success criterion ("pick last 6 months"). 3 months is noisy at the topic level (some topics have under 80 papers in 3 months); 12 months hides anything that started this year. |
| level | topics (116) | The design doc's premise 1: topics are the unit, areas are the grouping. 20 areas are too coarse to show arrival; 368 sub-topics are too many to label at full-map zoom. |
| colour | growth | The question is growth. |
| map layers | points on; topic nodes off; bridge links off; phrase links off | Nodes and edges cover the points. The arriving question is answered by colour. |
| bottom tab | Topics, sorted by growth, descending | The table names what the colour shows. |
| inspector | "This window" summary (no selection needed) | An empty inspector wastes 400 px on the first view. See section e, "empty selection". |
| highlight | the 5 fastest-growing topics get a numbered badge (1 to 5) at their centroid and a label even where label collision would hide it; their rows in the Topics tab carry the same numbers | The success criterion is "name the five fastest-growing topics and their flagged sub-topics without reading the report". The badges make that a 5-second task. |

Ranking for the badges: topics with at least 200 papers in the window, sorted by g. The 200-paper floor keeps a 40-to-90-paper topic from taking badge 1 on noise. The floor is shown in the Topics tab header ("ranked: topics with 200+ papers in the window").

What the user does next:
1. Reads the five badges and the Topics tab. Each topic row shows its arriving sub-topics as chips under the name (for example "world action models, new" or "agent harnesses ×3.1").
2. Clicks a badge, a row or a point. The topic becomes selection A (section d, flow 1). The inspector shows its numbers, monthly bars and its arriving sub-topics.
3. Clicks a sub-topic chip. The map zooms to that sub-topic and outlines it; the inspector switches to the sub-topic.
4. Optionally changes the window (section d, flow 6) to check whether the ranking holds at 3 or 12 months.

### Screen 2: "How topics connect"

Pressing the second button changes:

| control | value | why |
|---|---|---|
| points | grey `#5a5a5a`, opacity 0.12 | Edges and nodes must read on top. The points stay as the outline of the field. |
| topic nodes | on; size = papers in the window, fill = topic growth | Growth stays visible, so a forming link between two growing topics is easy to see. |
| bridge links | on; the 40 strongest pairs that have at least 10 bridge papers in the window | 116 topics give 6,670 possible pairs; drawing all of them is unreadable. 40 is about one edge per three topics. A slider in the layer panel sets 10 to 200. |
| edge colour | link growth on the same stepped scale as topic growth | One scale to learn. Orange edges are forming links. |
| phrase links | off | Phrase links count all 36 months and do not follow the window (design doc, `GET /links`). Showing them by default next to windowed edges would mix two time bases on one screen. The toggle label states this: "Phrase links (all 36 months)". |
| bottom tab | Links, in the design doc's sort order | |
| inspector | unchanged (keeps the current selection) | |

Highlight: the 5 pairs at the top of the Links tab sort (all "new" pairs first, then the highest link growth) are drawn at full opacity with a small number badge at the edge midpoint; other edges are drawn at 0.55 opacity.

What the user does next:
1. Hovers an edge: tooltip "Robot Manipulation Learning ↔ Diffusion and Flow Generation: 412 bridge papers in window, share 1.4% (was 1.0%), link growth ×1.46".
2. Clicks an edge or a Links row. The inspector opens that link's evidence (section d, flow 4).
3. From the evidence, presses "Set as A and B", then "Ideas between A and B".

## b. Layout and pixel budgets

All sizes are CSS pixels. The page never scrolls as a whole; each panel scrolls on its own.

### 1600 × 1000 (browser viewport; a 1600-wide external display)

```
+--------------------------------------------------------------------------------------------+
| header 44: name | [What is arriving][How topics connect] | window label + (i) | server status |
+--------------+---------------------------------------------------+-------------------------+
| left 232     | map 968 x 676                                     | inspector 400           |
| level        |  deck.gl canvas, zoom buttons top-right,           | scrolls on its own      |
| colour by    |  legend inside left column                         |                         |
| legend       |                                                    |                         |
| layers       +---------------------------------------------------+                         |
| search       | tab bar 32: Topics | Links | Phrases | Ideas       |                         |
| A / B slots  | tab body 248 (table, own scroll)                   |                         |
| saved list   |                                                    |                         |
+--------------+---------------------------------------------------+-------------------------+
```

- Header: 44 px tall.
- Left column: 232 px wide, 956 px tall, scrolls if needed. Content in order: level (3-way segmented control), colour by (4 options), legend (about 190 px tall), map layers (3 toggles and the edge-count slider), search (input plus phrase / title toggle), A and B slots, lasso button, saved selections list (fills the rest).
- Map: 1600 − 232 − 400 = 968 px wide; 1000 − 44 − 280 = 676 px tall.
- Tabs: 32 px tab bar plus 248 px body = 280 px. A 6 px drag handle on the top edge resizes it between 120 and 520 px; double-click collapses it to the 32 px bar.
- Inspector: 400 px wide, full height under the header.

### 1440 × 900 (14-inch MacBook Pro at its default scaled resolution, 1512 × 982 screen, about 1440 × 860 usable viewport in Chrome with the tab bar; the budgets below use 1440 × 900 and shrink the tabs first if the viewport is shorter)

Below 1500 px viewport width the left column collapses:

- Level, colour and window move into the header as compact dropdowns. The header grows to 2 rows, 72 px total: row 1 is name, question switch, search box (240 px), server status; row 2 is window, window label, level, colour, layers dropdown, legend strip.
- The legend becomes a horizontal strip, 320 × 20 px, in header row 2.
- A and B slots and the saved list move to the top of the inspector, as one 64 px row ("A: [chip ▾]  B: [chip ▾]  Saved ▾").
- Lasso moves to a button on the map, top-left.
- Inspector: 380 px.
- Map: 1440 − 380 = 1060 px wide; 900 − 72 − 240 = 588 px tall.
- Tabs: 32 + 208 = 240 px by default.

Below 1200 px wide (not a target, but it must not break): the inspector becomes an overlay drawer, 380 px, opened by selecting anything and closed with Escape.

## c. Visual encoding

Background `#111` (as `report/merge.html`). Panels `#161616`, borders 1 px `#2a2a2a`. Text `#dddddd` primary, `#9a9a9a` secondary.

### Growth colour scale (stepped, 7 steps, on the CS-corrected ratio g)

Stepped, not continuous. The arriving rule in the design doc is a threshold (2×), so a hard step at 2× makes "flagged" and "orange" mean the same thing, and a 7-step legend can carry a count per step. The hues follow the current scheme (blue shrinking, grey flat, orange growing).

| step | g range | hex | legend text |
|---|---|---|---|
| 1 | g < 0.50 | `#2f62b8` | shrinking fast (under ×0.5) |
| 2 | 0.50 ≤ g < 0.71 | `#5a86c8` | shrinking |
| 3 | 0.71 ≤ g < 0.90 | `#8ea3c0` | slightly shrinking |
| 4 | 0.90 ≤ g < 1.11 | `#8c8c8c` | flat (×0.9 to ×1.1) |
| 5 | 1.11 ≤ g < 1.41 | `#c8a57a` | slightly growing |
| 6 | 1.41 ≤ g < 2.00 | `#e3883a` | growing |
| 7 | g ≥ 2.00 | `#ff6a1a` | growing fast (×2 or more) |
| new | rule "new" from the design doc | `#ff6a1a` plus the text badge `new` | new in this window |
| no data | fewer than 30 papers in both windows | `#333333` | too few papers to rate |

Steps are symmetric in log2: ±0.15, ±0.5, ±1.0 (×1.11, ×1.41, ×2.0 and their inverses, rounded). "New" uses the top colour because a new cluster is the strongest case of growth; the badge, not a hue, tells it apart, so the legend stays 7 colours. "No data" is darker than "flat" so it recedes into the background and is never read as "flat".

Where the scale is used: points (colour by growth: each point takes its cluster's step at the current level), topic nodes, bridge edges (link growth), the growth column in both tables, the monthly bars in the inspector (bars are neutral grey; only the growth number carries colour).

Other colour modes:
- **Topic**: 20 categorical colours, one per area, all points of an area share it; topics inside an area vary in lightness ±10%. Used for orientation only; no legend beyond area names on hover.
- **Date**: lightness ramp on the paper's month, `#2b2b2b` (Nov 2023) to `#f0f0f0` (Oct 2026), grey only, so it is never confused with growth.
- **Distance to A**: lightness ramp on the cosine distance of the point's sub-topic centroid to A's centroid, `#f0f0f0` (nearest 5% of sub-topics) to `#2b2b2b` (farthest 50%). A's own points get A's outline (below). Disabled with the tooltip "Set A first" when A is empty.

### Points (459,249)

- deck.gl `ScatterplotLayer`, `radiusUnits: 'pixels'`, radius 1.0 px at full-map zoom, growing with zoom to a cap of 4 px (`radiusMinPixels: 0.8`, `radiusMaxPixels: 4`).
- Opacity 0.35 in colour by growth, topic and date; 0.12 grey in "How topics connect".
- No stroke. No picking on hover at full-map zoom (cost); picking on click only, then hover picking turns on above 4× zoom.
- Dimming when a selection exists: points outside A and B drop to opacity 0.08; points inside keep their colour at 0.6. Selections are outlined (below), never recoloured, so growth stays readable inside them.

### Topic nodes (when the node layer is on)

- Circle at the cluster centroid. Radius in px: `3 + 10 * sqrt(n_window / n_window_max)`, where `n_window_max` is the largest cluster at the current level in the current window. Range about 4 to 13 px at the topic level.
- Fill: growth step. Stroke 1 px `#000`.
- Selected A or B: 3 px ring in the A or B colour plus a 10 px letter tag ("A" or "B") outside the ring.

### Bridge edges

- Straight line between centroids, drawn under the nodes.
- Width in px: `1 + 7 * min(strength / s95, 1)`, where `s95` is the 95th percentile of strength among the edges drawn in the current level and window. Range 1 to 8 px; the cap stops one or two very strong neighbour pairs from flattening the rest.
- Colour: link growth step. A pair labelled "new" gets the top colour and a `new` text tag at the midpoint.
- Only pairs with at least 10 bridge papers in the window are drawn, because pairs under 10 have no growth value (design doc) and so have no colour.
- Opacity 0.55, full for hovered, selected and the 5 badge pairs.

### Phrase edges

- Dashed 4 px on, 3 px off (deck.gl `PathStyleExtension`), 1.5 px wide, colour `#d0d0d0`, opacity 0.7.
- No growth colour, because phrase links have no window. Hover shows the phrase list, for example "shared rising phrases: world action models (41 / 63 papers)".
- If a pair has both a bridge edge and a phrase edge, the dashed line is offset 4 px to one side so both stay visible.

### Selection colours

- **A**: `#e05cf5` (magenta). **B**: `#3fd68a` (green). Neither appears in the growth scale or the grey ramps. Both always come with the letter, so the meaning never depends on colour alone.
- Topic or sub-topic selection: outline = convex hull of its points, 2 px, in the slot colour, with a 15% fill of the same colour. Lasso: the polygon itself. Phrase or title search: no hull (the papers are scattered); points drawn 2 px with a 1 px ring in the slot colour.
- An unassigned current selection (before it is put in A or B) uses a white 2 px outline.

### Labels per zoom level

deck.gl `TextLayer` with `CollisionFilterExtension`, priority = papers in window; badge topics and selected clusters have top priority.

| map zoom (1 = whole map fits) | labels drawn | size |
|---|---|---|
| under 2× | 20 area names (`#e6e6e6`, 14 px, weight 600) plus the 5 badge topics in arriving view (12 px) | at most 25 labels |
| 2× to 5× | topic names (12 px) for the current level, collision-filtered | about 30 to 50 visible |
| over 5× | sub-topic names (11 px) | about 30 to 50 visible |

Label text has a 3 px `#111` halo. The level control changes colour, nodes and tables; labels follow zoom so the map is readable at any level setting.

## d. Interaction flows

### Flow 1: select a topic by click

1. Click a point, a node, a badge or a Topics row.
2. The page selects the clicked point's cluster at the current level. It draws the hull in white and sends `POST /select {rows}` with that cluster's row indices (the page knows them from `points.bin`).
3. If A is empty, the selection goes into A (magenta). If A is set and B is empty, it goes into the current slot only; the inspector shows "Set as B" and "Replace A" buttons. Rationale in section h, decision 5.
4. Inspector fills in this order: name and level ("Topic, in area Robot Manipulation and RL"), papers in window and prior, growth with its window label, monthly bars (36 months, window shaded), arriving sub-topics, distinctive phrases, top cited, newest, links to other topics.
5. Clicking empty map space does nothing. Escape clears (section g).

### Flow 2: lasso a region

1. Press L or the lasso button. Cursor becomes a crosshair; the map stops panning.
2. Press and drag to draw a freehand polygon (white line). Release to close it.
3. The browser runs point-in-polygon on the 459k positions (one pass, typically under 150 ms) and sends the row indices.
4. The selection gets the label "Lasso, 1,204 papers, mostly Robot Manipulation Learning (62%)" from `paper_topics` shares. It enters the slot as in flow 1, step 3.
5. A lasso with under 20 points shows "Only 7 papers: too few for growth or phrases. Drag a larger area." and does not call the server.

### Flow 3: set A and B

1. Make a selection (flow 1, 2 or 7).
2. Press "Set as A" or "Set as B" in the inspector, or the keys A or B.
3. Both outlines stay on the map. The inspector gets an "A vs B" block at its top: cosine distance between centroids, overlap count, bridge papers between them (count, first 5 titles), and the Ideas button.
4. Swapping: a ⇄ button between the two slot chips.
5. A saved selection can be loaded into a slot from the slot's dropdown.

### Flow 4: open a link's evidence

1. Click an edge on the map or a row in the Links tab.
2. The edge is drawn at full opacity, 2 px wider; both end nodes get white rings.
3. The inspector shows the evidence view with a "← back" link at the top that returns to the previous selection view. Contents, in order:
   - title "Robot Manipulation Learning ↔ Diffusion and Flow Generation" and a status tag (new / forming / steady / fading);
   - numbers block: bridge papers in window (raw count), in prior window, share now and before, link growth, strength;
   - one-line definition with a "how this is measured" disclosure (section f);
   - bridge papers: 10 per page, sorted by citations, each with title, arXiv id link, month, and the neighbour split ("6 of 14 neighbours in Diffusion"); "newest first" toggle;
   - shared rising phrases: each with paper counts on each side, marked "all 36 months";
   - buttons: "Set as A and B" and "Show bridge papers on map" (draws them 3 px white over the dimmed map).
4. Status tags: **new** = design doc's rule (10+ bridges now, under 3 before); **forming** = link growth ≥ ×1.41; **fading** = ≤ ×0.71; **steady** = between. The thresholds reuse the colour steps, so tag and edge colour agree.

### Flow 5: change the window

1. The window control in the header is a dropdown: last 3, 6 (default), 12 months, plus "custom", which opens two month pickers (start, end) limited to complete months.
2. On change, the page calls `GET /topics` and `GET /links` for the new window and keeps the selection. Points recolour in one pass. The window label in the header updates first, before the data arrive, and the tables show a 1 px progress line under the tab bar while loading.
3. The A and B inspector numbers re-request. Ideas are not regenerated; existing ideas keep the window they were made with in their header.
4. Keyboard: `[` and `]` step through 3 / 6 / 12.

### Flow 6: search a phrase and see its papers on the map

1. Press `/` or click the search box. Type "world action models". A toggle under the box chooses **phrase** (title or abstract contains the text, literal) or **title** (title contains, case-insensitive). Default: phrase.
2. While typing, a dropdown suggests matching rising phrases from the Phrases tab with their counts (for example "world action models, 200 papers, ×13.0" and "world-action models, 137 papers, ×22.0"), so the user sees hyphen variants before searching.
3. Enter sends `POST /select {phrase}`. Matching papers that are on the map are drawn 2 px with a slot-colour ring; all other points dim. The map does not zoom (the papers can be spread out); a "Zoom to matches" button fits their bounding box.
4. The inspector shows the selection with the label `phrase: "world action models"` and the note "literal match on title and abstract; 3 of 200 papers are not on the map (not embedded)".

### Flow 7: ask for ideas between A and B

1. A and B must both be set; otherwise the button is disabled with the tooltip "Set A and B first".
2. Press "Ideas between A and B". The button area shows what will be sent before the call: "Sends 28 papers to the model: 8 bridge papers, 10 from A (5 most cited, 5 newest), 10 from B. Usually 10 to 40 seconds." and a "Send" button. One extra click, because the call costs subscription quota and 10 to 40 seconds.
3. While waiting: the button is replaced by "Asking the model … 14 s" with a seconds counter and a "Stop waiting" link. The rest of the page stays usable. A second request for the same A and B is blocked while one runs.
4. "Stop waiting" stops the page from waiting; the server finishes the call and stores the result, which then appears in the Ideas tab. The link text says so: "Stop waiting (the result will still be saved)".
5. On success: ideas appear in the inspector, each with its cited arXiv ids as links, and each id is tagged A, B or bridge. The Ideas tab gets a new row. If the user has moved to another selection, a 6-second toast says "3 ideas ready for Robot Manipulation Learning ↔ Diffusion and Flow Generation. Open".
6. After 90 seconds with no answer, the counter turns grey and reads "Still waiting (90 s). The model may be slow or stuck." The page keeps waiting until the server times out.

### Flow 8: save a selection

1. With a selection in the inspector, press "Save" (or Cmd+S, which is intercepted).
2. An inline field opens under the title with the current label as the default name; Enter saves.
3. `POST` stores paper ids, the lasso polygon if any, the window, and the map run id (see section h, approved-doc change 1).
4. The saved list (left column, or the "Saved" dropdown below 1500 px) gets the new item at the top, with its paper count and date.

### Flow 9: reopen a saved selection after a pipeline rerun

1. Click a saved item, or choose it in an A or B dropdown.
2. The server maps its paper ids to the current rows. Cluster ids are not used.
3. The inspector shows the selection with a one-line restore note:
   - all papers found, same map run: no note;
   - same papers, new map run: "Restored 1,204 papers. The map layout changed since you saved this on 2026-11-06, so the old lasso outline is not shown; the papers are highlighted instead.";
   - some papers missing: "Restored 1,198 of 1,204 papers; 6 are no longer on the map."
4. The label is recomputed: "Saved: my home field (now mostly Efficient LLM Inference and Compression, 71%)".
5. Stats are recomputed for the current window, not the saved one; the saved window is shown in grey under the title ("saved with window May to Oct 2026").

## e. States

| state | where | what the user sees |
|---|---|---|
| Loading `points.bin` (about 8 MB) | map area | Centered block: "Loading map points: 3.1 of 8.2 MB" with a 240 px bar, filled from the streamed `fetch` body. The header, left column, Topics tab and inspector summary load in parallel from `/topics` (small JSON) and are usable before the points arrive. `points.ids` (4.6 MB) is not loaded by the page; it is not needed in the browser (section h, approved-doc change 2). |
| Points loaded, first draw | map | Draw once, then measure frame time; if the first recolour takes over 1 s, show a one-time line under the legend: "Large map: colour changes take about 1 s on this machine." |
| Empty selection (first view) | inspector | "This window: May to Oct 2026 vs Nov 2025 to Apr 2026", CS papers in window (113,241) and prior (83,358), the 5 badge topics with their growth, and up to 10 arriving sub-topics across all of CS (name, parent topic, onset month, growth or `new`). A grey line at the bottom: "Click a topic, press L to lasso, or / to search." |
| Selection with 0 papers on the map | inspector, map | From a phrase or title search that matched only unembedded papers: "12 papers match. None are on the map (no embedding). Stats and paper lists are below; map highlight is not available." Growth and paper lists still show. |
| Search with 0 matches | inspector | `No paper title or abstract contains "wolrd action models". Phrase search is a literal match: check spelling or try a variant.` plus up to 3 closest rising phrases by edit distance. |
| Selection under 30 papers in either window | inspector | Growth shown as "too few papers to rate (12 now, 4 before)", grey; paper lists and phrases still shown; distinctive phrases need 3 papers per term, so very small selections show "too few papers for distinctive phrases". |
| Pair with fewer than 10 bridges | Links tab, evidence | Row in the bottom group "Pairs with fewer than 10 bridge papers (growth not computed)", collapsed by default with its count. Evidence view shows the bridge papers and the line "7 bridge papers in this window: too few to compute link growth. Strength is shown." No edge on the map. |
| "New" pair | Links tab, map, evidence | Tag `new` (top orange, black text) in place of a growth number. Tooltip: "New link: 10 or more bridge papers in this window, fewer than 3 in the prior window." |
| "New" sub-topic | Topics tab chip, inspector, map label | Chip text "world action models · new". Tooltip: "New sub-topic: started inside this window (onset Jun 2026), 30 or more papers now, fewer than 5 before." The two tooltips are worded differently on purpose, because the two "new" rules differ. |
| Language model failure | inspector Ideas block | "The model call failed: claude -p exited with code 1 (first line of its error). Nothing was saved." plus "Try again". Red is not used; text `#dddddd` on `#2a1a1a`. |
| Idea dropped by the citation check | inspector Ideas block, Ideas tab | Under the ideas that passed: "1 idea dropped: it cited arXiv:2405.01234, which was not among the 28 papers sent, and the retry also failed." If all ideas drop: "No idea passed the citation check (3 dropped). This happens when the model cites papers it was not shown." plus "Try again". |
| Server not reachable (it stopped after the page loaded) | full-width banner under the header, 32 px | "Server not reachable at localhost:8765. Start it with `.venv/bin/python -m radar.serve`. The map still pans and colours; selections, tables and ideas need the server." Retries every 5 s; the banner disappears on success. If the page was opened as a file, not through the server, the same banner with "Open http://localhost:8765 instead of the file." |
| Server refused a request (403 from the Host or Origin guard) | banner | "The server refused this request (wrong address). Open the page at http://localhost:8765, not 127.0.0.1 through another tab or a proxy." |
| Stale lock removed at start | footer of the left column (or the Saved dropdown below 1500 px), grey 11 px | "Server started 14:02. Removed stale lock data/run.lock (process 4411 was not running)." Shown for the session only. The live-lock case never reaches the page: the server refuses to start and prints "data/run.lock names a running pipeline (pid 4411, run_all.sh). Wait for it to finish, or delete the file if you are sure it is not running." |
| Selection request slow (over 3 s) | inspector | The previous content stays, greyed to 50%, with a 1 px progress line at the top; after 3 s the text "Still computing (large selection: 18,204 papers)". |

## f. Honesty elements

Each statement is placed where its number is, in one line, with the long form behind a disclosure.

| element | where | text |
|---|---|---|
| Window label | header, always visible | "May to Oct 2026 vs Nov 2025 to Apr 2026 · corrected for CS growth (×1.36) · last complete month Oct 2026 (i)". The (i) opens a 360 px popover with every rule in this list. |
| CS correction | same header line; also as a footnote under each growth number in the inspector | "×2.1 vs CS" in tables; inspector: "×2.1 after correction (raw ×2.9; all CS grew ×1.36)". Showing the raw value once stops a reader from doubting the corrected number. |
| Bridge definition | Links tab header line and evidence view | "Bridge paper: 4 or more of its 14 nearest neighbours in the other topic. Width = strength, bridges / √(papers₁ × papers₂). Growth = change in bridge share vs the prior window." |
| Phrase-link rule | Links tab, header of the phrase-links section; phrase-link toggle tooltip; evidence view next to shared phrases | "Phrase links use all 36 months and do not follow the window. A phrase links two topics when 10+ papers and 20%+ of its papers are in each, and it spans at most 4 topics." |
| Backtest line | Phrases tab, one grey line under the tab bar | "Track record: in backtests, 36% of the top-50 rising phrases then grew 1.5× faster than CS, vs 27% of all candidate phrases. A rising phrase is a lead, not a forecast." |
| Ranking floor | Topics tab header | "Ranked: topics with 200+ papers in the window." |
| Category pairs | not shown | As in the design doc. |
| Ideas provenance | each idea | Header "Generated 2026-11-06 14:10 from 28 papers (window May to Oct 2026)"; every cited id is a link and is tagged A, B or bridge. |

The popover and disclosures are closed by default. The only always-visible honesty text is the header line, the three tab header lines and the inspector footnotes, about 5 lines in total.

## g. Keyboard and accessibility

Focus order (Tab): header (question switch, window, info) → left column top to bottom (level, colour, layers, search, A, B, lasso, saved) → map canvas → tab bar → tab body (rows) → inspector (top to bottom). Below 1500 px the header controls replace the left column in the same order.

Keys:
- `Esc` clears one layer per press, innermost first: popover or dropdown → lasso in progress → evidence view (back to selection) → current unassigned selection → B → A.
- `/` focus search. `L` lasso. `A`, `B` put the current selection in that slot. `1`, `2` question switch. `[`, `]` window 3 / 6 / 12. `+`, `-` and arrow keys zoom and pan when the map has focus. `0` fits the whole map.
- In tables: up and down move the row focus, Enter selects the row (topic or link).
- Keys are inactive while a text field has focus.

Map focus: the canvas has `tabindex="0"`, a 2 px white focus ring, and an `aria-label` that states the current view ("Map of 459,249 papers, coloured by growth, May to Oct 2026"). Everything the map shows is also in a table (Topics, Links), so the map is never the only route to a number.

Colour-blind check: the scale uses blue against orange with a neutral grey middle, which stays separable for protanopia and deuteranopia (the two most common types) because it varies in blue-yellow and in lightness. Tritanopia confuses blue with grey more. Fallback: a "Lightness only" checkbox under the legend switches growth to a 7-step lightness ramp (`#2b2b2b` … `#f2f2f2`, shrinking dark, growing light) with the step number printed in the legend; "new" keeps its text badge. A and B (magenta, green) differ in lightness and always carry their letter. Check during build with Chrome DevTools "Emulate vision deficiencies" on the screenshot of the default view.

Text sizes: 13 px body in the inspector and tables, 12 px table secondary text and legend, 11 px minimum anywhere (sub-topic map labels, footnotes). Contrast on `#111`: primary `#dddddd` about 13:1, secondary `#9a9a9a` about 6.5:1; no text below `#8a8a8a`.

Motion: map zoom-to-selection animates 300 ms; off when `prefers-reduced-motion` is set.

## h. Decisions made in this plan

Each decision, the losing alternative, and when the loser would have won.

1. **Question switch as presets in the header.** Lost: no switch, the user sets layers and colour by hand. The loser wins if the user only ever uses one question; then the switch is noise. Kept because the user's goal is literally two questions, and each needs 4 settings changed together.
2. **Default window 6 months, level topics (116), colour growth, links off.** Lost: open on the links view. The links view wins if the user's first question each visit is "how do topics connect"; the success criterion lists the growth task first.
3. **Stepped 7-colour scale with a hard step at ×2.** Lost: continuous scale (current `merge.html`). Continuous wins when comparing topics with close values on the map; the tables carry exact numbers, so the map only needs bins, and bins can carry counts in the legend.
4. **Selections are outlined, never recoloured; A magenta, B green.** Lost: paint selected points in the slot colour. Painting wins when the user only cares where a selection is, not how it grows; outlining keeps growth readable inside the selection.
5. **First click fills A; later clicks do not overwrite A without a button press.** Lost: every click replaces the current selection and A/B are only set by button. The loser wins if most sessions never use A and B; then auto-filling A is surprising. Kept because the first click of a session is almost always "what is this" and costs nothing if A is replaced later.
6. **Only edges with 10+ bridges drawn; default 40 edges.** Lost: draw all pairs above a strength threshold. The threshold wins once Part 1 calibration gives a meaningful strength cut-off; then the count could vary by window. 40 is a readability choice until then.
7. **Phrase edges off by default, labelled "all 36 months".** Lost: on by default, as in the design doc sketch. On wins if phrase links are few (under 20) and the user reads them as the main evidence; check after Part 1 how many pairs pass the phrase rule.
8. **Colour by "category" removed from v1** (4 modes: growth, topic, date, distance to A). Lost: keep all 5 from the design doc sketch. Category colouring wins if the user wants to see arXiv's own labels against the clusters; 40 primary categories cannot be told apart by colour, and the design doc already keeps category pairs out of the workbench.
9. **Badges on the 5 fastest-growing topics with a 200-paper floor.** Lost: no badges, colour only. Colour only wins if more than 5 topics are worth reading each month; the Topics tab still lists all 116.
10. **Ideas need a confirm click showing what will be sent.** Lost: one-click send. One click wins if the call is cheap and fast; at 10 to 40 s and subscription quota, the extra click is cheap insurance against a misclick.
11. **Empty inspector shows a window summary.** Lost: blank inspector with a hint. Blank wins only on screens too narrow for an inspector.
12. **Dark wireframe** (a deviation from a white wireframe): the colour stops can only be judged on the real `#111` background.
13. **Left column collapses into a 2-row header below 1500 px.** Lost: keep three columns and narrow the map to about 830 px. The narrow map loses more than the 28 px extra header costs.

### Changes the approved design doc should make

1. **Lasso polygon after a rerun.** The design doc stores "the lasso polygon for display", but 2-D coordinates change on every UMAP run, so an old polygon would outline the wrong region. Store a map run id (for example the `points.bin` file hash) with each saved selection and hide the polygon when it differs.
2. **`points.ids` is not needed in the browser.** The browser sends row indices and the server maps them. Hover titles would need a small `GET /paper?row=` endpoint; without it, hover shows only cluster, month and category. Recommend adding the endpoint and not loading `points.ids` in the page.
3. **`GET /topics` must return per-cluster growth for the requested level** (all 368 sub-topics when colouring at the finest level), plus the CS factor and the window's paper totals for the header label and the empty-inspector summary. Add `level=` to `/topics`.
4. **The sketch's "colour by: category" should be dropped** (decision 8), and **phrase edges should be off by default** (decision 7).
5. **Links tab detail (concern R3-3):** state that the under-10 group is collapsed by default and that the map draws no edge for it.
6. **Two "new" rules (concern R3-4):** state that the page uses different tooltip text for a new link and a new sub-topic.
7. **Window-following vs whole-corpus edges (concern R3-10):** the page labels the phrase-link toggle and section "all 36 months"; the design doc should say the same in the `/links` description and the honesty section.
8. **Status tags for links** (new / forming / steady / fading at ×1.41 and ×0.71) are a page rule; record them in the design doc so the report and the page use the same words.

### Open questions for the user

1. Is 6 months the window you would pick first, or do you think in quarters (3 months)?
2. Should the first click fill A automatically (decision 5), or do you want A and B set only by button?
3. 40 edges by default: after Part 1, look at the map at 20, 40 and 80 edges and pick.
4. Do you want hover titles on points (needs `GET /paper?row=`), or is cluster, month and category on hover enough?
5. Should the home field get a permanent slot (a star marker as in `merge.html`) or stay one saved selection among others, as the design doc says?
