"""Merge ideas: rising map topics in the middle distance band from the home field.

Usage: python -m radar.merge [--home "metal kernel,gpu kernel,small language model"] [--n 8]

Home field = mean SPECTER2 vector of embedded papers whose title contains any
home phrase (no external text-embedding model needed). Rising topics = medium
level (1) map clusters ranked by growth (signal S1). For each topic inside the
cosine-distance band [LO, HI] an LLM writes one merge idea citing 3 papers.
Distances are computed in 768-d, never from the 2-D map.
"""
import argparse, sys
import duckdb, numpy as np
from .harvest import DB, fetch_vecs
from .llm import ask

DEFAULT_HOME = "metal kernel,gpu kernel,cuda kernel,apple silicon,small language model,on-device language model,agent harness,coding agent"
SCHEMA = "create table if not exists merge_ideas (run_date date, cluster int, label text, dist double, growth double, idea text, model text);"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--home", default=DEFAULT_HOME); ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--model", default="sonnet"); ap.add_argument("--months", type=int, default=5)
    a = ap.parse_args()
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    like = " or ".join("lower(p.title) like '%" + p.strip().lower().replace("'", "") + "%'" for p in a.home.split(","))
    _, H = fetch_vecs(con, f"select p.id, e.vec from papers p join embeddings e using(id) where {like}")
    if len(H) == 0: raise SystemExit("no home papers matched; change --home")
    h = H.mean(0); h /= np.linalg.norm(h)
    print(f"home field: {len(H)} seed papers", file=sys.stderr)

    df, V = fetch_vecs(con, """select tm.cluster, strftime(p.submitted,'%Y-%m') ym, e.vec
                               from topic_members tm join papers p using(id) join embeddings e using(id)
                               where tm.level=1 and tm.cluster >= 0""")
    yms = sorted(df.ym.unique())[:-1]  # drop the partial latest month
    recent, prior = set(yms[-a.months:]), set(yms[-2 * a.months:-a.months])
    labels = dict(con.execute("select cluster, label from topics where level=1").fetchall())
    cands, dists = [], []
    for c, idx in df.groupby("cluster").indices.items():
        r = int(df.ym.values[idx][np.isin(df.ym.values[idx], list(recent))].size); p = int(np.isin(df.ym.values[idx], list(prior)).sum())
        cen = V[idx].mean(0); d = 1 - float(cen @ h / np.linalg.norm(cen))
        dists.append(d)
        cands.append((c, d, (r + 1) / (p + 1), r, p))
    # Band = middle of the observed distances (25th–75th percentile), not a fixed number: SPECTER2 cosine
    # distances between CS topics sit in a narrow range, so a hand-set band could select all or none.
    lo, hi = np.percentile(dists, [25, 75])
    print(f"distance band {lo:.3f}–{hi:.3f} (25th–75th percentile of {len(dists)} topics)", file=sys.stderr)
    band = sorted([x for x in cands if lo <= x[1] <= hi and x[3] >= 20], key=lambda x: -x[2] * np.log1p(x[3]))
    print(f"{len(band)} topics in band; writing {a.n} ideas", file=sys.stderr, flush=True)
    con.execute("delete from merge_ideas where run_date = current_date")
    for c, d, g, r, p in band[:a.n]:
        top = con.execute("""select p.id, p.title, coalesce(s.tldr, left(p.abstract, 400)) from topic_members tm join papers p using(id)
                             left join s2_meta s using(id) where tm.level=1 and tm.cluster=? order by coalesce(s.citations,0) desc limit 5""", [int(c)]).fetchall()
        papers = "\n".join(f"- arXiv:{i} {t}. {s}" for i, t, s in top)
        prompt = f"""You help a researcher find ideas that merge two fields.
Home field (the researcher's own work): {a.home}.
Rising topic: "{labels.get(c, c)}" ({r} papers in the last {a.months} months, {p} in the {a.months} before).
Representative papers:
{papers}

Write ONE concrete merge idea in 4-6 sentences: name the method from the rising topic, the problem in the home field it could address, why the fit is plausible, and the first experiment to try. Cite exactly 3 of the arXiv ids above inline as arXiv:ID. Plain language, no hype. Output only the idea text."""
        idea = ask(prompt, model=a.model)
        con.execute("insert into merge_ideas values (current_date,?,?,?,?,?,?)", [int(c), labels.get(c, str(c)), d, g, idea, a.model])
        print(f"\n## {labels.get(c, c)}  (dist {d:.2f}, growth x{g:.1f}, {r}/{p})\n{idea}", flush=True)


if __name__ == "__main__":
    main()
