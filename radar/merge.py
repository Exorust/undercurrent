"""Merge ideas: rising map topics in the middle distance band from the home field.

Usage: python -m radar.merge [--home "metal kernel,gpu kernel,small language model"] [--n 8]
       python -m radar.merge --view     # only write report/merge.html from the stored ideas (no LLM calls)

Home field = mean SPECTER2 vector of embedded papers whose title contains any
home phrase (no external text-embedding model needed). Rising topics = medium
level (1) map clusters ranked by growth (signal S1). For each topic inside the
cosine-distance band [LO, HI] an LLM writes one merge idea citing 3 papers.
Distances are computed in 768-d, never from the 2-D map.
report/merge.html shows the 116 topics twice: on the map coordinates with
lines from the home field to the chosen topics, and as distance vs growth
with the band shaded. Click a topic for its papers and idea.
"""
import argparse, json, pathlib, sys
import duckdb, numpy as np
from .harvest import DB, ROOT, fetch_vecs
from .llm import ask

DEFAULT_HOME = "metal kernel,gpu kernel,cuda kernel,apple silicon,small language model,on-device language model,agent harness,coding agent"
SCHEMA = "create table if not exists merge_ideas (run_date date, cluster int, label text, dist double, growth double, idea text, model text);"


def topic_table(con, home, months):
    """Every medium-level topic with its 768-d distance to the home field and its growth. Returns (rows, lo, hi, home_xy)."""
    like = " or ".join("lower(p.title) like '%" + p.strip().lower().replace("'", "") + "%'" for p in home.split(","))
    hdf, H = fetch_vecs(con, f"""select p.id, tm.x, tm.y, e.vec from papers p join embeddings e using(id)
                                 left join topic_members tm on tm.id = p.id and tm.level = 1 where {like}""")
    if len(H) == 0: raise SystemExit("no home papers matched; change --home")
    h = H.mean(0); h /= np.linalg.norm(h)
    print(f"home field: {len(H)} seed papers", file=sys.stderr)
    df, V = fetch_vecs(con, """select tm.cluster, strftime(p.submitted,'%Y-%m') ym, e.vec
                               from topic_members tm join papers p using(id) join embeddings e using(id)
                               where tm.level=1 and tm.cluster >= 0""")
    yms = sorted(df.ym.unique())[:-1]  # drop the partial latest month
    recent, prior = list(yms[-months:]), list(yms[-2 * months:-months])
    rows = []
    for c, idx in df.groupby("cluster").indices.items():
        r = int(np.isin(df.ym.values[idx], recent).sum()); p = int(np.isin(df.ym.values[idx], prior).sum())
        cen = V[idx].mean(0)
        rows.append(dict(cluster=int(c), dist=1 - float(cen @ h / np.linalg.norm(cen)), growth=(r + 1) / (p + 1), recent=r, prior=p))
    # Band = middle of the observed distances (25th–75th percentile), not a fixed number: SPECTER2 cosine
    # distances between CS topics sit in a narrow range, so a hand-set band could select all or none.
    lo, hi = np.percentile([x["dist"] for x in rows], [25, 75])
    return rows, float(lo), float(hi), (float(hdf.x.mean()), float(hdf.y.mean()))


def top_papers(con, c, k=5):
    return con.execute("""select p.id, p.title, coalesce(s.tldr, left(p.abstract, 400)) from topic_members tm join papers p using(id)
                          left join s2_meta s using(id) where tm.level=1 and tm.cluster=? order by coalesce(s.citations,0) desc limit ?""", [int(c), k]).fetchall()


def view(con, home, months, out):
    rows, lo, hi, home_xy = topic_table(con, home, months)
    meta = {c: (l, n, cx, cy) for c, l, n, cx, cy in con.execute("select cluster, label, n, cx, cy from topics where level=1").fetchall()}
    ideas = dict(con.execute("select cluster, idea from merge_ideas where run_date=(select max(run_date) from merge_ideas)").fetchall())
    for x in rows:
        l, n, cx, cy = meta.get(x["cluster"], (str(x["cluster"]), 0, 0, 0))
        x.update(label=l, n=n, x=cx, y=cy, idea=ideas.get(x["cluster"]), papers=[list(t) for t in top_papers(con, x["cluster"])])
    data = dict(home=home, months=months, lo=lo, hi=hi, home_xy=home_xy, topics=rows)
    html = (pathlib.Path(__file__).with_name("merge.html")).read_text().replace("/*DATA*/", json.dumps(data))
    pathlib.Path(out).parent.mkdir(exist_ok=True); pathlib.Path(out).write_text(html)
    print(f"done: {out} ({len(rows)} topics, {len(ideas)} ideas)")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--home", default=DEFAULT_HOME); ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--model", default="sonnet"); ap.add_argument("--months", type=int, default=5)
    ap.add_argument("--view", action="store_true", help="only write the HTML view from stored ideas")
    ap.add_argument("--out", default=str(ROOT / "report" / "merge.html"))
    a = ap.parse_args()
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    if a.view: return view(con, a.home, a.months, a.out)
    rows, lo, hi, _ = topic_table(con, a.home, a.months)
    labels = dict(con.execute("select cluster, label from topics where level=1").fetchall())
    print(f"distance band {lo:.3f}–{hi:.3f} (25th–75th percentile of {len(rows)} topics)", file=sys.stderr)
    band = sorted([x for x in rows if lo <= x["dist"] <= hi and x["recent"] >= 20], key=lambda x: -x["growth"] * np.log1p(x["recent"]))
    print(f"{len(band)} topics in band; writing {a.n} ideas", file=sys.stderr, flush=True)
    con.execute("delete from merge_ideas where run_date = current_date")
    for x in band[:a.n]:
        c, d, g, r, p = x["cluster"], x["dist"], x["growth"], x["recent"], x["prior"]
        papers = "\n".join(f"- arXiv:{i} {t}. {s}" for i, t, s in top_papers(con, c))
        prompt = f"""You help a researcher find ideas that merge two fields.
Home field (the researcher's own work): {a.home}.
Rising topic: "{labels.get(c, c)}" ({r} papers in the last {a.months} months, {p} in the {a.months} before).
Representative papers:
{papers}

Write ONE concrete merge idea in 4-6 sentences: name the method from the rising topic, the problem in the home field it could address, why the fit is plausible, and the first experiment to try. Cite exactly 3 of the arXiv ids above inline as arXiv:ID. Plain language, no hype. Output only the idea text."""
        idea = ask(prompt, model=a.model)
        con.execute("insert into merge_ideas values (current_date,?,?,?,?,?,?)", [int(c), labels.get(c, str(c)), d, g, idea, a.model])
        print(f"\n## {labels.get(c, c)}  (dist {d:.2f}, growth x{g:.1f}, {r}/{p})\n{idea}", flush=True)
    view(con, a.home, a.months, a.out)


if __name__ == "__main__":
    main()
