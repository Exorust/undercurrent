"""Map v1: UMAP of SPECTER2 embeddings, HDBSCAN labels at three levels, datamapplot HTML,
plus the neighbour graph and row index the workbench reads (arch §2.1, §3, §4).

Usage: python -m radar.map [--sample N] [--llm-labels] [--out report/map.html]
       python -m radar.map --check-knn [--seed 0] [--gate 95]   # kNN precision gate (F3); exit 1 below the gate
       python -m radar.map --selftest                          # points.bin round trip + self-drop on 2,000 points

Uses every paper that has an embedding (or a random --sample of them). Rows are the papers in
arXiv id order; `point_index`, `knn`, `knn_exact_sample` and `report/points.bin` all use that row.
The 15-NN graph is built once with pynndescent (same parameters UMAP uses internally) and passed
to UMAP via `precomputed_knn`, so the stored graph is the one UMAP used. Self is dropped by row,
leaving 14 neighbours. All table writes happen in one transaction after UMAP and HDBSCAN.
`map_meta` gets one new row per run (run_id, created, ...); links.py fills its later columns.
The precision check compares the bridge decision (level 1, threshold `map_meta.bridge_T`, default 4)
with approximate vs exact 768-d cosine neighbours on 1,000 random rows. The map run stores the
seed-0 result and only warns below the gate; `--check-knn` exits 1.
Distances for any downstream calculation must use the 768-d vectors in the `embeddings` table,
never the 2-D coordinates written here (UMAP distorts).
"""
import argparse, datetime, json, os, pathlib, random, struct, sys
import duckdb, numpy as np, pandas as pd
from .harvest import DB, ROOT, fetch_vecs

SCHEMA = """
create table if not exists topics (level int, cluster int, label text, n int, cx double, cy double, primary key(level, cluster));
create table if not exists topic_members (id text, level int, cluster int, x double, y double, primary key(id, level));
create table if not exists map_meta (run_id text primary key, created timestamp not null, run_unix bigint not null,
  n_points integer not null, month0 text not null, n_months integer not null, months json not null, categories json not null,
  umap_version text, pynndescent_version text, knn_short_rows integer,
  knn_recall14 double, knn_bridge_agree double, knn_seed integer,     -- agree is a percentage (97.3), recall a fraction
  bridge_T integer, onset_share double, links_run_id text);           -- written by links.py
"""
GRAPH = """
create or replace table point_index (row integer primary key, id text not null unique, month smallint not null,
  cat smallint not null, l0 smallint not null, l1 smallint not null, l2 smallint not null, x real not null, y real not null);
create or replace table knn (row integer not null, nbr integer not null, rank tinyint not null, dist real not null,
  id integer generated always as (row) virtual);   -- `id` = row, for queries written against the design's knn(id, nbr, rank)
create or replace table knn_exact_sample (row integer not null, nbr integer not null, rank tinyint not null, primary key (row, rank));
"""
# min_cluster_size per level as a fraction of the point count: coarse / medium / fine (tune after first run)
LEVELS = {2: 0.012, 1: 0.0015, 0: 0.0003}
K, MAGIC = 14, 0x54505246


def drop_self(idx, dist, k=K):
    """Drop each row's own index and -1 padding by value, keep the first k left. Missing slots become -1."""
    bad = (idx == np.arange(len(idx))[:, None]) | (idx < 0)
    order = np.argsort(bad, axis=1, kind="stable")[:, :k]
    nbr, d = np.take_along_axis(idx, order, 1).astype(np.int32), np.take_along_axis(dist, order, 1).astype(np.float32)
    nbr[np.take_along_axis(bad, order, 1)] = -1
    return nbr, d


_i = np.tile(np.arange(10, 25), (3, 1)); _i[0, 0] = 0; _i[1, 3] = 1; _i[2, 5] = -1   # self at 0, self at 3 (duplicate), a -1
_n, _ = drop_self(_i, np.zeros((3, 15)))
assert (_n >= 0).all() and _n.shape == (3, 14) and not (_n == np.arange(3)[:, None]).any() and list(_n[2]) == [10, 11, 12, 13, 14] + list(range(16, 25))


def encode_points(xy, month, labels, cat, month0, n_months, n_cats, run_unix):
    """points.bin bytes per arch §3: 32-byte header, then x/y interleaved f4, month u2, l0/l1/l2 i2, category u1."""
    n = len(month)
    head = struct.pack("<IIIIHHI8x", MAGIC, 1, n, month0, n_months, n_cats, run_unix)
    cols = [np.asarray(xy, "<f4").reshape(-1), np.asarray(month, "<u2")] + [np.asarray(l, "<i2") for l in labels] + [np.asarray(cat, "u1")]
    return head + b"".join(c.tobytes() for c in cols)


def decode_points(b):
    magic, ver, n, month0, n_months, n_cats, run_unix = struct.unpack_from("<IIIIHHI", b)
    assert magic == MAGIC and ver == 1 and len(b) == 32 + 17 * n
    f = lambda dt, off, cnt: np.frombuffer(b, dt, cnt, off)
    return dict(n=n, month0=month0, n_months=n_months, n_cats=n_cats, run_unix=run_unix, xy=f("<f4", 32, 2 * n).reshape(n, 2),
                month=f("<u2", 32 + 8 * n, n), labels=[f("<i2", 32 + (10 + 2 * k) * n, n) for k in range(3)], cat=f("u1", 32 + 16 * n, n))


def _roundtrip(n, seed=0):
    r = np.random.default_rng(seed)
    p = dict(xy=r.normal(size=(n, 2)).astype("f4"), month=r.integers(0, 37, n), labels=[r.integers(-1, 368, n) for _ in range(3)], cat=r.integers(0, 256, n))
    d = decode_points(encode_points(**p, month0=202311, n_months=37, n_cats=149, run_unix=1762450000))
    assert d["n"] == n and d["month0"] == 202311 and d["n_months"] == 37 and d["n_cats"] == 149 and d["run_unix"] == 1762450000
    assert (d["xy"] == p["xy"]).all() and (d["month"] == p["month"]).all() and (d["cat"] == p["cat"]).all()
    assert all((a == b).all() for a, b in zip(d["labels"], p["labels"]))


_roundtrip(5)


def bridge(own, nl, T):
    """Bridge pair (c1 < c2) when the top two neighbour clusters both have >= T of the neighbours, else None.
    Ties go to the paper's own cluster, then the lower id (arch §4.3). ponytail: links.py owns the production rule."""
    c, k = np.unique(nl[nl >= 0], return_counts=True)
    if len(c) < 2: return None
    top = sorted(zip(c.tolist(), k.tolist()), key=lambda t: (-t[1], t[0] != own, t[0]))[:2]
    return tuple(sorted((top[0][0], top[1][0]))) if top[1][1] >= T else None


assert bridge(1, np.array([1] * 7 + [2] * 7), 4) == (1, 2) and bridge(1, np.array([1] * 12 + [2] * 2), 4) is None
assert bridge(9, np.array([1] * 12 + [2] * 2), 4) is None and bridge(2, np.array([3] * 4 + [1] * 4 + [2] * 4 + [-1] * 2), 4) == (1, 2)


def exact_top(V, rows, k=K, chunk=50000):
    """Exact cosine top-k of V[rows] over all of V, self excluded, streamed in chunks (arch §4.2)."""
    Q = V[rows] / np.linalg.norm(V[rows], axis=1, keepdims=True)
    best_s, best_i = np.full((len(rows), k), -np.inf, np.float32), np.zeros((len(rows), k), np.int64)
    for s in range(0, len(V), chunk):
        C = V[s:s + chunk]; S = (Q @ (C / np.linalg.norm(C, axis=1, keepdims=True)).T).astype(np.float32)
        own = (rows >= s) & (rows < s + len(C)); S[own, rows[own] - s] = -np.inf
        cs, ci = np.concatenate([best_s, S], 1), np.concatenate([best_i, np.arange(s, s + len(C))[None].repeat(len(rows), 0)], 1)
        top = np.argpartition(-cs, k - 1, axis=1)[:, :k]
        best_s, best_i = np.take_along_axis(cs, top, 1), np.take_along_axis(ci, top, 1)
    o = np.argsort(-best_s, axis=1)
    return np.take_along_axis(best_i, o, 1).astype(np.int32)


def gate_numbers(approx, exact, rows, l1, T):
    """recall@14 (fraction) and bridge-decision agreement (percent) on the sampled rows."""
    recall = np.mean([len(set(a[a >= 0]) & set(e)) / K for a, e in zip(approx, exact)])
    agree = 100 * np.mean([bridge(l1[r], l1[a[a >= 0]], T) == bridge(l1[r], l1[e], T) for r, a, e in zip(rows, approx, exact)])
    return round(float(recall), 3), round(float(agree), 1)


def report_gate(n_rows, recall, agree, gate):
    print(f"knn check: {n_rows} papers, bridge decision agrees {agree}% (gate {gate:g}%), recall@14 {recall}", flush=True)
    if agree < gate: print(f"FAIL: bridge decision agrees {agree}% < gate {gate:g}%", flush=True)
    return agree >= gate


def bridge_T(con):
    r = con.execute("select bridge_T from map_meta where bridge_T is not null order by created desc limit 1").fetchall()
    return r[0][0] if r else 4


def build_knn(V, seed=42):
    from pynndescent import NNDescent
    n = len(V)
    nnd = NNDescent(V, n_neighbors=K + 1, metric="cosine", random_state=seed, n_jobs=1, low_memory=True, compressed=False,
                    n_trees=min(64, 5 + int(round(n ** 0.5 / 20))), n_iters=max(5, int(round(np.log2(n)))), max_candidates=60)
    return nnd.neighbor_graph  # (n, 15), self at column 0 by the pynndescent 0.7.0 contract


def selftest():
    _roundtrip(2000, seed=1)
    r = np.random.default_rng(0)
    V = (r.normal(size=(20, 32))[r.integers(0, 20, 2000)] + 0.3 * r.normal(size=(2000, 32))).astype(np.float32)
    nbr, _ = drop_self(*build_knn(V))
    assert not (nbr == np.arange(len(V))[:, None]).any() and (nbr >= 0).all(axis=1).mean() >= 0.999
    rows = r.choice(len(V), 200, replace=False); ex = exact_top(V, rows)
    assert not (ex == rows[:, None]).any()
    recall = np.mean([len(set(a) & set(e)) / K for a, e in zip(nbr[rows], ex)])
    assert recall > 0.8, recall
    print(f"selftest ok: points.bin round trip on 2000 rows; knn self-drop on 2000 points, recall@14 {recall:.3f}")


def check_knn(seed, gate):
    con = duckdb.connect(str(DB))
    run_id, n = con.execute("select run_id, n_points from map_meta order by created desc limit 1").fetchall()[0]
    _, V = fetch_vecs(con, "select p.row, e.vec from point_index p join embeddings e using(id) order by p.row")
    assert len(V) == n, f"point_index has {len(V)} embedded rows, map_meta says {n}; rerun radar.map"
    l1 = con.execute("select l1 from point_index order by row").fetchnumpy()["l1"].astype(np.int32)
    k = con.execute("select row, rank, nbr from knn").fetchnumpy()
    A = np.full((n, K), -1, np.int32); A[k["row"], k["rank"].astype(int) - 1] = k["nbr"]
    rows = np.random.default_rng(seed).choice(n, min(1000, n), replace=False)
    recall, agree = gate_numbers(A[rows], exact_top(V, rows), rows, l1, bridge_T(con))
    con.execute("update map_meta set knn_recall14 = ?, knn_bridge_agree = ?, knn_seed = ? where run_id = ?", [recall, agree, seed, run_id])
    con.close()
    sys.exit(0 if report_gate(len(rows), recall, agree, gate) else 1)


def tfidf_labels(texts, labels, k=4):
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=5, max_df=0.3, stop_words="english", sublinear_tf=True)
    X = vec.fit_transform(texts); vocab = vec.get_feature_names_out()
    out = {}
    for c in np.unique(labels):
        if c < 0: continue
        m = np.asarray(X[labels == c].mean(0)).ravel()
        out[int(c)] = ", ".join(vocab[i] for i in m.argsort()[::-1][:k])
    return out


def llm_names(texts, labels, tfidf, model="sonnet", per_call=25):
    """Ask the LLM for a 2–5 word name per cluster from its top terms and 5 sample titles. Batched."""
    from .llm import ask
    rng = random.Random(0); out = dict(tfidf); ids = sorted(tfidf)
    for i in range(0, len(ids), per_call):
        blocks = []
        for c in ids[i:i + per_call]:
            idx = np.where(labels == c)[0]; samp = rng.sample(list(idx), min(5, len(idx)))
            blocks.append(f"cluster {c}\nterms: {tfidf[c]}\ntitles:\n" + "\n".join(f"- {texts[j][:120]}" for j in samp))
        prompt = ("Name each cluster of CS papers in 2-5 words, specific not generic (no 'machine learning' alone). "
                  "Reply ONLY with a JSON object mapping cluster id (string) to name.\n\n" + "\n\n".join(blocks))
        try:
            txt = ask(prompt, model=model); txt = txt[txt.find("{"):txt.rfind("}") + 1]
            out.update({int(k): str(v) for k, v in json.loads(txt).items() if int(k) in tfidf})
        except Exception as e:  # ponytail: keep tf-idf names on any failure
            print(f"llm naming failed for a batch: {e}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None, help="random subset of embedded papers")
    ap.add_argument("--out", default=str(ROOT / "report" / "map.html"))
    ap.add_argument("--llm-labels", action="store_true", help="LLM-written names for the coarse and medium levels")
    ap.add_argument("--check-knn", action="store_true", help="kNN precision gate on the stored graph; exit 1 below --gate")
    ap.add_argument("--seed", type=int, default=0, help="sample seed for --check-knn")
    ap.add_argument("--gate", type=float, default=95.0, help="bridge agreement gate, percent")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest: return selftest()
    if a.check_knn: return check_knn(a.seed, a.gate)
    import umap, hdbscan, datamapplot, pynndescent
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    q = "select p.id, p.title, p.submitted, p.primary_cat, e.vec from papers p join embeddings e using(id)"
    if a.sample: q = f"select * from ({q}) using sample reservoir({a.sample} rows) repeatable (42)"
    df, V = fetch_vecs(con, f"select * from ({q}) order by id")  # row = position in arXiv id order (arch §3)
    n = len(df)
    print(f"{n} papers, knn...", file=sys.stderr, flush=True)
    idx, dist = build_knn(V)
    nbr, nd = drop_self(idx, dist)
    short = int((nbr < 0).any(axis=1).sum())
    print(f"knn: 15-NN graph, {K} neighbours per paper after dropping self, {short} short rows; umap...", file=sys.stderr, flush=True)
    xy = umap.UMAP(n_neighbors=K + 1, min_dist=0.05, metric="cosine", random_state=42, low_memory=True,
                   precomputed_knn=(idx.copy(), dist.copy())).fit_transform(V)  # copies: UMAP writes -1 into its arrays
    ex_rows = np.random.default_rng(0).choice(n, min(1000, n), replace=False)
    exact = exact_top(V, ex_rows)
    del V, idx, dist
    texts = df.title.values
    labels, names_by, layers = {}, {}, {}
    for lvl, frac in LEVELS.items():
        mcs = max(15, int(frac * n))
        # Coarse level uses leaf selection: the default (eom) merged 88% of 60k papers into one cluster.
        cl = hdbscan.HDBSCAN(min_cluster_size=mcs, min_samples=10, cluster_selection_method="leaf" if lvl == 2 else "eom").fit_predict(xy)
        noise = cl < 0
        if noise.any() and (~noise).any():
            # HDBSCAN leaves 25–40% of points unassigned; give each the majority label of its 15 nearest assigned points.
            from sklearn.neighbors import KNeighborsClassifier
            cl[noise] = KNeighborsClassifier(n_neighbors=15).fit(xy[~noise], cl[~noise]).predict(xy[noise])
        names = tfidf_labels(texts, cl)
        print(f"level {lvl} (min size {mcs}): {len(names)} clusters, {100 * noise.mean():.0f}% assigned by nearest neighbours", file=sys.stderr, flush=True)
        if a.llm_labels and lvl >= 1:
            names = llm_names(texts, cl, names)
        labels[lvl], names_by[lvl] = cl, names
        layers[lvl] = np.array([names.get(int(c), "Unlabelled") for c in cl])
    print(f"clusters per level 2/1/0: {len(names_by[2])}/{len(names_by[1])}/{len(names_by[0])}", file=sys.stderr, flush=True)
    recall, agree = gate_numbers(nbr[ex_rows], exact, ex_rows, labels[1], bridge_T(con))
    if not report_gate(len(ex_rows), recall, agree, 95.0):
        print("warning: kNN gate failed; see arch §4.2 for the rerun recipe", file=sys.stderr)

    # months and categories for point_index / points.bin
    m0, m1 = con.execute("select min(submitted), max(submitted) from papers").fetchall()[0]
    months = [f"{y}-{m:02d}" for y, m in ((m0.year + (m0.month - 1 + i) // 12, (m0.month - 1 + i) % 12 + 1)
                                          for i in range((m1.year - m0.year) * 12 + m1.month - m0.month + 1))]
    sub = pd.to_datetime(df.submitted)
    month = ((sub.dt.year - m0.year) * 12 + sub.dt.month - m0.month).to_numpy(np.int16)
    cats = sorted(df.primary_cat.dropna().unique()); assert len(cats) < 255
    cat = df.primary_cat.map({c: i for i, c in enumerate(cats)}).fillna(255).to_numpy(np.int16)
    now = datetime.datetime.now(datetime.timezone.utc); run_id, run_unix = now.strftime("%Y%m%dT%H%M%SZ"), int(now.timestamp())

    pi = pd.DataFrame({"row": np.arange(n, dtype=np.int32), "id": df.id, "month": month, "cat": cat,
                       "l0": labels[0].astype(np.int16), "l1": labels[1].astype(np.int16), "l2": labels[2].astype(np.int16),
                       "x": xy[:, 0].astype(np.float32), "y": xy[:, 1].astype(np.float32)})
    ok = nbr >= 0
    kn = pd.DataFrame({"row": np.repeat(np.arange(n, dtype=np.int32), K)[ok.ravel()], "nbr": nbr[ok],
                       "rank": np.tile(np.arange(1, K + 1, dtype=np.int8), n)[ok.ravel()], "dist": nd[ok]})
    ke = pd.DataFrame({"row": np.repeat(ex_rows.astype(np.int32), K), "nbr": exact.ravel(), "rank": np.tile(np.arange(1, K + 1, dtype=np.int8), len(ex_rows))})
    con.execute("begin")
    con.execute("delete from topics"); con.execute("delete from topic_members")
    for lvl, cl in labels.items():
        names = names_by[lvl]
        con.executemany("insert into topics values (?,?,?,?,?,?)",
                        [(lvl, c, names[c], int((cl == c).sum()), float(xy[cl == c, 0].mean()), float(xy[cl == c, 1].mean())) for c in names])
        m = pd.DataFrame({"id": df.id, "level": lvl, "cluster": cl.astype(int), "x": xy[:, 0].astype(float), "y": xy[:, 1].astype(float)})
        con.register("m_df", m); con.execute("insert into topic_members select * from m_df"); con.unregister("m_df")
    con.execute(GRAPH)
    for t, d in (("point_index", pi), ("knn", kn), ("knn_exact_sample", ke)):
        cols = ", ".join(d.columns)
        con.register("t_df", d); con.execute(f"insert into {t} ({cols}) select {cols} from t_df"); con.unregister("t_df")
    con.execute("insert into map_meta (run_id, created, run_unix, n_points, month0, n_months, months, categories, umap_version, "
                "pynndescent_version, knn_short_rows, knn_recall14, knn_bridge_agree, knn_seed) values (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                [run_id, now.replace(tzinfo=None), run_unix, n, months[0], len(months), json.dumps(months), json.dumps(cats),
                 umap.__version__, pynndescent.__version__, short, recall, agree])
    con.execute("commit")
    print(f"run id {run_id}: wrote point_index ({n}), knn ({len(kn)}), knn_exact_sample ({len(ke)}), map_meta", file=sys.stderr, flush=True)

    out_bin = ROOT / "report" / "points.bin"; out_bin.parent.mkdir(exist_ok=True)
    tmp = out_bin.with_suffix(".bin.tmp")
    tmp.write_bytes(encode_points(xy, month, [labels[0], labels[1], labels[2]], cat, m0.year * 100 + m0.month, len(months), len(cats), run_unix))
    os.replace(tmp, out_bin)
    (ROOT / "report" / "points.ids").unlink(missing_ok=True)  # dropped by the design (amendment 5); never serve a stale one
    print(f"wrote {out_bin} ({out_bin.stat().st_size / 1e6:.2f} MB)", file=sys.stderr, flush=True)

    hover = (df.title + " [" + df.primary_cat + ", " + sub.dt.strftime("%Y-%m") + "] arXiv:" + df.id).values
    plot = datamapplot.create_interactive_plot(
        xy, layers[0], layers[1], layers[2], hover_text=hover,  # datamapplot wants finest first
        title="undercurrent: arXiv CS",
        sub_title=f"{n:,} papers, {sub.min():%Y-%m} to {sub.max():%Y-%m}",
        histogram_data=sub, histogram_n_bins=sub.nunique(), histogram_settings={"histogram_title": "submitted"},
        enable_search=True, darkmode=True, inline_data=True)
    pathlib.Path(a.out).parent.mkdir(exist_ok=True)
    plot.save(a.out)
    print(f"done: {a.out} ({pathlib.Path(a.out).stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
