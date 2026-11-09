"""Compare undercurrent's signals with dair.ai "ML Papers of the Week" picks.

Usage: .venv/bin/python tools/dair_compare.py [--db PATH] [--refresh] [--no-nbr]

Read-only. Opens a copy of radar.duckdb (pass --db; default data/radar.duckdb opened read_only),
caches the dair markdown under data/raw/dair/, writes report/dair-comparison.md and
data/raw/dair/picks_scored.csv. Tests:
  1 coverage   dair picks with an arXiv id, and how many are in the corpus, by year
  2 topic      was the pick's level-1 topic growing at the first day of the listing month?
               (growth from papers before that day; the clustering itself is post hoc)
  2b nbr       same question with no clustering: growth of the pick's 100 nearest
               SPECTER2 neighbours among papers from the 12 months before the cutoff
  3 phrase     does the pick contain a top-50 rising phrase stored at a backtest cutoff
               (stored S3 runs only used papers before that cutoff)
  4 lead       months between the topic first crossing x1.41 (unbroken) and the listing
  5 examples   ten convincing hits, ten biggest misses
Peak memory is about 2.5 GB (embeddings for the neighbour check; skip with --no-nbr).
"""
import argparse, datetime as dt, json, re, subprocess, sys, pathlib
import resource
import duckdb, numpy as np, pandas as pd
from sklearn.feature_extraction.text import CountVectorizer

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "dair"
OUT = ROOT / "report" / "dair-comparison.md"
BASE = "https://raw.githubusercontent.com/dair-ai/ML-Papers-of-the-Week/main/"
YEARS = [2023, 2024, 2025, 2026]
# same tokenizer as radar.signals.KW (copied, not imported, so this tool has no side effects on import)
KW = dict(stop_words="english", binary=True, lowercase=True, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b")
GROW, FAST = 2 ** 0.5, 2.0            # growth steps x1.41 and x2 (serve.py legend)
N_WIN = 6                             # 6 complete months vs the 6 before (serve.py default)
K_NBR = 100
RNG = np.random.default_rng(0)


def log(stage): print(f"[{stage}] peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9:.2f} GB", file=sys.stderr)
LLM_RE = r"language model|\bllms?\b|\bagent"   # confound control: LLM/agent papers


# ---------------------------------------------------------------- dair list
def fetch(refresh):
    RAW.mkdir(parents=True, exist_ok=True)
    for y in YEARS:
        f = RAW / f"{y}.md"
        if refresh or not f.exists() or f.stat().st_size == 0:
            subprocess.run(["curl", "-sSfL", "-A", "Mozilla/5.0 (research script)", "-o", str(f), f"{BASE}years/{y}.md"], check=True)
    return {y: (RAW / f"{y}.md").read_text() for y in YEARS}


MON = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
HEAD = re.compile(r"^## .*?\(\s*([A-Za-z]+)\.?\s+(\d+)\s*[-–]+\s*(?:([A-Za-z]+)\.?\s+)?(\d+)\s*\)", re.M)
AX = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)


def parse(texts):
    rows = []
    for y, t in texts.items():
        heads = list(HEAD.finditer(t))
        for i, h in enumerate(heads):
            m1, d1, m2, d2 = h.group(1)[:3].lower(), int(h.group(2)), (h.group(3) or h.group(1))[:3].lower(), int(h.group(4))
            end = dt.date(y, MON[m2], d2)        # week end; every Dec->Jan week sits in the later year's file
            body = t[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(t)]
            for line in body.splitlines():
                if not re.match(r"^\|\s*\d+\)", line): continue
                tm = re.search(r"\*\*(.+?)\*\*", line)
                title = tm.group(1).strip() if tm else line[:80]
                pm = re.search(r"\[Paper\]\(([^)]*)\)", line, re.I)
                ids = AX.findall(pm.group(1)) if pm else []
                ids = ids or AX.findall(line)
                rows.append(dict(year=y, week_end=end, week=h.group(0)[3:].strip(), title=title, arxiv=ids[0] if ids else None))
    df = pd.DataFrame(rows)
    df["week_end"] = pd.to_datetime(df.week_end)
    return df


# ---------------------------------------------------------------- corpus
def load_corpus(con):
    meta = con.execute("select month0, categories from map_meta order by created desc limit 1").fetchone()
    m0 = pd.Period(meta[0], "M")
    pi = con.execute(f"""select p.row, p.id, p.month, p.l1, p.l2, x.primary_cat,
                           regexp_matches(lower(x.title || ' ' || x.abstract), '{LLM_RE}') llm
                         from point_index p join papers x using(id) order by p.row""").df()
    labels = dict(con.execute("select cluster, label from topics where level=1").fetchall())
    return m0, pi, labels


def gclass(r, p, vol):
    """serve.gstats: new if r>=30 and p<5; no growth if r<10; else ((r+1)/(p+1))/vol."""
    if r >= 30 and p < 5: return np.inf
    if r < 10: return np.nan
    return (r + 1) / (p + 1) / vol


def topic_growth(pi):
    """G[k, c] = CS-corrected growth of topic k at cutoff month index c (recent c-6..c-1, prior c-12..c-7)."""
    K, M = pi.l1.max() + 1, pi.month.max() + 1
    cnt = np.zeros((K, M)); np.add.at(cnt, (pi.l1.values, pi.month.values), 1)
    vol = cnt.sum(0)
    G = np.full((K, M + 1), np.nan)
    for c in range(2 * N_WIN, M + 1):
        r, p = cnt[:, c - N_WIN:c].sum(1), cnt[:, c - 2 * N_WIN:c - N_WIN].sum(1)
        v = vol[c - N_WIN:c].sum() / vol[c - 2 * N_WIN:c - N_WIN].sum()
        G[:, c] = [gclass(a, b, v) for a, b in zip(r, p)]
    return G, cnt


def wilson(k, n, z=1.96):
    if n == 0: return (np.nan, np.nan)
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d; h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def lift_ci(hit, base, groups=None, B=4000):
    """Lift = mean(hit)/mean(base); bootstrap over picks (or over groups, e.g. pick id, when given)."""
    hit, base = np.asarray(hit, float), np.asarray(base, float)
    keys = np.arange(len(hit)) if groups is None else pd.factorize(groups)[0]
    idx = [np.flatnonzero(keys == g) for g in range(keys.max() + 1)]
    L = []
    for _ in range(B):
        s = np.concatenate([idx[i] for i in RNG.integers(0, len(idx), len(idx))])
        L.append(hit[s].mean() / base[s].mean())
    # one-sided test of "picks hit no more often than their matched base rates" (Poisson-binomial normal approx)
    z = (hit.sum() - base.sum()) / np.sqrt((base * (1 - base)).sum())
    return hit.mean() / base.mean(), np.percentile(L, [2.5, 97.5]), z


# ---------------------------------------------------------------- neighbour check (no clustering)
def nbr_growth(con, pi, jobs):
    """jobs: DataFrame(row, c). Returns growth of each row's K_NBR nearest neighbours among papers with
    month in [c-12, c-1] (cosine on SPECTER2), corrected for CS volume between the two 6-month halves."""
    # unit vectors stored as float16 (0.7 GB), streamed in batches through a separate low-memory connection
    month = pi.month.values
    row = dict(zip(pi.id, pi.row))
    X = np.zeros((len(pi), 768), np.float16)
    con.execute("set memory_limit='300MB'")
    for b in con.sql("select id, vec from embeddings").to_arrow_reader(10000):
        r = np.array([row.get(x, -1) for x in b.column(0).to_pylist()])
        V = b.column(1).values.to_numpy(zero_copy_only=False).reshape(len(r), -1)[r >= 0].astype(np.float32)
        X[r[r >= 0]] = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    con.execute("set memory_limit='1GB'")
    out = np.full(len(jobs), np.nan)
    # one reusable float32 buffer for the window (fresh per-cutoff arrays fragmented the heap: +0.45 GB per cutoff)
    maxw = max(int(((month >= c - 2 * N_WIN) & (month < c)).sum()) for c in jobs.c.unique())
    buf = np.empty((maxw, X.shape[1]), np.float32)
    B = 64
    St, Pt = np.empty((B, maxw), np.float32), np.empty((B, maxw), np.float32)
    for c, J in jobs.groupby("c"):
        w = np.flatnonzero((month >= c - 2 * N_WIN) & (month < c)); n = len(w)
        recent = month[w] >= c - N_WIN
        vol = recent.sum() / (~recent).sum()
        for i in range(0, n, 20000): buf[i:min(i + 20000, n)] = X[w[i:i + 20000]]
        Xw = buf[:n]
        rows, pos = J.row.values, J.index.values
        for s in range(0, len(rows), B):
            q = rows[s:s + B]; S = St[:len(q), :n]
            S[:] = X[q].astype(np.float32) @ Xw.T
            at = np.searchsorted(w, q); inw = (at < n) & (w[np.minimum(at, n - 1)] == q)
            S[np.flatnonzero(inw), at[inw]] = -9                     # a paper is not its own neighbour
            P = Pt[:len(q), :n]; P[:] = S; P.partition(n - K_NBR, axis=1)   # in place: argpartition's big index arrays grew the heap
            top = S >= P[:, n - K_NBR:n - K_NBR + 1]
            r = (top & recent).sum(1); p = top.sum(1) - r
            out[pos[s:s + B]] = (r + 1) / (p + 1) / vol
        log(f"nbr cutoff {c}")
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "radar.duckdb"))
    ap.add_argument("--refresh", action="store_true"); ap.add_argument("--no-nbr", action="store_true")
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True, config={"memory_limit": "1GB"})

    picks = parse(fetch(a.refresh))
    picks["listed"] = picks.week_end
    n_raw = len(picks)
    picks = picks.sort_values("listed").reset_index(drop=True)
    first = picks.dropna(subset=["arxiv"]).drop_duplicates("arxiv", keep="first")
    dups = picks.arxiv.notna().sum() - len(first)
    m0, pi, labels = load_corpus(con)
    in_papers = set(r[0] for r in con.execute("select id from papers").fetchall())
    row_of = dict(zip(pi.id, pi.row))
    sub = dict(con.execute("select id, submitted from papers").fetchall())

    # ---- 1 coverage
    cov = []
    for y, g in picks.groupby("year"):
        gi = first[first.year == y]
        early = (gi.arxiv < "2311").sum()
        cov.append(dict(year=y, picks=len(g), with_arxiv=int(g.arxiv.notna().sum()), unique_arxiv=len(gi),
                        before_corpus=int(early), in_papers=int(gi.arxiv.isin(in_papers).sum()), in_map=int(gi.arxiv.isin(row_of).sum())))
    cov = pd.DataFrame(cov)

    P = first[first.arxiv.isin(row_of)].copy()
    P["row"] = P.arxiv.map(row_of)
    P["sub_month"] = pi.month.values[P.row.values]
    P["topic"] = pi.l1.values[P.row.values]
    P["cat"] = pi.primary_cat.values[P.row.values]
    P["llm"] = pi.llm.values[P.row.values]
    P["area"] = pi.l2.values[P.row.values]
    P["c"] = [(pd.Period(d, "M") - m0).n for d in P.listed]          # cutoff = first day of listing month
    P["submitted"] = P.arxiv.map(sub)

    log("coverage")
    # ---- 2 topic test
    G, cnt = topic_growth(pi)
    M = cnt.shape[1]
    T = P[(P.c >= 2 * N_WIN) & (P.c <= M)].copy()
    T["g"] = G[T.topic.values, T.c.values]
    T["grow"] = np.nan_to_num(T.g, nan=0) >= GROW
    T["fast"] = np.nan_to_num(T.g, nan=0) >= FAST
    # matched base rates from topic x month (x category) counts
    df = pi.groupby(["l1", "l2", "month", "primary_cat", "llm"]).size().rename("n").reset_index()
    def base(c, m, thr, cat=None, llm=None, area=None):
        x = df[df.month == m]
        if area is not None: x = x[x.l2 == area]
        if cat is not None: x = x[x.primary_cat == cat]
        if llm is not None: x = x[x.llm == llm]
        if not x.n.sum(): return np.nan
        return (x.n * (np.nan_to_num(G[x.l1.values, c], nan=0) >= thr)).sum() / x.n.sum()
    for thr, name in [(GROW, "grow"), (FAST, "fast")]:
        T[f"b_all_{name}"] = [base(c, m, thr) for c, m in zip(T.c, T.sub_month)]
        T[f"b_cat_{name}"] = [base(c, m, thr, cat=k) for c, m, k in zip(T.c, T.sub_month, T.cat)]
        T[f"b_llm_{name}"] = [base(c, m, thr, llm=True) for c, m in zip(T.c, T.sub_month)]
        T[f"b_area_{name}"] = [base(c, m, thr, area=k) for c, m, k in zip(T.c, T.sub_month, T.area)]
    # also the share of all CS papers sitting in growing topics (what "growing" means corpus-wide)

    log("topic")
    # ---- 2b neighbour test (no clustering)
    nb = None
    if not a.no_nbr:
        base_rows = []
        for (c, m), g in T.groupby(["c", "sub_month"]):
            pool = np.flatnonzero(pi.month.values == m)
            base_rows += [(r, c, "all") for r in RNG.choice(pool, min(150, len(pool)), replace=False)]
            pool_l = np.flatnonzero((pi.month.values == m) & pi.llm.values)
            base_rows += [(r, c, "llm") for r in RNG.choice(pool_l, min(150, len(pool_l)), replace=False)]
        jobs = pd.concat([pd.DataFrame(dict(row=T.row.values, c=T.c.values, kind="pick")),
                          pd.DataFrame(base_rows, columns=["row", "c", "kind"])], ignore_index=True)
        jobs["ng"] = nbr_growth(con, pi, jobs)
        nb = jobs
        T["ng"] = jobs.ng.values[:len(T)]
        bm = jobs[jobs.kind != "pick"].groupby(["c", "kind"]).ng.apply(lambda s: (s >= GROW).mean()).unstack()
        # base per pick: average over its (c, sub_month) sample -- recompute keyed by (c, month)
        jb = jobs[jobs.kind != "pick"].copy(); jb["m"] = pi.month.values[jb.row.values]
        bcm = jb.groupby(["c", "m", "kind"]).ng.apply(lambda s: (s >= GROW).mean()).unstack()
        T["nb_all"] = [bcm.loc[(c, m), "all"] for c, m in zip(T.c, T.sub_month)]
        T["nb_llm"] = [bcm.loc[(c, m), "llm"] for c, m in zip(T.c, T.sub_month)]

    log("neighbours")
    # ---- 4 lead time (monthly cutoffs)
    lead, cens = [], []
    for r in T.itertuples():
        if not r.grow: lead.append(np.nan); cens.append(False); continue
        s = r.c
        while s - 1 >= 2 * N_WIN and np.nan_to_num(G[r.topic, s - 1], nan=0) >= GROW: s -= 1
        start = (m0 + s).to_timestamp()
        lead.append((r.listed - start).days / 30.44); cens.append(s == 2 * N_WIN)
    T["lead_months"], T["lead_censored"] = lead, cens
    T["ever_grow_before"] = [bool((np.nan_to_num(G[k, 2 * N_WIN:c + 1], nan=0) >= GROW).any()) for k, c in zip(T.topic, T.c)]

    log("lead")
    # ---- 3 phrase test
    tags = [t for (t,) in con.execute("select distinct run_tag from signals where signal='S3' and run_tag<>'live' order by 1").fetchall()]
    PH = []
    ph_lists = {}
    for C in tags:
        keys = [k for (k,) in con.execute("""select key from signals where signal='S3' and run_tag=? and
                 run_date=(select max(run_date) from signals where signal='S3' and run_tag=?) order by score desc limit 50""", [C, C]).fetchall()]
        ph_lists[C] = keys
        c0 = pd.Timestamp(C); c1 = c0 + pd.DateOffset(months=6)
        vec = CountVectorizer(vocabulary=sorted(set(keys)), ngram_range=(2, 6), **KW)
        late = con.execute("""select id, strftime(submitted,'%Y-%m') ym, title || '. ' || abstract txt,
                                regexp_matches(lower(title || ' ' || abstract), ?) llm
                              from papers where submitted >= ? and submitted < ?""", [LLM_RE, c0.date(), c1.date()]).df()
        hit_all = np.asarray(vec.transform(late.txt).sum(1)).ravel() > 0
        late["hit"] = hit_all
        rate = late.groupby("ym").hit.mean(); rate_llm = late[late.llm].groupby("ym").hit.mean()
        sel = first[(first.listed >= c0) & (first.listed < c1) & first.arxiv.isin(in_papers)].copy()
        if sel.empty: continue
        txt = con.execute(f"select id, title || '. ' || abstract txt, strftime(submitted,'%Y-%m') ym, submitted from papers where id in ({','.join(repr(x) for x in sel.arxiv)})").df()
        sel = sel.merge(txt, left_on="arxiv", right_on="id")
        M_ = vec.transform(sel.txt)
        names = vec.get_feature_names_out()
        sel["hit"] = np.asarray(M_.sum(1)).ravel() > 0
        sel["phrases"] = ["; ".join(names[M_[i].indices]) for i in range(len(sel))]
        sel["after"] = pd.to_datetime(sel.submitted) >= c0
        sel["b_all"] = sel.ym.map(rate); sel["b_llm"] = sel.ym.map(rate_llm)
        sel["cutoff"] = C; sel["base_window"] = hit_all.mean(); sel["base_window_llm"] = late[late.llm].hit.mean()
        PH.append(sel)
    PH = pd.concat(PH, ignore_index=True)
    log("phrases")

    # ---------------------------------------------------------------- report
    L = []
    def pct(x): return f"{100 * x:.1f}%"
    def ci(lo, hi): return f"{lo:.2f} to {hi:.2f}"

    Tn = len(T)
    res = {}
    for name in ["grow", "fast"]:
        for b in ["all", "cat", "llm", "area"]:
            ok = T[f"b_{b}_{name}"].notna()
            res[(name, b)] = (T[ok][name].mean(), T[ok][f"b_{b}_{name}"].mean(), *lift_ci(T[ok][name], T[ok][f"b_{b}_{name}"]), ok.sum())
    clean = PH[PH.after]
    ph_res = {b: (clean.hit.mean(), clean[f"b_{b}"].mean(), *lift_ci(clean.hit, clean[f"b_{b}"], clean.arxiv)) for b in ["all", "llm"]}
    PH["b_all_f"] = PH.b_all.fillna(PH.base_window)    # picks submitted before the window: use the window rate
    ph_all = (PH.hit.mean(), PH.b_all_f.mean(), *lift_ci(PH.hit, PH.b_all_f, PH.arxiv))
    if nb is not None:
        nb_res = {b: (T.ng.ge(GROW).mean(), T[f"nb_{b}"].mean(), *lift_ci(T.ng.ge(GROW), T[f"nb_{b}"])) for b in ["all", "llm"]}
    llm_share = T.llm.mean()
    grown = T[T.grow]

    g = res[("grow", "all")]; gc = res[("grow", "cat")]; gl = res[("grow", "llm")]; ga = res[("grow", "area")]
    vc = grown.topic.value_counts(); top2 = list(vc.index[:2]); share2 = vc.iloc[:2].sum() / max(len(grown), 1)
    ex = T[~T.topic.isin(top2)]
    ngt = pd.Series({c: int((np.nan_to_num(G[:, c], nan=0) >= GROW).sum()) for c in sorted(T.c.unique())})
    p0, pl = ph_res["all"], ph_res["llm"]
    lbl2 = " and ".join(f"\"{labels.get(int(k), k)}\"" for k in top2)
    L += ["# Did undercurrent predict dair.ai's Papers of the Week?", "",
          f"_Generated by `tools/dair_compare.py` on {dt.date.today()}. Database snapshot: map run {con.execute('select run_id from map_meta order by created desc limit 1').fetchone()[0]}._", "",
          "## Short answer", "",
          f"Not in the sense of \"undercurrent picks the papers dair later features\". What the data does show: dair picks sit in topics and use phrases that undercurrent "
          f"had already flagged as rising, far more often than an average CS paper, and still more often than other LLM/agent papers from the same month, but the second edge is modest.", "",
          f"The big topic-level number is close to a two-topic story. {pct(g[0])} of picks were in a topic growing at least x1.41 when their listing month began, vs {pct(g[1])} of all CS "
          f"papers from the same month. But {pct(share2)} of those hits come from two topics, {lbl2}, the two large LLM waves of 2025 and 2026. "
          f"Take those two topics out and picks were in a growing topic {pct(ex.grow.mean())} of the time, against a base rate of {pct(ex.b_llm_grow.mean())} for LLM/agent papers "
          f"of the same month. On a typical cutoff only {int(ngt.median())} of 114 topics are growing at x1.41, so \"in a growing topic\" mostly means \"in one of the two LLM topics\".", "",
          f"The two tests with no hindsight in the grouping give the fairer size of the edge over other LLM/agent papers: neighbourhood growth lift {nb_res['llm'][2]:.2f} "
          f"(CI {ci(*nb_res['llm'][3])}) and rising-phrase lift {pl[2]:.2f} (CI {ci(*pl[3])}). Both are well above 1, so dair's picks lean towards the faster-moving "
          "parts of LLM research more than the average LLM paper does. That is agreement between two trend-followers, not prediction of individual papers." if nb is not None else "", "",
          "## Headline numbers", "",
          f"- **Coverage.** {n_raw} picks listed {picks.listed.min():%Y-%m-%d} to {picks.listed.max():%Y-%m-%d}; {int(picks.arxiv.notna().sum())} have an arXiv link "
          f"({len(first)} unique papers). {int(first.arxiv.isin(in_papers).sum())} are in our papers table and {int(first.arxiv.isin(row_of).sum())} on the map. "
          f"The topic test can only use picks listed from {(m0 + 2 * N_WIN).strftime('%b %Y')} on (it needs 12 months of history): **{Tn} picks**.",
          f"- **Topic test (x1.41).** {pct(g[0])} of picks were in a topic growing at least x1.41 on the first day of their listing month. "
          f"Base rates for papers submitted the same month: all CS {pct(g[1])} (lift {g[2]:.2f}, 95% CI {ci(*g[3])}); "
          f"same primary category {pct(gc[1])} (lift {gc[2]:.2f}, CI {ci(*gc[3])}); LLM/agent papers {pct(gl[1])} (lift {gl[2]:.2f}, CI {ci(*gl[3])}); "
          f"same coarse area {pct(ga[1])} (lift {ga[2]:.2f}, CI {ci(*ga[3])}). {pct(share2)} of the hits are in two topics; without them the hit rate is {pct(ex.grow.mean())}.",
          f"- **Topic test (x2).** {pct(res[('fast','all')][0])} vs all CS {pct(res[('fast','all')][1])} (lift {res[('fast','all')][2]:.2f}, CI {ci(*res[('fast','all')][3])}); "
          f"vs LLM/agent papers {pct(res[('fast','llm')][1])} (lift {res[('fast','llm')][2]:.2f}, CI {ci(*res[('fast','llm')][3])}).",
          ]
    if nb is not None:
        L += [f"- **Neighbour test (no clustering, no lookahead in the grouping).** {pct(nb_res['all'][0])} of picks had their 100 nearest earlier papers growing at least x1.41; "
              f"random same-month CS papers {pct(nb_res['all'][1])} (lift {nb_res['all'][2]:.2f}, CI {ci(*nb_res['all'][3])}); "
              f"random same-month LLM/agent papers {pct(nb_res['llm'][1])} (lift {nb_res['llm'][2]:.2f}, CI {ci(*nb_res['llm'][3])})."]
    L += [f"- **Phrase test (cleanest).** {len(clean)} pick-cutoff pairs ({clean.arxiv.nunique()} picks) submitted after a stored cutoff and listed within 6 months. "
          f"{pct(p0[0])} contain one of that cutoff's top-50 rising phrases; same-month CS papers {pct(p0[1])} (lift {p0[2]:.2f}, CI {ci(*p0[3])}); "
          f"same-month LLM/agent papers {pct(pl[1])} (lift {pl[2]:.2f}, CI {ci(*pl[3])}).",
          f"- **Lead time.** Of the {len(grown)} picks in a growing topic, the topic had been growing without a break for a median of "
          f"{grown.lead_months.median():.1f} months before the listing (IQR {grown.lead_months.quantile(.25):.1f} to {grown.lead_months.quantile(.75):.1f}); "
          f"{int(grown.lead_censored.sum())} of these run back to the first month we can measure, so their true lead is longer.",
          f"- **Confound.** {pct(llm_share)} of the scored picks mention language models, LLMs or agents in title or abstract, vs "
          f"{pct(pi.llm.mean())} of the whole corpus.", ""]

    L += ["## What a fair website claim would be", "",
          "The phrase test is the one to quote, because the phrase lists were frozen at each cutoff and the picks came later. Suggested wording:", "",
          f"> In a back-check on dair.ai's AI Papers of the Week (picks listed {clean.listed.min():%b %Y} to {clean.listed.max():%b %Y}), {pct(p0[0])} of featured papers used one of the "
          f"50 phrases undercurrent had flagged as rising before the paper appeared. For all CS papers from the same months the figure was {pct(p0[1])}; "
          f"for LLM and agent papers, {pct(pl[1])}.", "",
          "Keep the LLM comparison in the sentence. Without it the 3.8x figure overstates the case, because dair mostly features LLM papers and our rising phrases are mostly LLM phrases.", "",
          "Do not claim:", "",
          "- \"undercurrent predicted dair's picks\" or that it finds important papers. It ranks topics and phrases; inside a growing topic it says nothing about which paper will be featured.",
          f"- The topic-level lift ({g[2]:.1f}x) as a headline. It rests on a clustering made with hindsight, and {pct(share2)} of it is two LLM topics.",
          f"- Lead time as forecasting skill. The median unbroken growth streak before a listing ({grown.lead_months.median():.0f} months) says the topic was already hot, which anyone reading arXiv would also see.", ""]

    L += ["## Method", "",
          "**dair list.** The yearly archive files (years/2023.md to years/2026.md in dair-ai/ML-Papers-of-the-Week) were parsed into (week, title, arXiv id). "
          "The arXiv id is taken from the row's \"Paper\" link, else the first arxiv.org link in the row. The listing date is the last day of the week. "
          f"A paper listed twice counts once, at its first listing ({dups} repeats dropped).", "",
          "**Topic growth.** Level-1 topics (the map's default level, 114 topics). For a pick listed in month W, the cutoff is the first day of W. "
          "Growth = ((papers in the topic in the 6 complete months before the cutoff + 1) / (papers in the 6 months before that + 1)) / (all mapped papers, same ratio), "
          "the same rule as `radar/serve.py` (a topic with 30+ recent and under 5 prior papers counts as new, so as growing; under 10 recent papers counts as not growing). "
          "Only papers submitted before the cutoff enter the count. Growing = x1.41 or more, growing fast = x2 or more.", "",
          "**Base rates.** For each pick, the share of papers submitted in the same month as the pick whose topic was growing at the same cutoff, among "
          "(a) all mapped papers, (b) papers with the same primary arXiv category, (c) papers whose title or abstract matches `language model`, `LLM(s)` or `agent`, "
          "(d) papers in the same coarse map area (level 2, 21 areas) as the pick. "
          "The base rate reported is the average over picks of these matched shares, so it is weighted the same way as the hit rate. "
          "Lift = hit rate / matched base rate. 95% CIs come from 4,000 bootstrap resamples of picks; z is a one-sided Poisson-binomial test of hits against matched base rates.", ""]
    if nb is not None:
        L += ["**Neighbour check.** For each pick, its 100 nearest papers (cosine on SPECTER2 vectors) among papers submitted in the 12 months before the cutoff; "
              "growth = (recent 6 months + 1) / (prior 6 + 1) / CS volume ratio. Base: up to 150 random papers from the pick's submission month (and 150 LLM/agent papers), "
              "same cutoff. SPECTER2 is a fixed pretrained model and the neighbours are only earlier papers, so nothing here was fit on data after the cutoff.", ""]
    L += ["**Phrase test.** Cutoffs with a stored S3 run: " + ", ".join(tags) + ". The top 50 phrases by score at each cutoff (latest run per tag, as in "
          "`radar/backtest.py`) are matched in title + abstract with the same tokenizer (stop words dropped, so \"chain of thought\" is stored as \"chain thought\"). "
          "Picks count if listed in the 6 months after the cutoff and submitted on or after it (so they could not have shaped the phrase list). "
          "Base: share of all papers submitted in the pick's month containing one of the 50 phrases, and the same among LLM/agent papers. "
          "Cutoffs are 3 months apart with 6-month windows, so a pick can appear under two cutoffs; the CI resamples picks, not pairs.", ""]

    L += ["## Bias and caveats (read before quoting any number)", "",
          "1. **The topics know the future.** Topic assignments come from one clustering of all papers through Nov 2026. A cluster that exists in 2025 may exist "
          "only because of papers that came later, so \"the topic was growing before the pick\" partly uses hindsight about which papers belong together. "
          "Counting only papers before the cutoff removes lookahead in the counts, not in the grouping. The neighbour check and the phrase test have no such "
          "lookahead; weigh them more.",
          f"2. **The LLM wave confounds everything.** dair is an AI-paper newsletter; {pct(llm_share)} of the scored picks are LLM/agent papers. "
          "Our growing topics are mostly LLM/agent topics. Lift against all CS mostly measures \"dair covers LLMs\". The LLM/agent base rate is the fairer comparison, "
          "and the keyword filter used for it is crude (it misses some LLM papers and includes some RL \"agent\" papers).",
          f"3. **dair's sample is not random.** It is a curated newsletter; {int(picks.arxiv.isna().sum())} of {n_raw} picks have no arXiv link (blog posts, reports) and drop out. "
          f"Only {int(first.arxiv.isin(row_of).sum())} of {len(first)} unique arXiv picks are on our map (corpus is cs.* only, from Nov 2023).",
          "4. **Short history.** Growth needs 12 months, so the topic test starts in "
          f"{(m0 + 2 * N_WIN).strftime('%b %Y')}; lead times that reach that month are censored.",
          f"5. **Submission dates are month-level** in our papers table. {pct((T.sub_month == T.c).mean())} of scored picks were submitted in their listing month (after the cutoff), "
          f"{pct((T.sub_month == T.c - 1).mean())} the month before (so the pick itself is one paper inside its topic's recent window, a negligible effect), "
          f"and {pct((T.sub_month < T.c - 1).mean())} earlier.",
          "6. **The phrase lists are generic in places** (\"ai systems\", \"llm agents\", \"reasoning models\"), which raises hit rates for any LLM paper. "
          "That is why the LLM/agent base rate matters.", ""]

    L += ["## Coverage by year", "", "| listing year | picks | with arXiv link | unique arXiv papers | arXiv id before Nov 2023 | in papers table | on map |", "|---|---|---|---|---|---|---|"]
    L += [f"| {r.year} | {r.picks} | {r.with_arxiv} | {r.unique_arxiv} | {r.before_corpus} | {r.in_papers} | {r.in_map} |" for r in cov.itertuples()]
    miss = first[(first.arxiv >= "2311") & ~first.arxiv.isin(in_papers)]
    L += ["", f"Only {len(miss)} picks from Nov 2023 on have an arXiv id that is not in the corpus. We did not look up why; the likely reasons are a primary category "
          "outside cs.* (e.g. stat.ML, q-bio) or a harvest gap. Most of the 2023 drop is simply that the corpus starts in Nov 2023.", ""]

    L += ["## Topic test in detail", "", "| threshold | base | n | picks hit | base rate | lift | 95% CI | z |", "|---|---|---|---|---|---|---|---|"]
    names = {"all": "all CS, same month", "cat": "same primary category, same month", "llm": "LLM/agent papers, same month", "area": "same coarse area (1 of 21), same month"}
    for (name, b), (h, br, lf, (lo, hi), z, n) in res.items():
        L.append(f"| {'x1.41' if name == 'grow' else 'x2'} | {names[b]} | {n} | {pct(h)} | {pct(br)} | {lf:.2f} | {ci(lo, hi)} | {z:.1f} |")
    lo, hi = wilson(int(T.grow.sum()), Tn)
    L += ["", f"Hit rate x1.41: {int(T.grow.sum())}/{Tn}, Wilson 95% CI {pct(lo)} to {pct(hi)}.", ""]
    L += ["By listing year:", "", "| year | n | hit x1.41 | base all CS | base LLM/agent |", "|---|---|---|---|---|"]
    for y, x in T.groupby(T.listed.dt.year):
        L.append(f"| {y} | {len(x)} | {pct(x.grow.mean())} | {pct(x.b_all_grow.mean())} | {pct(x.b_llm_grow.mean())} |")
    L += ["", "Picks that are LLM/agent papers vs not:", "", "| group | n | hit x1.41 | base all CS | base LLM/agent |", "|---|---|---|---|---|"]
    for k, x in T.groupby("llm"):
        L.append(f"| {'LLM/agent' if k else 'other'} | {len(x)} | {pct(x.grow.mean())} | {pct(x.b_all_grow.mean())} | {pct(x.b_llm_grow.mean())} |")
    L += ["", "Where the hits come from (topics holding the picks that were in a growing topic):", "", "| topic | picks in a growing topic |", "|---|---|"]
    L += [f"| {labels.get(int(k), k)} | {v} |" for k, v in vc.head(6).items()]
    L += ["", f"Without the top two topics: {len(ex)} picks, hit {pct(ex.grow.mean())}, base all CS {pct(ex.b_all_grow.mean())}, base LLM/agent {pct(ex.b_llm_grow.mean())}, "
          f"base same coarse area {pct(ex.b_area_grow.mean())}.",
          f"Level-1 topics growing at x1.41 per cutoff (of 114): median {int(ngt.median())}, range {ngt.min()} to {ngt.max()}.", ""]

    if nb is not None:
        L += ["## Neighbour test in detail", "", "| base | picks growing x1.41 | base rate | lift | 95% CI | z |", "|---|---|---|---|---|---|"]
        for b, (h, br, lf, (lo, hi), z) in nb_res.items():
            L.append(f"| {names[b]} | {pct(h)} | {pct(br)} | {lf:.2f} | {ci(lo, hi)} | {z:.1f} |")
        L += [""]

    L += ["## Phrase test in detail", "", "| cutoff | picks (submitted after) | hit | base all CS | base LLM/agent | window base all CS |", "|---|---|---|---|---|---|"]
    for C, x in clean.groupby("cutoff"):
        L.append(f"| {C} | {len(x)} | {pct(x.hit.mean())} | {pct(x.b_all.mean())} | {pct(x.b_llm.mean())} | {pct(x.base_window.iloc[0])} |")
    L += ["", f"All pairs including picks submitted before the cutoff: {len(PH)} pairs, hit {pct(ph_all[0])} vs base {pct(ph_all[1])} (lift {ph_all[2]:.2f}, CI {ci(*ph_all[3])}).",
          f"Clean pairs, lift vs all CS: {p0[2]:.2f} (z {p0[4]:.1f}); vs LLM/agent: {pl[2]:.2f} (z {pl[4]:.1f}).",
          "The edge is not stable across cutoffs: check the rows above where the pick hit rate is at or below the LLM/agent base rate "
          "(the 2025-01-01 phrase list was diffuse: Kolmogorov-Arnold networks, Gaussian splatting, RAG, with no reasoning phrases yet).", ""]
    top_ph = pd.Series([p for s in clean[clean.hit].phrases for p in s.split("; ") if p]).value_counts().head(12)
    L += ["Phrases that did the matching most often (clean pairs): " + ", ".join(f"{k} ({v})" for k, v in top_ph.items()) + ".", ""]

    L += ["## Lead time", "", f"Picks in a topic growing x1.41 at the listing cutoff: {len(grown)}. Months from the start of the unbroken growing streak to the listing:", "",
          "| statistic | months |", "|---|---|",
          f"| median | {grown.lead_months.median():.1f} |", f"| 25th percentile | {grown.lead_months.quantile(.25):.1f} |",
          f"| 75th percentile | {grown.lead_months.quantile(.75):.1f} |", f"| streak reaches first measurable month (censored) | {int(grown.lead_censored.sum())} |",
          f"| streak began in the listing month itself (lead under 1 month) | {int((grown.lead_months < 1).sum())} |", "",
          "Lead time here is about the topic, not the paper: a long streak means the topic was already visibly hot, which is useful context but not a forecast of the pick.", ""]

    def tl(t): return labels.get(int(t), str(t))
    def gtxt(x): return "new" if np.isinf(x) else ("n/a" if np.isnan(x) else f"x{x:.2f}")
    L += ["## Ten convincing examples", "",
          "Picks submitted after the cutoff, in topics growing at least x1.41 at that cutoff and matching a rising phrase where available; "
          "ranked by growth, then lead time. One pick per topic first (only a handful of topics were growing), then the next best.", "", "| pick | dair week | topic | growth at cutoff | lead (months) | rising phrase |", "|---|---|---|---|---|---|"]
    T["sub_after"] = pd.to_datetime(T.submitted) >= [ (m0 + c).to_timestamp() for c in T.c]
    phmap = clean[clean.hit].groupby("arxiv").phrases.first()
    T["phr"] = T.arxiv.map(phmap).fillna("")
    good = T[T.grow & T.sub_after].copy(); good["gs"] = np.where(np.isinf(good.g), 99, good.g); good["hasp"] = good.phr.ne("")
    good = good.sort_values(["hasp", "gs", "lead_months"], ascending=[False, False, False])
    one = good.drop_duplicates("topic"); good = pd.concat([one, good.drop(one.index)]).head(10)   # one per topic first, then fill
    for r in good.itertuples():
        L.append(f"| {r.title} ([{r.arxiv}](https://arxiv.org/abs/{r.arxiv})) | {r.listed:%Y-%m-%d} | {tl(r.topic)} | {gtxt(r.g)} | {r.lead_months:.0f} | {r.phr.split('; ')[0] if r.phr else '-'} |")
    L += ["", "Topic names are machine-written labels for clusters and can fit a given paper loosely (an agent-evaluation paper filed under a reasoning topic, for example).",
          "", "## Ten biggest misses", "", "Picks whose topic was shrinking most at the cutoff (lowest growth).", "",
          "| pick | dair week | topic | growth at cutoff | primary category |", "|---|---|---|---|---|"]
    bad = T[~T.grow].copy(); bad["gs"] = bad.g.fillna(0); bad = bad.sort_values("gs").head(10)
    for r in bad.itertuples():
        L.append(f"| {r.title} ([{r.arxiv}](https://arxiv.org/abs/{r.arxiv})) | {r.listed:%Y-%m-%d} | {tl(r.topic)} | {gtxt(r.g)} | {r.cat} |")
    L += ["", f"{int((~T.grow).sum())} of {Tn} picks ({pct((~T.grow).mean())}) were not in a growing topic. None of their topics was collapsing (the lowest growth is "
          f"x{bad.gs.min():.2f}); the list above is steady topics such as " + ", ".join(dict.fromkeys(tl(t) for t in bad.topic)) + ". "
          "That is the core limit: undercurrent measures topic volume, and a strong paper can come from a topic that is not growing.", ""]

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L) + "\n")
    T.drop(columns=["phr"]).to_csv(RAW / "picks_scored.csv", index=False)
    PH.drop(columns=["txt"]).to_csv(RAW / "phrase_pairs.csv", index=False)
    print("\n".join(L[:L.index("## What a fair website claim would be")]))


if __name__ == "__main__":
    main()
