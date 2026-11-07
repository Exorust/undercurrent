"""Harvest arXiv CS metadata via OAI-PMH into DuckDB.

Usage: python -m radar.harvest [--from 2023-11-01] [--until YYYY-MM-DD]

OAI `from`/`until` filter on the record datestamp (last update), not on
submission date, so old papers that were updated recently appear too. The
`created` field is NOT the first-submission date (observed 2026-11-04: a 2022
paper showed created=2026-11-02), so the submission month is derived from the
id (YYMM.NNNNN) into `submitted`, and only ids at or after --from's month are
kept. Raw pages are saved gzipped in data/raw/ so the database can be rebuilt.
"""
import argparse, gzip, pathlib, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
import duckdb

OAI = "https://oaipmh.arxiv.org/oai"
NS = {"o": "http://www.openarchives.org/OAI/2.0/", "a": "http://arxiv.org/OAI/arXiv/"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA, RAW = ROOT / "data", ROOT / "data" / "raw"
import os
DB = pathlib.Path(os.environ.get("RADAR_DB", DATA / "radar.duckdb"))  # RADAR_DB lets a test run use a copy

SCHEMA = """
create table if not exists papers (
  id text primary key, submitted date, created date, updated date, title text, abstract text,
  primary_cat text, categories text[], authors text[], harvested_at timestamp default now());
"""


def fetch_vecs(con, sql):
    """Run a query whose last column is `vec` (float[768]); return (pandas df of the other columns, float32 matrix).
    Goes through Arrow so vectors never become Python lists (150k vectors as lists cost several GB)."""
    import numpy as np
    t = con.sql(sql).to_arrow_table()
    V = np.asarray(t.column("vec").combine_chunks().flatten(), dtype=np.float32).reshape(len(t), -1)
    return t.drop_columns(["vec"]).to_pandas(), V


def fetch(url, tries=12):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "undercurrent/0.1"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 503:  # ponytail: honour Retry-After, arXiv asks for it
                wait = int(e.headers.get("Retry-After", "20"))
                print(f"503, waiting {wait}s", file=sys.stderr); time.sleep(wait); continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"fetch error {e!r}, try {i + 1}/{tries}", file=sys.stderr, flush=True)
            time.sleep(min(30 * (i + 1), 300))
    raise RuntimeError("fetch failed: " + url)


def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    rows = []
    for rec in root.iterfind(".//o:record", NS):
        m = rec.find(".//a:arXiv", NS)
        if m is None:
            continue  # deleted record
        g = lambda tag: (m.findtext(f"a:{tag}", default="", namespaces=NS) or "").strip()
        cats = g("categories").split()
        authors = [" ".join(filter(None, [a.findtext("a:forenames", "", NS), a.findtext("a:keyname", "", NS)])).strip()
                   for a in m.iterfind("a:authors/a:author", NS)]
        pid = g("id")
        ym = re.match(r"^(\d{2})(\d{2})\.\d{4,5}$", pid)
        submitted = f"20{ym.group(1)}-{ym.group(2)}-01" if ym else None  # old-style ids (math/0501001) have no YYMM
        rows.append((pid, submitted, g("created") or None, g("updated") or None,
                     re.sub(r"\s+", " ", g("title")), re.sub(r"\s+", " ", g("abstract")),
                     cats[0] if cats else None, cats, authors))
    tok = root.find(".//o:resumptionToken", NS)
    size = tok.get("completeListSize") if tok is not None else None
    return rows, (tok.text.strip() if tok is not None and tok.text else None), size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2023-11-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--set", default="cs")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)

    # One request per calendar month of datestamps: a single multi-year `from`
    # never returned its first page (observed 2026-11-04), one month answers in <1s.
    import datetime as dt
    start = dt.date.fromisoformat(a.frm)
    stop = dt.date.fromisoformat(a.until) if a.until else dt.date.today()
    kept_total = 0
    m0 = start.replace(day=1)
    while m0 <= stop:
        m1 = (m0.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        frm, until = max(m0, start).isoformat(), min(m1, stop).isoformat()
        if (RAW / f"{a.set}_{frm}_done").exists():  # resumable per month
            m0 = m1 + dt.timedelta(days=1); continue
        q = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": a.set, "from": frm, "until": until}
        url = f"{OAI}?{urllib.parse.urlencode(q)}"
        page, kept, seen = 0, 0, 0
        while url:
            xml = fetch(url)
            (RAW / f"{a.set}_{frm}_{page:04d}.xml.gz").write_bytes(gzip.compress(xml))
            rows, tok, size = parse(xml)
            seen += len(rows)
            rows = [r for r in rows if r[1] and r[1] >= a.frm[:7] + "-01"]
            if rows:
                con.executemany("insert or replace into papers values (?,?,?,?,?,?,?,?,?,now())", rows)
                kept += len(rows)
            page += 1
            url = f"{OAI}?verb=ListRecords&resumptionToken={urllib.parse.quote(tok)}" if tok else None
            time.sleep(3)  # ponytail: fixed polite delay, arXiv asks ~3s between requests
        kept_total += kept
        (RAW / f"{a.set}_{frm}_done").touch()
        print(f"{frm}: {page} pages, seen {seen}, kept {kept} (total {kept_total})", file=sys.stderr, flush=True)
        m0 = m1 + dt.timedelta(days=1)
    n = con.execute("select count(*) from papers").fetchone()[0]
    print(f"done: {kept_total} kept this run, {n} papers in db")


if __name__ == "__main__":
    main()
