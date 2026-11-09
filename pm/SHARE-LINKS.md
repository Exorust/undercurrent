# Share links: design (draft, 2026-11-09, not built)

Goal: every object on the map has a "Copy link" action. Opening the link shows that object: the map flies to it, it is selected, and its panel is open. Works on the static site with no server and no login.

## The constraint that shapes everything

The weekly job reruns `radar.map` (UMAP + HDBSCAN), so **topic numbers and point coordinates change every week**. Today's `?topic=1-34` names cluster 34 of this week's run; next week cluster 34 is a different topic. A share link must name things by something that survives a rebuild:

| Object | Stable key in the URL | How the page finds it after a rebuild |
|---|---|---|
| paper | arXiv id `2405.12345` | direct lookup |
| topic | level + the topic's anchor paper (its medoid, the paper nearest the topic centre) | the topic that contains that paper now |
| link (topic pair) | two anchor papers | the two topics that contain them |
| phrase | the phrase text | direct lookup in the phrase list |
| lasso selection | polygon + snapshot id, plus up to 8 sample paper ids from inside it | same snapshot: the polygon. Newer snapshot: the topics holding most of the sample papers, with a note |
| A·B pair | two selections, each as above | as above |
| idea | precomputed idea: id = short hash of (two anchor papers + idea title). Idea written by someone's own agent: the idea text itself, compressed, in the URL fragment | precomputed: look up in `ideas.json`; own idea: decode and show it, with the A·B pair it was made for |

Window, level, colour and filters stay as today's query parameters (`win`, `level`, `colour`, filter keys), so a link also restores the view.

## URL shape

`charoori.com/undercurrent/map/?s=2026-11-09&win=6#t=1.2405.12345`

- `s` = the snapshot date the link was made on (from `meta.json` `generated`).
- Object after `#`: `p=` paper, `t=` topic, `l=` link, `ph=` phrase, `sel=` selection, `ab=` pair, `i=` idea. The fragment is never sent to a server, so an idea's text stays private between the sender and receiver.
- Size: topic, link, phrase and paper links are under 100 characters. A lasso is about 200. An own-agent idea, deflate-compressed and base64url-encoded, is about 1–3 KB, which every browser accepts.
- Old links (`?topic=1-34`, `?link=…`) keep working for the snapshot they were made on; the export adds the anchor paper to each topic so the old form can be upgraded.

## Behaviour

1. One "Copy link" button in every panel header (topic, link, phrase, paper, selection, pair, idea) and in the right-click menu on the map. It writes the URL to the clipboard and shows "Link copied".
2. On load, one router reads the fragment, resolves the stable key, then runs the same code a click would run (select, fly to, open the panel). No second code path.
3. If the link's snapshot is older and the object changed (topic split or merged, phrase dropped), the page opens the closest match and says so in one line: "This link was made on 2 Nov. The topic has since merged with …".
4. If nothing resolves, the map opens normally with a one-line note.

## What it needs

- Export: per topic, the anchor paper id (one extra field in `topics-*.json`); per precomputed idea, its hash id.
- Map page: the router, the Copy link button, deflate via the browser's built-in `CompressionStream` (no library).
- Later, optional: link previews in Slack/X (a picture of the object) need a small server function that renders an og:image per link. Without it, every shared link shows the general site preview.

## Build order

1. Paper, topic, link, phrase (anchor ids in the export + router + button). Most shares will be these.
2. Lasso selection and A·B pair.
3. Ideas (precomputed by id, own-agent ideas in the fragment).
4. Optional per-link preview images.
