"""Backtest S3 (phrases) and S2 (category pairs): did what was flagged at a cutoff keep growing afterwards?

Usage: python -m radar.backtest [--cutoffs 2025-01-01,2025-04-01,2025-07-01,2025-10-01] [--top 50] [--months 6]

For each item flagged with data before a cutoff: monthly paper rate in months
7–12 AFTER the cutoff, divided by the rate in the `months` before it, divided
by the growth of all arXiv CS between the same two windows (else everything
looks like it grew). grew = ratio >= 1.5, faded = ratio < 0.75.
S3 needs a stored run per cutoff (python -m radar.signals --cutoff D); a cutoff
without one, or without 6 full later months, is skipped. S3 baseline: every
candidate the run stored. S2 is recomputed here from the papers table; its
baseline is every pair with at least 30 papers before the cutoff.
Several cutoffs are pooled because one cutoff gives only 50 phrases. Writes BACKTEST.md.
"""
import argparse
import duckdb, numpy as np, pandas as pd
from sklearn.feature_extraction.text import CountVectorizer
from .harvest import DB, ROOT
from .signals import KW, by_month, pair_rows, pair_window


def win(cutoff, a, b):
    cut = pd.Timestamp(cutoff).to_period("M")
    return [str(cut + i) for i in range(a, b)]


def s3(con, cutoff, months, top):
    sig = con.execute("""select key, recent, score from signals where signal='S3' and run_tag=?
                         and run_date=(select max(run_date) from signals where run_tag=?) order by score desc""", [cutoff, cutoff]).df()
    q = "select {} from papers where strftime(submitted,'%Y-%m') in ({})"
    late = con.execute(q.format("strftime(submitted,'%Y-%m') ym, title || '. ' || abstract txt", ",".join(f"'{m}'" for m in win(cutoff, 6, 12)))).df()
    if sig.empty or late.ym.nunique() < 6: return None
    vec = CountVectorizer(vocabulary=sorted(set(sig.key)), **{**KW, "ngram_range": (2, 6)})  # extended phrases can have 6 words
    later = dict(zip(vec.get_feature_names_out(), np.asarray(vec.transform(late.txt).sum(0)).ravel()))
    n_before = con.execute(q.format("count(*)", ",".join(f"'{m}'" for m in win(cutoff, -months, 0)))).fetchone()[0]
    vol = (len(late) / 6) / (n_before / months)
    sig["before"] = sig.recent / months; sig["later"] = sig.key.map(later) / 6
    sig["ratio"] = sig.later / sig.before / vol
    sig["flagged"] = np.arange(len(sig)) < top; sig["cutoff"] = cutoff
    return sig


def s2(bm, cutoff, months):
    recent, prior, late = win(cutoff, -months, 0), win(cutoff, -2 * months, -months), win(cutoff, 6, 12)
    if not all(m in bm for m in late + prior): return None
    rows, base = pair_rows(bm, recent, prior)
    (nr, _), (nl, cl) = pair_window(bm, recent), pair_window(bm, late)
    flagged = {r[1] for r in rows}
    return pd.DataFrame([dict(cutoff=cutoff, key=k, before=r / months, later=cl.get(tuple(k.split("+")), 0) / 6,
                              ratio=cl.get(tuple(k.split("+")), 0) / r / (nl / nr), flagged=k in flagged) for k, r in base.items()])


def table(d, flagged_name):
    L = ["| group | n | grew (>=1.5x) | flat | faded (<0.75x) | median ratio |", "|---|---|---|---|---|---|"]
    def row(name, x):
        if len(x): L.append(f"| {name} | {len(x)} | {100 * (x.ratio >= 1.5).mean():.0f}% | {100 * ((x.ratio >= 0.75) & (x.ratio < 1.5)).mean():.0f}% | "
                            f"{100 * (x.ratio < 0.75).mean():.0f}% | {x.ratio.median():.2f} |")
    row(f"{flagged_name}, all cutoffs", d[d.flagged]); row("baseline, all cutoffs", d)
    for c, x in d.groupby("cutoff"): row(f"{flagged_name}, {c}", x[x.flagged]); row(f"baseline, {c}", x)
    return L


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--cutoffs", default="2025-01-01,2025-04-01,2025-07-01,2025-10-01")
    ap.add_argument("--top", type=int, default=50); ap.add_argument("--months", type=int, default=6)
    a = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    cuts = a.cutoffs.split(","); bm = by_month(con)
    P = [x for x in (s3(con, c, a.months, a.top) for c in cuts) if x is not None]
    C = [x for x in (s2(bm, c, a.months) for c in cuts) if x is not None]
    if not P: raise SystemExit("no usable S3 backtest run; run `python -m radar.signals --cutoff D` first")
    P, C = pd.concat(P), pd.concat(C)
    L = ["# Backtest", "",
         f"Ratio = monthly paper rate in months 7 to 12 after the cutoff, divided by the rate in the {a.months} months before it, divided by the growth "
         "of all arXiv CS papers between the same two windows. A ratio of 1.0 means the item only kept pace with the field.", "",
         "## Phrases (S3)", "", f"Flagged = the top {a.top} phrases by score at each cutoff. Baseline = every phrase that passed the filters.", ""]
    L += table(P, f"top {a.top}")
    L += ["", "## Category pairs (S2)", "", "Flagged = pairs that pass the S2 rule. Baseline = every pair with at least 30 papers in the 6 months before the cutoff.", ""]
    L += table(C, "flagged")
    f, b = C[C.flagged], C
    if len(f) and (f.ratio < 0.75).mean() >= (b.ratio < 0.75).mean():
        L += ["", "Flagged pairs faded at least as often as the baseline. Read the S2 table as a description of the last 6 months, not as a forecast."]
    L += ["", "## Details", ""]
    for c, x in P.groupby("cutoff"):
        L += [f"### Top {a.top} phrases flagged before {c}", "", "| phrase | before/mo | later/mo | ratio |", "|---|---|---|---|"]
        L += [f"| {r.key} | {r.before:.1f} | {r.later:.1f} | {r.ratio:.2f} |" for r in x[x.flagged].itertuples()] + [""]
    for c, x in C[C.flagged].groupby("cutoff"):
        L += [f"### Pairs flagged before {c}", "", "| pair | before/mo | later/mo | ratio |", "|---|---|---|---|"]
        L += [f"| {r.key} | {r.before:.1f} | {r.later:.1f} | {r.ratio:.2f} |" for r in x.sort_values("ratio", ascending=False).itertuples()] + [""]
    (ROOT / "BACKTEST.md").write_text("\n".join(L) + "\n")
    print("\n".join(L[:L.index("## Details")]))


if __name__ == "__main__":
    main()
