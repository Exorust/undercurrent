"""Topic workbench server: stdlib ThreadingHTTPServer over the pipeline database, read-only.

Usage: python -m radar.serve [--port 8765] [--fake-llm PATH] [--llm-timeout 300] [--slow SECONDS]
       python -m radar.serve --selftest          # tiny fixture database in a temp dir; prints ok

Also serves the filter page's data: /filter.js, /filterdata, /institutions, /countries, /rows and the signal/citation fields (tables
from radar.load_extra and radar.citations; each may be absent, then the fields are empty or null).
Reads data/radar.duckdb (or RADAR_DB) read-only and keeps ids, months, clusters, the 14-NN graph,
citations and the 768-d vectors in memory. Writes only data/workbench.duckdb (saved selections and
ideas), which the pipeline never opens. Endpoints and rules: docs/designs/topic-workbench-architecture.md §5-6.
Test hooks: --fake-llm / RADAR_LLM_FAKE (a program that gets the prompt on stdin and prints the reply),
--llm-timeout, --slow N (sleep N s before data endpoints), RADAR_FAULT=1 (GET /fault raises).
"""
import argparse, atexit, collections, json, logging, math, os, pathlib, re, secrets, signal, socket, subprocess
import sys, tempfile, threading, time, urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import duckdb, numpy as np
from . import harvest
from .merge import IDEA_RULES

ROOT = harvest.ROOT
P = dict(db=harvest.DB, pid=ROOT / "data/serve.pid", lock=ROOT / "data/run.lock", wb=ROOT / "data/workbench.duckdb",
         log=ROOT / "data/log/serve.log", app=ROOT / "radar/app.html",
         points=pathlib.Path(os.environ.get("RADAR_POINTS", ROOT / "report/points.bin")))
CFG = dict(port=8765, fake=os.environ.get("RADAR_LLM_FAKE"), llm_timeout=300, slow=0.0, model="sonnet")
MAX_BODY = 8 << 20
BACKTEST_LINE = "top-50 phrases grew 1.5x faster than CS in 36% of cases vs 27% baseline (pooled, 4 cutoffs)"
WB_SCHEMA = """
create table if not exists selections (sel_id text primary key, name text, kind text not null, spec json,
  n integer not null, created timestamp not null default now(), run_id text not null);
create table if not exists selection_ids (sel_id text not null, id text not null);
create table if not exists ideas (idea_id text primary key, created timestamp not null default now(),
  a_sel text not null, b_sel text not null, a_name text, b_name text, title text, text text not null,
  cited text[] not null, shown_ids text[] not null,
  status text not null check (status in ('ok', 'regenerated', 'dropped')), reason text,
  model text not null, run_id text not null, window_from text, window_to text);
alter table ideas add column if not exists shown_sides JSON;
"""
log = logging.getLogger("radar.serve")
S = argparse.Namespace()            # server state, filled by load()
CACHE, CACHE_LOCK = collections.OrderedDict(), threading.Lock()   # transient selections, LRU 64
WB_LOCK, IDEAS_LOCK = threading.Lock(), threading.Lock()
EXTRA_LOCK = threading.Lock()       # guards the lazily built filterdata / countries caches
SIG_WINS = (3, 6, 12)


class E(Exception):
    def __init__(self, status, code, msg): super().__init__(msg); self.status, self.code, self.msg = status, code, msg


def bad(msg): return E(400, "bad_request", msg)


# ---------- pure rules (each with a self-check below) ----------

def month_window(e, n):
    """Recent window of n complete months ending at month index e, and the prior window before it: (rf, rt, pf, pt)."""
    if n < 1 or e - 2 * n + 1 < 0: raise ValueError(f"a {n}-month window needs {2 * n} complete months")
    return e - n + 1, e, e - 2 * n + 1, e - n


def growth(r, p, vol):
    """CS-corrected growth: ((r+1)/(p+1)) / vol, vol = embedded papers recent / prior (flow F11.1 step 3)."""
    return (r + 1) / (p + 1) / vol


def link_status(b_r, b_p):
    return "low" if b_r < 10 else "new" if b_p < 3 else "ratio"


def host_ok(host, port): return host in (f"localhost:{port}", f"127.0.0.1:{port}")
def origin_ok(origin, port): return origin in (f"http://localhost:{port}", f"http://127.0.0.1:{port}")


CITE = re.compile(r"arXiv:(\d{4}\.\d{4,5})(?:v\d+)?")


def check_cites(text, A, B, bridge):
    """(ok, cited ids, reason). Every cited id must be shown; and cite >= 1 A and >= 1 B, or >= 1 bridge."""
    cited = list(dict.fromkeys(CITE.findall(text)))
    out = [c for c in cited if c not in A and c not in B and c not in bridge]
    if not cited: return False, cited, "cites no paper"
    if out: return False, cited, "cites " + ", ".join("arXiv:" + c for c in out) + " not in the shown set"
    if any(c in bridge for c in cited) or (any(c in A for c in cited) and any(c in B for c in cited)): return True, cited, None
    return False, cited, "must cite papers from both sides (at least one A and one B) or one bridge paper"


def fightin_words(k_s, n_all, n_s, N, alpha0=1000.0):
    """Log-odds with an informative Beta prior (arch §6.6). Returns (delta, z, keep mask)."""
    k_s, n_all = np.asarray(k_s, float), np.asarray(n_all, float)
    pi = n_all / N; a, b = alpha0 * pi, alpha0 * (1 - pi)
    k_b, n_b = n_all - k_s, N - n_s
    d = np.log((k_s + a) / (n_s - k_s + b)) - np.log((k_b + a) / (n_b - k_b + b))
    z = d / np.sqrt(1 / (k_s + a) + 1 / (n_s - k_s + b) + 1 / (k_b + a) + 1 / (n_b - k_b + b))
    return d, z, (k_s >= 3) & (k_s >= 0.01 * n_s) & (z >= 3)


def pid_alive(pidfile, tag):
    """(alive, pid, why): alive when pidfile names a running process whose command line contains tag."""
    try: pid = int(pathlib.Path(pidfile).read_text().split()[0])
    except (FileNotFoundError, ValueError, IndexError): return False, None, "unreadable"
    try: os.kill(pid, 0)
    except ProcessLookupError: return False, pid, f"pid {pid} not running"
    except PermissionError: pass
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()
    return (tag in cmd), pid, (None if tag in cmd else f"pid {pid} is {cmd.split()[0].rsplit('/', 1)[-1] if cmd else '?'}, not {tag}")


assert month_window(35, 6) == (30, 35, 24, 29)    # E = 2026-10 (index 35 from 2023-11): recent 2026-05..10, prior 2025-11..2026-04
try: month_window(35, 19); raise AssertionError
except ValueError: pass
assert growth(30, 0, 1.0) == 31.0 and abs(growth(20, 10, 2.0) - 21 / 11 / 2) < 1e-12
assert (link_status(30, 2), link_status(9, 0), link_status(20, 10)) == ("new", "low", "ratio")
assert host_ok("localhost:8765", 8765) and host_ok("127.0.0.1:8765", 8765)
assert not any(host_ok(h, 8765) for h in ("evil.com", "localhost:8765.evil.com", None, "localhost:8766", ""))
assert origin_ok("http://localhost:8765", 8765) and not any(origin_ok(o, 8765) for o in (None, "http://attacker.test", "http://localhost:8766"))
_A, _B, _X = {"2501.00001"}, {"2501.00002"}, {"2501.00003"}
assert check_cites("arXiv:2501.00001v2 and arXiv:2501.00002", _A, _B, _X)[:2] == (True, ["2501.00001", "2501.00002"])
assert check_cites("only arXiv:2501.00003", _A, _B, _X)[0] and not check_cites("arXiv:2501.00001", _A, _B, _X)[0]
assert check_cites("nothing", _A, _B, _X)[2] == "cites no paper" and "2405.01234" in check_cites("arXiv:2405.01234", _A, _B, _X)[2]
_d, _z, _k = fightin_words([10, 10, 2], [10, 5000, 2], 10, 10000)
assert _d[0] > _d[1] and _k[0] and not _k[2]


# ---------- months ----------

def ym_abs(s): y, m = map(int, str(s)[:7].split("-")); return y * 12 + m - 1
def ym(i): a = S.m0 + int(i); return f"{a // 12}-{a % 12 + 1:02d}"


def window(q):
    """Window from query/body keys `from`, `to` (YYYY-MM); default the last 6 complete months."""
    f, t = q.get("from"), q.get("to")
    if f is None and t is None: rf, rt, pf, pt = month_window(S.E, 6)
    else:
        if not (isinstance(f, str) and isinstance(t, str) and re.fullmatch(r"\d{4}-\d{2}", f) and re.fullmatch(r"\d{4}-\d{2}", t)):
            raise bad("from and to must both be YYYY-MM")
        rf, rt = ym_abs(f) - S.m0, ym_abs(t) - S.m0
        last = f"the last complete month is {ym(S.E)}"
        if rf > S.E or rt < 0: raise bad(f"window {f}..{t} is outside the data ({ym(0)}..{ym(S.E)}); {last}")
        if rt > S.E: raise bad(f"window must end at or before the last complete month {ym(S.E)} (later months are partial)")
        if rf > rt: raise bad("from must not be after to")
        try: rf, rt, pf, pt = month_window(rt, rt - rf + 1)
        except ValueError as e: raise bad(f"{e}; the longest window ending {t} is {(rt + 1) // 2} months")
    vr, vp = int(S.vol[rf:rt + 1].sum()), int(S.vol[pf:pt + 1].sum())
    return dict(rf=rf, rt=rt, pf=pf, pt=pt, vol=vr / max(vp, 1),
                obj={"from": ym(rf), "to": ym(rt), "prior_from": ym(pf), "prior_to": ym(pt), "last_complete": ym(S.E),
                     "n_months": rt - rf + 1, "vol_recent": vr, "vol_prior": vp, "vol_ratio": vr / max(vp, 1)})


def gstats(r, p, w):
    r, p = int(r), int(p); new = r >= 30 and p < 5
    return {"recent": r, "prior": p, "growth": None if new or r < 10 else growth(r, p, w["vol"]), "is_new": new}


# ---------- start-up ----------

def die(msg): print(msg, file=sys.stderr); sys.exit(1)


def take_locks():
    """serve.pid (O_EXCL, stale removal) then run.lock check (arch §5.3). Returns the stale-removal note or None."""
    notes, rel = [], lambda p: str(p.relative_to(ROOT)) if str(p).startswith(str(ROOT)) else str(p)
    for _ in range(2):
        try: fd = os.open(P["pid"], os.O_CREAT | os.O_EXCL | os.O_WRONLY); break
        except FileExistsError:
            alive, pid, why = pid_alive(P["pid"], "radar.serve")
            if alive: die(f"server already running (pid {pid}); stop it first: kill {pid}")
            print(f"removed stale lock {rel(P['pid'])} ({why})"); notes.append(f"{rel(P['pid'])} ({why})")
            P["pid"].unlink(missing_ok=True)
    else: die(f"could not create {P['pid']}")
    os.write(fd, f"{os.getpid()}\n".encode()); os.close(fd)
    atexit.register(lambda: P["pid"].exists() and P["pid"].read_text().split()[:1] == [str(os.getpid())] and P["pid"].unlink())
    if P["lock"].exists():
        alive, pid, why = pid_alive(P["lock"], "run_all.sh")
        if alive: die(f"{rel(P['lock'])} names a running pipeline (pid {pid}, run_all.sh). Wait for it to finish, or delete the file if you are sure it is not running.")
        print(f"removed stale lock {rel(P['lock'])} ({why})"); notes.append(f"{rel(P['lock'])} ({why})")
        P["lock"].unlink(missing_ok=True)
    return "; ".join(notes) or None


def map_metadata(con):
    cols = [r[0] for r in con.execute("describe map_meta").fetchall()]
    if cols[:2] == ["key", "value"]: meta = dict(con.execute("select key, value from map_meta").fetchall())
    else:   # ponytail: map.py writes one row per run (flows F2/F3), arch §2.1 says key/value; read either, latest run wins
        cur = con.execute("select * from map_meta order by created desc limit 1"); meta = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    return meta


def last_complete_month(con, meta=None):
    meta = map_metadata(con) if meta is None else meta
    max_sub, max_harv = con.execute("select max(submitted), max(harvested_at) from papers").fetchone()
    return min(ym_abs(max_sub), ym_abs(max_harv) - 1) - ym_abs(meta.get("month0", "2023-11"))


def load():
    from scipy import sparse
    t0 = time.time()
    con = duckdb.connect(str(P["db"]), read_only=True, config={"memory_limit": "2GB"})
    meta = map_metadata(con)
    S.con, S.meta, S.run_id = con, meta, meta["run_id"]
    S.tables = {r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()}
    S.filterdata, S.countries = None, None
    if meta.get("links_run_id") != S.run_id: die(f"map run {S.run_id} has no links stage. Run: .venv/bin/python -m radar.links")
    pi = con.execute("select row, id, month, cat, l0, l1, l2 from point_index order by row").fetchnumpy()
    S.n = n = len(pi["row"]); assert (np.asarray(pi["row"]) == np.arange(n)).all(), "point_index.row must be 0..n-1"
    S.ids = np.array(pi["id"].tolist() if hasattr(pi["id"], "tolist") else list(pi["id"]), dtype=str)
    assert n < 2 or (S.ids[1:] > S.ids[:-1]).all(), "rows must be in arXiv id order"
    S.month, S.cat = np.asarray(pi["month"], np.int32), np.asarray(pi["cat"], np.int32)
    S.L = [np.asarray(pi[f"l{k}"], np.int32) for k in range(3)]
    S.Lx = [np.append(l, -1) for l in S.L]          # index n (from a -1 neighbour) maps to "no cluster"
    S.m0 = ym_abs(meta.get("month0", "2023-11"))
    S.E = last_complete_month(con, meta)          # last complete month (arch §6.1)
    S.nm = max(int(meta.get("n_months", 0)), int(S.month.max()) + 1, S.E + 1)
    S.vol = np.bincount(S.month, minlength=S.nm)
    kn = con.execute("select row, nbr from knn order by row, rank").fetchnumpy()
    r, nb = np.asarray(kn["row"], np.int64), np.asarray(kn["nbr"], np.int32)
    pos = np.arange(len(r)) - np.searchsorted(r, r); ok = pos < 14
    S.K = np.full((n, 14), -1, np.int32); S.K[r[ok], pos[ok]] = nb[ok]
    S.cit = np.asarray(con.execute("select coalesce(s.citations, -1) c from point_index p left join s2_meta s using(id) order by p.row").fetchnumpy()["c"], np.int64)
    S.cats = json.loads(meta.get("categories", "[]"))
    S.citp = np.full(n, 255, np.uint8)               # paper_cites.cit_pct per row, 255 = unknown
    if "paper_cites" in S.tables:
        for r_, v_ in con.execute("select row, cit_pct from paper_cites where cit_pct is not null and row >= 0 and row < ?", [n]).fetchall():
            S.citp[r_] = min(max(int(v_), 0), 254)
    S.sig, S.extra = {}, {}
    if "topic_signals" in S.tables:
        for lv, c, win, nn, vs, cs, cm, t10, ti, tv in con.execute(
                "select level, cluster, win, n, venue_share, code_share, cit_median_pct, top10_share, top_insts, top_venues from topic_signals").fetchall():
            S.sig[(int(lv), int(c), int(win))] = {"win": int(win), "n": nn, "venue_share": vs, "code_share": cs, "cit_median_pct": cm,
                "top10_share": t10, "top_insts": json.loads(ti) if ti else [], "top_venues": json.loads(tv) if tv else []}
    if "extra_meta" in S.tables: S.extra = {str(k): v for k, v in con.execute("select key, value from extra_meta").fetchall()}
    S.T = int(float(meta.get("bridge_T") or 4))
    S.k, S.lab, S.tinfo = {}, {}, {}
    for lv, c, label, cnt, cx, cy in con.execute("select level, cluster, label, n, cx, cy from topics where cluster >= 0 order by 1, 2").fetchall():
        S.tinfo[(lv, c)] = (label or f"cluster {c}", cnt, cx, cy)
    for lv in range(3):
        S.k[lv] = max([c + 1 for (l, c) in S.tinfo if l == lv] + [int(S.L[lv].max()) + 1])
        S.lab[lv] = [S.tinfo.get((lv, c), (f"cluster {c}",))[0] for c in range(S.k[lv])]
    S.TM = {lv: np.zeros((S.k[lv], S.nm), np.int64) for lv in range(3)}
    for lv, c, m, cnt in con.execute("select level, cluster, month, n from topic_months where cluster >= 0").fetchall():
        if c < S.k[lv] and m < S.nm: S.TM[lv][c, m] = cnt
    S.onset0 = np.full(S.k[0], -1)
    for c, m in con.execute("select cluster, onset_month from topic_onset where level = 0 and cluster >= 0").fetchall(): S.onset0[c] = m
    S.parent = {}
    for lv, c, pl, p, sh in con.execute("select level, cluster, parent_level, parent, share from topic_parent where cluster >= 0").fetchall():
        S.parent.setdefault((lv, pl), {})[c] = (int(p), float(sh))
    S.onset_share = float(meta.get("onset_share") or (con.execute("select max(onset_share) from topic_onset").fetchone()[0] or 0.1))
    S.level0 = (meta.get("level0_links", "").lower() in ("true", "1")) if "level0_links" in meta else \
        bool(con.execute("select count(*) from (select 1 from topic_links where level = 0 limit 1)").fetchone()[0])
    S.plinks = {lv: phrase_links(con, lv) for lv in range(3)}
    S.rising = [k for k, in con.execute("""select key from signals where signal='S3' and run_tag='live'
                and run_date=(select max(run_date) from signals where signal='S3' and run_tag='live')""").fetchall()]
    _, S.V = harvest.fetch_vecs(con, "select p.row, e.vec from point_index p join embeddings e using(id) order by p.row")
    if len(S.V) != n: die(f"{n - len(S.V)} points have no vector; rerun radar.map")
    S.C = {}
    for lv in range(3):
        v = S.L[lv] >= 0
        M = sparse.csr_matrix((np.ones(v.sum(), np.float32), (S.L[lv][v], np.nonzero(v)[0])), shape=(S.k[lv], n))
        C = np.asarray(M @ S.V); S.C[lv] = C / np.maximum(np.linalg.norm(C, axis=1, keepdims=True), 1e-12)
    S.load_s = time.time() - t0


def phrase_links(con, lv):
    """{(c1, c2): [{phrase, n1, n2, share1, share2}]} for phrases with >= 2 clusters at n >= 10, share >= 0.2 and eff <= 4 (arch §6.5)."""
    by = collections.defaultdict(list)
    for ph, c, cnt, sh in con.execute("select phrase, cluster, n, share from phrase_topics where level = ? and cluster >= 0", [lv]).fetchall():
        by[ph].append((int(c), int(cnt), float(sh)))
    out = collections.defaultdict(list)
    for ph, rows in by.items():
        eff = math.exp(-sum(s * math.log(s) for _, _, s in rows if s > 0))
        q = sorted(x for x in rows if x[1] >= 10 and x[2] >= 0.2)
        if len(q) < 2 or eff > 4: continue
        for i in range(len(q)):
            for j in range(i + 1, len(q)):
                (c1, n1, s1), (c2, n2, s2) = q[i], q[j]
                out[(c1, c2)].append({"phrase": ph, "n1": n1, "n2": n2, "share1": s1, "share2": s2})
    return dict(out)


def open_wb():
    S.wb = duckdb.connect(str(P["wb"])); S.wb.execute(WB_SCHEMA)


# ---------- helpers on selections ----------

def pextra(cur, ids):
    """{id: (venue, code_url)} from paper_comments; empty when that table is absent."""
    if "paper_comments" not in S.tables or not ids: return {}
    return {i: (v, u) for i, v, u in cur.execute("select id, venue, code_url from paper_comments where id in (select unnest(?::varchar[]))",
                                                 [list(ids)]).fetchall()}


def citpct(r): return None if r is None or r < 0 or S.citp[r] == 255 else int(S.citp[r])


def papers(cur, rows):
    """paper objects in the given row order."""
    rows = [int(r) for r in rows]
    if not rows: return []
    got = {i: (t, c, s) for i, t, c, s in cur.execute(
        """select p.id, p.title, s.citations, coalesce(s.tldr, left(p.abstract, 400)) from papers p left join s2_meta s using(id)
           where p.id in (select unnest(?::varchar[]))""", [[str(S.ids[r]) for r in rows]]).fetchall()}
    out, ex = [], pextra(cur, [str(S.ids[r]) for r in rows])
    for r in rows:
        t, c, s = got.get(str(S.ids[r]), (None, None, None)); v, u = ex.get(str(S.ids[r]), (None, None))
        out.append({"row": r, "id": str(S.ids[r]), "title": t, "month": ym(S.month[r]), "cat": catname(S.cat[r]),
                    "citations": c, "summary": (s or "")[:400], "venue": v, "code_url": u, "cit_pct": citpct(r)})
    return out


def catname(i): return S.cats[i] if 0 <= i < len(S.cats) else None
def by_cites(rows, k): return rows[np.argsort(-S.cit[rows], kind="stable")[:k]]
def newest(rows, k): return rows[np.lexsort((-rows, -S.month[rows]))[:k]]


def remember(rows, kind, spec, n_unemb=0):
    sid = secrets.token_hex(6)
    with CACHE_LOCK:
        CACHE[sid] = dict(sel_id=sid, rows=rows, kind=kind, spec=spec, n_unembedded=n_unemb, saved=False, missing=0)
        while len(CACHE) > 64: CACHE.popitem(last=False)
    return CACHE[sid]


def lookup(sid):
    if not isinstance(sid, str) or not sid: raise bad("sel_id must be a string")
    with CACHE_LOCK:
        if sid in CACHE: CACHE.move_to_end(sid); return CACHE[sid]
    with WB_LOCK:
        row = S.wb.execute("select name, kind, spec, n, run_id from selections where sel_id = ?", [sid]).fetchone()
        ids = [i for i, in S.wb.execute("select id from selection_ids where sel_id = ?", [sid]).fetchall()]
    if not row: raise E(404, "not_found", f"selection {sid} not found (transient selections end when the server restarts)")
    a = np.array(ids, dtype=str) if ids else np.array([], dtype=str)
    pos = np.searchsorted(S.ids, a); hit = (pos < S.n) & (S.ids[np.minimum(pos, S.n - 1)] == a)
    sel = dict(sel_id=sid, rows=np.unique(pos[hit]).astype(np.int64), kind=row[1], spec=json.loads(row[2] or "{}"), name=row[0],
               n_unembedded=0, saved=True, missing=int(len(a) - hit.sum()), run_id=row[4], n_saved=row[3])
    with CACHE_LOCK: CACHE[sid] = sel
    return sel


def centroid(rows):
    s = sum(S.V[rows[i:i + 65536]].sum(0, dtype=np.float64) for i in range(0, len(rows), 65536))
    return s / max(len(rows), 1)


def ab_bridges(ra, rb):
    """Rows whose 14 neighbours hold >= T of A and >= T of B; papers outside both count (arch §5.5). Returns (rows, kA, kB)."""
    mA, mB = np.zeros(S.n + 1, bool), np.zeros(S.n + 1, bool); mA[ra] = True; mB[rb] = True
    kA, kB = mA[S.K].sum(1), mB[S.K].sum(1)
    br = np.nonzero((kA >= S.T) & (kB >= S.T))[0]
    return br, kA, kB


def auto_name(sel):
    k, sp, n = sel["kind"], sel["spec"], len(sel["rows"])
    if k == "topic": return f"Topic: {S.lab[sp['level']][sp['c']]}"
    if k == "phrase": return f"Phrase: {sp['phrase']}"
    if k == "query": return f"Title search: {sp['query']}"
    return f"Lasso: {n:,} papers, mostly {S.lab[1][np.bincount(S.L[1][sel['rows']] + 1).argmax() - 1] if n else '-'}"


def save(sel, name, polygon=None, win=None):
    """Copy a selection to workbench.duckdb as arXiv ids; returns its sel_id (same id as the transient one)."""
    spec = dict(sel["spec"]); spec["polygon"] = polygon if polygon is not None else spec.get("polygon")
    spec["window"] = win or spec.get("window") or {k: window({})["obj"][k] for k in ("from", "to")}
    with WB_LOCK:
        if S.wb.execute("select 1 from selections where sel_id = ?", [sel["sel_id"]]).fetchone():
            S.wb.execute("update selections set name = ? where sel_id = ?", [name, sel["sel_id"]])
        else:
            S.wb.execute("begin")
            try:
                S.wb.execute("insert into selections (sel_id, name, kind, spec, n, run_id) values (?,?,?,?,?,?)",
                             [sel["sel_id"], name, sel["kind"], json.dumps(spec), len(sel["rows"]), S.run_id])
                import pyarrow as pa
                S.wb.register("_ids", pa.table({"id": [str(i) for i in S.ids[sel["rows"]]]}))
                S.wb.execute("insert into selection_ids select ?, id from _ids", [sel["sel_id"]]); S.wb.unregister("_ids")
                S.wb.execute("commit")
            except Exception: S.wb.execute("rollback"); raise
    sel.update(saved=True, name=name, spec=spec, run_id=sel.get("run_id", S.run_id))
    return sel["sel_id"]


# ---------- handlers: fn(handler, query, body, cursor) -> JSON-able or None ----------

def qint(q, key, lo, hi, default=None):
    v = q.get(key, default)
    if v is None: raise bad(f"{key} is required")
    try: v = int(v)
    except (TypeError, ValueError): raise bad(f"{key} must be an integer")
    if not lo <= v < hi: raise bad(f"{key} must be in {lo}..{hi - 1}")
    return v


def h_index(h, q, body, cur):
    if not P["app"].exists(): raise E(404, "not_found", "radar/app.html is not built yet")
    h.send_bytes(200, P["app"].read_bytes(), "text/html; charset=utf-8", {"Cache-Control": "no-cache"})


def h_points(h, q, body, cur):
    f, tag = P["points"], f'"{S.run_id}"'
    if not f.exists(): raise E(404, "not_found", f"{f.name} missing; rerun radar.map")
    hdr = {"ETag": tag, "Cache-Control": "no-cache"}
    if h.headers.get("If-None-Match") == tag: return h.send_bytes(304, b"", "application/octet-stream", hdr)
    h.send_bytes(200, f.read_bytes(), "application/octet-stream", hdr)


def h_meta(h, q, body, cur):
    m = S.meta
    fl = lambda k: float(m[k]) if m.get(k) not in (None, "", "null") else None
    return {"run_id": S.run_id, "n_points": S.n, "months": [ym(i) for i in range(S.E + 1)], "last_complete": ym(S.E),
            "categories": S.cats, "levels": {str(lv): len([1 for (l, _) in S.tinfo if l == lv]) for lv in (2, 1, 0)},
            "bridge_T": S.T, "onset_share": S.onset_share, "knn_recall14": fl("knn_recall14"), "knn_bridge_agree": fl("knn_bridge_agree"),
            "backtest_line": m.get("backtest_line", BACKTEST_LINE), "level0_links": S.level0, "stale_removed": S.stale, "started": S.started, "extra": S.extra}


def h_labels(h, q, body, cur):
    lv = qint(q, "level", 0, 3, 1)
    par = S.parent.get((lv, lv + 1), {}) if lv < 2 else {}
    return {"level": lv, "clusters": [{"c": c, "label": S.tinfo[(lv, c)][0], "n": S.tinfo[(lv, c)][1], "cx": S.tinfo[(lv, c)][2],
                                       "cy": S.tinfo[(lv, c)][3], "parent": par.get(c, (None, None))[0], "parent_share": par.get(c, (None, None))[1]}
                                      for c in range(S.k[lv]) if (lv, c) in S.tinfo]}


def h_paper(h, q, body, cur):
    r = qint(q, "row", 0, S.n); i = str(S.ids[r])
    t, ab, au, c, s = cur.execute("""select p.title, p.abstract, p.authors, s.citations, coalesce(s.tldr, left(p.abstract, 400))
                                     from papers p left join s2_meta s using(id) where p.id = ?""", [i]).fetchone()
    v, u = pextra(cur, [i]).get(i, (None, None))
    return {"row": r, "id": i, "title": t, "month": ym(S.month[r]), "cat": catname(S.cat[r]), "citations": c, "summary": (s or "")[:400],
            "venue": v, "code_url": u, "cit_pct": citpct(r), "abstract": ab, "authors": list(au or [])[:5], "url": f"https://arxiv.org/abs/{i}"}


def sigwin(w):
    """topic_signals window for a request window, or None. Cached signals exist only for the standard windows: the
    latest 3, 6 or 12 complete months ending at the last complete month. Any other period or length gets None
    (null signals; the inspector hides the lines) rather than numbers from a different window."""
    n = w["obj"]["n_months"]
    return n if n in SIG_WINS and w["rt"] == S.E and w["rf"] == S.E - n + 1 else None


def topic_sigs(lv, c, w):
    """signals for the request window (rule in sigwin) and signals_all (whole snapshot, always)."""
    win = sigwin(w)
    return {"signals": S.sig.get((lv, c, win)) if win else None, "signals_all": S.sig.get((lv, c, 0))}


def h_topic(h, q, body, cur):
    lv, w = qint(q, "level", 0, 3, 1), window(q); c = qint(q, "c", 0, S.k[lv])
    r, p = S.TM[lv][c, w["rf"]:w["rt"] + 1].sum(), S.TM[lv][c, w["pf"]:w["pt"] + 1].sum()
    return {"window": w["obj"], "level": lv, "c": c, "label": S.lab[lv][c], "n_total": S.tinfo.get((lv, c), (0, 0))[1], **gstats(r, p, w),
            **topic_sigs(lv, c, w)}


def h_topics(h, q, body, cur):
    lv, w = qint(q, "level", 0, 3, 1), window(q)
    rf, rt, pf, pt = w["rf"], w["rt"], w["pf"], w["pt"]
    rec = lambda lv_: (S.TM[lv_][:, rf:rt + 1].sum(1), S.TM[lv_][:, pf:pt + 1].sum(1))
    arriving = collections.defaultdict(list)
    if lv > 0:
        r0, p0 = rec(0); par = S.parent.get((0, lv), {})
        if lv == 2:
            upper = S.parent.get((1, 2), {})
            # ponytail: chain immediate parents; parent_share stays the fine cluster's level-1 share.
            par = {c: (upper[p][0], share) for c, (p, share) in S.parent.get((0, 1), {}).items() if p in upper}
        # ponytail: both shares use level-0 topic_months; partial months never enter the denominator.
        total = S.TM[0][:, :S.E + 1].sum(1)
        corpus_share = float(r0.sum()) / max(int(total.sum()), 1)
        for c in range(S.k[0]):
            share = float(r0[c]) / max(int(total[c]), 1)
            if r0[c] >= 30 and share >= 1.6 * corpus_share and c in par:
                arriving[par[c][0]].append({"c": c, "label": S.lab[0][c], **gstats(r0[c], p0[c], w),
                    "onset": ym(S.onset0[c]) if S.onset0[c] >= 0 else None, "parent_share": par[c][1],
                    "window_share": share, "corpus_share": corpus_share})
    r, p = rec(lv); win = sigwin(w)
    return {"window": w["obj"], "level": lv, "topics": [
        {"c": c, "label": S.lab[lv][c], "n_total": S.tinfo.get((lv, c), (0, 0))[1], **gstats(r[c], p[c], w),
         **{k: ((S.sig.get((lv, c, win)) if win else None) or {}).get(k) for k in ("venue_share", "code_share", "cit_median_pct")},
         "arriving": sorted(arriving.get(c, []), key=lambda a: a["window_share"], reverse=True)} for c in range(S.k[lv])]}


def h_phrases(h, q, body, cur):
    from .links import phrase_eff
    lv, w = qint(q, "level", 0, 3, 1), window(q)
    counts = cur.execute("""with rising as (
        select distinct key as phrase from signals where signal = 'S3' and run_tag = 'live'
        and run_date = (select max(run_date) from signals where run_tag = 'live'))
        select r.phrase, count(pp.row), count(*) filter (where pi.month between ? and ?),
               count(*) filter (where pi.month between ? and ?)
        from rising r left join phrase_papers pp using(phrase) left join point_index pi using(row)
        group by r.phrase order by r.phrase""", [w["rf"], w["rt"], w["pf"], w["pt"]]).fetchall()
    topics = collections.defaultdict(list)
    for ph, c, sh, label in cur.execute("""select p.phrase, p.cluster, p.share, t.label from phrase_topics p
            left join topics t on t.level = p.level and t.cluster = p.cluster
            where p.level = ? order by p.share desc, p.cluster""", [lv]).fetchall():
        topics[ph].append({"c": c, "label": label or f"cluster {c}", "share": sh})
    out = [{"phrase": ph, "recent": r, "prior": p, "growth": growth(r, p, w["vol"]) if r >= 10 else None,
            "n": n, "eff": phrase_eff([t["share"] for t in topics[ph]]),
            "topics": [t for t in topics[ph] if t["share"] >= 0.05]} for ph, n, r, p in counts]
    out.sort(key=lambda p: (p["growth"] is None, -(p["growth"] or 0)))
    return {"run_id": S.run_id, "level": lv, "window": w["obj"], "phrases": out}


def link_level(q):
    lv = qint(q, "level", 0, 3, 1)
    if lv == 0 and not S.level0: raise bad("level 0 links not computed")
    return lv


def cite_pairs(cur, lv, rf, rt):
    out = {}
    if "topic_citelinks" not in S.tables: return out
    for a, b, n, x, y in cur.execute("select c1, c2, sum(n_cites), sum(n_1to2), sum(n_2to1) from topic_citelinks where level = ? and month between ? and ? group by 1, 2", [lv, rf, rt]).fetchall():
        if a == b: continue
        if a > b: a, b, x, y = b, a, y, x
        old = out.get((a, b), (0, 0, 0))
        out[(a, b)] = tuple(o + int(v or 0) for o, v in zip(old, (n, x, y)))
    return out


def h_links(h, q, body, cur):
    lv, w = link_level(q), window(q)
    rf, rt, pf, pt = w["rf"], w["rt"], w["pf"], w["pt"]
    nr, np_ = S.TM[lv][:, rf:rt + 1].sum(1), S.TM[lv][:, pf:pt + 1].sum(1)
    rows = cur.execute("""select c1, c2, coalesce(sum(n_bridge) filter (where month between ? and ?), 0),
                                 coalesce(sum(n_bridge) filter (where month between ? and ?), 0)
                          from topic_links where level = ? and month between ? and ? group by 1, 2""", [rf, rt, pf, pt, lv, pf, rt]).fetchall()
    cl = cite_pairs(cur, lv, rf, rt)
    out = []
    for c1, c2, br, bp in rows:
        if br == 0: continue
        n1, n2, n1p, n2p = (int(x) for x in (nr[c1], nr[c2], np_[c1], np_[c2]))
        st = link_status(br, bp); sp = (bp + 1) / (n1p + n2p + 1)
        out.append({"c1": c1, "c2": c2, "n_bridge": int(br), "n_bridge_prior": int(bp), "n1": n1, "n2": n2, "n1_prior": n1p, "n2_prior": n2p,
                    "strength": br / math.sqrt(n1 * n2) if n1 and n2 else None, "share_now": br / (n1 + n2) if n1 + n2 else None,
                    "share_prior": None if st == "new" else sp, "growth": (br / (n1 + n2)) / sp if st == "ratio" else None, "status": st, "n_cites": cl.get((c1, c2), (0, 0, 0))[0]})
    seen = {(x["c1"], x["c2"]) for x in out}
    cite_links = sorted(({"c1": a, "c2": b, "n_cites": v[0], "n_1to2": v[1], "n_2to1": v[2]} for (a, b), v in cl.items()
                         if v[0] >= 10 and (a, b) not in seen), key=lambda x: (-x["n_cites"], x["c1"], x["c2"]))[:200]
    order = {"new": 0, "ratio": 1, "low": 2}
    out.sort(key=lambda x: (order[x["status"]], -(x["growth"] if x["status"] == "ratio" else x["n_bridge"])))
    return {"window": w["obj"], "level": lv, "rule": {"k": 14, "T": S.T, "strength": "B/sqrt(n1*n2)", "share": "B/(n1+n2)"},
            "bridge_links": out, "cite_links": cite_links, "phrase_window": "whole corpus",
            "phrase_links": [{"c1": a, "c2": b, "phrases": v} for (a, b), v in sorted(S.plinks[lv].items())]}


def h_link(h, q, body, cur):
    lv, w = link_level(q), window(q)
    c1, c2 = qint(q, "c1", 0, S.k[lv]), qint(q, "c2", 0, S.k[lv])
    if c1 == c2: raise bad("c1 and c2 must differ")
    c1, c2 = min(c1, c2), max(c1, c2)
    b = cur.execute("select row, k1, k2 from bridges where level = ? and c1 = ? and c2 = ? and month between ? and ?",
                    [lv, c1, c2, w["rf"], w["rt"]]).fetchall()
    kk = {r: (k1, k2) for r, k1, k2 in b}
    top = by_cites(np.array(sorted(kk), np.int64), 30)
    cites = {"n_cites": 0, "n_1to2": 0, "n_2to1": 0, "top": []}
    if "topic_citelinks" in S.tables:
        t = cite_pairs(cur, lv, w["rf"], w["rt"]).get((c1, c2), (0, 0, 0))
        cites.update(n_cites=int(t[0]), n_1to2=int(t[1]), n_2to1=int(t[2]))
    if "citelink_evidence" in S.tables:
        ev = cur.execute("select cited_row, sum(n) from citelink_evidence where level = ? and ((c1 = ? and c2 = ?) or (c1 = ? and c2 = ?)) and cited_row >= 0 and cited_row < ? "
                         "group by cited_row order by sum(n) desc, cited_row limit 30", [lv, c1, c2, c2, c1, S.n]).fetchall()
        cites["top"] = [{**p, "n": int(n)} for p, (_, n) in zip(papers(cur, [r for r, _ in ev]), ev)]
    return {"window": w["obj"], "level": lv, "c1": c1, "c2": c2, "n_bridge": len(b), "cites": cites,
            "bridges": [{**p, "k1": kk[p["row"]][0], "k2": kk[p["row"]][1]} for p in papers(cur, top)],
            "phrases": S.plinks[lv].get((c1, c2), [])}


SELECTORS = ("rows", "topic", "phrase", "query", "sel_id")


def h_select(h, q, body, cur):
    keys = [k for k in SELECTORS if k in body]
    if len(keys) != 1: raise bad("exactly one of rows, topic, phrase, query, sel_id")
    k, v = keys[0], body[keys[0]]
    w = window(body)
    if k == "sel_id": sel = lookup(v)
    elif k == "rows":
        if not isinstance(v, list) or not all(type(x) is int for x in v): raise bad("rows must be a list of integers")
        a = np.unique(np.array(v, np.int64))
        if len(a) and (a[0] < 0 or a[-1] >= S.n): raise bad(f"rows must be in 0..{S.n - 1}")
        if not len(a): raise E(422, "empty_selection", "no papers in this selection")
        sel = remember(a, "rows", {})
    elif k == "topic":
        if not isinstance(v, dict): raise bad('topic must be {"level": L, "c": C}')
        lv = qint(v, "level", 0, 3); c = qint(v, "c", 0, S.k[lv])
        sel = remember(np.nonzero(S.L[lv] == c)[0], "topic", {"level": lv, "c": c})
    else:
        if not isinstance(v, str) or not v.strip(): raise bad(f"{k} must be a non-empty string")
        if k == "phrase":   # ponytail: always the literal full-text match (flow F13.3), never phrase_papers
            sql = "select pi.row, p.id from papers p left join point_index pi using(id) where contains(lower(p.title || ' ' || p.abstract), ?)"
            arg = v.strip().lower()
        else:
            sql = "select pi.row, p.id from papers p left join point_index pi using(id) where p.title ilike '%' || ? || '%' escape '\\'"
            arg = v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        log.info("select %s: %s  [1 parameter]", k, sql)
        got = cur.execute(sql, [arg]).fetchall()
        rows = np.unique(np.array([r for r, _ in got if r is not None], np.int64)); nu = len(got) - len(rows)
        if not got:
            near = [x for x, in cur.execute("select unnest(?::varchar[]) x order by levenshtein(x, ?) limit 3", [S.rising, arg]).fetchall()] if S.rising else []
            raise E(422, "empty_selection", f"no embedded papers match {k} '{v}'" + (f" ({nu} unembedded papers do)" if nu else "")
                    + (f"; nearest rising phrases: {', '.join(near)}" if near else ""))
        sel = remember(rows, k, {k: v}, nu)
        if not len(rows):
            ex = pextra(cur, [i for _, i in got])
            ps = [{"id": i, "title": t, "month": str(m)[:7], "citations": c, "url": f"https://arxiv.org/abs/{i}",
                   "venue": ex.get(i, (None, None))[0], "code_url": ex.get(i, (None, None))[1], "cit_pct": None}
                  for i, t, m, c in cur.execute("""select p.id, p.title, p.submitted, s.citations
                      from papers p left join s2_meta s using(id) where p.id in (select unnest(?::varchar[]))""",
                      [[i for _, i in got]]).fetchall()]
            sel["unembedded_stats"] = {"months": [0] * (S.E + 1), "growth": None, "terms": [], "topics": [],
                "top_cited": sorted(ps, key=lambda p: (-(p["citations"] or 0), p["id"]))[:10],
                "newest": sorted(ps, key=lambda p: (p["month"], p["id"]), reverse=True)[:10]}
    out = {"sel_id": sel["sel_id"], "kind": sel["kind"], "n": len(sel["rows"]), "n_unembedded": sel["n_unembedded"], "missing": sel["missing"]}
    if sel["kind"] not in ("rows", "topic") or sel["saved"]: out["rows"] = sel["rows"].tolist()
    if sel["saved"]:
        out.update(name=sel.get("name"), polygon=sel["spec"].get("polygon"), saved_window=sel["spec"].get("window"), run_id=sel.get("run_id"),
                   polygon_valid=bool(sel["spec"].get("polygon")) and sel.get("run_id") == S.run_id)
    sp = sel.get("spec") or {}
    # local-mode inspector reads signals from /select; a saved topic from another map run has other cluster ids
    if sel["kind"] == "topic" and "level" in sp and "c" in sp and (not sel["saved"] or sel.get("run_id") == S.run_id):
        out.update(topic_sigs(int(sp["level"]), int(sp["c"]), w))
    return {**out, **(sel.get("unembedded_stats") or stats(cur, sel["rows"], w))}


def stats(cur, rows, w):
    n = len(rows)
    if not n: raise E(422, "empty_selection", "no embedded papers left in this selection")
    mon = np.bincount(S.month[rows], minlength=S.nm)
    g = gstats(mon[w["rf"]:w["rt"] + 1].sum(), mon[w["pf"]:w["pt"] + 1].sum(), w)
    if n < 20: g["growth"] = None          # ponytail: same minimum as the lasso (F25); counts still shown
    top, new = by_cites(rows, 10), newest(rows, 10)
    ps = {p["row"]: p for p in papers(cur, np.concatenate([top, new]))}
    t_rows, sampled = rows, None
    if n > 20000: t_rows, sampled = np.sort(np.random.default_rng(0).choice(rows, 20000, replace=False)), 20000
    terms = []
    if len(t_rows) >= 3:
        import pyarrow as pa
        cur.register("sel", pa.table({"row": t_rows.astype(np.int32)}))
        got = cur.execute("""with k as (select pt.term_id, count(*) k_s from paper_terms pt join sel using(row) group by 1 having count(*) >= ?)
                             select t.term, k.k_s, t.n_all from k join terms t using(term_id)""", [max(3, 0.01 * len(t_rows))]).fetchall()
        cur.unregister("sel")
        if got:
            ks, na = np.array([x[1] for x in got], float), np.array([x[2] for x in got], float)
            d, z, keep = fightin_words(ks, na, len(t_rows), S.n)
            terms = sorted(({"term": got[i][0], "k": int(ks[i]), "share_sel": ks[i] / len(t_rows), "share_all": na[i] / S.n,
                             "delta": float(d[i]), "z": float(z[i])} for i in np.nonzero(keep)[0]), key=lambda t: -t["delta"])[:20]
    topics = []
    for lv in range(3):
        own = np.bincount(S.L[lv][rows] + 1, minlength=S.k[lv] + 1)[1:] / n
        nl = S.Lx[lv][S.K[rows]].ravel()
        nbr = np.bincount(nl + 1, minlength=S.k[lv] + 1)[1:] / (14 * n)
        for c in np.lexsort((-nbr, -own))[:10]:
            if own[c] > 0 or nbr[c] > 0:
                topics.append({"level": lv, "c": int(c), "label": S.lab[lv][c], "own_share": float(own[c]), "nbr_share": float(nbr[c])})
    return {"months": mon[:S.E + 1].tolist(), "growth": {**g, "window": w["obj"]}, "top_cited": [ps[int(r)] for r in top],
            "newest": [ps[int(r)] for r in new], "terms": terms, "terms_sampled": sampled, "topics": topics}


def h_distance(h, q, body, cur):
    if not q.get("a") or not q.get("b"): raise bad("a and b (selection ids) are required")
    ra, rb = lookup(q["a"])["rows"], lookup(q["b"])["rows"]
    ca, cb = centroid(ra), centroid(rb)
    br, kA, kB = ab_bridges(ra, rb)
    top = by_cites(br, 20)
    return {"cos_dist": float(1 - ca @ cb / (np.linalg.norm(ca) * np.linalg.norm(cb))), "n_a": len(ra), "n_b": len(rb),
            "overlap": int(len(np.intersect1d(ra, rb))), "n_bridges": len(br),
            "bridges": [{**p, "k_a": int(kA[p["row"]]), "k_b": int(kB[p["row"]])} for p in papers(cur, top)]}


def h_colour_dist(h, q, body, cur):
    lv = qint(q, "level", 0, 3, 0)
    if not q.get("a"): raise bad("a (selection id) is required")
    c = centroid(lookup(q["a"])["rows"]); c = c / max(np.linalg.norm(c), 1e-12)
    return {"level": lv, "dist": np.clip(1 - S.C[lv] @ c, 0, 2).round(6).tolist()}


def iso(t): return t.isoformat() if hasattr(t, "isoformat") else t


def h_selections_get(h, q, body, cur):
    with WB_LOCK:
        rows = S.wb.execute("select sel_id, name, kind, n, created, run_id from selections order by created desc").fetchall()
    return [{"sel_id": s, "name": nm, "kind": k, "n": n, "created": iso(c), "run_id": r} for s, nm, k, n, c, r in rows]


def h_selections_post(h, q, body, cur):
    name = body.get("name")
    if not isinstance(name, str) or not name.strip(): raise bad("name is required")
    sel, poly, win = lookup(body.get("sel_id")), body.get("polygon"), body.get("window")
    if poly is not None and not (isinstance(poly, list) and all(isinstance(p, list) and len(p) == 2 for p in poly)): raise bad("polygon must be [[x, y], ...]")
    if win is not None:
        if not isinstance(win, dict): raise bad('window must be {"from": "YYYY-MM", "to": "YYYY-MM"}')
        window(win); win = {"from": win["from"], "to": win["to"]}
    return {"sel_id": save(sel, name.strip(), poly, win)}


def h_selections_delete(h, q, body, cur):
    sid = body.get("sel_id")
    if not isinstance(sid, str): raise bad("sel_id is required")
    with WB_LOCK:
        if not S.wb.execute("select 1 from selections where sel_id = ?", [sid]).fetchone(): raise E(404, "not_found", f"saved selection {sid} not found")
        n = S.wb.execute("select count(*) from ideas where a_sel = ? or b_sel = ?", [sid, sid]).fetchone()[0]
        if n: raise E(409, "in_use", f"{n} idea{'s reference' if n != 1 else ' references'} this selection; it cannot be deleted")
        S.wb.execute("delete from selection_ids where sel_id = ?", [sid]); S.wb.execute("delete from selections where sel_id = ?", [sid])
    with CACHE_LOCK: CACHE.pop(sid, None)
    return {"deleted": True}


def call_llm(prompt):
    """Text reply; RuntimeError on failure, subprocess.TimeoutExpired on timeout. The fake hook takes the prompt on stdin."""
    if CFG["fake"]:
        r = subprocess.run([CFG["fake"]], input=prompt, capture_output=True, text=True, timeout=CFG["llm_timeout"])
        if r.returncode: raise RuntimeError(f"fake model exited {r.returncode}: {r.stderr[:300]}")
        return r.stdout
    from .llm import ask
    return ask(prompt, model=CFG["model"], timeout=CFG["llm_timeout"])


def llm_failure_message(exc):
    text = str(exc)
    match = re.fullmatch(r"(claude -p|fake model) exited (-?\d+): ?(.*)", text, re.DOTALL)
    if not match: return text[:300]
    model, code, stderr = match.groups()
    first = stderr.splitlines()[0].strip() if stderr else ""
    return f"{model} exited with code {code} ({first or 'no output'})"


def parse_ideas(text):
    a = json.loads(text[text.index("["):text.rindex("]") + 1])
    if not isinstance(a, list) or not all(isinstance(x, dict) and isinstance(x.get("idea"), str) for x in a): raise ValueError("not a list of ideas")
    return [{"title": str(x.get("title") or ""), "text": x["idea"]} for x in a]


def h_ideas_post(h, q, body, cur):
    n = body.get("n", 3)
    if type(n) is not int or not 1 <= n <= 5: raise bad("n must be an integer in 1..5")
    if not body.get("a") or not body.get("b"): raise bad("a and b (selection ids) are required")
    if body["a"] == body["b"]: raise bad("A and B must differ")
    A, B = lookup(body["a"]), lookup(body["b"])
    if not IDEAS_LOCK.acquire(blocking=False): raise E(409, "ideas_busy", "another ideas call is running; wait for it to finish")
    try:
        w = window(body)
        for s in (A, B):
            if not s["saved"]: save(s, s.get("name") or auto_name(s))
        br, _, _ = ab_bridges(A["rows"], B["rows"])
        shown, seen = [], set()
        def add(rows, side, k):
            for r in rows:
                if len([1 for _, sd in shown if sd == side]) >= k: break
                if int(r) not in seen: seen.add(int(r)); shown.append((int(r), side))
        add(by_cites(br, 8), "bridge", 8)
        for s, side in ((A, "A"), (B, "B")):
            add(by_cites(s["rows"], 50), side, 5); add(newest(s["rows"], 50), side, 10)
        ps = papers(cur, [r for r, _ in shown])
        shown_p = [{**p, "side": sd} for p, (_, sd) in zip(ps, shown)]
        ids = {sd: {p["id"] for p in shown_p if p["side"] == sd} for sd in ("A", "B", "bridge")}
        head = (f'You help a researcher find ideas that merge two areas of computer science research.\n'
                f'Selection A: "{A["name"]}" ({len(A["rows"]):,} papers). Selection B: "{B["name"]}" ({len(B["rows"]):,} papers).\n'
                f'Papers, each tagged [A], [B] or [bridge] (a paper whose nearest neighbours sit in both A and B):\n'
                + "\n".join(f'- [{p["side"]}] arXiv:{p["id"]} {p["title"]}. {p["summary"]}' for p in shown_p) + "\n\n")
        rules = (IDEA_RULES.format(src="one side", dst="the other") + " Cite only arXiv ids from the list above, inline as arXiv:ID; "
                 "each idea must cite at least one [A] paper and one [B] paper, or at least one [bridge] paper.")
        prompt = head + f"Write {n} concrete merge ideas between A and B, 4-6 sentences each: {rules}\n" \
                        'Reply ONLY with a JSON array of {"title": "...", "idea": "..."} objects.'
        try:
            try: got = parse_ideas(call_llm(prompt))
            except ValueError:
                log.warning("ideas: parse failed, retrying whole call")
                try: got = parse_ideas(call_llm(prompt))
                except ValueError as e: raise E(502, "llm_failed", f"model reply was not a JSON list of ideas twice ({e})")
        except subprocess.TimeoutExpired: raise E(504, "llm_timeout", f"the model ran longer than {CFG['llm_timeout']} s")
        except RuntimeError as e: raise E(502, "llm_failed", llm_failure_message(e))
        res = []
        for idea in got[:n]:
            ok, cited, reason = check_cites(idea["text"], ids["A"], ids["B"], ids["bridge"]); first = reason
            if not ok:
                log.warning("ideas: regenerating %r: %s", idea["title"], reason)
                try:
                    alt = parse_ideas(call_llm(head + f'This idea failed a check ({reason}):\n{idea["title"]}: {idea["text"]}\n\n'
                                               f'Write ONE replacement idea, 4-6 sentences: {rules}\nReply ONLY with a JSON array of one '
                                               '{"title": "...", "idea": "..."} object.'))[0]
                    ok, cited, reason = check_cites(alt["text"], ids["A"], ids["B"], ids["bridge"]); idea = alt if ok else idea
                    if not ok: cited = CITE.findall(idea["text"])
                except (ValueError, IndexError, RuntimeError, subprocess.TimeoutExpired) as e:
                    reason = f"{first}; regeneration failed ({type(e).__name__})"
            if not ok: log.warning("ideas: dropped %r: %s", idea["title"], reason); print(f"ideas: dropped: {reason}", file=sys.stderr)
            res.append(dict(idea_id=secrets.token_hex(6), title=idea["title"], text=idea["text"], cited=list(dict.fromkeys(cited)),
                            status="ok" if ok else "dropped", reason=None if ok and first is None else (f"regenerated: {first}" if ok else reason)))
        model = "fake" if CFG["fake"] else CFG["model"]
        created = datetime.now().isoformat()
        sides = {p["id"]: p["side"] for p in shown_p}
        with WB_LOCK:
            for x in res:
                S.wb.execute("""insert into ideas (idea_id, a_sel, b_sel, a_name, b_name, title, text, cited, shown_ids, status, reason, model,
                                run_id, window_from, window_to, shown_sides, created) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [x["idea_id"], A["sel_id"], B["sel_id"], A["name"], B["name"], x["title"], x["text"], x["cited"],
                              [p["id"] for p in shown_p], x["status"], x["reason"], model, S.run_id, w["obj"]["from"], w["obj"]["to"], json.dumps(sides), created])
        return {"a": A["sel_id"], "b": B["sel_id"], "model": model, "shown": shown_p, "dropped": sum(x["status"] == "dropped" for x in res),
                "created": created, "window": w["obj"], "dropped_reasons": [x["reason"] for x in res if x["status"] == "dropped"],
                "ideas": [{k: x[k] for k in ("idea_id", "title", "text", "cited", "status")} for x in res if x["status"] != "dropped"]}
    finally: IDEAS_LOCK.release()


def h_ideas_get(h, q, body, cur):
    sql, args = "select idea_id, created, a_sel, b_sel, a_name, b_name, title, text, cited, status, shown_sides, window_from, window_to from ideas where status != 'dropped'", []
    for k in ("a", "b"):
        if q.get(k): sql += f" and {k}_sel = ?"; args.append(q[k])
    with WB_LOCK: rows = S.wb.execute(sql + " order by created desc", args).fetchall()
    keys = ("idea_id", "created", "a_sel", "b_sel", "a_name", "b_name", "title", "text", "cited", "status")
    return [{**dict(zip(keys, r)), "created": iso(r[1]), "shown_sides": json.loads(r[10] or "{}"),
             "window": {"from": r[11], "to": r[12]}} for r in rows]


def h_fault(h, q, body, cur):
    if not os.environ.get("RADAR_FAULT"): raise E(404, "not_found", "no route GET /fault")
    return 1 / 0


ID_RE, CC_RE = re.compile(r"I\d+"), re.compile(r"[A-Z]{2}")


def h_filterjs(h, q, body, cur):
    f = ROOT / "radar/filter.js"
    if not f.exists(): raise E(404, "not_found", "radar/filter.js is missing")
    h.send_bytes(200, f.read_bytes(), "application/javascript; charset=utf-8", {"Cache-Control": "no-cache"})


def h_filterdata(h, q, body, cur):
    """Uint8 flags per row (bit0 accepted, bit1 workshop, bit2 submitted, bit3 code, bit4 withdrawn), then Uint8 cit_pct per row (255 unknown)."""
    with EXTRA_LOCK:
        if S.filterdata is None:
            fl = np.zeros(S.n, np.uint8)
            if "paper_comments" in S.tables:
                for r, kind, code, wd in cur.execute("select p.row, c.kind, c.code_url is not null, c.withdrawn from point_index p join paper_comments c using(id)").fetchall():
                    fl[r] = (kind == "accepted") | (2 if kind == "workshop" else 0) | (4 if kind == "submitted" else 0) | (8 if code else 0) | (16 if wd else 0)
            S.filterdata = fl.tobytes() + S.citp.tobytes()
    h.send_bytes(200, S.filterdata, "application/octet-stream", {"X-N": str(S.n), "Cache-Control": "no-cache"})


def h_institutions(h, q, body, cur):
    if "institutions" not in S.tables: return []
    s = (q.get("q") or "").strip().lower()
    if not s: sql, a = "select inst_id, name, country, n from institutions where n >= 30 order by n desc, inst_id limit 20", []
    else:
        sql = """select inst_id, name, country, n from institutions where n >= 30 and contains(lower(name), ?)
                 order by not starts_with(lower(name), ?), n desc, inst_id limit 20"""; a = [s, s]
    return [[i, nm, cc, int(n)] for i, nm, cc, n in cur.execute(sql, a).fetchall()]


def h_countries(h, q, body, cur):
    with EXTRA_LOCK:
        if S.countries is None:
            S.countries = [] if not {"institutions", "paper_institutions"} <= S.tables else [[cc, int(n)] for cc, n in cur.execute(
                """select upper(i.country), count(distinct a.id) n from paper_institutions a join institutions i using(inst_id)
                   join point_index p on p.id = a.id where regexp_full_match(i.country, '[A-Za-z]{2}') group by 1 having count(distinct a.id) >= 30 order by n desc, 1""").fetchall()]
    return S.countries


def h_rows(h, q, body, cur):
    insts = [x for x in (q.get("inst") or "").split(",") if x] if q.get("inst") else []
    cc = q.get("country")
    if len(insts) > 5 or not all(ID_RE.fullmatch(x) for x in insts): raise bad("inst must be at most 5 comma-separated ids like I123")
    if cc is not None and not CC_RE.fullmatch(cc): raise bad("country must be a two-letter upper-case code")
    if not insts and cc is None: raise bad("inst or country is required")
    blocks = []
    have = {"institutions", "paper_institutions"} <= S.tables
    for x in insts:
        got = cur.execute("select distinct p.row from paper_institutions a join point_index p on p.id = a.id where a.inst_id = ? order by 1", [x]).fetchnumpy()["row"] if have else []
        blocks.append(np.asarray(got, "<u4"))
    if cc is not None:
        got = cur.execute("""select distinct p.row from paper_institutions a join institutions i using(inst_id) join point_index p on p.id = a.id
                             where i.country = ? order by 1""", [cc]).fetchnumpy()["row"] if have else []
        blocks.append(np.asarray(got, "<u4"))
    h.send_bytes(200, b"".join(b.tobytes() for b in blocks), "application/octet-stream", {"X-Lens": ",".join(str(len(b)) for b in blocks)})


ROUTES = {("GET", "/"): h_index, ("GET", "/points.bin"): h_points, ("GET", "/meta"): h_meta, ("GET", "/labels"): h_labels,
          ("GET", "/phrases"): h_phrases, ("GET", "/paper"): h_paper, ("GET", "/topics"): h_topics, ("GET", "/links"): h_links, ("GET", "/link"): h_link,
          ("POST", "/select"): h_select, ("GET", "/distance"): h_distance, ("GET", "/colour/dist"): h_colour_dist,
          ("GET", "/selections"): h_selections_get, ("POST", "/selections"): h_selections_post,
          ("POST", "/selections/delete"): h_selections_delete, ("POST", "/ideas"): h_ideas_post, ("GET", "/ideas"): h_ideas_get,
          ("GET", "/fault"): h_fault, ("GET", "/topic"): h_topic, ("GET", "/filter.js"): h_filterjs, ("GET", "/filterdata"): h_filterdata,
          ("GET", "/institutions"): h_institutions, ("GET", "/countries"): h_countries, ("GET", "/rows"): h_rows}


class Handler(BaseHTTPRequestHandler):
    server_version = "undercurrent"

    def log_message(self, fmt, *args): log.info("%s %s", self.address_string(), fmt % args)
    def do_GET(self): self.dispatch("GET")
    def do_POST(self): self.dispatch("POST")

    def send_bytes(self, status, data, ctype, headers=None):
        self.send_response(status); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers()
        if status != 304: self.wfile.write(data)

    def send_json(self, status, obj):
        if isinstance(obj, dict) and "run_id" not in obj: obj = {**obj, "run_id": S.run_id}
        self.send_bytes(status, json.dumps(obj, default=lambda o: o.item() if hasattr(o, "item") else str(o)).encode(), "application/json")

    def dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            port = CFG["port"]
            if not host_ok(self.headers.get("Host"), port): raise E(403, "forbidden", f"Host header must be localhost:{port} or 127.0.0.1:{port}")
            rid = self.headers.get("X-Run-Id")
            if rid and rid != S.run_id: raise E(409, "stale_run", f"the map changed (run {S.run_id}); reload the page")
            body = None
            if method == "POST":
                if not origin_ok(self.headers.get("Origin"), port): raise E(403, "forbidden", f"POST needs Origin http://localhost:{port}")
                if not (self.headers.get("Content-Type") or "").startswith("application/json"): raise E(415, "bad_type", "POST body must be application/json")
                size = int(self.headers.get("Content-Length") or 0)
                if size > MAX_BODY:
                    left = min(size, 64 << 20)
                    while left > 0: left -= len(self.rfile.read(min(left, 1 << 20)) or b"x" * left)
                    raise E(413, "too_large", f"body over {MAX_BODY >> 20} MB")
                try: body = json.loads(self.rfile.read(size) or b"null")
                except ValueError: raise bad("body is not valid JSON")
                if not isinstance(body, dict): raise bad("body must be a JSON object")
            fn = ROUTES.get((method, url.path))
            if fn is None: raise E(404, "not_found", f"no route {method} {url.path}")
            if CFG["slow"] and url.path not in ("/", "/meta", "/points.bin", "/filter.js"): time.sleep(CFG["slow"])
            cur = S.con.cursor()
            try: out = fn(self, q, body, cur)
            finally: cur.close()
            if out is not None: self.send_json(200, out)
        except E as e: self.send_json(e.status, {"error": e.code, "message": e.msg, "run_id": S.run_id,
                                              **({"n_unembedded": 0} if e.code == "empty_selection" else {})})
        except Exception as e:
            log.exception("internal error on %s %s", method, self.path)
            self.send_json(500, {"error": "internal", "message": type(e).__name__, "run_id": S.run_id})


def setup_log():
    P["log"].parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(P["log"]); fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    sh = logging.StreamHandler(sys.stderr); sh.setLevel(logging.WARNING); sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    log.handlers[:] = [fh, sh]; log.setLevel(logging.INFO); log.propagate = False


def start(port):
    """Locks, port check, load, bind. Returns the server (not yet serving)."""
    CFG["port"] = port
    S.started = datetime.now().isoformat(timespec="seconds")
    setup_log()
    S.stale, S.run_id = take_locks(), None
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0: die(f"port {port} is in use (not by radar.serve). Stop that process or run with --port {port + 1}.")
    load(); open_wb()
    try: srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError: die(f"port {port} is in use (not by radar.serve). Stop that process or run with --port {port + 1}.")
    srv.daemon_threads = True
    print(f"undercurrent workbench: http://localhost:{port}  (run {S.run_id}, {S.n:,} points, vectors loaded in {S.load_s:.0f} s)", flush=True)
    log.info("started on %d, run %s, %d points", port, S.run_id, S.n)
    return srv


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fake-llm", help="program that reads the prompt on stdin and prints the model reply (test hook)")
    ap.add_argument("--llm-timeout", type=float, default=300); ap.add_argument("--slow", type=float, default=0)
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    CFG.update(llm_timeout=a.llm_timeout, slow=a.slow, model=a.model, fake=a.fake_llm or CFG["fake"])
    if a.selftest: return selftest()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    srv = start(a.port)
    try: srv.serve_forever()
    finally: srv.server_close()


# ---------- self-test on a tiny fixture database (flow F35.1 step 2) ----------

def make_fixture(path):
    """90 embedded papers in 3 fine clusters (0 robots, 1 diffusion, 2 graphs; 10 papers between 0 and 1), plus 1 unembedded."""
    rng = np.random.default_rng(0); n = 90
    cen = np.eye(3, 768, dtype=np.float32) * 10
    l0 = np.array([i % 3 for i in range(80)] + [0] * 5 + [1] * 5)
    V = cen[l0] + rng.normal(0, 1, (n, 768)).astype(np.float32); V[80:] = (cen[0] + cen[1]) / 2 + rng.normal(0, 1, (10, 768))
    month = np.array([(i * 7) % 36 for i in range(n)]); month[:30] = np.maximum(month[:30], 30)   # cluster 0 grows recently
    ids = [f"{(23 * 12 + 10 + m) // 12 % 100:02d}{(23 * 12 + 10 + m) % 12 + 1:02d}.{i:05d}" for i, m in enumerate(month)]
    order = np.argsort(ids); ids = [ids[i] for i in order]; l0, month, V = l0[order], month[order], V[order]
    l1 = np.where(l0 == 2, 1, 0); l2 = np.zeros(n, int)
    words = {0: "robot manipulation policy", 1: "diffusion model generation", 2: "graph algorithm complexity"}
    Vn = V / np.linalg.norm(V, axis=1, keepdims=True); sim = Vn @ Vn.T; np.fill_diagonal(sim, -9)
    K = np.argsort(-sim, axis=1)[:, :14]
    con = duckdb.connect(str(path))
    con.execute(harvest.SCHEMA)
    con.execute("create table embeddings (id text, vec float[768]); create table s2_meta (id text, s2_id text, citations int, influential int, tldr text, fetched_at timestamp)")
    con.execute("create table topics (level int, cluster int, label text, n int, cx double, cy double)")
    con.execute("create table signals (run_date date, signal text, key text, recent int, prior int, growth double, first_seen date, breadth int, accel double, score double, run_tag text)")
    for r, i in enumerate(ids):
        m = S_m0_abs = 2023 * 12 + 10 + int(month[r])
        title = f"{words[l0[r]]} study {r}" + (" world action models" if r % 9 == 0 else "")
        con.execute("insert into papers values (?,?,null,null,?,?,'cs.LG',['cs.LG'],['Ann','Bo'],'2026-11-05')",
                    [i, f"{m // 12}-{m % 12 + 1:02d}-01", title, f"We study {words[l0[r]]} with experiments {r}."])
        con.execute("insert into embeddings values (?,?)", [i, V[r].tolist()])
        con.execute("insert into s2_meta values (?,null,?,0,null,now())", [i, r])
    con.execute("insert into papers values ('2611.99999','2026-11-01',null,null,'diffusion of a lost paper','x','cs.LG',['cs.LG'],[],'2026-11-05')")
    pi = [(r, ids[r], int(month[r]), 0, int(l0[r]), int(l1[r]), int(l2[r]), float(V[r, 0]), float(V[r, 1])) for r in range(n)]
    con.execute("create table point_index (row integer, id text, month smallint, cat smallint, l0 smallint, l1 smallint, l2 smallint, x real, y real)")
    con.executemany("insert into point_index values (?,?,?,?,?,?,?,?,?)", pi)
    con.execute("create table knn (row integer, nbr integer, rank tinyint, dist real)")
    con.executemany("insert into knn values (?,?,?,?)", [(r, int(K[r, j]), j + 1, 0.0) for r in range(n) for j in range(14)])
    con.execute("""create table map_meta (run_id text, created timestamp, n_points integer, month0 text, n_months integer, categories json,
                   knn_recall14 double, knn_bridge_agree double, bridge_T integer, onset_share double, links_run_id text)""")
    con.execute("""insert into map_meta values ('T0', '2026-11-01', 1, '2023-11', 37, '[]', null, null, null, null, null),
                   ('T1', '2026-11-06', ?, '2023-11', 37, '["cs.LG"]', 1.0, 100.0, 4, 0.1, 'T1')""", [n])
    L = [l0, l1, l2]
    for lv, names in ((0, words), (1, {0: "robots and diffusion", 1: "graphs"}), (2, {0: "all of CS"})):
        for c, nm in names.items(): con.execute("insert into topics values (?,?,?,?,0,0)", [lv, c, nm, int((L[lv] == c).sum())])
    con.execute("""create table topic_months as select l.level, case l.level when 0 then l0 when 1 then l1 else l2 end as cluster, month, count(*) n
                   from point_index cross join (values (0), (1), (2)) l(level) group by all""")
    con.execute("create table topic_onset as select level, cluster, 30::smallint onset_month, 0.1::real onset_share from topic_months group by all")
    con.execute("create table topic_parent (level tinyint, cluster smallint, parent_level tinyint, parent smallint, share real)")
    con.executemany("insert into topic_parent values (?,?,?,?,1.0)", [(0, 0, 1, 0), (0, 1, 1, 0), (0, 2, 1, 1), (0, 0, 2, 0), (0, 1, 2, 0), (0, 2, 2, 0),
                                                                      (1, 0, 2, 0), (1, 1, 2, 0)])
    con.execute("create table bridges (row integer, level tinyint, c1 smallint, c2 smallint, k1 tinyint, k2 tinyint, month smallint)")
    for lv in range(2):
        for r in range(n):
            cnt = np.bincount(L[lv][K[r]], minlength=3); a, b = np.argsort(-cnt, kind="stable")[:2]
            if cnt[a] >= 4 and cnt[b] >= 4:
                c1, c2 = sorted((int(a), int(b))); con.execute("insert into bridges values (?,?,?,?,?,?,?)", [r, lv, c1, c2, int(cnt[c1]), int(cnt[c2]), int(month[r])])
    con.execute("create table topic_links as select level, c1, c2, month, count(*)::int n_bridge from bridges group by all")
    vocab = sorted({w for r in range(n) for w in words[l0[r]].split()})
    con.execute("create table terms (term_id integer, term text, n_all integer)")
    con.executemany("insert into terms values (?,?,?)", [(j, w, int(sum(w in words[x].split() for x in l0))) for j, w in enumerate(vocab)])
    con.execute("create table paper_terms (row integer, term_id integer)")
    con.executemany("insert into paper_terms values (?,?)", [(r, vocab.index(w)) for r in range(n) for w in words[l0[r]].split()])
    con.execute("create table phrase_topics (phrase text, level tinyint, cluster smallint, n integer, share real)")
    con.executemany("insert into phrase_topics values (?,?,?,?,?)", [("shared phrase", lv, c, 12, 0.5) for lv in (0, 1) for c in (0, 1)])
    con.execute("insert into signals values ('2026-11-05','S3','world action model',1,1,1,null,1,1,1,'live'), ('2026-11-05','S3','agent harness',1,1,1,null,1,1,1,'live')")
    con.execute("create table phrase_papers as select 'world action model'::text phrase, row from point_index where l0 = 0")
    con.execute("""insert into phrase_topics select 'world action model', 1, 0, count(*), 1.0 from phrase_papers""")
    con.close()


FAKE = r'''#!/usr/bin/env python3
import json, pathlib, re, sys, time
d = pathlib.Path(__file__).parent; mode = (d / "mode").read_text().strip(); p = sys.stdin.read()
ids = {s: re.findall(r"\[" + s + r"\] arXiv:(\S+)", p) for s in ("A", "B", "bridge")}
n = (d / "count"); c = int(n.read_text()) if n.exists() else 0; n.write_text(str(c + 1))
if mode == "good": print(json.dumps([{"title": "t", "idea": f"Use arXiv:{ids['A'][0]} on arXiv:{ids['B'][0]}."}]))
elif mode == "outside": print(json.dumps([{"title": "t", "idea": "Cites arXiv:2405.01234 only."}]))
elif mode == "badjson": print("not json")
elif mode == "badthengood": print("not json" if c == 0 else json.dumps([{"title": "t", "idea": f"arXiv:{ids['A'][0]} arXiv:{ids['B'][0]}"}]))
elif mode == "exit": sys.exit(3)
elif mode == "sleep": time.sleep(5)
'''


def selftest():
    import urllib.request
    assert [llm_failure_message(RuntimeError(s)) for s in (
        "claude -p exited 1:  denied  \nmore detail", "fake model exited 3:  failed  \nmore detail",
        "claude -p exited 2: ", "fake model exited 4: ")] == [
        "claude -p exited with code 1 (denied)", "fake model exited with code 3 (failed)",
        "claude -p exited with code 2 (no output)", "fake model exited with code 4 (no output)"]
    t0 = time.time()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="radar-serve-selftest-"))
    P.update(db=tmp / "fx.duckdb", pid=tmp / "serve.pid", lock=tmp / "run.lock", wb=tmp / "wb.duckdb", log=tmp / "serve.log", points=tmp / "points.bin")
    make_fixture(P["db"]); (tmp / "points.bin").write_bytes(b"FRPT" + bytes(28))
    fake = tmp / "fake.py"; fake.write_text(FAKE); fake.chmod(0o755); CFG.update(fake=str(fake), llm_timeout=3)
    me = tmp / "me.pid"; me.write_text(str(os.getpid()))
    assert pid_alive(me, "radar.serve")[0] and not pid_alive(me, "run_all.sh")[0]
    (tmp / "dead.pid").write_text("999999"); assert pid_alive(tmp / "dead.pid", "radar.serve")[:2] == (False, 999999)
    P["pid"].write_text("999999\n")
    with socket.socket() as s: s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    srv = start(port); threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def req(path, body=None, host=f"localhost:{port}", origin=f"http://localhost:{port}", ctype="application/json"):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        r = urllib.request.Request(base + path, data=data, headers={"Host": host, **({"Origin": origin} if origin else {}), "Content-Type": ctype})
        try:
            with urllib.request.urlopen(r) as f: raw = f.read(); return f.status, (json.loads(raw) if f.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def ok(cond, what): assert cond, what; print("ok", what)
    st, m = req("/meta"); ok(st == 200 and "999999" in (m["stale_removed"] or "") and P["pid"].read_text().strip() == str(os.getpid()), "stale serve.pid removed and replaced")
    ok(m["levels"] == {"2": 1, "1": 2, "0": 3} and m["last_complete"] == "2026-10" and len(m["months"]) == 36, "/meta levels and months")
    assert set(m) == {"run_id", "n_points", "months", "last_complete", "categories", "levels", "bridge_T", "onset_share",
                      "knn_recall14", "knn_bridge_agree", "backtest_line", "level0_links", "stale_removed", "started", "extra"}
    assert datetime.fromisoformat(m["started"])
    ok(req("/meta", host="evil.example")[0] == 403, "Host guard")
    ok(req("/select", {"rows": [1, 2]}, origin=None)[0] == 403 and req("/select", {"rows": [1]}, origin="http://attacker.test")[0] == 403, "Origin guard")
    ok(req("/select", b"x", ctype="text/plain")[0] == 415, "Content-Type 415")
    ok(req("/select", b"{" + b" " * (9 << 20) + b"}")[0] == 413 and req("/meta")[0] == 200, "body over 8 MB is 413, server still up")
    st, e = req("/select", {"rows": [1], "phrase": "x"}); ok(st == 400 and e["message"] == "exactly one of rows, topic, phrase, query, sel_id", "/select two selectors 400")
    ok(req("/select", {"rows": [-1]})[0] == 400 and req("/select", {"rows": [90]})[0] == 400 and req("/select", {"rows": [1.5]})[0] == 400, "/select rows out of range 400")
    ok(req("/nothing")[0] == 404 and req("/topics?from=2030-01&to=2030-06")[0] == 400 and req("/topics?from=2026-06&to=2026-11")[0] == 400, "404 and window 400s")
    st, t = req("/topics"); ok(st == 200 and len(t["topics"]) == 2 and t["window"]["from"] == "2026-05", "/topics default window")
    tp = t["topics"][0]; ok(tp["growth"] is None or abs(tp["growth"] - (tp["recent"] + 1) / (tp["prior"] + 1) / t["window"]["vol_ratio"]) < 1e-9, "topic growth formula")
    # ponytail: count fixture isolates arriving from onset/growth; cluster 1 matches the corpus share.
    tm0, onset0, parents = S.TM[0], S.onset0, S.parent
    try:
        S.TM[0] = np.zeros_like(tm0); S.onset0 = np.array([-1, 30, 30])
        S.TM[0][:, 30] = [40, 40, 0]; S.TM[0][:, 0] = [0, 60, 60]
        S.TM[0][:, 36] = [1000, 0, 0]   # partial month must not dilute cluster 0's 100% share
        arriving = [a for tp in h_topics(None, {}, None, None)["topics"] for a in tp["arriving"]]
        assert [(a["c"], a["window_share"], a["corpus_share"], a["onset"]) for a in arriving] == [(0, 1.0, 0.4, None)]
        S.parent = {(0, 1): {0: (1, 0.75)}, (1, 2): {1: (0, 0.5)}}
        assert [(tp["c"], a["c"], a["parent_share"]) for tp in h_topics(None, {"level": "2"}, None, None)["topics"]
                for a in tp["arriving"]] == [(0, 0, 0.75)]
    finally: S.TM[0], S.onset0, S.parent = tm0, onset0, parents
    st, ph = req("/phrases")
    assert st == 200 and ph["level"] == 1 and ph["window"] == t["window"]
    assert [p["phrase"] for p in ph["phrases"]] == ["world action model", "agent harness"]
    p = ph["phrases"][0]
    assert p["n"] == 32 and p["eff"] == 1 and p["topics"] == [{"c": 0, "label": "robots and diffusion", "share": 1.0}]
    assert p["growth"] == (p["recent"] + 1) / (p["prior"] + 1) / ph["window"]["vol_ratio"]
    assert ph["phrases"][1]["growth"] is None and req("/phrases?level=3")[0] == 400
    st, u = req("/select", {"phrase": "diffusion of a lost paper"})
    assert st == 200 and u["n"] == 0 and u["n_unembedded"] == 1 and u["rows"] == []
    assert u["growth"] is None and not any(u["months"]) and u["terms"] == u["topics"] == []
    assert u["top_cited"][0]["id"] == u["newest"][0]["id"] == "2611.99999"
    assert req("/select", {"phrase": "zzqx"})[1]["n_unembedded"] == 0
    st, a = req("/select", {"topic": {"level": 0, "c": 0}}); ok(st == 200 and a["n"] == 32 and "rows" not in a and len(a["months"]) == 36, "/select topic")
    st, b = req("/select", {"topic": {"level": 0, "c": 1}}); st2, c = req("/select", {"topic": {"level": 0, "c": 2}})
    ok(req("/select", {"phrase": "world action models"})[1]["kind"] == "phrase" and req("/select", {"phrase": "zzqx"})[0] == 422, "/select phrase and empty 422")
    st, qy = req("/select", {"query": "diffusion"}); ok(st == 200 and qy["n_unembedded"] == 1 and req("/select", {"query": "'; drop table papers; --"})[0] == 422, "/select query, injection is 422")
    st, d = req(f"/distance?a={a['sel_id']}&b={b['sel_id']}"); st2, d2 = req(f"/distance?a={a['sel_id']}&b={c['sel_id']}")
    ok(st == 200 and d["n_bridges"] > 0 and d["cos_dist"] < d2["cos_dist"], "/distance bridges and ordering")
    ok(abs(req(f"/distance?a={a['sel_id']}&b={a['sel_id']}")[1]["cos_dist"]) < 1e-6, "/distance to itself is 0")
    ok(len(req(f"/colour/dist?a={a['sel_id']}&level=0")[1]["dist"]) == 3, "/colour/dist")
    st, L = req("/links?level=0"); ok(st == 200 and L["phrase_links"] and L["phrase_links"][0]["phrases"][0]["phrase"] == "shared phrase", "/links")
    ok(req("/link?level=0&c1=1&c2=1")[0] == 400 and req("/link?level=0&c1=0&c2=1")[0] == 200, "/link")
    ok(req("/paper?row=0")[1]["url"].startswith("https://arxiv.org/abs/") and req("/paper?row=90")[0] == 400, "/paper")
    (tmp / "mode").write_text("outside")
    st, r = req("/ideas", {"a": a["sel_id"], "b": b["sel_id"], "n": 1, "a_name": "ignored", "b_name": "ignored", "window": {}})
    ok(st == 200 and r["ideas"] == [] and r["dropped"] == 1 and len(r["shown"]) <= 28, "ideas: out-of-set citation dropped after one regeneration")
    reason = S.wb.execute("select reason from ideas where status = 'dropped'").fetchone()[0]; ok("2405.01234" in reason, "dropped reason stored")
    assert r["dropped_reasons"] == [reason]
    assert datetime.fromisoformat(r["created"]) and r["window"] == t["window"]
    ok(len(req("/selections")[1]) == 2, "ideas auto-saved A and B")
    (tmp / "mode").write_text("good"); st, r = req("/ideas", {"a": a["sel_id"], "b": b["sel_id"], "n": 1})
    ok(st == 200 and len(r["ideas"]) == 1 and r["ideas"][0]["status"] == "ok", "ideas: valid idea kept")
    ok(len(req(f"/ideas?a={a['sel_id']}")[1]) == 1, "GET /ideas")
    stored = req(f"/ideas?a={a['sel_id']}")[1][0]
    assert stored["shown_sides"] == {p["id"]: p["side"] for p in r["shown"]}
    assert stored["window"] == {k: r["window"][k] for k in ("from", "to")} and stored["created"] == r["created"]
    S.wb.execute("update ideas set shown_sides = null where idea_id = ?", [stored["idea_id"]])
    assert req(f"/ideas?a={a['sel_id']}")[1][0]["shown_sides"] == {}

    (tmp / "count").unlink(); (tmp / "mode").write_text("badthengood"); ok(req("/ideas", {"a": a["sel_id"], "b": b["sel_id"], "n": 1})[0] == 200, "ideas: parse retry")
    (tmp / "mode").write_text("badjson"); st, r = req("/ideas", {"a": a["sel_id"], "b": b["sel_id"]}); ok(st == 502 and r["error"] == "llm_failed", "ideas: bad JSON twice 502")
    (tmp / "mode").write_text("exit"); ok(req("/ideas", {"a": a["sel_id"], "b": b["sel_id"]})[0] == 502, "ideas: model exit 502")
    (tmp / "mode").write_text("sleep"); st, r = req("/ideas", {"a": a["sel_id"], "b": b["sel_id"]}); ok(st == 504 and r["error"] == "llm_timeout", "ideas: timeout 504")
    ok(req("/ideas", {"a": a["sel_id"], "b": a["sel_id"]})[0] == 400 and req("/ideas", {"a": a["sel_id"], "b": b["sel_id"], "n": 9})[0] == 400
       and req("/ideas", {"a": "nope", "b": b["sel_id"]})[0] == 404, "ideas validation")
    ok(req("/selections/delete", {"sel_id": a["sel_id"]})[0] == 409, "delete refused while ideas reference it")
    ok(req("/selections", {"sel_id": c["sel_id"], "name": ""})[0] == 400 and req("/selections", {"sel_id": c["sel_id"], "name": "g"})[0] == 200, "save needs a name")
    assert req("/selections", {"sel_id": c["sel_id"], "name": "g", "run_id": "ignored"})[0] == 200
    ok(req("/selections/delete", {"sel_id": c["sel_id"]})[1] == {"deleted": True, "run_id": "T1"} and req("/selections/delete", {"sel_id": "nope"})[0] == 404, "delete")
    with CACHE_LOCK: CACHE.clear()
    st, s2 = req("/select", {"sel_id": b["sel_id"]}); ok(st == 200 and s2["n"] == b["n"] and s2["missing"] == 0, "saved selection reloads from workbench")

    # ---- filter page endpoints: absent tables first, then a reload with the extra tables ----
    import struct
    def raw(path, host=f"localhost:{port}"):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, headers={"Host": host})) as f: return f.status, dict(f.headers), f.read()
        except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()
    ok(m["extra"] == {} and req("/institutions")[1] == [] and req("/countries")[1] == [], "absent tables: /meta extra, /institutions, /countries empty")
    st, hd, fd = raw("/filterdata"); ok(st == 200 and hd["X-N"] == "90" and fd == bytes(90) + bytes([255]) * 90, "absent tables: /filterdata all zero flags, cit_pct 255")
    st, hd, rb = raw("/rows?inst=I1&country=US"); ok(st == 200 and hd["X-Lens"] == "0,0" and rb == b"", "absent tables: /rows empty blocks")
    st, tj = req("/topic?level=0&c=0"); ok(st == 200 and tj["signals"] is None and tj["signals_all"] is None and tj["label"] == "robot manipulation policy", "absent tables: /topic signals null")
    ok(all(tp["venue_share"] is None and tp["cit_median_pct"] is None for tp in req("/topics?level=0")[1]["topics"]), "absent tables: /topics signal fields null")
    st, pj = req("/paper?row=0"); ok(pj["venue"] is None and pj["code_url"] is None and pj["cit_pct"] is None, "absent tables: /paper extras null")
    ok(req("/links?level=0")[1]["cite_links"] == [] and all(b["n_cites"] == 0 for b in req("/links?level=0")[1]["bridge_links"]), "absent tables: /links cite_links empty")
    st, lk = req("/link?level=0&c1=0&c2=1"); ok(lk["cites"] == {"n_cites": 0, "n_1to2": 0, "n_2to1": 0, "top": []}, "absent tables: /link cites empty")
    st, hd, js = raw("/filter.js"); ok(st == 200 and hd["Content-Type"].startswith("application/javascript") and b"UCFilter" in js, "/filter.js served")
    for bad_path in ("/rows", "/rows?inst=x1", "/rows?inst=I1,I2,I3,I4,I5,I6", "/rows?country=us", "/rows?country=USA", "/rows?inst=I1;drop"):
        assert raw(bad_path)[0] == 400, bad_path
    print("ok /rows rejects bad input with 400")
    # load the extras into the fixture and reload server state
    S.con.close(); c = duckdb.connect(str(P["db"]))
    c.execute("""create table paper_comments as select id, null::varchar comment,
                 case when row % 5 in (0, 4) then 'NeurIPS' when row % 5 = 1 then 'WS' end venue, null::varchar venue_source,
                 case row % 5 when 0 then 'accepted' when 1 then 'workshop' when 2 then 'submitted' when 3 then 'preprint' else 'accepted' end kind,
                 2026 as year, case when row % 4 = 0 then 'https://code/' || row end as code_url, null::int as pages, row = 7 as withdrawn from point_index""")
    c.execute("create table institutions (inst_id text, ror text, name text, country text, type text, n int)")
    c.execute("""insert into institutions values ('I1', null, 'Mass Inst of Tech', 'US', null, 40), ('I2', null, 'Tsinghua University', 'CN', null, 35),
                 ('I3', null, 'Tiny Lab', 'US', null, 5), ('I4', null, 'Technion', 'IL', null, 50)""")
    c.execute("create table paper_institutions (id text, inst_id text, author_pos int)")
    c.execute("""insert into paper_institutions select id, 'I1', 0 from point_index where row % 2 = 0 union all select id, 'I1', 1 from point_index where row % 2 = 0
                 union all select id, 'I2', 0 from point_index where row % 3 = 0 union all select id, 'I3', 0 from point_index where row < 4
                 union all select id, 'I4', 0 from point_index where row % 10 = 0""")
    c.execute("create table extra_meta (key text, value text)"); c.execute("insert into extra_meta values ('snapshot', '2026-11-05'), ('n', '90')")
    c.execute("create table paper_cites as select row, case when row = 3 then null else row end::int cit_pct from point_index")
    c.execute("""create table topic_signals (level int, cluster int, win int, n int, venue_share double, code_share double, cit_median_pct double,
                 top10_share double, top_insts varchar, top_venues varchar)""")
    c.execute("""insert into topic_signals values (0, 0, 6, 20, 0.4, 0.25, 50, 0.1, '[["I1","MIT",9]]', '[["NeurIPS",5]]'),
                 (0, 0, 0, 32, 0.5, 0.3, 48, 0.1, null, null), (0, 1, 3, 7, null, null, null, null, null, null)""")
    c.execute("create table topic_citelinks (level int, c1 int, c2 int, month int, n_cites int, n_1to2 int, n_2to1 int)")
    c.execute("insert into topic_citelinks values (0, 0, 2, 30, 7, 5, 2), (0, 0, 2, 31, 6, 1, 5), (0, 0, 2, 5, 99, 99, 0), (0, 0, 1, 31, 12, 6, 6), (0, 1, 2, 31, 4, 4, 0)")
    c.execute("create table citelink_evidence (level int, c1 int, c2 int, cited_row int, n int)")
    c.execute("insert into citelink_evidence values (0, 0, 2, 5, 3), (0, 0, 2, 9, 4), (0, 0, 2, 1, 4)")
    c.close(); load()
    st, m2 = req("/meta"); ok(m2["extra"] == {"snapshot": "2026-11-05", "n": "90"}, "/meta extra from extra_meta")
    st, hd, fd = raw("/filterdata"); fl, cp = fd[:90], fd[90:]
    exp = lambda r: (r % 5 == 0 or r % 5 == 4) | (2 if r % 5 == 1 else 0) | (4 if r % 5 == 2 else 0) | (8 if r % 4 == 0 else 0) | (16 if r == 7 else 0)
    ok(st == 200 and hd["X-N"] == "90" and len(fd) == 180 and all(fl[r] == exp(r) for r in range(90)) and cp[3] == 255 and cp[5] == 5 and cp[89] == 89,
       "/filterdata flags and cit_pct")
    ok(raw("/filterdata")[2] is not None and S.filterdata is not None, "/filterdata cached")
    st, ins = req("/institutions"); ok([r[0] for r in ins] == ["I4", "I1", "I2"] and ins[0] == ["I4", "Technion", "IL", 50], "/institutions empty q: top by n, n >= 30 only")
    ok([r[0] for r in req("/institutions?q=TECH")[1]] == ["I4", "I1"], "/institutions prefix match before substring, case-insensitive")
    ok(req("/institutions?q=tiny")[1] == [] and req("/institutions?q=zzz")[1] == [], "/institutions n < 30 and no match excluded")
    st, cs = req("/countries"); ok(cs == [["US", 47], ["CN", 30]], "/countries")
    ids = np.array(S.ids)
    def rows_of(pred): return [r for r in range(90) if pred(r)]
    st, hd, rb = raw("/rows?inst=I1,I2&country=US")
    e1, e2, e3 = rows_of(lambda r: r % 2 == 0), rows_of(lambda r: r % 3 == 0), rows_of(lambda r: r % 2 == 0 or r < 4)
    lens = [int(x) for x in hd["X-Lens"].split(",")]
    got = list(struct.unpack(f"<{len(rb) // 4}I", rb)); parts = [got[:lens[0]], got[lens[0]:lens[0] + lens[1]], got[lens[0] + lens[1]:]]
    ok(st == 200 and lens == [len(e1), len(e2), len(e3)] and parts == [e1, e2, e3] and hd["Content-Type"] == "application/octet-stream", "/rows blocks sorted uint32 with X-Lens")
    st, tj = req("/topic?level=0&c=0"); tj0 = tj
    ok(tj["signals"] == {"win": 6, "n": 20, "venue_share": 0.4, "code_share": 0.25, "cit_median_pct": 50, "top10_share": 0.1,
                         "top_insts": [["I1", "MIT", 9]], "top_venues": [["NeurIPS", 5]]} and tj["signals_all"]["n"] == 32 and tj["signals_all"]["top_insts"] == [], "/topic signals and signals_all")
    st, tj = req("/topic?level=0&c=0&from=2026-08&to=2026-10"); ok(tj["signals"] is None and tj["signals_all"]["win"] == 0, "/topic window 3 with no signals row is null")
    tp0 = req("/topics?level=0")[1]["topics"]; ok(tp0[0]["venue_share"] == 0.4 and tp0[0]["code_share"] == 0.25 and tp0[0]["cit_median_pct"] == 50 and tp0[1]["venue_share"] is None, "/topics signal fields")
    st, pj = req("/paper?row=8"); ok(pj["venue"] is None and pj["code_url"] == "https://code/8" and pj["cit_pct"] == 8, "/paper venue, code_url, cit_pct")
    st, pj = req("/paper?row=0"); ok(pj["venue"] == "NeurIPS" and pj["cit_pct"] == 0, "/paper venue from paper_comments")
    st, d = req(f"/distance?a={a['sel_id']}&b={b['sel_id']}"); ok(all({"venue", "code_url", "cit_pct"} <= set(p) for p in d["bridges"]), "bridge paper objects carry the new fields")
    ok(req("/topic?level=0&c=0&from=2026-06&to=2026-10")[1]["signals"] is None, "/topic custom 5-month window: signals null, not the 6-month row")
    ok(req("/topic?level=0&c=0&from=2026-04&to=2026-09")[1]["signals"] is None, "/topic 6 months ending before E: signals null")
    ok(req("/topic?level=0&c=0&from=2026-05&to=2026-10")[1]["signals"]["win"] == 6, "/topic explicit latest 6 months: cached signals")
    ok(req("/topics?level=0&from=2026-06&to=2026-10")[1]["topics"][0]["venue_share"] is None, "/topics custom window: signal fields null")
    st, s3 = req("/select", {"topic": {"level": 0, "c": 0}}); ok(all({"venue", "code_url", "cit_pct"} <= set(p) for p in s3["top_cited"] + s3["newest"]), "top_cited and newest carry the new fields")
    ok(s3["signals"] == tj0["signals"] and s3["signals_all"]["n"] == 32, "/select topic carries signals and signals_all (local mode)")
    ok(req("/select", {"topic": {"level": 0, "c": 0}, "from": "2026-06", "to": "2026-10"})[1]["signals"] is None, "/select topic custom window: signals null")
    ok("signals" not in req("/select", {"rows": [1, 2, 3]})[1], "/select rows has no signals")
    st, L = req("/links?level=0"); br = {(x["c1"], x["c2"]): x for x in L["bridge_links"]}
    ok((0, 1) in br and br[(0, 1)]["n_cites"] == 12 and [(x["c1"], x["c2"], x["n_cites"], x["n_1to2"], x["n_2to1"]) for x in L["cite_links"]] == [(0, 2, 13, 6, 7)],
       "/links n_cites for the window, cite_links excludes bridge pairs and n_cites < 10")
    st, lk = req("/link?level=0&c1=0&c2=2")
    ok(lk["cites"]["n_cites"] == 13 and (lk["cites"]["n_1to2"], lk["cites"]["n_2to1"]) == (6, 7) and [(p["row"], p["n"]) for p in lk["cites"]["top"]] == [(1, 4), (9, 4), (5, 3)]
       and "venue" in lk["cites"]["top"][0], "/link cites with top evidence papers")
    # Reverse orientations contribute to the same pair; evidence is summed before the top-30 cut.
    S.con.close()
    c = duckdb.connect(str(P["db"]))
    c.execute("insert into topic_citelinks values (0, 2, 0, 35, 5, 2, 3)")
    c.execute("insert into citelink_evidence select 0, 2, 0, range, 10 from range(35)")
    c.close(); load()
    st, lk = req("/link?level=0&c1=0&c2=2")
    ok(lk["cites"]["n_cites"] == 18 and (lk["cites"]["n_1to2"], lk["cites"]["n_2to1"]) == (9, 9)
       and len(lk["cites"]["top"]) == 30 and lk["cites"]["top"][0]["n"] == 14, "both citation orientations summed, top 30")
    srv.shutdown(); srv.server_close(); S.wb.close(); S.con.close()
    import shutil; shutil.rmtree(tmp, ignore_errors=True)
    print(f"ok ({time.time() - t0:.1f} s)")


if __name__ == "__main__":
    main()
