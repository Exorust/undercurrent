"""Merge ideas: rising topics in the middle distance band from the home field.

Usage: python -m radar.merge [--home "metal kernel,gpu kernel,small language model"] [--n 8]

Home field = mean SPECTER2 vector of papers whose title matches any home
phrase (no external text-embedding model needed). Rising topics = level-1
map clusters ranked by growth (S1). For each topic in the band
[LO, HI] cosine distance, an LLM writes one merge idea citing 3 papers.
Distances are computed in 768-d, never from the 2-D map.
"""
import argparse, json, sys
import duckdb, numpy as np
from .harvest import DB
from .llm import ask

LO, HI = 0.25, 0.55  # ponytail: hand-set band; tune on feedback marks
DEFAULT_HOME = "metal kernel,gpu kernel,cuda kernel,apple silicon,small language model,on-device language model,agent harness,coding agent"
SCHEMA = "create table if not exists merge_ideas (run_date date, cluster int, label text, dist double, growth double, idea text, model text);"


def home_vector(con, phrases):
    like = " or ".join("lower(title) like '%" + p.strip().lower() + "%'" for p in phrases)
    V = con.execute(f"select vec from papers join embeddings using(id) where {like}").fetchnumpy()["vec"]
    if len(V) == 0: raise SystemExit("no home papers matched; change --home")
    M = np.vstack(V); print(f"home field: {len(M)} seed papers", file=sys.stderr)
    return M.mean(0)


def topic_growth(con, months=6):
    """S1: per level-1 cluster, recent vs prior window counts and centroid in 768-d."""
    df = con.execute("""
      with m as (select tm.cluster, strftime(p.submitted,'%Y-%m') ym, e.vec
                 from topic_members tm join papers p using(id) join embeddings e using(id) where tm.level=1),
      last as (select max(ym) mx from m)
      select cluster, ym, vec from m""").df()
    yms = sorted(df.ym.unique())[:-1]  # drop partial current month
    recent, prior = set(yms[-months:]), set(yms[-2 * months:-months])
    out = []
    for c, g in df.groupby("cluster"):
        r = g.ym.isin(recent).sum(); p = g.ym.isin(prior).sum()
        out.append((int(c), int(r), int(p), (r + 1) / (p + 1), np.vstack(g.vec.values).mean(0)))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--home", default=DEFAULT_HOME); ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    h = home_vector(con, a.home.split(","))
    labels = dict(con.execute("select cluster, label from topics where level=1").fetchall())
    cands = []
    for c, r, p, g, cen in topic_growth(con):
        if r < 30: continue
        d = 1 - float(cen @ h / (np.linalg.norm(cen) * np.linalg.norm(h)))
        if LO <= d <= HI: cands.append((g * np.log1p(r), c, d, g, r, p))
    cands.sort(reverse=True)
    print(f"{len(cands)} rising topics in band; writing {a.n} ideas", file=sys.stderr)
    con.execute("delete from merge_ideas where run_date = current_date")
    for _, c, d, g, r, p in cands[:a.n]:
        top = con.execute("""select p.id, p.title, coalesce(s.tldr, left(p.abstract, 400)) from topic_members tm join papers p using(id)
                             left join s2_meta s using(id) where tm.level=1 and tm.cluster=? order by coalesce(s.citations,0) desc limit 5""", [c]).fetchall()
        papers = "\n".join(f"- arXiv:{i} {t}. {s}" for i, t, s in top)
        prompt = f"""You help a researcher find ideas that merge two fields.
Home field (the researcher's own work): {a.home}.
Rising topic: "{labels.get(c, c)}" ({r} papers in the last 6 months, {p} in the 6 before).
Representative papers:
{papers}

Write ONE concrete merge idea in 4-6 sentences: name the method from the rising topic, the problem in the home field it could address, why the fit is plausible, and the first experiment to try. Cite exactly 3 of the arXiv ids above inline as arXiv:ID. Plain language, no hype."""
        idea = ask(prompt, model=a.model)
        con.execute("insert into merge_ideas values (current_date,?,?,?,?,?,?)", [c, labels.get(c, str(c)), d, g, idea, a.model])
        print(f"\n## {labels.get(c, c)}  (dist {d:.2f}, growth x{g:.1f}, {r}/{p})\n{idea}", flush=True)


if __name__ == "__main__":
    main()
