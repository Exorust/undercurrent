"""Evaluate a backtest run of S3: did the phrases flagged at the cutoff keep growing afterwards?

Usage: python -m radar.backtest [--cutoff 2025-04-01] [--top 50] [--months 6]

For each of the top-N phrases flagged with data before the cutoff, compare the
monthly paper rate in months 7–12 AFTER the cutoff with the rate in the
`months` before it. grew = ratio >= 1.5, faded = ratio < 0.75.
Baseline: the same measurement for every candidate phrase the run stored
(all S3 rows of that run), which shows what "flagged" adds over "any phrase
that passed the minimum filters". Writes BACKTEST.md.
"""
import argparse, sys
import duckdb, numpy as np, pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
from .harvest import DB, ROOT


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--cutoff", default="2025-04-01"); ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--months", type=int, default=6)
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    sig = con.execute("""select key, recent, prior, growth, score from signals where signal='S3' and run_tag=?
                         and run_date=(select max(run_date) from signals where run_tag=?) order by score desc""", [a.cutoff, a.cutoff]).df()
    if sig.empty: raise SystemExit(f"no S3 rows for run_tag {a.cutoff}; run signals --cutoff first")
    cut = pd.Timestamp(a.cutoff).to_period("M")
    late = [str(cut + i) for i in range(6, 12)]  # months 7–12 after the cutoff
    df = con.execute("select strftime(submitted,'%Y-%m') ym, title || '. ' || abstract txt from papers where strftime(submitted,'%Y-%m') in (" +
                     ",".join(f"'{m}'" for m in late) + ")").df()
    have = sorted(df.ym.unique())
    if len(have) < 3: raise SystemExit(f"only {len(have)} months of data after the cutoff window; cannot evaluate")
    vec = CountVectorizer(vocabulary=sorted(set(sig.key)), ngram_range=(2, 4), stop_words="english", binary=True, lowercase=True,
                          token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b")
    later = dict(zip(vec.get_feature_names_out(), np.asarray(vec.transform(df.txt).sum(0)).ravel()))
    sig["later_rate"] = sig.key.map(later) / len(have)
    sig["before_rate"] = sig.recent / a.months
    # Correct for the growth of all of arXiv CS between the two windows, else every phrase looks like it grew.
    before_months = [str(cut - i) for i in range(1, a.months + 1)]
    n_before = con.execute("select count(*) from papers where strftime(submitted,'%Y-%m') in (" + ",".join(f"'{m}'" for m in before_months) + ")").fetchone()[0]
    vol = (len(df) / len(have)) / (n_before / a.months)
    sig["ratio"] = sig.later_rate / sig.before_rate / vol
    top = sig.head(a.top)

    def stats(d): return (d.ratio >= 1.5).mean(), ((d.ratio >= 0.75) & (d.ratio < 1.5)).mean(), (d.ratio < 0.75).mean(), d.ratio.median()
    L = [f"# Backtest: S3 phrases flagged with data before {a.cutoff}", "",
         f"Measured: monthly paper rate in {have[0]}..{have[-1]} divided by the monthly rate in the {a.months} months before the cutoff, "
         f"then divided by {vol:.2f}, the growth of all arXiv CS papers between the same two windows. A ratio of 1.0 means the phrase only kept pace with the field.", "",
         "| group | n | grew (>=1.5x) | flat | faded (<0.75x) | median ratio |", "|---|---|---|---|---|---|"]
    for name, d in ((f"top {a.top} flagged", top), ("all candidates (baseline)", sig)):
        g, f, x, m = stats(d); L.append(f"| {name} | {len(d)} | {100*g:.0f}% | {100*f:.0f}% | {100*x:.0f}% | {m:.2f} |")
    L += ["", f"## Top {a.top} flagged phrases", "", "| phrase | before/mo | later/mo | ratio |", "|---|---|---|---|"]
    for r in top.itertuples():
        L.append(f"| {r.key} | {r.before_rate:.1f} | {r.later_rate:.1f} | {r.ratio:.2f} |")
    (ROOT / "BACKTEST.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:9 + min(a.top, 40)]))


if __name__ == "__main__":
    main()
