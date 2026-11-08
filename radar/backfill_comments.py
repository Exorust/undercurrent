"""Backfill the arXiv author comment (<arxiv:comment>) for every paper on the map.

    python -m radar.backfill_comments [--db data/radar.duckdb] [--out data/raw/comments] [--limit N] [--batch 200]
    python -m radar.backfill_comments --selftest

Writes chunked Parquet (id, comment) to --out, resumable, one request per >=3 s.
Never writes the database (opened read_only).
"""
import argparse, glob, os, re, signal, sys, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
import pyarrow as pa, pyarrow.parquet as pq
from .harvest_api import NS

API = "https://export.arxiv.org/api/query"
UA = "undercurrent/0.1 (+https://charoori.com/undercurrent/)"
GAP = 3.0
CHUNK = 5000
TRIES = 5
# harvest_api.parse defines its whitespace helper `sq` as a local lambda, so it cannot be imported; same regex here.
sq = lambda s: re.sub(r"\s+", " ", s or "").strip()

_last = [0.0]
_stop = [False]


def http_get(url):
    """One polite request: >=GAP seconds since the previous one."""
    wait = _last[0] + GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def parse_entries(xml_bytes):
    """-> {id_without_version: comment or None}"""
    out = {}
    for e in ET.fromstring(xml_bytes).iterfind("a:entry", NS):
        m = re.search(r"/abs/(.+?)(?:v\d+)?$", e.findtext("a:id", "", NS).strip())
        if not m:
            continue
        out[m.group(1)] = sq(e.findtext("x:comment", "", NS)) or None
    return out


def fetch_batch(ids, get=http_get, sleep=time.sleep):
    """Return ({id: comment}, missing_ids). Retries up to TRIES on errors or short feeds."""
    url = f"{API}?id_list={','.join(ids)}&max_results={len(ids)}"
    got = {}
    want = set(ids)
    for i in range(TRIES):
        try:
            got.update({k: v for k, v in parse_entries(get(url)).items() if k in want})
        except Exception as e:
            print(f"  api error {e!r}, try {i + 1}/{TRIES}", file=sys.stderr, flush=True)
        if want <= got.keys():
            return got, []
        if _stop[0]:
            break
        if i < TRIES - 1:
            if got:  # only ask for what is still missing
                rest = [x for x in ids if x not in got]
                url = f"{API}?id_list={','.join(rest)}&max_results={len(rest)}"
            sleep(min(10 * 2 ** i, 120))
    return got, [x for x in ids if x not in got]


def done_ids(out):
    seen = set()
    for f in glob.glob(os.path.join(out, "[0-9]*.parquet")):
        seen.update(pq.read_table(f, columns=["id"]).column("id").to_pylist())
    return seen


def write_chunk(out, rows):
    n = len(glob.glob(os.path.join(out, "[0-9]*.parquet")))
    path = os.path.join(out, f"{n:05d}.parquet")
    tmp = path + ".tmp"
    pq.write_table(pa.table({"id": [r[0] for r in rows], "comment": pa.array([r[1] for r in rows], pa.string())}), tmp)
    os.replace(tmp, path)


def run(ids, out, batch=200, get=http_get, sleep=time.sleep, chunk=CHUNK, quiet=False):
    os.makedirs(out, exist_ok=True)
    seen = done_ids(out)
    todo = [i for i in ids if i not in seen]
    total, start = len(ids), time.monotonic()
    pending, nfetched, ncomm, failed = [], 0, 0, []
    base = len(seen)

    def flush():
        nonlocal pending
        if pending:
            write_chunk(out, pending)
            pending = []

    def progress():
        el = max(time.monotonic() - start, 1e-9)
        rate = nfetched / el * 60
        eta = (len(todo) - nfetched - len(failed)) / rate / 60 if rate else float("nan")
        share = ncomm / nfetched if nfetched else 0
        if not quiet:
            print(f"{base + nfetched}/{total} done, {share:.1%} with comment, {rate:.0f} ids/min, ETA {eta:.2f} h", flush=True)

    for s in range(0, len(todo), batch):
        if _stop[0]:
            break
        b = todo[s:s + batch]
        got, miss = fetch_batch(b, get, sleep)
        for k in b:
            if k in got:
                pending.append((k, got[k]))
                nfetched += 1
                ncomm += got[k] is not None
        if miss:
            failed += miss
            with open(os.path.join(out, "failed.txt"), "a") as f:
                f.write("\n".join(miss) + "\n")
        if len(pending) >= chunk:
            flush()
            progress()
    flush()
    progress()
    return nfetched, ncomm, failed


def selftest():
    import tempfile
    ATOM = '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">{}</feed>'
    ent = lambda i, c: (f'<entry><id>http://arxiv.org/abs/{i}</id>' + (f'<arxiv:comment>{c}</arxiv:comment>' if c is not None else '') + '</entry>')
    # mapping: version stripped, whitespace squeezed, missing comment -> None, old-style id kept
    r = parse_entries(ATOM.format(ent("2310.06825v2", "12 pages,\n 3 figures") + ent("2310.00001v1", None) + ent("cs/0101001v3", "old")).encode())
    assert r == {"2310.06825": "12 pages, 3 figures", "2310.00001": None, "cs/0101001": "old"}, r
    # short feed -> retry for the rest
    calls = []
    def get(url):
        calls.append(url)
        if len(calls) == 1:
            return ATOM.format("").encode()
        if len(calls) == 2:
            return ATOM.format(ent("2310.00001v1", "a")).encode()
        return ATOM.format(ent("2310.00002v1", None)).encode()
    got, miss = fetch_batch(["2310.00001", "2310.00002"], get, lambda s: None)
    assert got == {"2310.00001": "a", "2310.00002": None} and not miss and len(calls) == 3, (got, miss, calls)
    assert "2310.00001" not in calls[2].split("id_list=")[1], calls[2]
    # permanent failure -> failed.txt
    d = tempfile.mkdtemp()
    n, c, f = run(["2310.00009"], d, get=lambda u: ATOM.format("").encode(), sleep=lambda s: None, quiet=True)
    assert f == ["2310.00009"] and open(os.path.join(d, "failed.txt")).read().split() == ["2310.00009"]
    # resume skip + chunking
    d = tempfile.mkdtemp()
    ids = [f"2310.{i:05d}" for i in range(7)]
    seen_urls = []
    def get2(url):
        seen_urls.append(url)
        want = url.split("id_list=")[1].split("&")[0].split(",")
        return ATOM.format("".join(ent(i + "v1", "c" if int(i[-1]) % 2 else None) for i in want)).encode()
    run(ids[:3], d, batch=2, get=get2, sleep=lambda s: None, chunk=2, quiet=True)
    assert len(glob.glob(d + "/0*.parquet")) == 2
    seen_urls.clear()
    n, c, f = run(ids, d, batch=10, get=get2, sleep=lambda s: None, quiet=True)
    assert n == 4 and not f and len(seen_urls) == 1 and "2310.00000" not in seen_urls[0], seen_urls
    t = pq.read_table(glob.glob(d + "/0*.parquet")[0])
    assert t.schema.names == ["id", "comment"]
    assert sorted(done_ids(d)) == ids
    print("ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/radar.duckdb")
    ap.add_argument("--out", default="data/raw/comments")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    import duckdb
    con = duckdb.connect(a.db, read_only=True)
    ids = [r[0] for r in con.execute("SELECT id FROM point_index ORDER BY id").fetchall()]
    con.close()
    if a.limit:
        ids = ids[:a.limit]
    def stop(*_):
        _stop[0] = True
        print("signal received, flushing after current batch", file=sys.stderr, flush=True)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    n, c, f = run(ids, a.out, a.batch)
    print(f"fetched {n} ({c} with comment), failed {len(f)}")


if __name__ == "__main__":
    main()
