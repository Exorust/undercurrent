"""Map v1: UMAP of SPECTER2 embeddings, HDBSCAN labels at three levels, datamapplot HTML.

Usage: python -m radar.map [--sample N] [--out report/map.html]

Distances for any downstream calculation must use the 768-d vectors in the
`embeddings` table, never the 2-D coordinates written here (UMAP distorts).
Labels are top TF-IDF terms per cluster for now; LLM-written labels are a
later step. The time slider uses datamapplot's histogram_data on `submitted`.
"""
import argparse, pathlib, sys
import duckdb, numpy as np, pandas as pd
from .harvest import DB, ROOT

SCHEMA = """
create table if not exists topics (level int, cluster int, label text, n int, cx double, cy double, primary key(level, cluster));
create table if not exists topic_members (id text, level int, cluster int, x double, y double, primary key(id, level));
"""
LEVELS = {0: 2000, 1: 200, 2: 40}  # min_cluster_size per level: ~10 / ~100 / ~1000 clusters (tune after first run)


def tfidf_labels(texts, labels, k=4):
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=5, max_df=0.3, stop_words="english", sublinear_tf=True)
    X = vec.fit_transform(texts); vocab = vec.get_feature_names_out()
    out = {}
    for c in np.unique(labels):
        if c < 0: continue
        m = np.asarray(X[labels == c].mean(0)).ravel()
        out[c] = ", ".join(vocab[i] for i in m.argsort()[::-1][:k])
    return out


def llm_names(texts, labels, tfidf, model="sonnet", per_call=25):
    """Ask the LLM for a 2–5 word name per cluster from its top terms and 5 sample titles. Batched."""
    from .llm import ask
    import random, json
    rng = random.Random(0); out = dict(tfidf); ids = sorted(tfidf)
    for i in range(0, len(ids), per_call):
        chunk = ids[i:i + per_call]
        blocks = []
        for c in chunk:
            idx = np.where(labels == c)[0]; samp = rng.sample(list(idx), min(5, len(idx)))
            blocks.append(f"cluster {c}\nterms: {tfidf[c]}\ntitles:\n" + "\n".join(f"- {texts[j][:120]}" for j in samp))
        prompt = ("Name each cluster of CS papers in 2-5 words, specific not generic (no 'machine learning' alone). "
                  "Reply ONLY with a JSON object mapping cluster id (string) to name.\n\n" + "\n\n".join(blocks))
        try:
            txt = ask(prompt, model=model); txt = txt[txt.find("{"):txt.rfind("}") + 1]
            out.update({int(k): v for k, v in json.loads(txt).items() if int(k) in tfidf})
        except Exception as e:  # ponytail: keep tf-idf names on any failure
            print(f"llm naming failed for a batch: {e}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None, help="random subset of papers (speed / datamapplot size risk)")
    ap.add_argument("--out", default=str(ROOT / "report" / "map.html"))
    ap.add_argument("--llm-labels", action="store_true", help="LLM-written names for levels 1 and 2 (tf-idf terms otherwise)")
    a = ap.parse_args()
    import umap, hdbscan, datamapplot
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    q = "select p.id, p.title, p.submitted, p.primary_cat, e.vec from papers p join embeddings e using(id)"
    if a.sample: q += f" using sample {a.sample} rows (reservoir, 42)"
    df = con.execute(q).df()
    V = np.vstack(df.vec.values).astype(np.float32)
    print(f"{len(df)} papers, umap...", file=sys.stderr)
    xy = umap.UMAP(n_neighbors=15, min_dist=0.05, metric="cosine", random_state=42).fit_transform(V)
    label_layers, texts = [], (df.title).values
    con.execute("delete from topics"); con.execute("delete from topic_members")
    for lvl, mcs in LEVELS.items():
        cl = hdbscan.HDBSCAN(min_cluster_size=mcs, min_samples=10, cluster_selection_method="eom").fit_predict(xy)
        names = tfidf_labels(texts, cl)
        print(f"level {lvl}: {len(names)} clusters, {100 * (cl < 0).mean():.0f}% noise", file=sys.stderr)
        if a.llm_labels and lvl >= 1:
            names = llm_names(texts, cl, names)
        label_layers.append(np.array([names.get(c, "Unlabelled") for c in cl]))
        con.executemany("insert into topics values (?,?,?,?,?,?)",
                        [(lvl, int(c), names[c], int((cl == c).sum()), float(xy[cl == c, 0].mean()), float(xy[cl == c, 1].mean())) for c in names])
        con.executemany("insert into topic_members values (?,?,?,?,?)",
                        [(i, lvl, int(c), float(x), float(y)) for i, c, (x, y) in zip(df.id, cl, xy)])
    hover = (df.title + " [" + df.primary_cat + ", " + df.submitted.astype(str).str[:7] + "]").values
    plot = datamapplot.create_interactive_plot(
        xy, *label_layers[::-1], hover_text=hover, title="field-radar: arXiv CS",
        sub_title=f"{len(df)} papers, {df.submitted.min():%Y-%m} to {df.submitted.max():%Y-%m}",
        histogram_data=pd.to_datetime(df.submitted), histogram_settings={"histogram_title": "submitted"},
        on_click="window.open(`https://arxiv.org/abs/{hover_text}`)" if False else None,  # ponytail: ids not in hover; wire later
        enable_search=True, cluster_boundary_polygons=True, darkmode=True, inline_data=True)
    pathlib.Path(a.out).parent.mkdir(exist_ok=True)
    plot.save(a.out)
    print(f"done: {a.out}")


if __name__ == "__main__":
    main()
