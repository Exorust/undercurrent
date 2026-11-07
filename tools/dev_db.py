"""Build data/dev.duckdb: a 20,000-paper copy of data/radar.duckdb for development runs (arch §8.2).

Usage: .venv/bin/python tools/dev_db.py [--n 20000]
Then:  RADAR_DB=data/dev.duckdb .venv/bin/python -m radar.map

The source database is attached read-only. Re-running replaces the dev database.
Copies: reservoir sample of `papers` (seed 42), the matching `embeddings` and `s2_meta`,
the latest run of `signals`, and the small `hn_counts` / `merge_ideas` tables as they are.
"""
import argparse, pathlib
import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC, DST = ROOT / "data" / "radar.duckdb", ROOT / "data" / "dev.duckdb"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20000); a = ap.parse_args()
    tmp = DST.with_suffix(".tmp.duckdb"); tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute(f"attach '{SRC}' as src (read_only)")
    con.execute(f"create table papers as select * from src.papers using sample reservoir({a.n} rows) repeatable (42)")
    con.execute("create table embeddings as select e.* from src.embeddings e semi join papers using(id)")
    con.execute("create table s2_meta as select s.* from src.s2_meta s semi join papers using(id)")
    con.execute("create table signals as select * from src.signals where run_date = (select max(run_date) from src.signals)")
    for t in ("hn_counts", "merge_ideas"):
        con.execute(f"create table {t} as select * from src.{t}")
    con.execute("detach src")
    counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("papers", "embeddings", "s2_meta", "signals")}
    con.close()
    tmp.replace(DST)  # ponytail: build aside then swap, so a failed run never leaves half a dev db
    pathlib.Path(str(tmp) + ".wal").unlink(missing_ok=True)
    print(f"{DST}: {counts}")


if __name__ == "__main__":
    main()
