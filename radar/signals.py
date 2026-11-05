"""Signals S2 (field convergence) and S3 (term emergence) over the papers table.

Usage: python -m radar.signals [--months 6] [--top 30] [--cutoff YYYY-MM-DD]

Growth is measured as the count in the latest `months` window divided by the
count in the window before it. Results are written to the `signals` table and
the top lists are printed. S1 (map-region growth) lives in merge.py because it
needs clusters; S4 (HN check) is run for flagged terms only, see hn.py.
"""
import argparse, collections, sys
import duckdb, numpy as np, pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import CountVectorizer
from .harvest import DB

SCHEMA = """
create table if not exists signals (
  run_date date, signal text, key text, recent int, prior int, growth double,
  first_seen date, breadth int, accel double, score double, run_tag text default 'live');
"""
CUTOFF = None  # set by --cutoff for backtests: ignore papers submitted on/after this date


def papers_sql(cols):
    return f"select {cols} from papers" + (f" where submitted < '{CUTOFF}'" if CUTOFF else "")


def windows(con, months):
    last = con.execute(papers_sql("max(submitted)")).fetchone()[0]
    # ponytail: the latest month is partial in a live run, drop it so growth isn't biased low
    end = pd.Timestamp(last).to_period("M") - (0 if CUTOFF else 1)
    recent = [str(end - i) for i in range(months)]
    prior = [str(end - months - i) for i in range(months)]
    return recent, prior


def convergence(con, months, min_recent=15):
    """S2: category pairs whose co-occurrence is growing."""
    df = con.execute(papers_sql("strftime(submitted, '%Y-%m') ym, categories")).df()
    recent, prior = windows(con, months)
    cnt = {"recent": collections.Counter(), "prior": collections.Counter()}
    for ym, cats in zip(df.ym, df.categories):
        w = "recent" if ym in recent else "prior" if ym in prior else None
        if not w: continue
        cs = sorted(set(cats))
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                cnt[w][(cs[i], cs[j])] += 1
    rows = []
    for pair, r in cnt["recent"].items():
        p = cnt["prior"].get(pair, 0)
        if r < min_recent: continue
        g = (r + 1) / (p + 1)
        rows.append(("S2", "+".join(pair), r, p, g, None, None, None, g * np.log1p(r)))
    return rows


def terms(con, months, min_recent=30):
    """S3: 2–4 word phrases from titles+abstracts that are young, growing, and used by many author groups.

    Stop words are removed before phrases are formed, so "chain of thought" is counted as "chain thought".
    """
    df = con.execute(papers_sql("strftime(submitted, '%Y-%m') ym, title || '. ' || abstract txt, authors[1] a1")).df()
    recent, prior = windows(con, months)
    kw = dict(ngram_range=(2, 4), stop_words="english", binary=True, lowercase=True,
              token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b")
    # Memory: fitting 2-4-grams on all docs at once builds tens of millions of strings.
    # Find candidates in 10k-doc chunks of the recent window (seen twice in a chunk), then count only those everywhere.
    ym = df.ym.values
    rec_mask = np.isin(ym, recent)
    rec_txt = df.txt.values[rec_mask]
    cand = set()
    for i in range(0, len(rec_txt), 10000):
        cand.update(CountVectorizer(min_df=2, **kw).fit(rec_txt[i:i + 10000]).vocabulary_)
    print(f"S3: {len(cand)} candidate phrases from {len(rec_txt)} recent papers", file=sys.stderr)
    vec = CountVectorizer(vocabulary=sorted(cand), **kw)
    X = vec.transform(df.txt)
    rc = np.asarray(X[rec_mask].sum(0)).ravel()
    cols = np.where(rc >= min_recent)[0]
    X = X[:, cols].tocsc(); vocab = np.array(vec.get_feature_names_out())[cols]

    months_sorted = sorted(set(ym)); mi = {m: i for i, m in enumerate(months_sorted)}
    M = sparse.csr_matrix((np.ones(len(ym)), (np.arange(len(ym)), [mi[m] for m in ym])), shape=(len(ym), len(months_sorted)))
    per_month = np.asarray((X.T @ M).todense())  # phrases x months
    rec_idx = [mi[m] for m in recent if m in mi]; pri_idx = [mi[m] for m in prior if m in mi]
    rcount = per_month[:, rec_idx].sum(1); pcount = per_month[:, pri_idx].sum(1)
    first = np.array([months_sorted[j] for j in (per_month > 0).argmax(1)])

    def slope(idx):  # least-squares slope of the monthly count over a window
        if len(idx) < 2: return np.zeros(per_month.shape[0])
        x = np.arange(len(idx)) - (len(idx) - 1) / 2; y = per_month[:, sorted(idx)]
        return (x * (y - y.mean(1, keepdims=True))).sum(1) / (x ** 2).sum()
    accel = slope(rec_idx) - slope(pri_idx)
    # ponytail: breadth = distinct first-author surnames, cheap proxy for independent groups; affiliations if arXiv ever ships them
    surname = df.a1.fillna("").map(lambda s: s.split()[-1].lower() if s else "").values
    rows = []
    for j in np.where((rcount + 1) / (pcount + 1) >= 1.5)[0]:
        docs = X[:, j].nonzero()[0]
        breadth = len(set(surname[docs]) - {""})
        if breadth < 5: continue
        g = (rcount[j] + 1) / (pcount[j] + 1)
        age_bonus = 1.5 if first[j] >= prior[-1] else 1.0  # first seen inside the two comparison windows
        score = g * np.log1p(rcount[j]) * age_bonus * (1.0 + max(accel[j], 0) / 10)
        rows.append(("S3", vocab[j], int(rcount[j]), int(pcount[j]), float(g), f"{first[j]}-01", breadth, float(accel[j]), float(score)))
    # A 4-gram and its sub-phrases all score alike; keep the longest phrase of each nested family.
    rows.sort(key=lambda r: -r[-1])
    kept = []
    for r in rows:
        if any((r[1] in k[1] or k[1] in r[1]) and abs(r[2] - k[2]) <= 0.3 * max(r[2], k[2]) for k in kept[:400]):
            i = next(i for i, k in enumerate(kept[:400]) if (r[1] in k[1] or k[1] in r[1]) and abs(r[2] - k[2]) <= 0.3 * max(r[2], k[2]))
            if len(r[1]) > len(kept[i][1]): kept[i] = r[:8] + (kept[i][8],)
            continue
        kept.append(r)
    return kept


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--months", type=int, default=6); ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--cutoff", default=None, help="backtest: only use papers submitted before this date (YYYY-MM-DD)")
    a = ap.parse_args()
    global CUTOFF; CUTOFF = a.cutoff
    run_tag = a.cutoff or "live"
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    out = []
    for name, fn in (("S2", convergence), ("S3", terms)):
        rows = fn(con, a.months)
        rows.sort(key=lambda r: -r[-1])
        out += rows
        print(f"\n== {name} [{run_tag}]: top {a.top} (recent/prior, growth, first_seen, breadth)")
        for r in rows[:a.top]:
            print(f"{r[1]:45s} {r[2]:5d}/{r[3]:<5d} x{r[4]:<6.1f} {r[5] or '':10s} {r[6] or ''}")
    con.execute("delete from signals where run_date = current_date and run_tag = ?", [run_tag])
    con.executemany("insert into signals values (current_date,?,?,?,?,?,?,?,?,?,?)", [r + (run_tag,) for r in out])
    print(f"\n{len(out)} signal rows written", file=sys.stderr)


if __name__ == "__main__":
    main()
