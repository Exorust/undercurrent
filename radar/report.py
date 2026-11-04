"""Write report/YYYY-MM.md from the signals, hn_counts and merge_ideas tables.
Usage: python -m radar.report"""
import datetime as dt, pathlib
import duckdb
from .harvest import DB, ROOT


def main():
    con = duckdb.connect(str(DB), read_only=True)
    n, lo, hi = con.execute("select count(*), min(submitted), max(submitted) from papers").fetchone()
    L = [f"# field-radar report {dt.date.today()}", "", f"{n:,} arXiv CS papers, {lo:%Y-%m} to {hi:%Y-%m}. Growth = last 6 full months vs the 6 before.", ""]
    L += ["## Rising phrases (S3)", "", "| phrase | recent/prior | growth | first seen | author groups | HN last 6 mo |", "|---|---|---|---|---|---|"]
    for k, r, p, g, f, b in con.execute("""select key, recent, prior, growth, first_seen, breadth from signals
        where signal='S3' and run_tag='live' and run_date=(select max(run_date) from signals where run_tag='live') order by score desc limit 30""").fetchall():
        hn = con.execute("select coalesce(sum(n),0) from (select n from hn_counts where term=? order by ym desc limit 6)", [k]).fetchone()[0]
        L.append(f"| {k} | {r}/{p} | x{g:.1f} | {f} | {b} | {hn} |")
    L += ["", "## Converging fields (S2)", "", "| category pair | recent/prior | growth |", "|---|---|---|"]
    for k, r, p, g in con.execute("""select key, recent, prior, growth from signals where signal='S2' and run_tag='live'
        and run_date=(select max(run_date) from signals where run_tag='live') order by score desc limit 20""").fetchall():
        L.append(f"| {k} | {r}/{p} | x{g:.1f} |")
    L += ["", "## Merge ideas", ""]
    for lab, d, g, idea in con.execute("select label, dist, growth, idea from merge_ideas where run_date=(select max(run_date) from merge_ideas) order by growth desc").fetchall():
        L += [f"### {lab}", f"*distance from home {d:.2f}, growth x{g:.1f}*", "", idea, ""]
    L += ["", "Map: `report/map.html`. Mark items useful / not useful in `notes` to tune thresholds."]
    out = ROOT / "report" / f"{dt.date.today():%Y-%m}.md"
    out.parent.mkdir(exist_ok=True); out.write_text("\n".join(L))
    print(f"done: {out}")


if __name__ == "__main__":
    main()
