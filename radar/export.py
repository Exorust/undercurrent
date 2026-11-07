"""Static snapshot of the topic workbench for hosting with no server (charoori.com/undercurrent/).

Usage: python -m radar.export [--out site] [--ideas N] [--db data/radar.duckdb]
       python -m radar.export --selftest

Loads the same state as radar.serve (radar.duckdb read-only) and calls serve.py's own handlers, so every
JSON file equals what the live endpoint returns. serve.py's workbench, pid, lock and log paths are pointed
at a temp dir: the export never touches data/workbench.duckdb, data/serve.pid or data/run.lock and never
binds a port. Writes <out>.tmp, then swaps it into <out>. All paths in the site are relative.
Layout: index.html = radar/landing.html (front page), map/index.html = radar/app.html reading ../data/,
data/ = the JSON and binary files, preview.png carried over from the previous <out> when present.
"""
import argparse, gzip, json, pathlib, shutil, sys, tempfile, time
from datetime import datetime
import numpy as np
from . import serve
from .serve import S, P, E

WINDOWS = {"3m": 3, "6m": 6, "12m": 12}
SHARD = 10000
DROP = ("summary", "abstract")


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
MCP: https://charoori.com/undercurrent/api/mcp
Free, read-only Streamable HTTP; no authentication. Rate limits may apply.
Claude Code setup:
claude mcp add --transport http undercurrent https://charoori.com/undercurrent/api/mcp
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
""")


# ---------- export ----------

class Writer:
    def __init__(self, root): self.root, self.groups = pathlib.Path(root), {}

    def put(self, rel, data, group=None):
        f = self.root / rel; f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str): data = data.encode()
        f.write_bytes(data)
        g = self.groups.setdefault(group or rel, [0, 0]); g[0] += 1; g[1] += len(data)


def win_q(n): return {"from": serve.ym(S.E - n + 1), "to": serve.ym(S.E)}


def export(out, n_ideas):
    t0 = time.time()
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="radar-export-"))
    P.update(wb=scratch / "wb.duckdb", pid=scratch / "serve.pid", lock=scratch / "run.lock", log=scratch / "serve.log")
    serve.load(); S.stale, S.started = None, datetime.now().isoformat(timespec="seconds")
    print(f"loaded run {S.run_id}, {S.n:,} points in {S.load_s:.0f} s", flush=True)
    tmp = pathlib.Path(str(out) + ".tmp"); shutil.rmtree(tmp, ignore_errors=True)
    w, cur, gen = Writer(tmp), S.con.cursor(), datetime.now().isoformat(timespec="seconds")
    call = lambda fn, q=None, body=None: fn(None, q or {}, body, cur)

    html = P["app"].read_text()
    assert "<head>" in html, "radar/app.html has no <head>"
    w.put("index.html", LANDING.read_bytes())
    w.put("map/index.html", static_app(html))
    if (pathlib.Path(out) / "preview.png").exists():   # written by the weekly job; keep it until the next one
        w.put("preview.png", (pathlib.Path(out) / "preview.png").read_bytes())
    wins = {k: serve.window(win_q(n))["obj"] for k, n in WINDOWS.items()}
    w.put("data/meta.json", dumps({**call(serve.h_meta), "static": True, "generated": gen, "windows": wins}))
    w.put("data/points.bin", P["points"].read_bytes())
    D = "data/"
    for lv in range(3): w.put(f"{D}labels-{lv}.json", dumps(call(serve.h_labels, {"level": lv})), "labels-*.json")

    link_keys = {}
    for wk, n in WINDOWS.items():
        q = win_q(n)
        w.put(f"{D}phrases-{wk}.json", dumps(call(serve.h_phrases, {"level": 1, **q})), "phrases-*.json")
        for lv in range(3):
            w.put(f"{D}topics-{lv}-{wk}.json", dumps(call(serve.h_topics, {"level": lv, **q})), "topics-*.json")
            try: L = call(serve.h_links, {"level": lv, **q})
            except E as e: print(f"warning: links level {lv}: {e.msg}; skipped"); continue
            w.put(f"{D}links-{lv}-{wk}.json", dumps(L), "links-*.json")
            bl = L["bridge_links"]
            top = sorted(bl, key=lambda x: -(x["strength"] if x["strength"] is not None else -1))[:40]
            pairs = {(x["c1"], x["c2"]) for x in top} | ({(x["c1"], x["c2"]) for x in bl if x["n_bridge"] >= 10} if lv == 1 else set())
            strength = {(x["c1"], x["c2"]): x["strength"] or 0 for x in bl}
            for c1, c2 in sorted(pairs):
                r = call(serve.h_link, {"level": lv, "c1": c1, "c2": c2, **q})
                r["bridges"] = [slim(p, url=True) for p in r["bridges"]]
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
                                              int(S.cit[r]) if S.cit[r] >= 0 else None] for r in rows]), "papers/")
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
    agent_assets(w)
    nf, nb = sum(g[0] for g in w.groups.values()), sum(g[1] for g in w.groups.values())
    w.put(f"{D}manifest.json", dumps({"files": nf + 1, "bytes": nb, "generated": gen, "run_id": S.run_id}))
    cur.close(); S.con.close()
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
    assert LANDING.exists() and 'href="map/"' in lp and "fetch('data/'" in lp and 'src="preview.png"' in lp
    assert not any(s in lp for s in ('href="/', 'src="/', "'/data", '"/data')), "landing.html must use relative URLs only"
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
    a = ap.parse_args()
    if a.selftest: return selftest()
    if a.db: P["db"] = pathlib.Path(a.db)
    export(pathlib.Path(a.out), a.ideas)


if __name__ == "__main__":
    main()
