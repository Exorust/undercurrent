"""Static snapshot of the topic workbench for hosting with no server (charoori.com/undercurrent/).

Usage: python -m radar.export [--out site] [--ideas N] [--db data/radar.duckdb]
       python -m radar.export --selftest | --check DIR (F50 size budgets on a finished export)

Loads the same state as radar.serve (radar.duckdb read-only) and calls serve.py's own handlers, so every
JSON file equals what the live endpoint returns. serve.py's workbench, pid, lock and log paths are pointed
at a temp dir: the export never touches data/workbench.duckdb, data/serve.pid or data/run.lock and never
binds a port. Writes <out>.tmp, then swaps it into <out>. All paths in the site are relative.
Layout: index.html = radar/landing.html (front page), map/index.html = radar/app.html reading ../data/,
data/ = the JSON and binary files, preview.png carried over from the previous <out> when present.
"""
import argparse, gzip, json, math, pathlib, re, shutil, sys, tempfile, time
from collections import Counter
from datetime import datetime
import numpy as np
from . import serve
from .serve import S, P, E

WINDOWS = {"3m": 3, "6m": 6, "12m": 12}
SHARD = 10000
DROP = ("summary", "abstract")
MIN_INST = 30            # institutions (and countries) with fewer map papers get no row file
MIN_CITE_LINK = 10       # a citation link needs at least this many cites to be listed
MAX_CITE_LINKS = 200
ROOT = pathlib.Path(__file__).resolve().parent.parent
FILTER_JS = pathlib.Path(__file__).with_name("filter.js")
KIND_BIT = {"accepted": 1, "workshop": 2, "submitted": 4}


def dumps(obj):
    if isinstance(obj, dict) and "run_id" not in obj: obj = {**obj, "run_id": S.run_id}   # what send_json adds
    return json.dumps(obj, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o))


def slim(p, url=False):
    p = {k: v for k, v in p.items() if k not in DROP}
    if url: p["url"] = f"https://arxiv.org/abs/{p['id']}"
    return p


# ---------- pure helpers (checked by --selftest) ----------

def shard_of(row): return row // SHARD, row % SHARD


LANDING = pathlib.Path(__file__).with_name("landing.html")


def static_app(html):
    """app.html as served from site/map/: data lives one level up."""
    return html.replace("<head>", '<head>\n<script>window.UC_STATIC={base:"../data/"}</script>', 1)


def cit_bytes(cit):
    """citations per row -> Uint16 LE bytes; null (-1 or None) -> 0, clamp at 65535."""
    a = np.asarray([-1 if c is None else c for c in cit], np.int64)
    return np.clip(a, 0, 65535).astype("<u2").tobytes()


def flag_bytes(n, kinds, codes, withdrawn):
    """Uint8 per row: bit0 accepted, bit1 workshop, bit2 submitted, bit3 has code_url, bit4 withdrawn.
    kinds/codes/withdrawn are dicts row -> value (rows not present get 0)."""
    a = np.zeros(n, np.uint8)
    for r, k in kinds.items(): a[r] |= KIND_BIT.get(k, 0)
    for r, c in codes.items():
        if c: a[r] |= 8
    for r, wd in withdrawn.items():
        if wd: a[r] |= 16
    return a.tobytes()


def citpct_bytes(n, pct):
    """Uint8 per row: cit_pct 0..100, 255 unknown. pct is a dict row -> value or None."""
    a = np.full(n, 255, np.uint8)
    for r, v in pct.items():
        if v is not None: a[r] = int(min(max(v, 0), 100))
    return a.tobytes()


def venue_index(venues):
    """Venue names most frequent first (ties alphabetical) -> (list, name -> idx)."""
    names = [v for v, _ in sorted(Counter(v for v in venues if v).items(), key=lambda x: (-x[1], x[0]))]
    return names, {v: i for i, v in enumerate(names)}


def rows_bin(rows):
    """Sorted unique rows -> Uint32 LE bytes."""
    return np.unique(np.asarray(list(rows), np.int64)).astype("<u4").tobytes()


def short_label(label):
    """landing.html short(): drop terms contained in another term, keep the first three."""
    t = label.split(", ")
    return ", ".join([a for a in t if not any(b != a and a in b for b in t)][:3])


def landing_rows(T, L, n=5):
    """The rows of the three front-page lists, same fields and order landing.html reads (level 1, 6m)."""
    name, arr = {}, []
    for t in T["topics"]:
        name[t["c"]] = t["label"]
        for a in t.get("arriving") or []: arr.append((a, t))
    grow = sorted([t for t in T["topics"] if t["recent"] >= 200 and t["growth"] is not None], key=lambda t: -t["growth"])[:n]
    per, seen, arrive = Counter(t["c"] for _, t in arr), set(), []
    for a, t in sorted(arr, key=lambda o: (not (o[0].get("is_new") or o[0]["growth"] is None), -(o[0]["growth"] or 0))):
        if t["c"] in seen: continue
        seen.add(t["c"]); arrive.append((a, t))
    rank = lambda b: 0 if b["status"] == "new" else 1
    bl = sorted([b for b in L["bridge_links"] if b["status"] == "new" or b["growth"] is not None],
                key=lambda b: (rank(b), -(b["growth"] or 0), -b["n_bridge"]))[:n]
    return {"window": T["window"], "arriving_total": len(arr),   # all arriving sub-topics, before dedupe and the top-n cut
            "growing": [{k: t.get(k) for k in ("c", "label", "growth", "recent", "prior")} for t in grow],
            "arriving": [{"t": {"c": t["c"], "label": t["label"]},
                          "a": {k: a.get(k) for k in ("label", "growth", "recent")},
                          "short": short_label(a["label"]), "n_arriving": per[t["c"]]} for a, t in arrive[:n]],
            "links": [{**{k: b.get(k) for k in ("c1", "c2", "status", "growth", "n_bridge", "n_bridge_prior")},
                       "name1": name.get(b["c1"]), "name2": name.get(b["c2"])} for b in bl]}


def gz(b): return len(gzip.compress(b, 6))


def check_dir(d):
    """F50 budget checks on a real export dir. Prints each number, returns True when all pass."""
    d, ok = pathlib.Path(d) / "data", True
    def line(name, val, limit, unit):
        nonlocal ok
        good = val <= limit; ok &= good
        print(f"{'ok  ' if good else 'FAIL'} {name:34s} {val:12,.0f} {unit:6s} (limit {limit:,})")
    pair = [d / "flags.bin", d / "citpct.bin"]
    if all(f.exists() for f in pair): line("flags.bin+citpct.bin gzip", sum(gz(f.read_bytes()) for f in pair) / 1024, 600, "KB")
    else: print("skip flags.bin+citpct.bin (absent)")
    f = d / "institutions.json"
    if f.exists(): line("institutions.json gzip", gz(f.read_bytes()) / 1024, 150, "KB")
    else: print("skip institutions.json (absent)")
    sizes = [(gz(f.read_bytes()), f.name) for f in sorted((d / "inst").glob("*.bin"))] if (d / "inst").is_dir() else []
    if sizes: s, nm = max(sizes); line(f"largest inst/*.bin gzip ({nm}, of {len(sizes)})", s / 1024, 64, "KB")
    else: print("skip inst/*.bin (absent)")
    line("total files", sum(1 for f in pathlib.Path(d).parent.rglob("*") if f.is_file()), 10000, "files")
    return ok


def centroids(labels, V, k):
    """Mean (not normalised) vector of each cluster's rows, float32 (k, d); empty clusters are zeros."""
    out, n = np.zeros((k, V.shape[1]), np.float64), np.bincount(labels[labels >= 0], minlength=k)
    for i in range(0, len(V), 65536):   # ponytail: chunked add.at keeps the float64 temp small
        l = labels[i:i + 65536]; ok = l >= 0
        np.add.at(out, l[ok], V[i:i + 65536][ok])
    return (out / np.maximum(n, 1)[:, None]).astype("<f4"), n


def majority(child, parent, k):
    """Most common parent label (>= 0) per child cluster, -1 when none."""
    ok = (child >= 0) & (parent >= 0)
    kp = int(parent.max()) + 1 if ok.any() else 1
    m = np.bincount(child[ok] * kp + parent[ok], minlength=k * kp).reshape(k, kp)
    return np.where(m.sum(1) > 0, m.argmax(1), -1)


def swap(tmp, out):
    """tmp -> out atomically-ish: out -> out.old, tmp -> out, delete old."""
    tmp, out = pathlib.Path(tmp), pathlib.Path(out); old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists(): out.rename(old)
    tmp.rename(out)
    shutil.rmtree(old, ignore_errors=True)


def agent_assets(w):
    """Ship the read-only MCP function and discovery text inside the atomic export."""
    mcp = pathlib.Path(__file__).resolve().parent.parent / "mcp"
    for f in sorted((mcp / "api").rglob("*")):
        if f.is_file(): w.put("api/" + f.relative_to(mcp / "api").as_posix(), f.read_bytes())
    w.put("package.json", (mcp / "package.json").read_bytes())
    w.put("llms.txt", """# undercurrent

A static map of 459,249 arXiv CS papers with topic growth, arriving sub-topics,
and links supported by bridge papers. Growth adjusts for overall CS growth.
Windows W = 3m, 6m, 12m cover complete months. Levels L = 0 sub-topic, 1 topic, 2 area.

Map: https://charoori.com/undercurrent/map/
Project: https://charoori.com/undercurrent/
Source: https://github.com/Exorust/undercurrent
MCP: https://charoori.com/undercurrent/api/mcp/
Free, read-only Streamable HTTP; no authentication. Rate limits may apply.
Claude Code setup:
claude mcp add --transport http undercurrent https://charoori.com/undercurrent/api/mcp/
Tools: list_topics, get_topic, arriving, links, compare, get_papers, novelty_check.
Prompt: ideas_between. Abstracts and novelty searches use the live arXiv API.

Data paths below are relative to https://charoori.com/undercurrent/data/.
meta.json: object with n_points, months, levels, last_complete, and windows;
each window records date bounds, prior bounds and CS volume correction.
labels-L.json: {level, clusters: [{c, label, n, cx, cy, parent, parent_share}]}.
topics-L-W.json: {window, level, topics: [{c, label, n_total, recent, prior,
growth, is_new, arriving}]}; arriving entries include sub-topic shares and growth.
links-L-W.json: {window, level, rule, bridge_links, phrase_links}; bridge entries
include c1, c2, n_bridge, strength, growth, status and prior counts.
link-index-L-W.json: array of "c1-c2" strings identifying exported pair files.
link/L-W-c1-c2.json: {window, level, c1, c2, n_bridge, bridges, phrases};
bridges contain row, id, title, month, cat, citations and arXiv URL.
topic/L-W-c.json: {n, months, growth, top_cited, newest, terms, topics};
months are counts aligned to meta.months, terms are distinctive terms, topics
include own_share and nbr_share. Paper lists cover the whole snapshot.
phrases-W.json: {level, window, phrases}, with rising phrase statistics.
phrase-rows.json: object mapping phrase strings to paper-row arrays.
papers/k.json: arrays [id, title, month, cat, citations]; k = floor(row/10000),
with position row % 10000 in each shard.
centroids-0.bin: little-endian Float32 matrix of level-0 means, n x 768.
counts-0.json: {n, parent1, parent2}, parallel arrays of counts and majority parents;
parent centroid means use counts as weights.
ideas.json: array of saved idea packs with topic pair, model, shown papers and ideas.
Newer files (present only when the source tables exist; every paper shard row has three
trailing fields [..., venue_idx|null, code_url|null, cit_pct|null]):
flags.bin: one byte per map row; bit0 accepted, bit1 workshop, bit2 submitted,
bit3 has code link, bit4 withdrawn (from the arXiv comment field).
citpct.bin: one byte per map row, rounded percent of same-month papers with strictly fewer citations, 255 unknown.
flags.bin and citpct.bin are always written (all 0 / all 255 when their source table is absent).
venues.json: array of venue names; venue_idx in the paper shards indexes it, most frequent first.
institutions.json: [[inst_id, name, country, n]] for institutions with 30 or more map papers, n descending.
inst/<inst_id>.bin: sorted little-endian Uint32 map rows of the papers with that institution.
country/<CC>.bin: same per country (30 or more papers); countries.json: [[CC, n]].
topic/L-W-c.json: also signals and signals_all ({venue_share, code_share, cit_median_pct,
top10_share, top_insts, top_venues}) for the window and for the whole snapshot.
topics-L-W.json rows: also venue_share, code_share, cit_median_pct.
links-L-W.json: bridge entries also n_cites; cite_links lists pairs linked by citations
({c1, c2, n_cites, n_1to2, n_2to1}) that are not bridge links.
link/L-W-c1-c2.json: also cites {n_cites, n_1to2, n_2to1, top}, top = up to 30 papers most cited across both orientations of the pair, with summed n.
meta.json: also extra {comment_share, venue_share, code_share, inst_known_share, n_institutions, n_cite_refs}.
landing.json: window bounds, arriving_total, and five rows per front-page list: growing, arriving, links.
Arriving growth null means new and sorts first; deep links pin the window to 3, 6, or 12 months.
""")


# ---------- export ----------

class Writer:
    def __init__(self, root): self.root, self.groups = pathlib.Path(root), {}

    def put(self, rel, data, group=None):
        f = self.root / rel; f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str): data = data.encode()
        f.write_bytes(data)
        g = self.groups.setdefault(group or rel, [0, 0]); g[0] += 1; g[1] += len(data)


def tcols(cur, t):
    try: return {r[0] for r in cur.execute(f"describe {t}").fetchall()}
    except Exception: return set()


def jl(s):
    try: return json.loads(s) if s else []
    except Exception: return []


class Extra:
    """Optional tables: whatever is missing leaves its attribute empty and its output skipped."""
    def __init__(self, cur):
        self.cur, self.n = cur, S.n
        have = {t: tcols(cur, t) for t in ("paper_comments", "paper_institutions", "institutions", "extra_meta",
                                           "paper_cites", "topic_signals", "topic_citelinks", "citelink_evidence")}
        self.have = {t: bool(c) for t, c in have.items()}
        self.venue_names, self.venue_of, self.code_of, self.pct = [], {}, {}, {}
        self.flags = self.citpct = None
        cc = have["paper_comments"]
        if cc:
            f = lambda c: f"c.{c}" if c in cc else "null"
            q = cur.execute(f"select pi.row, {f('kind')}, {f('venue')}, {f('code_url')}, {f('withdrawn')} "
                            "from point_index pi join paper_comments c using(id)").fetchall()
            self.venue_names, vidx = venue_index(v for _, _, v, _, _ in q)
            kinds, codes, wd = {}, {}, {}
            for r, k, v, c, w_ in q:
                if k: kinds[r] = k
                if c: codes[r], self.code_of[r] = True, c
                if w_: wd[r] = True
                if v: self.venue_of[r] = vidx[v]
            self.flags = flag_bytes(S.n, kinds, codes, wd)
        if have["paper_cites"]:
            self.pct = dict(cur.execute("select row, cit_pct from paper_cites").fetchall())
            self.citpct = citpct_bytes(S.n, self.pct)
        self.row_fields = bool(cc or have["paper_cites"])
        self.signals = {}
        if have["topic_signals"]:
            for lv, c, win, n, vs, cs, cm, t10, ti, tv in cur.execute(
                    "select level, cluster, win, n, venue_share, code_share, cit_median_pct, top10_share, top_insts, top_venues from topic_signals").fetchall():
                self.signals[(lv, c, win)] = {"venue_share": vs, "code_share": cs, "cit_median_pct": cm, "top10_share": t10,
                                              "top_insts": jl(ti), "top_venues": jl(tv)}
        self.meta = dict(cur.execute("select key, value from extra_meta").fetchall()) if have["extra_meta"] else {}

    def paper_tail(self, r):
        p = self.pct.get(r)
        return [self.venue_of.get(r), self.code_of.get(r), None if p is None else int(p)]

    def sig(self, lv, c, win): return self.signals.get((lv, c, win))

    def put_institutions(self, w):
        if not (self.have["paper_institutions"] and self.have["institutions"]): return
        cur = self.cur
        insts = cur.execute("select inst_id, coalesce(name, ''), coalesce(country, ''), n from institutions where n >= ? order by n desc, inst_id", [MIN_INST]).fetchall()
        w.put("data/institutions.json", dumps([[i, nm, cc, int(n)] for i, nm, cc, n in insts]))
        keep = {i for i, _, _, _ in insts}
        by, byc = {}, {}
        for i, row, cc in cur.execute("""select distinct a.inst_id, pi.row, i.country from paper_institutions a
                                         join point_index pi using(id) left join institutions i on i.inst_id = a.inst_id""").fetchall():
            if i in keep: by.setdefault(i, set()).add(row)
            if cc and re.fullmatch(r"[A-Za-z]{2}", cc): byc.setdefault(cc.upper(), set()).add(row)
        for i, rows in sorted(by.items()):
            if re.fullmatch(r"[A-Za-z0-9_-]+", i): w.put(f"data/inst/{i}.bin", rows_bin(rows), "inst/")
        cn = sorted(((cc, len(r)) for cc, r in byc.items() if len(r) >= MIN_INST), key=lambda x: (-x[1], x[0]))
        for cc, _ in cn: w.put(f"data/country/{cc}.bin", rows_bin(byc[cc]), "country/")
        w.put("data/countries.json", dumps([[cc, n] for cc, n in cn]))

    def extra_meta(self):
        if not self.meta: return None
        def num(k):
            try: return float(self.meta[k])
            except Exception: return None
        out = {k: num(k) for k in ("comment_share", "venue_share", "code_share", "inst_known_share")}
        out["n_institutions"] = num("n_institutions_30"); out["n_cite_refs"] = num("n_cite_refs")
        return out

    def cite_pairs(self, lv, rf, rt):
        """(c1, c2) with c1 < c2 -> (n_cites, n_1to2, n_2to1) summed over the window months; {} when absent."""
        if not self.have["topic_citelinks"]: return {}
        out = {}
        for c1, c2, n, a, b in self.cur.execute("select c1, c2, sum(n_cites), sum(n_1to2), sum(n_2to1) from topic_citelinks "
                                                "where level = ? and month between ? and ? group by 1, 2", [lv, rf, rt]).fetchall():
            if c1 == c2: continue
            if c1 > c2: c1, c2, a, b = c2, c1, b, a
            o = out.get((c1, c2), (0, 0, 0)); out[(c1, c2)] = (o[0] + int(n), o[1] + int(a or 0), o[2] + int(b or 0))
        return out

    def cite_top(self, lv, c1, c2, k=30):
        if not self.have["citelink_evidence"]: return []
        got = self.cur.execute("select cited_row, sum(n) from citelink_evidence where level = ? and ((c1 = ? and c2 = ?) or (c1 = ? and c2 = ?)) "
                               "group by 1 order by 2 desc, 1 limit ?", [lv, c1, c2, c2, c1, k]).fetchall()
        n = {int(r): int(x) for r, x in got}
        return [{**slim(p, url=True), "n": n[p["row"]]} for p in serve.papers(self.cur, list(n))]


def win_q(n): return {"from": serve.ym(S.E - n + 1), "to": serve.ym(S.E)}
def win_q_idx(n): return S.E - n + 1, S.E   # the same window as month indexes (rf, rt)


def export(out, n_ideas):
    t0 = time.time()
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="radar-export-"))
    P.update(wb=scratch / "wb.duckdb", pid=scratch / "serve.pid", lock=scratch / "run.lock", log=scratch / "serve.log")
    serve.load(); S.stale, S.started = None, datetime.now().isoformat(timespec="seconds")
    print(f"loaded run {S.run_id}, {S.n:,} points in {S.load_s:.0f} s", flush=True)
    tmp = pathlib.Path(str(out) + ".tmp"); shutil.rmtree(tmp, ignore_errors=True)
    w, cur, gen = Writer(tmp), S.con.cursor(), datetime.now().isoformat(timespec="seconds")
    call = lambda fn, q=None, body=None: fn(None, q or {}, body, cur)

    X = Extra(cur)
    print("extra tables: " + (", ".join(t for t, h in X.have.items() if h) or "none"), flush=True)
    html = P["app"].read_text()
    assert "<head>" in html, "radar/app.html has no <head>"
    w.put("index.html", LANDING.read_bytes())
    w.put("map/index.html", static_app(html))
    if (pathlib.Path(out) / "preview.png").exists():   # written by the weekly job; keep it until the next one
        w.put("preview.png", (pathlib.Path(out) / "preview.png").read_bytes())
    wins = {k: serve.window(win_q(n))["obj"] for k, n in WINDOWS.items()}
    meta = {**call(serve.h_meta), "static": True, "generated": gen, "windows": wins}
    if X.meta: meta["extra"] = X.extra_meta()
    w.put("data/meta.json", dumps(meta))
    w.put("data/points.bin", P["points"].read_bytes())
    D = "data/"
    if FILTER_JS.exists(): w.put("map/filter.js", FILTER_JS.read_bytes())
    if S.n:   # always both files (defaults when a source table is absent): the page's filters need both
        w.put(f"{D}flags.bin", X.flags if X.flags is not None else flag_bytes(S.n, {}, {}, {}))
        w.put(f"{D}citpct.bin", X.citpct if X.citpct is not None else citpct_bytes(S.n, {}))
    if X.have["paper_comments"]: w.put(f"{D}venues.json", dumps(X.venue_names))
    X.put_institutions(w)
    landing_in = {}
    for lv in range(3): w.put(f"{D}labels-{lv}.json", dumps(call(serve.h_labels, {"level": lv})), "labels-*.json")

    link_keys = {}
    for wk, n in WINDOWS.items():
        q = win_q(n)
        w.put(f"{D}phrases-{wk}.json", dumps(call(serve.h_phrases, {"level": 1, **q})), "phrases-*.json")
        for lv in range(3):
            T = call(serve.h_topics, {"level": lv, **q})
            for t in T["topics"]:
                sg = X.sig(lv, t["c"], n)
                if sg: t.update({k: sg[k] for k in ("venue_share", "code_share", "cit_median_pct")})
            w.put(f"{D}topics-{lv}-{wk}.json", dumps(T), "topics-*.json")
            try: L = call(serve.h_links, {"level": lv, **q})
            except E as e: print(f"warning: links level {lv}: {e.msg}; skipped"); continue
            bl = L["bridge_links"]
            w.put(f"{D}links-{lv}-{wk}.json", dumps(L), "links-*.json")
            if (lv, wk) == (1, "6m"): landing_in = {"T": T, "L": L}
            top = sorted(bl, key=lambda x: -(x["strength"] if x["strength"] is not None else -1))[:40]
            pairs = {(x["c1"], x["c2"]) for x in top} | ({(x["c1"], x["c2"]) for x in bl if x["n_bridge"] >= 10} if lv == 1 else set())
            strength = {(x["c1"], x["c2"]): x["strength"] or 0 for x in bl}
            for c1, c2 in sorted(pairs):
                r = call(serve.h_link, {"level": lv, "c1": c1, "c2": c2, **q})
                r["bridges"] = [slim(p, url=True) for p in r["bridges"]]
                r["cites"]["top"] = [slim(p, url=True) for p in r["cites"]["top"]]
                w.put(f"{D}link/{lv}-{wk}-{c1}-{c2}.json", dumps(r), "link/")
            keys = sorted(f"{a}-{b}" for a, b in pairs)
            w.put(f"{D}link-index-{lv}-{wk}.json", dumps(keys), "link-index-*.json")
            link_keys[(lv, wk)] = sorted(pairs, key=lambda p: -strength[p])
        print(f"  window {wk} topics/links done ({time.time() - t0:.0f} s)", flush=True)

    for lv in range(3):
        for c in range(S.k[lv]):
            for wk, n in WINDOWS.items():
                try: r = call(serve.h_select, body={"topic": {"level": lv, "c": c}, **win_q(n)})
                except E as e: print(f"warning: topic {lv}/{c}: {e.msg}; skipped"); break
                with serve.CACHE_LOCK: serve.CACHE.pop(r["sel_id"], None)
                r["sel_id"] = f"topic-{lv}-{c}"
                for k in ("top_cited", "newest"): r[k] = [slim(p) for p in r[k]]
                if X.signals: r["signals"], r["signals_all"] = X.sig(lv, c, n), X.sig(lv, c, 0)
                w.put(f"{D}topic/{lv}-{wk}-{c}.json", dumps(r), "topic/")
        print(f"  topic level {lv} done ({time.time() - t0:.0f} s)", flush=True)

    pr = {}
    for ph, row in cur.execute("""select pp.phrase, pp.row from phrase_papers pp join point_index pi using(row)
                                  where pp.phrase in (select unnest(?::varchar[])) order by 1, 2""", [S.rising]).fetchall():
        pr.setdefault(ph, []).append(int(row))
    w.put(f"{D}phrase-rows.json", dumps({ph: pr.get(ph, []) for ph in S.rising}))

    titles = dict(cur.execute("select p.id, p.title from point_index pi join papers p using(id)").fetchall())
    for k in range((S.n + SHARD - 1) // SHARD):
        rows = range(k * SHARD, min((k + 1) * SHARD, S.n))
        w.put(f"{D}papers/{k}.json", dumps([[str(S.ids[r]), titles.get(str(S.ids[r])), serve.ym(S.month[r]), serve.catname(S.cat[r]),
                                              int(S.cit[r]) if S.cit[r] >= 0 else None] + (X.paper_tail(r) if X.row_fields else [])
                                             for r in rows]), "papers/")
        if k == 0: gz0 = len(gzip.compress((tmp / D / "papers/0.json").read_bytes(), 6)) / (tmp / D / "papers/0.json").stat().st_size
    del titles
    w.put(f"{D}citations.bin", cit_bytes(S.cit))
    C, n0 = centroids(S.L[0], S.V, S.k[0])
    w.put(f"{D}centroids-0.bin", C.tobytes())
    w.put(f"{D}counts-0.json", dumps({"n": n0.tolist(), "parent1": majority(S.L[0], S.L[1], S.k[0]).tolist(),
                                      "parent2": majority(S.L[0], S.L[2], S.k[0]).tolist()}))

    ideas = []
    if n_ideas > 0:
        serve.open_wb(); q = win_q(6)
        for c1, c2 in link_keys.get((1, "6m"), [])[:n_ideas]:
            try:
                a = call(serve.h_select, body={"topic": {"level": 1, "c": c1}, **q})["sel_id"]
                b = call(serve.h_select, body={"topic": {"level": 1, "c": c2}, **q})["sel_id"]
                r = call(serve.h_ideas_post, body={"a": a, "b": b, **q})
            except Exception as e:
                print(f"warning: ideas {c1}-{c2} skipped: {getattr(e, 'msg', None) or type(e).__name__}", flush=True); continue
            ideas.append({"level": 1, "window": "6m", "c1": c1, "c2": c2, "a_name": serve.lookup(a)["name"], "b_name": serve.lookup(b)["name"],
                          "created": r["created"], "model": r["model"], "shown": [slim(p) for p in r["shown"]],
                          "ideas": [{k: x[k] for k in ("title", "text", "cited", "status")} for x in r["ideas"]]})
            print(f"  ideas {c1}-{c2}: {len(r['ideas'])} kept, {r['dropped']} dropped", flush=True)
        S.wb.close()
    w.put(f"{D}ideas.json", dumps(ideas))
    if landing_in: w.put(f"{D}landing.json", dumps(landing_rows(landing_in["T"], landing_in["L"])))
    agent_assets(w)
    nf, nb = sum(g[0] for g in w.groups.values()), sum(g[1] for g in w.groups.values())
    w.put(f"{D}manifest.json", dumps({"files": nf + 1, "bytes": nb, "generated": gen, "run_id": S.run_id}))
    cur.close(); S.con.close()
    # keep site/ linked to the Vercel project "undercurrent"; without this `vercel deploy site` creates a new project
    (tmp / ".vercel").mkdir(exist_ok=True)
    shutil.copy(pathlib.Path(__file__).resolve().parent.parent / "tools" / "vercel-project.json", tmp / ".vercel" / "project.json")
    swap(tmp, out); shutil.rmtree(scratch, ignore_errors=True)

    for g, (cnt, b) in sorted(w.groups.items()):
        extra = f"  (gzip est. {b * gz0 / 1e6:.1f} MB at {gz0:.2f})" if g == "papers/" else ""
        print(f"{g:24s} {cnt:6d} files {b / 1e6:9.2f} MB{extra}")
    print(f"total {nf + 1} files, {(nb + w.groups[D + 'manifest.json'][1]) / 1e6:.1f} MB uncompressed -> {out}  ({time.time() - t0:.0f} s)")


def selftest():
    assert shard_of(0) == (0, 0) and shard_of(9999) == (0, 9999) and shard_of(10000) == (1, 0) and shard_of(459248) == (45, 9248)
    b = cit_bytes([None, -1, 5, 70000, 65535])
    assert np.frombuffer(b, "<u2").tolist() == [0, 0, 5, 65535, 65535] and len(b) == 10
    V = np.arange(5 * 768, dtype=np.float32).reshape(5, 768); lab = np.array([0, 0, 2, -1, 2])
    C, n = centroids(lab, V, 3)
    assert C.shape == (3, 768) and C.dtype == np.dtype("<f4") and n.tolist() == [2, 0, 2]
    assert np.allclose(C[0], V[:2].mean(0)) and not C[1].any() and np.allclose(C[2], V[[2, 4]].mean(0))
    assert len(C.tobytes()) == 3 * 768 * 4
    assert majority(np.array([0, 0, 0, 1, -1]), np.array([2, 2, 1, -1, 0]), 3).tolist() == [2, -1, -1]
    assert static_app("<html><head><title>x</title><head>") == '<html><head>\n<script>window.UC_STATIC={base:"../data/"}</script><title>x</title><head>'
    lp = LANDING.read_text()
    assert LANDING.exists() and 'href="map/"' in lp and "fetch('data/'" in lp and re.search(r'<meta property="og:image" content="[^"]*preview\.png"', lp)
    assert not any(s in lp for s in ('href="/', 'src="/', "'/data", '"/data')), "landing.html must use relative URLs only"
    assert flag_bytes(6, {0: "accepted", 1: "workshop", 2: "submitted", 3: "other"}, {0: True, 4: True}, {5: True}) == bytes([9, 2, 4, 0, 8, 16])
    assert citpct_bytes(4, {0: 0, 1: 100, 2: None}) == bytes([0, 100, 255, 255])
    assert venue_index(["b", "a", "b", None, "c", "a", "b"]) == (["b", "a", "c"], {"b": 0, "a": 1, "c": 2})
    assert np.frombuffer(rows_bin({5, 1, 3}), "<u4").tolist() == [1, 3, 5]
    assert short_label("agents, llm agents, tools, code, math") == "llm agents, tools, code"
    T = {"window": {}, "topics": [{"c": c, "label": f"t{c}", "growth": 1.0 + c, "recent": 300, "prior": 5,
                                   "arriving": [{"label": f"s{c}", "growth": c, "recent": 50}] * 2} for c in range(8)]}
    L = {"bridge_links": [{"c1": i, "c2": i + 1, "status": "ratio", "growth": i, "n_bridge": 10, "n_bridge_prior": 2} for i in range(1, 9)]
         + [{"c1": 0, "c2": 9, "status": "new", "growth": None, "n_bridge": 12, "n_bridge_prior": 0}]}
    lr = landing_rows(T, L)
    assert [x["c"] for x in lr["growing"]] == [7, 6, 5, 4, 3] and [x["t"]["c"] for x in lr["arriving"]] == [7, 6, 5, 4, 3]
    T["topics"][0]["arriving"] = [{"label": "new", "growth": None, "is_new": True, "recent": 50}] * 2
    assert landing_rows(T, L)["arriving"][0]["t"]["c"] == 0
    assert lr["arriving_total"] == 16, "arriving_total counts before dedupe and truncation"
    assert lr["arriving"][0]["n_arriving"] == 2 and lr["links"][0]["status"] == "new" and len(lr["links"]) == 5 and len(json.dumps(lr)) < 10240
    d = pathlib.Path(tempfile.mkdtemp(prefix="radar-export-selftest-")); out = d / "site"
    out.mkdir(); (out / "old.txt").write_text("old")
    tmp = d / "site.tmp"; tmp.mkdir(); (tmp / "new.txt").write_text("new")
    agent_assets(Writer(tmp))
    swap(tmp, out)
    assert (out / "api/mcp.js").is_file() and (out / "llms.txt").is_file()
    assert (out / "package.json").is_file() and not (out / "node_modules").exists()
    assert (out / "new.txt").read_text() == "new" and not (out / "old.txt").exists() and not tmp.exists() and not (d / "site.old").exists()
    tmp.mkdir(); (tmp / "x").write_text("x"); shutil.rmtree(out); swap(tmp, out)   # first export: no old site
    assert (out / "x").exists()
    shutil.rmtree(d)
    print("ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="site"); ap.add_argument("--ideas", type=int, default=0)
    ap.add_argument("--db"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", metavar="DIR", help="run the F50 size budgets on an existing export dir and exit")
    a = ap.parse_args()
    if a.selftest: return selftest()
    if a.check: sys.exit(0 if check_dir(a.check) else 1)
    if a.db: P["db"] = pathlib.Path(a.db)
    export(pathlib.Path(a.out), a.ideas)


if __name__ == "__main__":
    main()
