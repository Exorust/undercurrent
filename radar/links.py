"""Stage `links`: bridge papers, topic links, topic time tables, phrase tables (arch §2.2, §4.2-4.5, §6.3-6.6).

Usage:
  python -m radar.links                 the stage: kNN gate, then all §2.2 tables in one transaction, links_run_id last
  python -m radar.links --check-knn     only the kNN precision gate (F3): prints recall@14 and bridge agreement,
                                        stores both in map_meta, exit 1 below --gate (percent, default 95); --seed N
                                        bootstraps the 1,000 sample rows for a second estimate
  python -m radar.links --calibrate     read-only: bridge threshold table for T = 2..6 on named pairs, onset share check
  python -m radar.links --selftest      tiny hand-made checks, prints ok lines
  --T N                                 bridge threshold for this run instead of the module constant T
  python -m radar.links --citelinks-only   only topic_citelinks + citelink_evidence from cite_refs (rerun as references arrive)
  --db PATH                             database instead of data/radar.duckdb (test copies)

Reads point_index, knn, knn_exact_sample, map_meta (from radar.map), topic_members, topics, papers, signals.
Months are smallint indices, 0 = first month in papers (2023-11). E = last complete month (arch §6.1).
"""
import argparse, re, sys
import duckdb, numpy as np, pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
from .harvest import DB
from .signals import KW, PAPER_COLS, candidate_vocab, papers_sql, windows

K_NBR = 14
# bridge threshold, neighbours out of 14 in each of the top two clusters. 2026-11-07 full run: T=4 gave 20.2% of papers as
# level-1 bridges (outside the 1-15% band), T=5 10.0% with top-10 min B = 53, T=6 3.8%; PM chose 5. Override with --T.
T = 5
ONSET_SHARE = 0.10
GATE = 95.0       # percent bridge-decision agreement, approximate vs exact kNN
PAIRS = {  # named calibration pairs at level 1, matched on labels (cluster ids change between runs)
    "pos robot~diffusion": (r"robot.*manipulat", r"diffusion|flow"),
    "pos agents~code": (r"\bagent", r"code gen"),
    "neg graphalg~medimg": (r"graph algorithm", r"medical imag"),
    "neg pde~recsys": (r"numerical|finite element|differential equation|\bpde", r"recommend"),
}


# ---------- pure logic (self-checked) ----------

def top_two(lab, K, own):
    """Top two neighbour clusters per row. lab: cluster per row (-1 noise); K: (m, 14) neighbour rows (-1 missing);
    own: each query row's own cluster. Ties go to the own cluster, then the lower id. Returns a, b, ka, kb (b=-1, kb=0 if none)."""
    NL = np.where(K >= 0, lab[np.maximum(K, 0)], -1)
    cnt = (NL[:, :, None] == NL[:, None, :]).sum(2)
    score = np.where(NL >= 0, cnt * 4096 + (NL == own[:, None]) * 2048 + (2047 - NL), -1)
    r = np.arange(len(K))
    ia = score.argmax(1); a = NL[r, ia]; ka = np.where(score[r, ia] >= 0, cnt[r, ia], 0)
    s2 = np.where(NL == a[:, None], -1, score)
    ib = s2.argmax(1); ok = s2[r, ib] >= 0
    return a, np.where(ok, NL[r, ib], -1), ka, np.where(ok, cnt[r, ib], 0)


def bridge_pairs(lab, K, t, own=None):
    """Bridge rule (arch §4.3): >= t of 14 neighbours in each of the top two clusters. Returns is_bridge, c1, c2, k1, k2 (c1 < c2)."""
    a, b, ka, kb = top_two(lab, K, lab if own is None else own)
    swap = b < a
    return ((ka >= t) & (kb >= t) & (b >= 0), np.where(swap, b, a), np.where(swap, a, b),
            np.where(swap, kb, ka), np.where(swap, ka, kb))


def link_growth(b_r, b_p, n1_r, n2_r, n1_p, n2_p):
    """Arch §6.3: (status, growth). Pseudo-count on the prior share keeps a zero prior finite."""
    share_now = b_r / max(n1_r + n2_r, 1)
    share_prior = (b_p + 1) / (n1_p + n2_p + 1)
    if b_r < 10: return "low", None
    if b_p < 3: return "new", None
    return "ratio", share_now / share_prior


def onset(counts, share=ONSET_SHARE):
    """First index where the cumulative count reaches share x total; None for an all-zero series."""
    c = np.cumsum(counts)
    return None if c[-1] <= 0 else int(np.argmax(c >= share * c[-1]))


def phrase_eff(shares):
    """Effective number of clusters, exp(entropy)."""
    s = np.asarray(shares, float); s = s[s > 0] / s.sum()
    return float(np.exp(-(s * np.log(s)).sum()))


def phrase_links(rows, min_n=10, min_share=0.2, max_eff=4.0):
    """Arch §6.5. rows: (phrase, cluster, n, share). Returns {phrase: [(c1, c2, n1, n2, s1, s2)]} for phrases with eff <= max_eff."""
    out = {}
    for ph, g in pd.DataFrame(rows, columns=["phrase", "cluster", "n", "share"]).groupby("phrase"):
        q = g[(g.n >= min_n) & (g.share >= min_share)].sort_values("cluster").values.tolist()
        if len(q) >= 2 and phrase_eff(g.share) <= max_eff:
            out[ph] = [(x[1], y[1], x[2], y[2], x[3], y[3]) for i, x in enumerate(q) for y in q[i + 1:]]
    return out


def fightin_words(k_s, n_s, k_b, n_b, pi, a0=1000.0):
    """Arch §6.6 log-odds with an informative Beta prior. Returns (delta, z, keep); keep = k_s>=3, k_s>=1% of n_s, z>=3."""
    k_s, k_b, pi = (np.asarray(x, float) for x in (k_s, k_b, pi))
    a, b = a0 * pi, a0 * (1 - pi)
    d = np.log((k_s + a) / (n_s - k_s + b)) - np.log((k_b + a) / (n_b - k_b + b))
    z = d / np.sqrt(1 / (k_s + a) + 1 / (n_s - k_s + b) + 1 / (k_b + a) + 1 / (n_b - k_b + b))
    return d, z, (k_s >= 3) & (k_s >= 0.01 * n_s) & (z >= 3)


def norm_text(s):
    """Phrase matching normal form (arch §6.5): lower, non-alphanumerics (hyphens included) to single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def match_phrases(texts, phrases):
    """(phrase index, text index) for each text holding the phrase word for word, last word optionally plural:
    the same as matching \\b<phrase>s?\\b on normal-form text. One pass over the words, phrases looked up by first word;
    a DuckDB contains() scan per phrase measured 75 ms per phrase on 20k papers (about 12 min at full size)."""
    first = {}
    for j, p in enumerate(phrases):
        w = p.split()
        if w: first.setdefault(w[0], []).append((j, w))
    out = []
    for d, t in enumerate(texts):
        ws, seen = t.split(), set()
        for i, w in enumerate(ws):
            for j, pw in first.get(w, ()):
                e = i + len(pw)
                if e <= len(ws) and ws[i + 1:e - 1] == pw[1:-1] and ws[e - 1] in (pw[-1], pw[-1] + "s"): seen.add(j)
        out += [(j, d) for j in sorted(seen)]
    return out


def selftest():
    # bridge rule: clusters 0, 1, 2; neighbour rows 0-4 in c0, 5-9 in c1, 10-14 in c2, query rows appended
    lab = np.array([0] * 5 + [1] * 5 + [2] * 5 + [0, 0, 0, 2, 1])
    c0, c1, c2 = list(range(5)), list(range(5, 10)), list(range(10, 15))
    K = np.array([c0 + c1[:4] + c2 + [-1] * 0,                 # own 0: 5 in c0, 4 in c1, 5 in c2 -> tie 5/5, pair (0, 2)
                  c0 + c0[:2] + c1 + c1[:2],                   # own 0: 7/7 split with c1 -> bridge (0, 1)
                  c0 + c0 + c0[:2] + c1[:2],                   # own 0: 12/2 -> not a bridge
                  c1 + c1 + c1[:2] + c2[:2],                   # own 2 (0 own, 12 in c1, 2 in c2) -> not a bridge
                  c0[:4] + c1[:4] + c2[:4] + [-1, -1]])        # own 1: 4/4/4 tie -> own c1 first, then lower id c0 -> (0, 1)
    br, a, b, ka, kb = bridge_pairs(lab, K, 4, own=lab[15:])
    assert br.tolist() == [True, True, False, False, True] and (a < b)[br].all(), br
    assert (a[0], b[0], ka[0], kb[0]) == (0, 2, 5, 5) and (a[1], b[1], ka[1], kb[1]) == (0, 1, 7, 7) and (a[4], b[4]) == (0, 1)
    assert not bridge_pairs(lab, K, 6, own=lab[15:])[0][[0, 4]].any()
    print("ok bridge rule on a hand-made 3-cluster graph (7/7 bridge, 12/2 no, 0-own/12-other no, ties to own then lower id)")
    assert link_growth(9, 0, 100, 100, 100, 100) == ("low", None) and link_growth(30, 2, 100, 100, 100, 100) == ("new", None)
    st, g = link_growth(20, 10, 100, 100, 100, 100); assert st == "ratio" and abs(g - 0.1 / (11 / 201)) < 1e-9
    st, g = link_growth(20, 3, 100, 100, 0, 0); assert st == "ratio" and np.isfinite(g)
    print("ok link growth with pseudo-counts (low/new/ratio, zero prior volume gives a finite ratio)")
    assert onset([0, 0, 5, 5, 90], 0.10) == 3 and onset([0, 0, 5, 5, 90], 0.05) == 2 and onset([0, 0, 0]) is None
    assert onset([100, 0, 0]) == 0
    print("ok onset month on a synthetic series (cumulative share reached, all-zero gives none)")
    assert abs(phrase_eff([0.5, 0.5]) - 2) < 1e-9 and abs(phrase_eff([1 / 30] * 30) - 30) < 1e-9
    spread = [10, 10] + [20 / 25] * 25   # two clusters at 25%, the rest over 25 clusters
    assert phrase_eff(spread) > 4
    rows = [("narrow", 1, 50, 0.5), ("narrow", 2, 40, 0.4), ("narrow", 3, 10, 0.1)] + \
           [("broad", 1, 25, 0.25), ("broad", 2, 25, 0.25)] + [("broad", 10 + i, 2, 0.02) for i in range(25)]
    pl = phrase_links(rows); assert list(pl) == ["narrow"] and pl["narrow"] == [(1, 2, 50, 40, 0.5, 0.4)]
    print("ok effective-cluster count (2 for halves, 30 for 30 equal, broad phrase excluded from links)")
    d, z, keep = fightin_words([10, 10, 2], 10, [10, 5000, 0], 10000, [20 / 10010, 5010 / 10010, 2 / 10010])
    assert d[0] > d[1] and keep[0] and not keep[2], (d, z, keep)
    print("ok distinctive-term score on a toy corpus (rare term beats common term, k_s = 2 filtered)")
    assert norm_text("World-Action  Models, (WAM)") == "world action models wam"
    assert match_phrases(["we train world action models", "world action modelling", "a world action model", "agent harness"],
                         ["world action model", "agent harness"]) == [(0, 0), (0, 2), (1, 3)]


# ---------- database ----------

def meta_get(con, key):
    cols = [r[0] for r in con.execute("describe map_meta").fetchall()]
    if "key" in cols:
        r = con.execute("select value from map_meta where key = ?", [key]).fetchall()
    else:
        r = con.execute(f"select {key} from map_meta order by created desc limit 1").fetchall() if key in cols else None
    return r[0][0] if r else None


def meta_set(con, **kv):
    """Update the latest run's row of map_meta (one typed row per run, as map.py writes it; flows F2/F3).
    ponytail: the key/value branch only serves arch §2.1's older layout, used by the synthetic test copy."""
    cols = [r[0] for r in con.execute("describe map_meta").fetchall()]
    for k, v in kv.items():
        if "key" in cols:
            con.execute("delete from map_meta where key = ?", [k]); con.execute("insert into map_meta values (?, ?)", [k, str(v)])
        else:
            con.execute(f"alter table map_meta add column if not exists {k} {'text' if isinstance(v, str) else 'integer' if isinstance(v, int) else 'double'}")
            con.execute(f"update map_meta set {k} = ? where run_id = (select run_id from map_meta order by created desc limit 1)", [v])


def load(con):
    """Arrays indexed by point_index.row: month index, clusters per level; the (n, 14) neighbour matrix; m0 and E."""
    m0 = con.execute("select date_trunc('month', min(submitted)) from papers").fetchall()[0][0]
    E = con.execute("select least(datediff('month', ?::date, date_trunc('month', max(submitted))),"
                    " datediff('month', ?::date, date_trunc('month', max(harvested_at))) - 1) from papers", [m0, m0]).fetchall()[0][0]
    p = con.execute("select p.row, datediff('month', ?::date, date_trunc('month', pa.submitted)) m from point_index p "
                    "join papers pa using(id) order by p.row", [m0]).fetchnumpy()
    n = int(p["row"].max()) + 1
    month = np.full(n, -1, np.int32); month[p["row"]] = p["m"]
    lab = {}
    for lv in (0, 1, 2):
        t = con.execute("select p.row, t.cluster from point_index p join topic_members t on t.id = p.id and t.level = ?", [lv]).fetchnumpy()
        lab[lv] = np.full(n, -1, np.int32); lab[lv][t["row"]] = t["cluster"]
    k = con.execute("select row, nbr, rank from knn").fetchnumpy()
    K = np.full((n, K_NBR), -1, np.int32)
    K[k["row"], k["rank"].astype(int) - int(k["rank"].min())] = k["nbr"]
    e = con.execute("select row, nbr, rank from knn_exact_sample").fetchnumpy()
    return month, lab, K, e, pd.Timestamp(m0).to_period("M"), int(E)


def knn_check(lab, K, e, gate, seed=None):
    """Arch §4.2 on the exact sample. Returns (n, agreement %, recall@14, ok)."""
    qs = np.unique(e["row"]); KE = np.full((len(qs), K_NBR), -1, np.int32)
    KE[np.searchsorted(qs, e["row"]), e["rank"].astype(int) - int(e["rank"].min())] = e["nbr"]
    if seed is not None:  # ponytail: the exact sample is fixed by map.py; a second seed bootstraps it, it does not draw new rows
        pick = np.random.default_rng(seed).choice(len(qs), len(qs)); qs, KE = qs[pick], KE[pick]
    KA = K[qs]
    rec = np.mean([len(set(x[x >= 0]) & set(y[y >= 0])) / K_NBR for x, y in zip(KA, KE)])
    a = bridge_pairs(lab[1], KA, T, own=lab[1][qs]); b = bridge_pairs(lab[1], KE, T, own=lab[1][qs])
    same = (a[0] == b[0]) & (~a[0] | ((a[1] == b[1]) & (a[2] == b[2])))
    agree = 100 * same.mean()
    ok = agree >= gate
    print(f"knn check: {len(qs)} papers, bridge decision agrees {agree:.1f}% (gate {gate:g}%), recall@14 {rec:.2f}")
    if not ok: print(f"FAIL: bridge decision agrees {agree:.1f}% < gate {gate:g}%")
    return float(agree), float(rec), ok


def write_tables(con, month, lab, K, E, m0):
    n = len(month)
    con.execute("begin")
    con.execute("""create or replace table bridges (row integer not null, level tinyint not null, c1 smallint not null,
      c2 smallint not null, k1 tinyint not null, k2 tinyint not null, share1 real not null, share2 real not null,
      month smallint not null, primary key (level, row), check (c1 < c2))""")
    for lv in (0, 1, 2):
        br, c1, c2, k1, k2 = bridge_pairs(lab[lv], K, T)
        keep = br & (month >= 0) & (month <= E)   # partial month excluded (arch §7)
        r = np.where(keep)[0]
        b = pd.DataFrame({"row": r, "level": lv, "c1": c1[r], "c2": c2[r], "k1": k1[r], "k2": k2[r],
                          "share1": k1[r] / K_NBR, "share2": k2[r] / K_NBR, "month": month[r]})
        con.execute("insert into bridges select * from b")
    con.execute("""create or replace table topic_links (level tinyint, c1 smallint, c2 smallint, month smallint,
      n_bridge integer not null, primary key (level, c1, c2, month), check (c1 < c2))""")
    con.execute("insert into topic_links select level, c1, c2, month, count(*) from bridges group by all")
    m0s = m0.to_timestamp().date()
    con.execute("""create or replace table topic_months (level tinyint, cluster smallint, month smallint, n integer not null,
      primary key (level, cluster, month))""")
    con.execute("""insert into topic_months select t.level, t.cluster, datediff('month', ?::date, date_trunc('month', p.submitted)) m,
      count(*) from topic_members t join papers p using(id) where t.cluster >= 0 group by all""", [m0s])
    tm = con.execute("select level, cluster, month, n from topic_months where month <= ?", [E]).df()
    rows = []
    for (lv, c), g in tm.groupby(["level", "cluster"]):
        s = np.zeros(E + 1); s[g.month.values] = g.n.values
        o = onset(s, ONSET_SHARE)
        if o is not None: rows.append((lv, c, o, ONSET_SHARE))
    ons = pd.DataFrame(rows, columns=["level", "cluster", "onset_month", "onset_share"])
    con.execute("""create or replace table topic_onset (level tinyint, cluster smallint, onset_month smallint not null,
      onset_share real not null, primary key (level, cluster))""")
    con.execute("insert into topic_onset select * from ons")
    # ponytail: immediate parent only (0 -> 1, 1 -> 2); F5.2 counts one row per fine cluster. Denominator includes noise.
    con.execute("""create or replace table topic_parent (level tinyint, cluster smallint, parent_level tinyint, parent smallint not null,
      share real not null, primary key (level, cluster, parent_level))""")
    con.execute("""insert into topic_parent
      with m as (select a.level lv, a.cluster c, b.level plv, b.cluster p, count(*) k from topic_members a
                 join topic_members b on b.id = a.id and b.level = a.level + 1 where a.cluster >= 0 group by all)
      select lv, c, plv, arg_max(p, k * 100000 - p) filter (where p >= 0), max(k) filter (where p >= 0) / sum(k)
      from m group by all having max(k) filter (where p >= 0) is not null""")
    con.execute("""create or replace view paper_topics as
      select k.row, i.id, l.level, case l.level when 0 then t0 when 1 then t1 else t2 end as cluster, count(*)::double / 14 as nbr_share
      from knn k join (select p.row, max(t.cluster) filter (where t.level = 0) t0, max(t.cluster) filter (where t.level = 1) t1,
                              max(t.cluster) filter (where t.level = 2) t2 from point_index p join topic_members t using(id) group by p.row) c
           on c.row = k.nbr
      join point_index i on i.row = k.row cross join (values (0), (1), (2)) l(level) group by all""")
    print(f"T = {T}", file=sys.stderr)
    for tb in ("bridges", "topic_links", "topic_months", "topic_onset", "topic_parent"):
        print(f"{tb}: {con.execute(f'select count(*) from {tb}').fetchall()[0][0]} rows", file=sys.stderr)
    print("paper_topics: " + ", ".join(f"level {lv} {c}" for lv, c in con.execute(
        "select level, count(*) from paper_topics group by 1 order by 1").fetchall()), file=sys.stderr)
    if ONSET_SHARE != 0.10: print(f"onset share adjusted to {ONSET_SHARE * 100:g}%", file=sys.stderr)


def write_phrases(con):
    """terms + paper_terms from the shared vocabulary; phrase_papers + phrase_topics for the latest live S3 phrases."""
    df = con.execute(papers_sql("id, " + PAPER_COLS)).df()
    vocab = candidate_vocab(df, windows(con, 6)[0])
    emb = df.merge(con.execute("select id, row from point_index").df(), on="id")
    X = CountVectorizer(vocabulary=vocab, **KW).transform(emb.txt).tocsc()   # binary: one entry per (paper, term)
    X.sort_indices()
    col = np.repeat(np.arange(len(vocab), dtype=np.int32), np.diff(X.indptr))
    pt = pd.DataFrame({"row": emb.row.values[X.indices].astype(np.int32), "term_id": col}).sort_values(["term_id", "row"])
    tdf = pd.DataFrame({"term_id": np.arange(len(vocab)), "term": vocab, "n_all": np.diff(X.indptr)})
    del df, X
    con.execute("create or replace table terms (term_id integer primary key, term text not null unique, n_all integer not null)")
    con.execute("insert into terms select * from tdf")
    con.execute("create or replace table paper_terms (row integer not null, term_id integer not null)")
    con.execute("insert into paper_terms select * from pt")
    ph = con.execute("""select distinct key from signals where signal = 'S3' and run_tag = 'live'
                        and run_date = (select max(run_date) from signals where run_tag = 'live')""").df()
    ph["p"] = ph.key.map(norm_text)
    con.execute("create or replace table phrase_papers (phrase text not null, row integer not null)")
    hits = match_phrases(emb.txt.fillna("").map(norm_text).values, list(ph.p))
    pp = pd.DataFrame({"phrase": ph.key.values[[j for j, _ in hits]], "row": emb.row.values[[d for _, d in hits]]})
    con.execute("insert into phrase_papers select * from pp")
    con.execute("""create or replace table phrase_topics (phrase text, level tinyint, cluster smallint, n integer not null,
      share real not null, primary key (phrase, level, cluster))""")
    con.execute("""insert into phrase_topics select phrase, level, cluster, n, n / sum(n) over (partition by phrase, level) from
      (select pp.phrase, t.level, t.cluster, count(*) n from phrase_papers pp join point_index p using(row)
       join topic_members t on t.id = p.id and t.cluster >= 0 group by all)""")
    n_none = con.execute("select count(*) from ph where key not in (select phrase from phrase_papers)").fetchall()[0][0]
    print(f"terms: {len(vocab)} rows; paper_terms: {len(pt)} rows; phrase_papers: "
          f"{con.execute('select count(*) from phrase_papers').fetchall()[0][0]} rows for {len(ph) - n_none} of {len(ph)} S3 phrases "
          f"({n_none} never occur word for word); phrase_topics: {con.execute('select count(*) from phrase_topics').fetchall()[0][0]} rows", file=sys.stderr)
    pl = phrase_links(con.execute("select phrase, cluster, n, share from phrase_topics where level = 1").fetchall())
    print(f"phrase_links (level 1, eff <= 4): {sum(len(v) for v in pl.values())} pairs from {len(pl)} phrases", file=sys.stderr)
    for k in list(pl)[:5]: print(f"  {k}: " + "; ".join(f"{a}~{b} n {x}/{y} share {s:.2f}/{u:.2f}" for a, b, x, y, s, u in pl[k]), file=sys.stderr)


def citelinks(con, own_tx=True):
    """Citation links between topics from cite_refs (citing_id, cited_id), topics per level from point_index.l0/l1/l2.
    topic_citelinks: per unordered pair (c1 < c2) and CITING paper's month, n_cites = edges with ends in the two topics,
    n_1to2 = citing in c1 -> cited in c2, n_2to1 the reverse. All months kept (windows select; the partial month is never
    in a window). citelink_evidence: per pair, the top 5 papers cited across the pair (all months), n = cross-pair citations.
    Edges are deduplicated, self-citations and noise (-1) ends dropped. own_tx=False when the caller holds the transaction."""
    import time
    t0 = time.time()
    if not con.execute("select count(*) from information_schema.tables where table_name = 'cite_refs'").fetchone()[0]:
        print("citelinks: no cite_refs table, skipped", file=sys.stderr); return False
    if own_tx: con.execute("begin")
    con.execute("""create or replace temp table _cite_e as
      select l.level, a.month, b.row cited_row,
             case l.level when 0 then a.l0 when 1 then a.l1 else a.l2 end ca,
             case l.level when 0 then b.l0 when 1 then b.l1 else b.l2 end cb
      from (select distinct citing_id, cited_id from cite_refs where citing_id <> cited_id) r
      join point_index a on a.id = r.citing_id join point_index b on b.id = r.cited_id
      cross join (values (0), (1), (2)) l(level)""")
    con.execute("delete from _cite_e where ca < 0 or cb < 0 or ca = cb")
    con.execute("""create or replace table topic_citelinks (level tinyint, c1 smallint, c2 smallint, month smallint,
      n_cites integer not null, n_1to2 integer not null, n_2to1 integer not null, primary key (level, c1, c2, month), check (c1 < c2))""")
    con.execute("""insert into topic_citelinks select level, least(ca, cb), greatest(ca, cb), month, count(*),
      count(*) filter (where ca < cb), count(*) filter (where ca > cb) from _cite_e group by all""")
    con.execute("""create or replace table citelink_evidence (level tinyint, c1 smallint, c2 smallint, cited_row integer,
      n integer not null, primary key (level, c1, c2, cited_row), check (c1 < c2))""")
    con.execute("""insert into citelink_evidence select level, c1, c2, cited_row, n from
      (select level, least(ca, cb) c1, greatest(ca, cb) c2, cited_row, count(*) n from _cite_e group by all)
      qualify row_number() over (partition by level, c1, c2 order by n desc, cited_row) <= 5""")
    n_e = con.execute("select count(*) from cite_refs").fetchone()[0]
    con.execute("drop table _cite_e")
    if own_tx: con.execute("commit")
    for tb in ("topic_citelinks", "citelink_evidence"):
        print(f"{tb}: {con.execute(f'select count(*) from {tb}').fetchone()[0]} rows", file=sys.stderr)
    print("topic_citelinks pairs: " + ", ".join(f"level {lv} {n} pairs / {c} cites" for lv, n, c in con.execute(
        "select level, count(distinct (c1, c2)), sum(n_cites) from topic_citelinks group by 1 order by 1").fetchall())
          + f"; from {n_e} cite_refs rows in {time.time() - t0:.1f}s", file=sys.stderr)
    return True


def citelinks_selftest():
    """6 papers in 3 topics at level 1; level 2 puts paper 'f' in noise."""
    con = duckdb.connect()
    con.execute("create table point_index (row integer, id text, month smallint, l0 smallint, l1 smallint, l2 smallint)")
    con.executemany("insert into point_index values (?,?,?,?,?,?)", [
        (0, "a", 3, 0, 0, 0), (1, "b", 3, 0, 0, 0), (2, "c", 4, 1, 1, 1), (3, "d", 5, 1, 1, 1), (4, "e", 5, 2, 2, 2), (5, "f", 6, 2, 2, -1)])
    con.execute("create table cite_refs (citing_id text, cited_id text)")
    con.executemany("insert into cite_refs values (?,?)", [
        ("c", "a"), ("d", "a"), ("d", "b"), ("a", "c"),   # 0~1: c->a (m4), d->a, d->b (m5), a->c (m3, 0 to 1)
        ("d", "a"),                                       # duplicate edge, counted once
        ("b", "a"), ("a", "a"),                           # same topic and self-cite, dropped
        ("e", "a"), ("f", "a"),                           # 0~2: e->a (m5), f->a (m6; noise at level 2)
        ("x", "a")])                                      # citing paper not on the map, dropped
    citelinks(con)
    got = {r[:4]: r[4:] for r in con.execute("select level, c1, c2, month, n_cites, n_1to2, n_2to1 from topic_citelinks").fetchall()}
    assert got[(1, 0, 1, 3)] == (1, 1, 0) and got[(1, 0, 1, 4)] == (1, 0, 1) and got[(1, 0, 1, 5)] == (2, 0, 2), got
    assert got[(1, 0, 2, 5)] == (1, 0, 1) and got[(1, 0, 2, 6)] == (1, 0, 1)
    assert (2, 0, 2, 6) not in got and got[(2, 0, 2, 5)] == (1, 0, 1)
    assert sum(v[0] for k, v in got.items() if k[0] == 1) == 6 and not any(k[1] == 1 and k[2] == 2 for k in got)
    ev = con.execute("select cited_row, n from citelink_evidence where level = 1 and c1 = 0 and c2 = 1 order by n desc, cited_row").fetchall()
    assert ev == [(0, 2), (1, 1), (2, 1)], ev
    con.execute("drop table cite_refs"); assert citelinks(con) is False
    print("ok citation links on 6 papers / 3 topics (pair counts, direction columns, citing-month buckets, dedupe, "
          "self/same-topic/noise/off-map dropped, evidence top paper, missing cite_refs skipped)")


def calibrate(con, month, lab, K, E, m0):
    labels = dict(con.execute("select cluster, label from topics where level = 1").fetchall())
    W = (month >= E - 11) & (month <= E)
    l1 = lab[1]
    nW = np.bincount(l1[W & (l1 >= 0)], minlength=max(labels, default=0) + 1)
    for lv in (0, 1, 2):
        NL = np.where(K >= 0, lab[lv][np.maximum(K, 0)], -2)
        print(f"level {lv}: mean neighbour purity {(NL == lab[lv][:, None]).sum(1)[lab[lv] >= 0].mean() / K_NBR:.2f}")
    a, b, ka, kb = top_two(l1, K, l1)
    c1, c2 = np.minimum(a, b), np.maximum(a, b)
    match = {k: [[c for c, s in labels.items() if re.search(p, s or "", re.I)] for p in pats] for k, pats in PAIRS.items()}
    res, chosen = {}, None
    print(f"\nlevel 1, window {m0 + E - 11}..{m0 + E}; rank by strength = B/sqrt(n1*n2) among linked pairs")
    print(f"{'T':>3} {'bridges':>8} {'share':>6}  " + "  ".join(f"{k:>22}" for k in PAIRS) + "  top10 min B")
    for t in (2, 3, 4, 5, 6):
        sel = (ka >= t) & (kb >= t) & (b >= 0) & W
        key, B = np.unique(c1[sel].astype(np.int64) * 1000 + c2[sel], return_counts=True)
        x, y = key // 1000, key % 1000
        st = B / np.sqrt(np.maximum(nW[x] * nW[y], 1))
        order = np.argsort(-st); rank = {(int(x[i]), int(y[i])): r + 1 for r, i in enumerate(order)}
        cells, ok = [], B[order[:10]].min() >= 5 if len(B) else False
        for k, (A, Bc) in match.items():
            rk = min((rank.get((min(p, q), max(p, q)), 10 ** 6) for p in A for q in Bc if p != q), default=None)
            ok &= rk is not None and (rk <= 40 if k.startswith("pos") else rk > 200)
            cells.append("label not found" if rk is None else "unlinked" if rk == 10 ** 6 else f"rank {rk}")
        mark = ""
        if ok and chosen is None: chosen, mark = t, "*"
        res[t] = (x, y, B, st, order)
        print(f"{str(t) + mark:>3} {sel.sum():>8} {sel.sum() / max(W.sum(), 1):>6.3f}  " + "  ".join(f"{c:>22}" for c in cells)
              + f"  {B[order[:10]].min() if len(B) else 0}")
    print(f"\nchosen T = {chosen if chosen else 'none (no T meets: positives in top 40, negatives outside top 200, top-10 B >= 5)'}; "
          f"module constant T = {T}")
    x, y, B, st, order = res[chosen or T]
    print(f"top 20 edges at T = {chosen or T}:")
    for i in order[:20]:
        print(f"  {st[i]:.4f}  B={B[i]:<4d} {labels.get(int(x[i]))} ({nW[x[i]]}) ~ {labels.get(int(y[i]))} ({nW[y[i]]})")
    # onset share check (arch §6.4): fine clusters whose label names the two known arrivals
    fine = con.execute("select cluster, label from topics where level = 0 and regexp_matches(label, 'world.?action|agent harness', 'i')").fetchall()
    print("\nonset check (fine clusters labelled world action / agent harness):" + ("" if fine else " none found at level 0"))
    for c, s in fine:
        ser = np.bincount(month[(lab[0] == c) & (month >= 0) & (month <= E)], minlength=E + 1)
        print(f"  {c} {s}: " + ", ".join(f"{sh:.2f} -> {m0 + onset(ser, sh)}" for sh in (0.05, 0.10, 0.20) if onset(ser, sh) is not None))


def main():
    global T
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true"); ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--check-knn", action="store_true"); ap.add_argument("--gate", type=float, default=GATE)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--T", type=int, default=None, help=f"bridge threshold override (default {T})")
    ap.add_argument("--citelinks-only", action="store_true", help="only rebuild topic_citelinks + citelink_evidence from cite_refs")
    ap.add_argument("--db", default=None, help="database path (default data/radar.duckdb)")
    a = ap.parse_args()
    if a.T is not None: T = a.T
    if a.selftest: selftest(); citelinks_selftest(); return
    con = duckdb.connect(str(a.db or DB), read_only=a.calibrate)
    if a.citelinks_only: citelinks(con); return
    month, lab, K, e, m0, E = load(con)
    print(f"{len(month)} points, months {m0}..{m0 + E} (last complete), run_id {meta_get(con, 'run_id')}", file=sys.stderr)
    if a.calibrate: calibrate(con, month, lab, K, E, m0); return
    agree, rec, ok = knn_check(lab, K, e, a.gate, a.seed)
    meta_set(con, knn_recall14=rec, knn_bridge_agree=agree)
    if not ok or a.check_knn: sys.exit(0 if ok else 1)
    write_tables(con, month, lab, K, E, m0)
    write_phrases(con)
    run_id = meta_get(con, "run_id")
    meta_set(con, bridge_T=int(T), onset_share=ONSET_SHARE, links_run_id=str(run_id))
    citelinks(con, own_tx=False)
    con.execute("commit")
    print(f"links_run_id = {run_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
