"""Enrich papers from Semantic Scholar: SPECTER2 embedding, tldr, citations.

Usage: python -m radar.enrich [--limit N]
       python -m radar.enrich --refresh [--older-days 25] [--limit N]   (re-fetch citations + venue only)
Set S2_API_KEY in the environment for higher rate limits (free key).
Resumable: only papers with no row in s2_meta are requested.
"""
import argparse, json, os, sys, time, urllib.request
import duckdb, numpy as np, pyarrow as pa
from .harvest import DB

URL = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=externalIds,citationCount,influentialCitationCount,tldr,embedding.specter_v2,publicationVenue,venue"
REFRESH_FIELDS = "externalIds,citationCount,influentialCitationCount,publicationVenue,venue"
SCHEMA = """
create table if not exists s2_meta (
  id text primary key, s2_id text, citations int, influential int, tldr text, fetched_at timestamp default now());
alter table s2_meta add column if not exists s2_venue text;
create table if not exists embeddings (id text primary key, vec float[768]);
"""


def venue_of(p):
    v = ((p.get("publicationVenue") or {}).get("name") or "").strip() or (p.get("venue") or "").strip()
    return v or None


def post(ids, tries=8, url=URL):
    body = json.dumps({"ids": [f"ARXIV:{i}" for i in ids]}).encode()
    hdr = {"Content-Type": "application/json", "User-Agent": "undercurrent/0.1"}
    if k := os.environ.get("S2_API_KEY"): hdr["x-api-key"] = k
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, hdr), timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(10 * (i + 1), 60)); continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(15)
    raise RuntimeError("s2 batch failed")


def refresh(con, older_days, limit):
    """Re-fetch citation counts + venue for papers whose s2_meta row is older than older_days. Keeps tldr/embeddings."""
    todo = [r[0] for r in con.execute(
        f"select m.id from s2_meta m left join papers p on p.id = m.id where "
        f"(m.s2_id is not null and m.fetched_at < now() - interval {int(older_days)} day) or "
        "(m.s2_id is null and p.submitted >= date_trunc('month', current_date) - interval 2 month) order by m.fetched_at, m.id"
        + (f" limit {int(limit)}" if limit else "")).fetchall()]
    print(f"{len(todo)} papers to refresh", file=sys.stderr)
    url = URL.split("?")[0] + "?fields=" + REFRESH_FIELDS
    refreshed = changed = with_venue = 0
    for i in range(0, len(todo), 500):
        batch = todo[i:i + 500]
        try:
            res = post(batch, url=url)
        except RuntimeError:  # S2 free pool kept refusing: leave these rows stale, the next run retries them
            print(f"{i + len(batch)}/{len(todo)} batch failed, skipped", file=sys.stderr, flush=True); time.sleep(60); continue
        rows = [(pid, p.get("citationCount"), p.get("influentialCitationCount"), venue_of(p), p.get("paperId")) for pid, p in zip(batch, res) if p]  # unknown to S2: leave row, retry next time
        if rows:
            rt = pa.table({"id": [r[0] for r in rows], "citations": pa.array([r[1] for r in rows], pa.int32()),
                           "influential": pa.array([r[2] for r in rows], pa.int32()), "s2_venue": [r[3] for r in rows], "s2_id": pa.array([r[4] for r in rows], pa.string())})
            con.register("rt", rt)
            changed += con.execute("select count(*) from s2_meta m join rt using (id) where m.citations is distinct from rt.citations "
                                   "or m.influential is distinct from rt.influential").fetchone()[0]
            con.execute("update s2_meta set s2_id = coalesce(rt.s2_id, s2_meta.s2_id), citations = rt.citations, influential = rt.influential, s2_venue = rt.s2_venue, fetched_at = now() "
                        "from rt where s2_meta.id = rt.id")
            con.unregister("rt")
            refreshed += len(rows); with_venue += sum(1 for r in rows if r[3])
        print(f"{i + len(batch)}/{len(todo)} refreshed so far {refreshed}", file=sys.stderr, flush=True)
        time.sleep(1.1 if os.environ.get("S2_API_KEY") else 3.5)
    print(f"refreshed {refreshed}, changed counts {changed}, with venue {with_venue}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=None); ap.add_argument("--months", type=int, default=18)
    ap.add_argument("--refresh", action="store_true", help="refetch citations + venue for stale s2_meta rows")
    ap.add_argument("--older-days", type=int, default=25)
    a = ap.parse_args()
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    if a.refresh:
        return refresh(con, a.older_days, a.limit)
    # --limit N = a fixed pseudo-random N-paper sample of the last --months months (ordered by md5 of the id, so a
    # larger N later extends the same set). Spread over time matters more than recency for the map's time slider and
    # for topic growth; without an API key S2 allows only ~1,500 papers/minute.
    todo = [r[0] for r in con.execute(
        f"select id from (select id from papers where submitted >= (select max(submitted) from papers) - interval {a.months} month "
        "order by md5(id)" + (f" limit {a.limit}" if a.limit else "") + ") where id not in (select id from s2_meta)").fetchall()]
    print(f"{len(todo)} papers to enrich", file=sys.stderr)
    got_emb = 0
    for i in range(0, len(todo), 500):
        batch = todo[i:i + 500]
        res = post(batch)
        meta, embs = [], []
        for pid, p in zip(batch, res):
            if not p:
                meta.append((pid, None, None, None, None, None)); continue  # ponytail: unknown to S2, record so we don't retry
            meta.append((pid, p.get("paperId"), p.get("citationCount"), p.get("influentialCitationCount"), (p.get("tldr") or {}).get("text"), venue_of(p)))
            v = (p.get("embedding") or {}).get("vector")
            if v: embs.append((pid, v))
        # Bulk insert through Arrow: executemany planned one query per row (768 constants each), ~0.1 s per paper.
        mt = pa.table({"id": [m[0] for m in meta], "s2_id": [m[1] for m in meta], "citations": pa.array([m[2] for m in meta], pa.int32()),
                       "influential": pa.array([m[3] for m in meta], pa.int32()), "tldr": [m[4] for m in meta], "s2_venue": [m[5] for m in meta]})
        con.register("mt", mt); con.execute("insert or replace into s2_meta (id, s2_id, citations, influential, tldr, fetched_at, s2_venue) select id, s2_id, citations, influential, tldr, now(), s2_venue from mt"); con.unregister("mt")
        if embs:
            V = np.asarray([v for _, v in embs], dtype=np.float32)
            et = pa.table({"id": [i for i, _ in embs], "vec": pa.FixedSizeListArray.from_arrays(pa.array(V.ravel()), 768)})
            con.register("et", et); con.execute("insert or replace into embeddings select id, vec from et"); con.unregister("et")
        got_emb += len(embs)
        print(f"{i + len(batch)}/{len(todo)} embeddings so far {got_emb}", file=sys.stderr, flush=True)
        time.sleep(1.1 if os.environ.get("S2_API_KEY") else 3.5)
    n, e = con.execute("select (select count(*) from papers), (select count(*) from embeddings)").fetchone()
    print(f"done: {e}/{n} papers have embeddings ({100 * e / max(n, 1):.1f}%)")


if __name__ == "__main__":
    main()
