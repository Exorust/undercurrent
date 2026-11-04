"""S4: HN mention counts per month for terms flagged by S3.

Usage: python -m radar.hn [--top 40] [--months 24]
Reads the latest S3 rows from `signals`, queries the HN Algolia API for each
term (stories + comments, exact phrase), stores monthly counts in `hn_counts`,
and prints terms where HN mentions rose before the paper count did.
"""
import argparse, datetime as dt, json, sys, time, urllib.parse, urllib.request
import duckdb
from .harvest import DB

SCHEMA = "create table if not exists hn_counts (term text, ym text, n int, primary key(term, ym));"


def hn_count(term, a, b):
    q = urllib.parse.quote(f'"{term}"')
    u = f"https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=(story,comment)&numericFilters=created_at_i>={a},created_at_i<{b}&hitsPerPage=0"
    for _ in range(3):
        try:
            with urllib.request.urlopen(u, timeout=30) as r: return json.load(r)["nbHits"]
        except Exception:
            time.sleep(2)
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--top", type=int, default=40); ap.add_argument("--months", type=int, default=24)
    a = ap.parse_args()
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    terms = [r[0] for r in con.execute(
        "select key from signals where signal='S3' and run_date=(select max(run_date) from signals) order by score desc limit ?", [a.top]).fetchall()]
    today = dt.date.today().replace(day=1)
    months = []
    m = today
    for _ in range(a.months):
        months.append(m); m = (m - dt.timedelta(days=1)).replace(day=1)
    months.reverse()
    for t in terms:
        rows = []
        for m0 in months:
            m1 = (m0.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            ts = lambda d: int(dt.datetime(d.year, d.month, 1, tzinfo=dt.timezone.utc).timestamp())
            n = hn_count(t, ts(m0), ts(m1))
            if n is not None: rows.append((t, m0.strftime("%Y-%m"), n))
            time.sleep(0.3)
        con.executemany("insert or replace into hn_counts values (?,?,?)", rows)
        total = sum(r[2] for r in rows); recent = sum(r[2] for r in rows[-6:])
        print(f"{t:45s} hn total {total:5d}  last 6 mo {recent:4d}", flush=True)


if __name__ == "__main__":
    main()
