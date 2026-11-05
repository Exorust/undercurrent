"""Write report/YYYY-MM.md from the signals, hn_counts and merge_ideas tables.
Usage: python -m radar.report"""
import datetime as dt, re
import duckdb
from .harvest import DB, ROOT


def norm(p):  # "world-action models" and "world action model" are one topic
    return re.sub(r"(ing|s)\b", "", p.replace("-", " "))


def families(rows, top):
    """Collapse spelling variants and nested phrases into the highest-ranked member; rows are sorted by score."""
    kept = []
    for r in rows:
        k = norm(r[0])
        fam = next((f for f in kept if k == f["k"] or k in f["k"] or f["k"] in k), None)
        if fam: fam["variants"].append(r[0])
        elif len(kept) < top: kept.append({"k": k, "row": r, "variants": []})
    return kept


def main():
    con = duckdb.connect(str(DB), read_only=True)
    n, lo, hi = con.execute("select count(*), min(submitted), max(submitted) from papers").fetchone()
    live = "run_tag='live' and run_date=(select max(run_date) from signals where run_tag='live')"
    L = [f"# field-radar report {dt.date.today()}", "",
         f"{n:,} arXiv CS papers, {lo:%Y-%m} to {hi:%Y-%m}. Growth compares the last 6 full months with the 6 before, corrected for the growth of CS as a whole.", ""]
    bt = ROOT / "BACKTEST.md"
    if bt.exists():
        # the summary part of BACKTEST.md, without its title, headings one level down
        L += ["## How much to trust this", ""] + bt.read_text().split("\n## Details")[0].replace("\n## ", "\n### ").split("\n")[2:] + [""]
    L += ["## Rising phrases (S3)", "", "| phrase | papers recent/prior | growth | first seen | author groups | HN mentions, last 6 mo | variants |", "|---|---|---|---|---|---|---|"]
    rows = con.execute(f"select key, recent, prior, growth, first_seen, breadth from signals where signal='S3' and {live} order by score desc").fetchall()
    has_hn = "hn_counts" in [r[0] for r in con.execute("show tables").fetchall()]
    for f in families(rows, 40):
        k, r, p, g, fs, b = f["row"]
        hn = con.execute("select coalesce(sum(n),0) from (select n from hn_counts where term=? order by ym desc limit 6)", [k]).fetchone()[0] if has_hn else ""
        L.append(f"| {k} | {r}/{p} | x{g:.1f} | {fs:%Y-%m} | {b} | {hn} | {', '.join(f['variants'][:4])} |")
    L += ["", "## Converging fields (S2)", "",
          "Category pairs that shared more papers in the last 6 months than in the 6 before. In the backtest above, flagged pairs did not keep growing "
          "more often than other pairs, so read this as a description of the recent past. arXiv also changes its cross-listing practice from time to time, "
          "which moves many pairs of one category together.", "",
          "| category pair | papers recent/prior | growth |", "|---|---|---|"]
    for k, r, p, g in con.execute(f"select key, recent, prior, growth from signals where signal='S2' and {live} order by score desc limit 15").fetchall():
        L.append(f"| {k} | {r}/{p} | x{g:.1f} |")
    if "merge_ideas" in [r[0] for r in con.execute("show tables").fetchall()]:
        L += ["", "## Merge ideas", "", "Written by an LLM from 5 papers per topic. Treat each as a prompt for your own thinking, and check the cited papers.", ""]
        for lab, d, g, idea in con.execute("select label, dist, growth, idea from merge_ideas where run_date=(select max(run_date) from merge_ideas) order by growth desc").fetchall():
            L += [f"### {lab}", f"*cosine distance from home field {d:.2f}, topic growth x{g:.1f}*", "", idea, ""]
    L += ["", "Map: `report/map.html` (open in a browser)."]
    out = ROOT / "report" / f"{dt.date.today():%Y-%m}.md"
    out.parent.mkdir(exist_ok=True); out.write_text("\n".join(L) + "\n")
    print(f"done: {out}")


if __name__ == "__main__":
    main()
