"""Second harvester: arXiv search API (export.arxiv.org/api/query), by submission date.

Usage: python -m radar.harvest_api --from 2025-07-01 [--until YYYY-MM-DD] [--days 7]

Why it exists: on 2026-11-04 the OAI-PMH backend hung on every uncached query,
so April–November 2026 could not be harvested there. This API filters on the
true first-submission date, so it also picks up papers the OAI run missed
because they were revised later. Rows are upserted by id into the same table.
Each window is fetched in pages of 2000 and re-tried until the number of
entries matches the API's own totalResults (the API sometimes returns short pages).
"""
import argparse, datetime as dt, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
import duckdb
from .harvest import DB, RAW, SCHEMA

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/", "x": "http://arxiv.org/schemas/atom"}
PAGE = 2000
# cat:cs.* does not match papers whose only CS category is an alias: cs.NA is indexed as math.NA and cs.SY as eess.SY
# (found 2026-11-05: about 600 papers a month were missing). arXiv cross-lists every paper of those two to CS.
CATS = "cs.*,math.NA,eess.SY"


def get(url, tries=8):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "field-radar/0.1"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as e:
            print(f"  api error {e!r}, try {i + 1}/{tries}", file=sys.stderr, flush=True)
            time.sleep(min(15 * (i + 1), 120))
    raise RuntimeError("api fetch failed: " + url)


def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    total = int(root.findtext("o:totalResults", "0", NS))
    rows = []
    for e in root.iterfind("a:entry", NS):
        m = re.search(r"abs/(\d{4}\.\d{4,5})(v\d+)?$", e.findtext("a:id", "", NS))
        if not m: continue  # old-style ids are outside any 2023+ window
        pid = m.group(1)
        cats = [c.get("term") for c in e.iterfind("a:category", NS)]
        prim = e.find("x:primary_category", NS)
        sq = lambda s: re.sub(r"\s+", " ", s or "").strip()
        rows.append((pid, f"20{pid[:2]}-{pid[2:4]}-01", e.findtext("a:published", "", NS)[:10] or None, e.findtext("a:updated", "", NS)[:10] or None,
                     sq(e.findtext("a:title", "", NS)), sq(e.findtext("a:summary", "", NS)),
                     prim.get("term") if prim is not None else (cats[0] if cats else None), cats,
                     [sq(a.findtext("a:name", "", NS)) for a in e.iterfind("a:author", NS)]))
    return total, rows


def window(d0, d1, cats=CATS):
    """All papers in `cats` first submitted in [d0, d1]; retries until the count matches totalResults."""
    q = f"({' OR '.join('cat:' + c for c in cats.split(','))}) AND submittedDate:[{d0:%Y%m%d}0000 TO {d1:%Y%m%d}2359]"
    for attempt in range(5):
        got, total, start = {}, None, 0
        while total is None or start < total:
            url = f"{API}?{urllib.parse.urlencode({'search_query': q, 'start': start, 'max_results': PAGE, 'sortBy': 'submittedDate', 'sortOrder': 'ascending'})}"
            t, rows = parse(get(url))
            total = t if total is None else total
            for r in rows: got[r[0]] = r
            start += PAGE
            time.sleep(3)  # arXiv asks for one request per 3 seconds
        if total == 0 or len(got) >= 0.98 * total:
            return total, list(got.values())
        print(f"  short result {len(got)}/{total} for {d0}..{d1}, retry {attempt + 1}", file=sys.stderr, flush=True)
        time.sleep(20)
    return total, list(got.values())  # ponytail: accept a short window after 5 tries, logged above


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", required=True); ap.add_argument("--until", default=None); ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--cats", default=CATS, help="comma-separated arXiv categories")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    d, stop, kept = dt.date.fromisoformat(a.frm), dt.date.fromisoformat(a.until) if a.until else dt.date.today(), 0
    while d <= stop:
        d1 = min(d + dt.timedelta(days=a.days - 1), stop)
        mark = RAW / f"api_{'' if a.cats == 'cs.*' else a.cats.replace(',', '+').replace('*', 'x') + '_'}{d}_{d1}_done"
        if not mark.exists():
            total, rows = window(d, d1, a.cats)
            if rows:
                con.executemany("insert or replace into papers values (?,?,?,?,?,?,?,?,?,now())", rows)
            kept += len(rows); mark.touch()
            print(f"{d}..{d1}: {len(rows)}/{total} (run total {kept})", file=sys.stderr, flush=True)
        d = d1 + dt.timedelta(days=1)
    n = con.execute("select count(*) from papers").fetchone()[0]
    print(f"done: {kept} upserted this run, {n} papers in db")


if __name__ == "__main__":
    main()
