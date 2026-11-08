"""Stage `refs`: in-corpus citation edges from Semantic Scholar (T1.6, spike data/spikes/s3.json).

Usage:
  python -m radar.refs [--db PATH] [--out data/raw/refs] [--limit N] [--ids-file F] [--chunk 2000]
  python -m radar.refs --selftest        offline checks on a mocked HTTP function, prints ok

Reads table `papers` (column id) read-only; that id set is both the list of citing papers to fetch and the
filter for cited papers. Writes, under --out:
  NNNNN.parquet       edges, columns citing_id, cited_id (strings), deduped per citing paper, no self-citations
  done_NNNNN.parquet  column citing_id: every paper fetched for chunk NNNNN (including ones with zero edges or
                      unknown to S2). Written after the edge file; an edge file without its done file is an
                      orphan from a crash and is deleted on start, so its papers are refetched.
  failed.txt          ids of batches that still failed after 6 tries (not marked done; a rerun retries them)
Both files are written to a .tmp name then renamed. Ctrl-C / SIGTERM flushes the partial chunk before exit.

API facts from the spike: POST /graph/v1/paper/batch, batches of 100 (500 silently caps nested references near
10,000 per response, HTTP 200 with empty lists). Guard: total nested refs >= 9,000, or a paper with
referenceCount > 0 but an empty list while others are filled, splits the batch in half and refetches.
"""
import argparse, glob, os, re, signal, sys, time
import duckdb, pyarrow as pa, pyarrow.parquet as pq

API = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=externalIds,referenceCount,references.externalIds"
UA = "undercurrent/0.1 (+https://charoori.com/undercurrent/)"
BATCH = 100
CHUNK = 2000
TRUNC_REFS = 9000
SPACING = 1.0
TRIES = 6
DEFAULT_DB = "data/radar.duckdb"
DEFAULT_OUT = "data/raw/refs"
_VER = re.compile(r"v\d+$")


class Http:
    """POST one batch with ~1 s spacing between call starts and exponential backoff on 429/5xx/timeouts.
    Returns the parsed JSON list, or None after TRIES failures."""

    def __init__(self, spacing=SPACING, sleep=time.sleep):
        import requests
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.spacing, self.sleep, self.last = spacing, sleep, 0.0
        self.calls = 0

    def __call__(self, ids):
        for a in range(TRIES):
            w = self.spacing - (time.time() - self.last)
            if w > 0:
                self.sleep(w)
            self.last = time.time()
            self.calls += 1
            try:
                r = self.s.post(API, json={"ids": ["ARXIV:" + i for i in ids]}, timeout=120)
            except Exception as e:  # timeouts, connection resets
                print(f"  retry {a + 1}/{TRIES}: {type(e).__name__}", flush=True)
                self.sleep(2 ** (a + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                print(f"  retry {a + 1}/{TRIES}: HTTP {r.status_code}", flush=True)
                self.sleep(2 ** (a + 1))
                continue
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}: {r.text[:200]}", flush=True)
                return None
            try:
                j = r.json()
            except ValueError:
                self.sleep(2 ** (a + 1))
                continue
            if isinstance(j, list) and len(j) == len(ids):
                return j
            print(f"  unexpected body ({type(j).__name__}), retry", flush=True)
            self.sleep(2 ** (a + 1))
        return None


def truncated(resp):
    """True when a batch response looks silently capped (spike: 500-id batch, HTTP 200, later lists empty)."""
    papers = [p for p in resp if p]
    lens = [len(p.get("references") or []) for p in papers]
    if sum(lens) >= TRUNC_REFS:
        return True
    empty_with_count = any((p.get("referenceCount") or 0) > 0 and n == 0 for p, n in zip(papers, lens))
    return empty_with_count and any(n > 0 for n in lens)


def fetch(ids, http, stats):
    """Fetch one batch, splitting on the truncation guard. Returns {citing_id: response or None} or None on failure.
    A single-id batch is never split (an empty list there is S2's real answer)."""
    resp = http(ids)
    if resp is None:
        return None
    if len(ids) > 1 and truncated(resp):
        stats["splits"] += 1
        h = len(ids) // 2
        a, b = fetch(ids[:h], http, stats), fetch(ids[h:], http, stats)
        if a is None or b is None:
            return None
        return {**a, **b}
    return dict(zip(ids, resp))


def edges_of(citing, paper, corpus):
    """In-corpus, non-self, deduped cited ids for one paper response, in first-seen order."""
    out, seen = [], set()
    for rf in (paper or {}).get("references") or []:
        a = ((rf or {}).get("externalIds") or {}).get("ArXiv")
        if not a:
            continue
        a = _VER.sub("", a)
        if a == citing or a not in corpus or a in seen:
            continue
        seen.add(a)
        out.append(a)
    return out


def write_atomic(table, path):
    tmp = path + ".tmp"
    pq.write_table(table, tmp)
    os.replace(tmp, path)


def load_state(out):
    """Delete orphan edge chunks, return (done ids, edges so far, next chunk number)."""
    os.makedirs(out, exist_ok=True)
    for t in glob.glob(os.path.join(out, "*.tmp")):
        os.remove(t)
    done, edges, nums = set(), 0, [-1]
    for f in sorted(glob.glob(os.path.join(out, "[0-9][0-9][0-9][0-9][0-9].parquet"))):
        n = os.path.basename(f)[:5]
        d = os.path.join(out, f"done_{n}.parquet")
        if not os.path.exists(d):
            print(f"orphan chunk {n} (no done file), deleting; its papers will be refetched", flush=True)
            os.remove(f)
            continue
        nums.append(int(n))
        edges += pq.ParquetFile(f).metadata.num_rows
        done.update(pq.read_table(d, columns=["citing_id"]).column(0).to_pylist())
    return done, edges, max(nums) + 1


def flush(out, num, rows, done_ids):
    n = f"{num:05d}"
    cit = [r[0] for r in rows]
    ced = [r[1] for r in rows]
    write_atomic(pa.table({"citing_id": pa.array(cit, pa.string()), "cited_id": pa.array(ced, pa.string())}),
                 os.path.join(out, f"{n}.parquet"))
    write_atomic(pa.table({"citing_id": pa.array(list(done_ids), pa.string())}), os.path.join(out, f"done_{n}.parquet"))


def run(corpus, todo, out, http, chunk=CHUNK, batch=BATCH, log=print):
    """Fetch `todo` (ordered list) in batches; corpus is the filter set. Returns stats dict."""
    done, edges_total, num = load_state(out)
    todo = [i for i in todo if i not in done]
    total = len(done) + len(todo)
    stats = {"splits": 0, "failed": 0, "fetched": 0, "found": 0, "edges": 0, "chunks": 0}
    rows, chunk_done = [], []
    t0 = time.time()
    failed_path = os.path.join(out, "failed.txt")

    def write_chunk():
        nonlocal num, rows, chunk_done, edges_total
        if not chunk_done:
            return
        flush(out, num, rows, chunk_done)
        edges_total += len(rows)
        done.update(chunk_done)
        stats["chunks"] += 1
        el = time.time() - t0
        ppm = stats["fetched"] / el * 60 if el > 0 else 0
        left = total - len(done)
        eta = left / ppm / 60 if ppm else float("inf")
        log(f"chunk {num:05d}: {len(done):,}/{total:,} papers, {edges_total:,} edges, "
            f"{ppm:,.0f} papers/min, ETA {eta:.1f} h, splits {stats['splits']}, failed {stats['failed']}")
        num += 1
        rows, chunk_done = [], []

    try:
        for k in range(0, len(todo), batch):
            ids = todo[k:k + batch]
            got = fetch(ids, http, stats)
            if got is None:
                stats["failed"] += len(ids)
                with open(failed_path, "a") as f:
                    f.write("\n".join(ids) + "\n")
                log(f"  batch failed, {len(ids)} ids -> failed.txt")
                continue
            for i in ids:
                p = got.get(i)
                stats["found"] += p is not None
                e = edges_of(i, p, corpus)
                rows.extend((i, c) for c in e)
                stats["edges"] += len(e)
                chunk_done.append(i)
            stats["fetched"] += len(ids)
            if len(chunk_done) >= chunk:
                write_chunk()
    except KeyboardInterrupt:
        log("interrupted, flushing partial chunk")
        write_chunk()
        raise
    write_chunk()
    stats["elapsed"] = time.time() - t0
    return stats


def _sigterm(*_):
    raise KeyboardInterrupt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, help="fetch at most N not-yet-done papers this run")
    ap.add_argument("--ids-file", help="restrict citing papers to these ids (one per line)")
    ap.add_argument("--chunk", type=int, default=CHUNK)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    con = duckdb.connect(a.db, read_only=True)
    corpus_list = [r[0] for r in con.execute("select id from papers order by id").fetchall()]
    con.close()
    corpus = set(corpus_list)
    todo = corpus_list
    if a.ids_file:
        want = {l.strip() for l in open(a.ids_file) if l.strip()}
        todo = [i for i in corpus_list if i in want]
        print(f"--ids-file: {len(todo):,} of {len(want):,} ids are in the corpus", flush=True)
    done, _, _ = load_state(a.out)
    todo = [i for i in todo if i not in done]
    if a.limit is not None:
        todo = todo[:a.limit]
    print(f"corpus {len(corpus):,}, already done {len(done):,}, this run {len(todo):,}", flush=True)
    signal.signal(signal.SIGTERM, _sigterm)
    http = Http()
    try:
        s = run(corpus, todo, a.out, http, chunk=a.chunk, log=lambda m: print(m, flush=True))
    except KeyboardInterrupt:
        return 130
    print(f"done: fetched {s['fetched']:,} papers ({s['found']:,} known to S2), {s['edges']:,} edges "
          f"({s['edges'] / max(s['fetched'], 1):.2f}/paper), {http.calls} calls, {s['splits']} splits, "
          f"{s['failed']} failed, {s['elapsed']:.0f} s", flush=True)
    return 0


def selftest():
    import tempfile
    corpus = {f"2401.{i:05d}" for i in range(20)} | {"2402.00001"}

    def ref(a):
        return {"externalIds": {"ArXiv": a} if a else {"DOI": "x"}}

    def paper(i, refs, rc=None):
        return {"externalIds": {"ArXiv": i}, "referenceCount": len(refs) if rc is None else rc,
                "references": [ref(r) for r in refs]}

    # edges_of: version strip, out-of-corpus drop, self drop, dedupe, non-arXiv drop
    p = paper("2401.00000", ["2401.00001v2", "2401.00001", "2401.00000", "9999.99999", None, "2402.00001"])
    assert edges_of("2401.00000", p, corpus) == ["2401.00001", "2402.00001"], edges_of("2401.00000", p, corpus)
    assert edges_of("x", None, corpus) == []

    # truncation guard: empty-with-count suffix while others filled -> split; mocked server truncates batches > 2
    calls = []

    def http(ids):
        calls.append(list(ids))
        resp = [paper(i, ["2401.00005", "2401.00006"]) for i in ids]
        if len(ids) > 2:
            for p in resp[2:]:
                p["references"] = []  # referenceCount stays 2: looks truncated
        if "2401.00019" in ids:
            resp[ids.index("2401.00019")] = None  # unknown to S2
        return resp

    st = {"splits": 0}
    got = fetch([f"2401.{i:05d}" for i in range(8)], http, st)
    assert st["splits"] == 3 and len(calls) == 7, (st, calls)
    assert all(len(got[k]["references"]) == 2 for k in got)
    # total >= 9,000 nested refs triggers too
    assert truncated([paper("a", ["b"] * 9000)])
    assert not truncated([paper("a", ["b"] * 10), paper("c", [], rc=0)])
    # a single-id batch with an empty list is accepted, not split forever
    st = {"splits": 0}
    one = fetch(["2401.00003"], lambda ids: [paper(ids[0], [], rc=5)], st)
    assert st["splits"] == 0 and one["2401.00003"]["references"] == []

    with tempfile.TemporaryDirectory() as out:
        quiet = lambda m: None
        todo = sorted(corpus)
        calls.clear()

        def http2(ids):  # batch of 2, no truncation (http() itself records the call)
            return http(ids)

        # first run: interrupt after 3 batches; partial chunk must be flushed
        n = [0]

        def http_int(ids):
            n[0] += 1
            if n[0] == 4:
                raise KeyboardInterrupt
            return http2(ids)

        try:
            run(corpus, todo, out, http_int, chunk=4, batch=2, log=quiet)
            raise AssertionError("expected interrupt")
        except KeyboardInterrupt:
            pass
        done, edges, nxt = load_state(out)
        assert len(done) == 6 and nxt == 2, (len(done), nxt)
        # orphan edge chunk (crash between files) is deleted on start
        pq.write_table(pa.table({"citing_id": ["x"], "cited_id": ["y"]}), os.path.join(out, "00007.parquet"))
        calls.clear()
        s = run(corpus, todo, out, http2, chunk=4, batch=2, log=quiet)
        assert not os.path.exists(os.path.join(out, "00007.parquet"))
        fetched = [i for c in calls for i in c]
        assert not set(fetched) & done and len(fetched) == len(corpus) - 6, "resume refetched done ids"
        assert s["found"] == len(corpus) - 6 - 1  # 2401.00019 is None
        # final contents: every corpus id done once; edges deduped, self-citation dropped
        done, edges, _ = load_state(out)
        assert done == corpus
        t = pa.concat_tables([pq.read_table(f) for f in sorted(glob.glob(os.path.join(out, "[0-9]*.parquet")))])
        pairs = list(zip(t.column("citing_id").to_pylist(), t.column("cited_id").to_pylist()))
        assert len(pairs) == len(set(pairs))
        assert not any(a == b for a, b in pairs)
        assert ("2401.00005", "2401.00006") in pairs and ("2401.00005", "2401.00005") not in pairs
        # 21 papers, 19 cite both 5 and 6, minus self-cites by 5 and 6, minus None paper 19 -> 2*20-2-... computed:
        expect = sum(len({"2401.00005", "2401.00006"} - {i}) for i in corpus if i != "2401.00019")
        assert len(pairs) == edges == expect, (len(pairs), edges, expect)
        # failed batch is recorded and not marked done
        out2 = os.path.join(out, "f")
        s = run(corpus, ["2401.00001", "2401.00002"], out2, lambda ids: None, chunk=4, batch=2, log=quiet)
        assert s["failed"] == 2 and open(os.path.join(out2, "failed.txt")).read().split() == ["2401.00001", "2401.00002"]
        assert load_state(out2)[0] == set()
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
