"""Enrich papers from Semantic Scholar: SPECTER2 embedding, tldr, citations.

Usage: python -m radar.enrich [--limit N]
Set S2_API_KEY in the environment for higher rate limits (free key).
Resumable: only papers with no row in s2_meta are requested.
"""
import argparse, json, os, sys, time, urllib.request
import duckdb
from .harvest import DB

URL = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=externalIds,citationCount,influentialCitationCount,tldr,embedding.specter_v2"
SCHEMA = """
create table if not exists s2_meta (
  id text primary key, s2_id text, citations int, influential int, tldr text, fetched_at timestamp default now());
create table if not exists embeddings (id text primary key, vec float[768]);
"""


def post(ids, tries=8):
    body = json.dumps({"ids": [f"ARXIV:{i}" for i in ids]}).encode()
    hdr = {"Content-Type": "application/json", "User-Agent": "field-radar/0.1"}
    if k := os.environ.get("S2_API_KEY"): hdr["x-api-key"] = k
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, body, hdr), timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(10 * (i + 1), 60)); continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(15)
    raise RuntimeError("s2 batch failed")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=None); a = ap.parse_args()
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    # --limit N means "the N newest papers"; on a rerun only the ones still missing are requested.
    todo = [r[0] for r in con.execute(
        "select id from (select id from papers order by submitted desc, id desc" + (f" limit {a.limit}" if a.limit else "") +
        ") where id not in (select id from s2_meta)").fetchall()]
    print(f"{len(todo)} papers to enrich", file=sys.stderr)
    got_emb = 0
    for i in range(0, len(todo), 500):
        batch = todo[i:i + 500]
        res = post(batch)
        meta, embs = [], []
        for pid, p in zip(batch, res):
            if not p:
                meta.append((pid, None, None, None, None)); continue  # ponytail: unknown to S2, record so we don't retry
            meta.append((pid, p.get("paperId"), p.get("citationCount"), p.get("influentialCitationCount"), (p.get("tldr") or {}).get("text")))
            v = (p.get("embedding") or {}).get("vector")
            if v: embs.append((pid, v))
        con.executemany("insert or replace into s2_meta values (?,?,?,?,?,now())", meta)
        if embs: con.executemany("insert or replace into embeddings values (?,?)", embs)
        got_emb += len(embs)
        print(f"{i + len(batch)}/{len(todo)} embeddings so far {got_emb}", file=sys.stderr, flush=True)
        time.sleep(1.1 if os.environ.get("S2_API_KEY") else 3.5)
    n, e = con.execute("select (select count(*) from papers), (select count(*) from embeddings)").fetchone()
    print(f"done: {e}/{n} papers have embeddings ({100 * e / max(n, 1):.1f}%)")


if __name__ == "__main__":
    main()
