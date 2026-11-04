"""Signals S2 (field convergence) and S3 (term emergence) over the papers table.

Usage: python -m radar.signals [--months 6] [--top 30]

Growth is measured as the count in the latest `months` window divided by the
count in the window before it. Results are written to the `signals` table and
the top lists are printed. S1 (map-region growth) lives in map.py because it
needs clusters; S4 (HN check) is run for flagged terms only, see hn.py.
"""
import argparse, collections, re, sys
import duckdb, numpy as np, pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
from .harvest import DB

SCHEMA = """
create table if not exists signals (
  run_date date, signal text, key text, recent int, prior int, growth double,
  first_seen date, breadth int, accel double, score double);
"""


def windows(con, months):
    last = con.execute("select max(submitted) from papers").fetchone()[0]
    # ponytail: the current month is partial, drop it so growth isn't biased low
    end = pd.Timestamp(last).to_period("M") - 1
    recent = [str(end - i) for i in range(months)]
    prior = [str(end - months - i) for i in range(months)]
    return recent, prior


def convergence(con, months, min_recent=15):
    """S2: category pairs whose co-occurrence is growing."""
    df = con.execute("select strftime(submitted, '%Y-%m') ym, categories from papers").df()
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


def terms(con, months, min_df=25):
    """S3: 2–4 word phrases from titles+abstracts that are young, growing, and used by many author groups."""
    df = con.execute("select id, strftime(submitted, '%Y-%m') ym, title || '. ' || abstract txt, authors[1] a1 from papers").df()
    recent, prior = windows(con, months)
    vec = CountVectorizer(ngram_range=(2, 4), min_df=min_df, stop_words="english", binary=True,
                          token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b", lowercase=True)
    X = vec.fit_transform(df.txt)  # docs x phrases, binary
    vocab = vec.get_feature_names_out()
    ym = df.ym.values
    months_sorted = sorted(set(ym))
    M = np.array([[1.0 if m == k else 0.0 for k in months_sorted] for m in ym])  # docs x months
    per_month = (X.T @ M)  # phrases x months
    mi = {m: i for i, m in enumerate(months_sorted)}
    rec_idx = [mi[m] for m in recent if m in mi]; pri_idx = [mi[m] for m in prior if m in mi]
    rcount = per_month[:, rec_idx].sum(1); pcount = per_month[:, pri_idx].sum(1)
    first = np.array([months_sorted[j] for j in (per_month > 0).argmax(1)])
    # acceleration: slope over recent window minus slope over prior window (per-month counts)
    def slope(idx):
        if len(idx) < 2: return np.zeros(per_month.shape[0])
        x = np.arange(len(idx)); y = per_month[:, idx]
        return ((x - x.mean()) * (y - y.mean(1, keepdims=True))).sum(1) / ((x - x.mean()) ** 2).sum()
    accel = slope(rec_idx) - slope(pri_idx)
    # ponytail: breadth = distinct first-author surnames, cheap proxy for independent groups
    surname = df.a1.fillna("").map(lambda s: s.split()[-1].lower() if s else "").values
    rows = []
    keep = np.where((rcount >= 30) & ((rcount + 1) / (pcount + 1) >= 1.5))[0]
    Xc = X.tocsc()
    for j in keep:
        docs = Xc[:, j].nonzero()[0]
        breadth = len(set(surname[docs]) - {""})
        if breadth < 5: continue
        g = (rcount[j] + 1) / (pcount[j] + 1)
        age_bonus = 1.0 + 0.5 * (first[j] >= prior[-1])  # young term
        score = g * np.log1p(rcount[j]) * age_bonus * (1.0 + max(accel[j], 0) / 10)
        rows.append(("S3", vocab[j], int(rcount[j]), int(pcount[j]), float(g), f"{first[j]}-01", breadth, float(accel[j]), float(score)))
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--months", type=int, default=6); ap.add_argument("--top", type=int, default=30)
    a = ap.parse_args()
    con = duckdb.connect(str(DB)); con.execute(SCHEMA)
    out = []
    for name, fn in (("S2", convergence), ("S3", terms)):
        rows = fn(con, a.months)
        rows.sort(key=lambda r: -r[-1])
        out += rows
        print(f"\n== {name}: top {a.top} (recent/prior, growth, first_seen, breadth)")
        for r in rows[:a.top]:
            print(f"{r[1]:45s} {r[2]:5d}/{r[3]:<5d} x{r[4]:.1f}  {r[5] or '':10s} {r[6] or ''}")
    con.execute("delete from signals where run_date = current_date")
    con.executemany("insert into signals values (current_date,?,?,?,?,?,?,?,?,?)", out)
    print(f"\n{len(out)} signal rows written", file=sys.stderr)


if __name__ == "__main__":
    main()
