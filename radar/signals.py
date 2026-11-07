"""Signals S2 (field convergence) and S3 (term emergence) over the papers table.

Usage: python -m radar.signals [--months 6] [--top 30] [--cutoff YYYY-MM-DD]

Growth = count in the latest `months` window divided by the count in the
window before it, corrected for the change in total paper volume between the
two windows. Results go to the `signals` table and the top lists are printed.
S1 (map-region growth) lives in merge.py because it needs clusters; S4 (HN
check) is run for flagged terms only, see hn.py.
"""
import argparse, collections, re, sys
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
# arXiv lists these as the same category under two names
ALIAS = {"math.IT": "cs.IT", "math.NA": "cs.NA", "eess.SY": "cs.SY", "stat.TH": "math.ST", "math.MP": "math-ph", "q-fin.EC": "econ.GN"}
# Product and model names are not fields, and in the backtest they faded ("openai o1", "deepseek r1").
# ponytail: hand list plus "any token with a digit" (o1, gpt-4o, qwen3); extend the list when a name gets through.
NAMES = set("openai gpt chatgpt deepseek gemini claude llama qwen grok mistral anthropic copilot sora deepmind nvidia kimi glm phi gemma".split())
KW = dict(ngram_range=(2, 4), stop_words="english", binary=True, lowercase=True, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b")


def stem(w):
    """Merge plural and singular: models/model, harnesses/harness. ponytail: not a real stemmer, policies/policy stay apart."""
    return re.sub(r"(?<=ss)es$|(?<=[^s])s$", "", w)


assert [stem(w) for w in "harnesses harness models architectures process".split()] == "harness harness model architecture process".split()


def papers_sql(cols):
    return f"select {cols} from papers" + (f" where submitted < '{CUTOFF}'" if CUTOFF else "")


def windows(con, months):
    last = con.execute(papers_sql("max(submitted)")).fetchone()[0]
    # ponytail: the latest month is partial in a live run, drop it so growth isn't biased low
    end = pd.Timestamp(last).to_period("M") - (0 if CUTOFF else 1)
    recent = [str(end - i) for i in range(months)]
    prior = [str(end - months - i) for i in range(months)]
    return recent, prior


def by_month(con):
    """{month: (paper count, Counter of category pairs)}; alias names merged."""
    out = {}
    for ym, cats in con.execute(papers_sql("strftime(submitted, '%Y-%m') ym, categories")).fetchall():
        n, pairs = out.get(ym) or (0, collections.Counter())
        cs = sorted({ALIAS.get(c, c) for c in cats})
        pairs.update((cs[i], cs[j]) for i in range(len(cs)) for j in range(i + 1, len(cs)))
        out[ym] = (n + 1, pairs)
    return out


def pair_window(bm, months):
    n, cnt = 0, collections.Counter()
    for m in months:
        if m in bm: n += bm[m][0]; cnt.update(bm[m][1])
    return n, cnt


def pair_rows(bm, recent, prior, min_recent=30):
    """The S2 flag rule. Returns (rows, {pair: recent count} for every pair with at least min_recent papers).
    min_recent is 30 because smaller pairs mostly fell back in the backtest (BACKTEST.md)."""
    (nr, cr), (np_, cp) = pair_window(bm, recent), pair_window(bm, prior)
    vol = nr / max(np_, 1)  # overall volume change; a pair must beat this
    rows = []
    for pair, r in cr.items():
        p = cp.get(pair, 0)
        exp = p * vol
        g = (r + 1) / (exp + 1)
        z = (r - exp) / np.sqrt(r + exp + 1)  # rough Poisson significance of the increase
        if r < min_recent or g < 1.3 or z < 2: continue
        rows.append(("S2", "+".join(pair), r, p, float(g), None, None, None, float(z * np.log(g))))
    return rows, {"+".join(k): v for k, v in cr.items() if v >= min_recent}


def convergence(con, months):
    """S2: category pairs whose co-occurrence grows faster than paper volume overall."""
    recent, prior = windows(con, months)
    return pair_rows(by_month(con), recent, prior)[0]


PAPER_COLS = "strftime(submitted, '%Y-%m') ym, title, title || '. ' || abstract txt, authors[1] a1, primary_cat"


def candidate_vocab(df=None, recent=None, min_recent=30):
    """The phrase vocabulary of S3: 2-4-grams seen twice in a 10k-paper chunk of the recent window, kept when at least
    min_recent recent papers use them and they hold no product name. Sorted list. Shared with links.py (paper_terms).
    With no arguments it reads the live 6-month window from the database (read-only)."""
    if df is None:
        con = duckdb.connect(str(DB), read_only=True)
        df = con.execute(papers_sql(PAPER_COLS)).df(); recent = windows(con, 6)[0]; con.close()
    # Memory: fitting 2-4-grams on all docs at once builds tens of millions of strings.
    # Find candidates in 10k-doc chunks of the recent window (seen twice in a chunk), then count only those.
    rec_txt = df.txt.values[np.isin(df.ym.values, recent)]
    cand = set()
    for i in range(0, len(rec_txt), 10000):
        cand.update(CountVectorizer(min_df=2, **KW).fit(rec_txt[i:i + 10000]).vocabulary_)
    vec = CountVectorizer(vocabulary=sorted(cand), **KW)
    rc = np.asarray(vec.transform(rec_txt).sum(0)).ravel()
    names = vec.get_feature_names_out()
    named = np.array([any(t in NAMES or any(ch.isdigit() for ch in t) for t in k.split()) for k in names], dtype=bool)
    return [str(k) for k in names[(rc >= min_recent) & ~named]]


def _old_inline_vocab(df, recent, min_recent):  # the pre-extraction code, kept only for the self-check below
    ym = df.ym.values; rec_mask = np.isin(ym, recent); rec_txt = df.txt.values[rec_mask]; cand = set()
    for i in range(0, len(rec_txt), 10000):
        cand.update(CountVectorizer(min_df=2, **KW).fit(rec_txt[i:i + 10000]).vocabulary_)
    vec = CountVectorizer(vocabulary=sorted(cand), **KW); X = vec.transform(df.txt)
    rc = np.asarray(X[rec_mask].sum(0)).ravel(); names = vec.get_feature_names_out()
    named = np.array([any(t in NAMES or any(ch.isdigit() for ch in t) for t in k.split()) for k in names])
    return list(np.array(names)[np.where((rc >= min_recent) & ~named)[0]])


_toy = pd.DataFrame({"ym": ["2026-10"] * 30 + ["2025-01"] * 10,
                     "txt": [f"world action models for robot {i % 3}. gpt4 agent harness rocks" for i in range(30)]
                            + ["world action models old paper"] * 10})
assert candidate_vocab(_toy, ["2026-10"], 5) == _old_inline_vocab(_toy, ["2026-10"], 5) and "world action models" in candidate_vocab(_toy, ["2026-10"], 5)
del _toy


def terms(con, months, min_recent=30):
    """S3: 2–4 word phrases that are young, growing, used by many author groups, and topical.

    "Topical" separates subjects from writing style: the phrase must appear in titles, and its papers
    must be concentrated in a few arXiv categories (KL divergence from the average recent paper).
    Stop words are removed before phrases are formed, so "chain of thought" is counted as "chain thought".
    """
    df = con.execute(papers_sql(PAPER_COLS)).df()
    recent, prior = windows(con, months)
    ym = df.ym.values
    rec_mask = np.isin(ym, recent); pri_mask = np.isin(ym, prior)
    vol = rec_mask.sum() / max(pri_mask.sum(), 1)
    vocab = np.array(candidate_vocab(df, recent, min_recent))
    print(f"S3: {len(vocab)} phrases from {rec_mask.sum()} recent papers; volume ratio {vol:.2f}", file=sys.stderr)
    # same counts as before the extraction: binary columns do not depend on which other columns exist
    X = CountVectorizer(vocabulary=list(vocab), **KW).transform(df.txt).tocsc()
    T = CountVectorizer(vocabulary=list(vocab), **KW).transform(df.title.values[rec_mask])
    title_n = np.asarray(T.sum(0)).ravel()

    months_sorted = sorted(set(ym)); mi = {m: i for i, m in enumerate(months_sorted)}
    M = sparse.csr_matrix((np.ones(len(ym)), (np.arange(len(ym)), [mi[m] for m in ym])), shape=(len(ym), len(months_sorted)))
    per_month = np.asarray((X.T @ M).todense())  # phrases x months
    rec_idx = sorted(mi[m] for m in recent if m in mi); pri_idx = sorted(mi[m] for m in prior if m in mi)
    rcount = per_month[:, rec_idx].sum(1); pcount = per_month[:, pri_idx].sum(1)
    first = np.array([months_sorted[j] for j in (per_month > 0).argmax(1)])

    def slope(idx):  # least-squares slope of the monthly count over a window
        if len(idx) < 2: return np.zeros(per_month.shape[0])
        x = np.arange(len(idx)) - (len(idx) - 1) / 2; y = per_month[:, idx]
        return (x * (y - y.mean(1, keepdims=True))).sum(1) / (x ** 2).sum()
    accel = slope(rec_idx) - slope(pri_idx)

    # category specificity: KL(phrase's category mix || average recent paper's mix), over recent papers
    cats, cat_idx = np.unique(df.primary_cat.fillna("?").values, return_inverse=True)
    C = sparse.csr_matrix((rec_mask.astype(float), (np.arange(len(ym)), cat_idx)), shape=(len(ym), len(cats)))
    pc = np.asarray((X.T @ C).todense()) + 1e-9  # phrases x categories
    pc /= pc.sum(1, keepdims=True)
    base = np.asarray(C.sum(0)).ravel() + 1e-9; base /= base.sum()
    kl = (pc * np.log(pc / base)).sum(1)

    # ponytail: breadth = distinct first-author surnames, cheap proxy for independent groups; affiliations if arXiv ever ships them
    surname = df.a1.fillna("").map(lambda s: s.split()[-1].lower() if s else "").values
    exp = pcount * vol
    g_all = (rcount + 1) / (exp + 1)
    z_all = (rcount - exp) / np.sqrt(rcount + exp + 1)  # rough Poisson significance of the rise
    rows = []
    txt = df.txt.values; plain = {}
    stop = CountVectorizer(stop_words="english").get_stop_words()
    def context(phrase, docs):
        """How the phrase occurs word for word in docs: (share of docs, next-word counts, previous-word counts).
        Punctuation becomes the word "." so a phrase cannot run across a comma or a bracket."""
        pat = re.compile(r"(\S+) " + re.escape(phrase) + r" (\S+)")
        hit, nxt, prv = 0, collections.Counter(), collections.Counter()
        for d in docs:
            if d not in plain: plain[d] = " . " + " ".join(re.sub(r"[^a-z0-9\-\s]+", " . ", txt[d].lower()).split()) + " . "
            m = pat.search(plain[d])
            if m: hit += 1; prv[m.group(1)] += 1; nxt[m.group(2)] += 1
        return hit / max(len(docs), 1), nxt, prv
    def dominant(words):
        """The neighbour word if one word (plural merged) takes at least 60% of the occurrences, else None."""
        by_stem = collections.Counter()
        for w, c in words.items(): by_stem[stem(w)] += c
        if not by_stem: return None
        st, c = by_stem.most_common(1)[0]
        w = max((w for w in words if stem(w) == st), key=words.get)
        ok = c >= 0.6 * sum(words.values()) and w not in stop and re.fullmatch(r"[a-z][a-z0-9\-]+", w)
        return (w, c / sum(words.values())) if ok else None
    for j in np.where((g_all >= 1.5) & (z_all >= 3) & (title_n >= 3) & (title_n >= 0.05 * rcount) & (kl >= 0.5))[0]:
        docs = X[:, j].nonzero()[0]
        breadth = len(set(surname[docs]) - {""})
        if breadth < 5: continue
        # Stop words are removed before phrases are formed, so "models write" is counted in "models that write".
        # Keep a phrase only if it occurs word for word in at least half of its recent papers.
        # ponytail: this also drops real names that contain a stop word ("chain of thought"); to keep them,
        # store the most common surface form instead of dropping.
        key, share = vocab[j], 1.0
        lit, nxt, prv = context(key, docs[rec_mask[docs]])
        if lit < 0.5: continue
        # Fragments: "frozen language" is nearly always followed by "model(s)", so the real phrase is the longer one.
        # Counts are scaled by the share of occurrences that have the extension (an approximation, not a recount).
        if (d := dominant(nxt)): key, share = f"{key} {d[0]}", share * d[1]
        if (d := dominant(prv)): key, share = f"{d[0]} {key}", share * d[1]
        age_bonus = 1.5 if first[j] >= prior[-1] else 1.0  # first seen inside the two comparison windows
        score = z_all[j] * np.log(g_all[j]) * age_bonus * (1.0 + max(accel[j], 0) / 10)
        rows.append(("S3", key, int(rcount[j] * share), int(pcount[j] * share), float(g_all[j]), f"{first[j]}-01", breadth, float(accel[j]), float(score)))
    best = {}
    # One row per topic: "world-action models", "world action model" and an extended fragment that equals an existing phrase
    # all reduce to the same form; the highest-scoring spelling is kept with its own counts.
    for r in sorted(rows, key=lambda r: -r[-1]): best.setdefault(" ".join(stem(t) for t in r[1].replace("-", " ").split()), r)
    rows = list(best.values())
    return rows


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
        print(f"\n== {name} [{run_tag}]: top {a.top} of {len(rows)} (recent/prior, growth, first_seen, breadth)")
        for r in rows[:a.top]:
            print(f"{r[1]:45s} {r[2]:5d}/{r[3]:<5d} x{r[4]:<6.1f} {r[5] or '':10s} {r[6] or ''}")
    con.execute("delete from signals where run_date = current_date and run_tag = ?", [run_tag])
    con.executemany("insert into signals values (current_date,?,?,?,?,?,?,?,?,?,?)", [r + (run_tag,) for r in out])
    print(f"\n{len(out)} signal rows written", file=sys.stderr)


if __name__ == "__main__":
    main()
