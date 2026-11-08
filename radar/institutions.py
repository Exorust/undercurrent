"""Fetch author institutions for our papers from OpenAlex into chunked Parquet.

Method (from data/spikes/s2.json): list filter on arXiv DOIs, 100 per call, 5 in
flight; map results back to our ids through `locations` (the primary doi is often
a journal DOI); ids the list call missed get a free single lookup (404 = not in
OpenAlex). Resumable; stops cleanly when credits fall under 500.

  python -m radar.institutions [--db PATH] [--out data/raw/openalex] [--limit N] [--since YYYY-MM-DD]
  python -m radar.institutions --selftest
"""
import argparse, os, re, signal, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.openalex.org"
UA = "undercurrent/0.1 (+https://charoori.com/undercurrent/)"
SEL = "id,doi,locations,authorships"
BATCH, WORKERS, CHUNK, MIN_CREDITS, MAX_TRY = 100, 5, 5000, 500, 5
COLS = ["id", "author_pos", "inst_id", "ror", "name", "country", "type"]

STOP = threading.Event()      # SIGTERM / SIGINT
BUDGET = threading.Event()    # credits low
STATE = {"remaining": None, "used": 0, "first_remaining": None}
RUN_FAILED = [0]              # ids that still failed after retries in the last run() (main exits 3 when > 0)
_lock = threading.Lock()


def load_key():
    k = os.environ.get("OPENALEX_KEY")
    if not k:
        p = os.path.join(ROOT, ".env")
        if os.path.exists(p):
            for line in open(p):
                m = re.match(r"\s*(?:export\s+)?OPENALEX_KEY\s*=\s*(.*)", line)
                if m:
                    k = m.group(1).strip().strip("'\"")
    return k or None


def _http(url, params, key):
    """Real HTTP. Returns (status, json_or_None, headers). status None = gave up.
    Never logs the URL or key."""
    import requests
    p = dict(params); p["api_key"] = key
    for att in range(MAX_TRY):
        try:
            r = _http.s.get(url, params=p, timeout=60, headers={"User-Agent": UA})
        except requests.RequestException:
            time.sleep(min(2 ** att, 30)); continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(2 ** att, 30)); continue
        try:
            body = r.json() if r.status_code == 200 else None
        except ValueError:
            body = None
        return r.status_code, body, r.headers
    return None, None, {}


def _session():
    import requests
    _http.s = requests.Session()


HTTP = _http  # replaced in selftest


def call(url, params, key):
    st, body, hdr = HTTP(url, params, key)
    rem = hdr.get("X-RateLimit-Remaining") if hdr else None
    if rem is not None:
        try:
            rem = float(rem)
            with _lock:
                if STATE["first_remaining"] is None:
                    STATE["first_remaining"] = rem
                STATE["remaining"] = rem
                STATE["used"] = STATE["first_remaining"] - rem
            if rem < MIN_CREDITS:
                BUDGET.set()
        except ValueError:
            pass
    return st, body


ARX = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.)(\d{4}\.\d{4,5})", re.I)


def arxiv_ids_of(w):
    blob = " ".join([(w.get("doi") or "")] + [
        f"{l.get('landing_page_url')} {l.get('pdf_url')}" for l in (w.get("locations") or [])])
    return {m.group(1) for m in ARX.finditer(blob)}


def rows_for(pid, w):
    out = []
    for pos, a in enumerate(w.get("authorships") or []):
        for ins in a.get("institutions") or []:
            iid = ins.get("id") or ""
            out.append((pid, pos, iid.rsplit("/", 1)[-1] or None, ins.get("ror"),
                        ins.get("display_name"), ins.get("country_code"), ins.get("type")))
    if not out:
        out.append((pid, None, None, None, None, None, None))
    return out


def fetch_batch(ids, key):
    """-> (found{id: work}, ok). One list call."""
    flt = "doi:" + "|".join(f"10.48550/arXiv.{i}" for i in ids)
    st, body = call(f"{BASE}/works", {"filter": flt, "per-page": 100, "select": SEL}, key)
    if st != 200 or body is None:
        return {}, False
    want = set(ids); found = {}
    for w in body.get("results", []):
        for i in arxiv_ids_of(w) & want:
            found[i] = w
    return found, True


def fetch_one(i, key):
    """-> ('ok', work) | ('404', None) | ('fail', None)"""
    st, body = call(f"{BASE}/works/doi:10.48550/arXiv.{i}", {"select": SEL}, key)
    if st == 200 and body: return "ok", body
    if st == 404: return "404", None
    return "fail", None


def process(ids, key, ex):
    """Look up a group of ids. -> rows, notfound, failed, looked_up_count"""
    rows, nf, failed = [], [], []
    found = {}
    batches = [ids[k:k + BATCH] for k in range(0, len(ids), BATCH)]
    futs = {}
    pending = set()
    it = iter(batches)
    skipped = []
    def submit_next():
        if STOP.is_set() or BUDGET.is_set():
            return False
        b = next(it, None)
        if b is None: return False
        f = ex.submit(fetch_batch, b, key); futs[f] = b; pending.add(f)
        return True
    for _ in range(WORKERS * 2):
        if not submit_next(): break
    listed = set(); bad = []
    while pending:
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        for f in done:
            pending.discard(f); b = futs[f]
            fnd, ok = f.result()
            if ok:
                found.update(fnd); listed.update(b)
            else:
                bad.append(b)
            submit_next()
    for b in it: skipped.extend(b)           # never submitted (stop/budget)
    for b in bad: failed.extend(b)           # list call failed after retries
    # fallback single lookups (free) for ids list call answered but did not return
    missing = [i for i in ids if i in listed and i not in found]
    results = list(ex.map(lambda i: (i, *fetch_one(i, key)), missing)) if not STOP.is_set() else []
    done_fb = set()
    for i, s, w in results:
        done_fb.add(i)
        if s == "ok": found[i] = w
        elif s == "404": nf.append(i)
        else: failed.append(i)
    # on stop before fallback finished: those ids are simply left for next run
    for i in ids:
        if i in found: rows.extend(rows_for(i, found[i]))
    looked = len(found) + len(nf)
    return rows, nf, failed, looked


def write_chunk(out, rows, idx):
    import pyarrow as pa, pyarrow.parquet as pq
    cols = list(zip(*rows)) if rows else [[] for _ in COLS]
    t = pa.table({
        "id": pa.array(cols[0], pa.string()), "author_pos": pa.array(cols[1], pa.int32()),
        "inst_id": pa.array(cols[2], pa.string()), "ror": pa.array(cols[3], pa.string()),
        "name": pa.array(cols[4], pa.string()), "country": pa.array(cols[5], pa.string()),
        "type": pa.array(cols[6], pa.string())})
    final = os.path.join(out, f"{idx:05d}.parquet")
    fd, tmp = tempfile.mkstemp(dir=out, suffix=".tmp"); os.close(fd)
    pq.write_table(t, tmp); os.replace(tmp, final)
    return final


def read_notfound(out):
    entries = {}
    p = os.path.join(out, "notfound.txt")
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                if not line.strip(): continue
                parts = line.strip().split("\t", 1)
                entries[parts[0]] = date.fromisoformat(parts[1]) if len(parts) == 2 else date.today()
    return entries


def write_notfound(out, entries):
    fd, tmp = tempfile.mkstemp(dir=out, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for pid, day in sorted(entries.items()):
                f.write(f"{pid}\t{day.isoformat()}\n")
        os.replace(tmp, os.path.join(out, "notfound.txt"))
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def done_ids(out, retry_notfound=False):
    import pyarrow.parquet as pq
    s = set(); mx = -1
    for fn in sorted(os.listdir(out)):
        if fn.endswith(".parquet") and fn[:-8].isdigit():
            mx = max(mx, int(fn[:-8]))
            s.update(pq.read_table(os.path.join(out, fn), columns=["id"]).column("id").to_pylist())
    if not retry_notfound:
        s.update(pid for pid, day in read_notfound(out).items()
                 if (date.today() - day).days < 30)
    return s, mx + 1


def append_lines(path, lines):
    if lines:
        with open(path, "a") as f: f.write("\n".join(lines) + "\n")


def read_ids(db, since=None, limit=None, done=()):
    """Candidate ids from `papers` (not point_index, which is the last map and misses papers harvested since),
    minus ids already looked up (`done`: in a chunk or notfound.txt). load_extra keeps only point_index rows,
    so extra ids cost credits but never reach the map. `since` is optional and month-safe: papers.submitted
    is stored as the first of the month, so it is compared against the month start of the given date."""
    import duckdb
    c = duckdb.connect(db, read_only=True)
    if since:
        q = "select id from papers where submitted >= date_trunc('month', cast(? as date)) order by id"
        r = c.execute(q, [since]).fetchall()
    else:
        r = c.execute("select id from papers order by id").fetchall()
    c.close()
    ids = [x[0] for x in r if x[0] not in done]
    return ids[:limit] if limit else ids


def run(ids, out, key, chunk=CHUNK, retry_notfound=False):
    os.makedirs(out, exist_ok=True)
    done, idx = done_ids(out, retry_notfound)
    todo = [i for i in ids if i not in done]
    notfound = read_notfound(out)
    for pid in todo: notfound.pop(pid, None)
    # Persist legacy dates and remove queued retries, including interrupted/failed lookups.
    write_notfound(out, notfound)
    total = len(ids)
    print(f"{len(ids)} ids, {len(ids) - len(todo)} already done, {len(todo)} to fetch", flush=True)
    t0 = time.time(); n_done = len(ids) - len(todo); with_inst = 0; seen = 0
    RUN_FAILED[0] = 0
    ex = ThreadPoolExecutor(WORKERS)
    try:
        for k in range(0, len(todo), chunk):
            if STOP.is_set() or BUDGET.is_set(): break
            group = todo[k:k + chunk]
            rows, nf, failed, looked = process(group, key, ex)
            if rows or nf:
                if rows: write_chunk(out, rows, idx); idx += 1
                notfound.update((pid, date.today()) for pid in nf)
                write_notfound(out, notfound)
            append_lines(os.path.join(out, "failed.txt"), failed)
            RUN_FAILED[0] += len(failed)
            n_done += looked
            inst_papers = len({r[0] for r in rows if r[2]}); with_inst += inst_papers; seen += looked
            el = time.time() - t0
            rate = (n_done - (len(ids) - len(todo))) / el if el else 0
            eta = (total - n_done) / rate if rate else float("nan")
            rem = STATE["remaining"]
            print(f"chunk {idx - 1 if rows else '-'}: {n_done}/{total} done, "
                  f"with-institution {with_inst / max(seen, 1):.1%}, credits remaining "
                  f"{int(rem) if rem is not None else '?'}, ETA {eta:.0f}s", flush=True)
    finally:
        ex.shutdown(wait=True)
    if BUDGET.is_set():
        print("Credits under 500: stopped cleanly. Progress saved; the run will resume tomorrow.")
    elif STOP.is_set():
        print("Interrupted: progress saved; rerun to resume.")
    return n_done


# ---------------------------------------------------------------- selftest
def selftest():
    import pyarrow.parquet as pq, shutil
    global HTTP
    tmp = tempfile.mkdtemp()
    def W(aid, doi, auths, extra_locs=()):
        locs = [{"landing_page_url": f"http://arxiv.org/abs/{aid}v2", "pdf_url": None}] + list(extra_locs)
        return {"id": "https://openalex.org/W" + aid.replace(".", ""), "doi": doi, "locations": locs,
                "authorships": auths}
    inst = {"id": "https://openalex.org/I123", "ror": "https://ror.org/x", "display_name": "MIT",
            "country_code": "US", "type": "education"}
    A, B, C, D = "2401.00001", "2401.00002", "2401.00003", "2401.00004"
    calls = {"list": 0, "single": 0}
    remaining = [9000.0]
    def fake(url, params, key):
        hdr = {"X-RateLimit-Remaining": str(remaining[0])}
        if url.endswith("/works"):
            calls["list"] += 1; remaining[0] -= 1
            # A: journal DOI primary, arXiv only via locations; B: no institutions; C, D not returned
            return 200, {"results": [
                W(A, "https://doi.org/10.1/journal", [{"institutions": [inst]}, {"institutions": []}]),
                W(B, "https://doi.org/10.48550/arxiv." + B, [{"institutions": []}])]}, hdr
        calls["single"] += 1
        if C in url: return 200, W(C, "https://doi.org/10.48550/arxiv." + C, [{"institutions": [inst, inst]}]), hdr
        return 404, None, hdr
    HTTP = fake
    out = os.path.join(tmp, "o")
    run([A, B, C, D], out, "k")
    t = pq.read_table(os.path.join(out, "00000.parquet")).to_pylist()
    byid = {}
    for r in t: byid.setdefault(r["id"], []).append(r)
    assert byid[A][0]["inst_id"] == "I123" and byid[A][0]["author_pos"] == 0 and len(byid[A]) == 1, "location mapping"
    assert len(byid[B]) == 1 and byid[B][0]["inst_id"] is None, "no-institution row"
    assert len(byid[C]) == 2, "fallback single lookup"
    assert D not in byid and read_notfound(out) == {D: date.today()}, "404 to notfound"
    assert calls["single"] == 2
    # resume: nothing refetched
    calls["list"] = calls["single"] = 0
    run([A, B, C, D], out, "k")
    assert calls == {"list": 0, "single": 0}, "resume skip"
    assert len([f for f in os.listdir(out) if f.endswith(".parquet")]) == 1
    # Legacy entries acquire a persistent date; only entries at least 30 days old retry.
    out_retry = os.path.join(tmp, "retry"); os.makedirs(out_retry)
    with open(os.path.join(out_retry, "notfound.txt"), "w") as f:
        f.write(f"{A}\n{C}\t{date.today() - timedelta(days=31)}\n"
                f"{D}\t{date.today() - timedelta(days=5)}\n")
    assert read_notfound(out_retry)[A] == date.today(), "undated lines read as today"
    calls["list"] = calls["single"] = 0
    run([A, C, D], out_retry, "k")
    assert calls == {"list": 1, "single": 1}, "only old notfound retried"
    assert pq.read_table(os.path.join(out_retry, "00000.parquet")).column("id").to_pylist() == [C, C], "old notfound found"
    assert read_notfound(out_retry) == {A: date.today(), D: date.today() - timedelta(days=5)}, "recent skipped, found removed"
    assert f"{A}\t{date.today()}" in open(os.path.join(out_retry, "notfound.txt")).read(), "legacy date persisted"
    # Exercise the actual CLI flag, including main's done filtering.
    from unittest.mock import patch
    calls["list"] = calls["single"] = 0
    with patch.object(sys, "argv", ["institutions", "--out", out_retry, "--retry-notfound"]), \
         patch(__name__ + ".load_key", return_value="k"), \
         patch(__name__ + "._session"), patch(__name__ + ".signal.signal"), \
         patch(__name__ + ".read_ids", side_effect=lambda db, since, limit, done: [i for i in [A, C, D] if i not in done]):
        assert main() == 0
    assert calls == {"list": 1, "single": 1}, "flag retries recent and legacy entries"
    assert read_notfound(out_retry) == {D: date.today()}, "retry 404 re-dated, found removed"
    # budget stop
    out2 = os.path.join(tmp, "o2"); remaining[0] = 400.0; BUDGET.clear()
    ids = [f"2402.{n:05d}" for n in range(300)]
    run(ids, out2, "k", chunk=100)
    assert BUDGET.is_set(), "budget stop"
    assert not os.path.exists(os.path.join(out2, "00001.parquet")), "stopped after first chunk"
    BUDGET.clear()
    assert RUN_FAILED[0] == 0
    # failed batches are counted (main exits 3) and are not marked done
    remaining[0] = 9000.0
    HTTP = lambda url, params, key: (None, None, {})
    out3 = os.path.join(tmp, "o3")
    run(["2403.00001", "2403.00002"], out3, "k")
    assert RUN_FAILED[0] == 2 and "2403.00001" not in done_ids(out3)[0], "failed ids counted, left for next run"
    # read_ids: candidates from papers (not point_index), done ids dropped, --since month-safe
    import duckdb
    dbp = os.path.join(tmp, "t.duckdb"); c = duckdb.connect(dbp)
    c.execute("create table papers(id text, submitted date)")
    c.execute("insert into papers values ('2410.00001','2024-10-01'),('2411.00001','2024-11-01'),('2411.00002','2024-11-01')")
    c.execute("create table point_index(id text)"); c.execute("insert into point_index values ('2410.00001')"); c.close()
    assert read_ids(dbp) == ["2410.00001", "2411.00001", "2411.00002"], "all papers, not point_index"
    assert read_ids(dbp, since="2024-11-20") == ["2411.00001", "2411.00002"], "since compares month start"
    assert read_ids(dbp, done={"2411.00001"}, limit=2) == ["2410.00001", "2411.00002"], "done dropped before limit"
    shutil.rmtree(tmp)
    print("ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.path.join(ROOT, "data/radar.duckdb"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data/raw/openalex"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--since")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--retry-notfound", action="store_true", help="retry all cached OpenAlex 404s now")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    key = load_key()
    if not key:
        print("OPENALEX_KEY not set (environment or ~/myproj/field-radar/.env)", file=sys.stderr)
        sys.exit(2)
    _session()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: STOP.set())
    os.makedirs(a.out, exist_ok=True)
    ids = read_ids(a.db, a.since, a.limit, done_ids(a.out, a.retry_notfound)[0])
    t0 = time.time()
    run(ids, a.out, key, retry_notfound=a.retry_notfound)
    print(f"credits used this run: {STATE['used']:.0f}, seconds: {time.time() - t0:.0f}")
    if RUN_FAILED[0] and not BUDGET.is_set():
        print(f"{RUN_FAILED[0]} ids failed after retries this run (listed in failed.txt); exit 3", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
