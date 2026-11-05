"""Map v1: UMAP of SPECTER2 embeddings, HDBSCAN labels at three levels, datamapplot HTML.

Usage: python -m radar.map [--sample N] [--llm-labels] [--out report/map.html]

Uses every paper that has an embedding (or a random --sample of them).
Distances for any downstream calculation must use the 768-d vectors in the
`embeddings` table, never the 2-D coordinates written here (UMAP distorts).
The time slider is datamapplot's histogram on `submitted`.
"""
import argparse, json, pathlib, random, sys
import duckdb, numpy as np, pandas as pd
from .harvest import DB, ROOT, fetch_vecs

SCHEMA = """
create table if not exists topics (level int, cluster int, label text, n int, cx double, cy double, primary key(level, cluster));
create table if not exists topic_members (id text, level int, cluster int, x double, y double, primary key(id, level));
"""
# min_cluster_size per level as a fraction of the point count: coarse / medium / fine (tune after first run)
LEVELS = {2: 0.012, 1: 0.0015, 0: 0.0003}


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
    a = ap.parse_args()
    import umap, hdbscan, datamapplot
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    q = "select p.id, p.title, p.submitted, p.primary_cat, e.vec from papers p join embeddings e using(id)"
    if a.sample: q = f"select * from ({q}) using sample reservoir({a.sample} rows) repeatable (42)"
    df, V = fetch_vecs(con, q)
    print(f"{len(df)} papers, umap...", file=sys.stderr, flush=True)
    xy = umap.UMAP(n_neighbors=15, min_dist=0.05, metric="cosine", random_state=42, low_memory=True).fit_transform(V)
    del V
    texts = df.title.values
    layers = {}
    con.execute("delete from topics"); con.execute("delete from topic_members")
    for lvl, frac in LEVELS.items():
        mcs = max(15, int(frac * len(df)))
        cl = hdbscan.HDBSCAN(min_cluster_size=mcs, min_samples=10).fit_predict(xy)
        names = tfidf_labels(texts, cl)
        print(f"level {lvl} (min size {mcs}): {len(names)} clusters, {100 * (cl < 0).mean():.0f}% unassigned", file=sys.stderr, flush=True)
        if a.llm_labels and lvl >= 1:
            names = llm_names(texts, cl, names)
        layers[lvl] = np.array([names.get(int(c), "Unlabelled") for c in cl])
        con.executemany("insert into topics values (?,?,?,?,?,?)",
                        [(lvl, c, names[c], int((cl == c).sum()), float(xy[cl == c, 0].mean()), float(xy[cl == c, 1].mean())) for c in names])
        m = pd.DataFrame({"id": df.id, "level": lvl, "cluster": cl.astype(int), "x": xy[:, 0].astype(float), "y": xy[:, 1].astype(float)})
        con.register("m_df", m); con.execute("insert into topic_members select * from m_df"); con.unregister("m_df")
    hover = (df.title + " [" + df.primary_cat + ", " + pd.to_datetime(df.submitted).dt.strftime("%Y-%m") + "] arXiv:" + df.id).values
    sub = pd.to_datetime(df.submitted)
    plot = datamapplot.create_interactive_plot(
        xy, layers[2], layers[1], layers[0], hover_text=hover, title="field-radar: arXiv CS",
        sub_title=f"{len(df):,} papers, {sub.min():%Y-%m} to {sub.max():%Y-%m}",
        histogram_data=sub, histogram_settings={"histogram_title": "submitted"},
        enable_search=True, darkmode=True, inline_data=True)
    pathlib.Path(a.out).parent.mkdir(exist_ok=True)
    plot.save(a.out)
    print(f"done: {a.out} ({pathlib.Path(a.out).stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
