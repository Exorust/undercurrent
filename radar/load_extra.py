"""Merge downloaded Parquet files (comments, OpenAlex institutions, references) into database tables.

Usage: python -m radar.load_extra [--raw data/raw] [--selftest]
DB path comes from env RADAR_DB (see radar/harvest.py). Every output is limited to papers in point_index
and is rebuilt atomically (temp table, then drop + rename inside one transaction).
"""
import argparse, glob, os, pathlib, re, sys, tempfile, time
import duckdb, pandas as pd
from radar.harvest import DB
from radar.venues import parse_comment

ARXIV_RE = re.compile(r"^\s*arxiv(\.org)?\s*$", re.I)
TABLES = ["paper_comments", "paper_institutions", "paper_looked_up", "institutions", "cite_refs", "extra_meta"]


def _cols(con, table):
    try:
        return {r[0] for r in con.execute(f"describe {table}").fetchall()}
    except duckdb.Error:
        return set()


def _files(raw, sub, pattern="*.parquet", skip_done=False):
    fs = sorted(glob.glob(os.path.join(str(raw), sub, pattern)))
    if skip_done:
        fs = [f for f in fs if re.fullmatch(r"\d+\.parquet", os.path.basename(f))]
    return fs


def _plist(fs):
    return "[" + ",".join("'" + f.replace("'", "''") + "'" for f in fs) + "]"


def _venue_row(comment, s2v):
    p = parse_comment(comment) if comment else parse_comment(None)
    venue, src, kind = p["venue"], None, p["kind"]
    if venue:
        src = "comment"
    elif s2v is not None and str(s2v).strip() and not ARXIV_RE.match(str(s2v)):
        venue, src = str(s2v).strip(), "s2"
        if kind is None:
            kind = "accepted"
    return (venue, src, kind, p["year"], p["code_url"], p["pages"], p["withdrawn"])


def load(raw, db=None):
    t0 = time.time()
    con = duckdb.connect(str(db or DB))
    pc, sc = _cols(con, "papers"), _cols(con, "s2_meta")
    cm_files = _files(raw, "comments")
    oa_files = _files(raw, "openalex")
    rf_files = _files(raw, "refs", skip_done=True)

    # raw comment backfill
    con.execute("create or replace temp table raw_comments(id varchar, comment varchar)")
    if cm_files:
        con.execute(f"insert into raw_comments select id, max(comment) from "
                    f"read_parquet({_plist(cm_files)}, union_by_name=true) where comment is not null group by id")
    pcomment = "p.comment" if "comment" in pc else "null::varchar"
    s2v = "s.s2_venue" if "s2_venue" in sc else "null::varchar"
    from_p = "left join papers p on p.id = pi.id" if "id" in pc else ""
    if not from_p:
        pcomment = "null::varchar"
    from_s = "left join (select id, max(s2_venue) s2_venue from s2_meta group by id) s on s.id = pi.id" \
        if "s2_venue" in sc else ""
    if not from_s:
        s2v = "null::varchar"
    base = con.execute(
        f"select pi.id, coalesce({pcomment}, rc.comment) as comment, {s2v} as s2v "
        f"from point_index pi {from_p} {from_s} left join raw_comments rc on rc.id = pi.id").df()
    rows = [_venue_row(c if isinstance(c, str) else None, v if isinstance(v, str) else None)
            for c, v in zip(base["comment"], base["s2v"])]
    out = pd.DataFrame(rows, columns=["venue", "venue_source", "kind", "year", "code_url", "pages", "withdrawn"])
    out.insert(0, "comment", base["comment"].where(base["comment"].notna(), None))
    out.insert(0, "id", base["id"])
    out["year"] = out["year"].astype("Int64")
    out["pages"] = out["pages"].astype("Int64")
    out["withdrawn"] = out["withdrawn"].astype(bool)
    con.register("pc_df", out)

    stage = {}  # final name -> create-select (all built into temp tables named _new_<name>)
    stage["paper_comments"] = "select * from pc_df"

    if oa_files:
        con.execute(f"create or replace temp table oa as select * from read_parquet({_plist(oa_files)}, union_by_name=true)")
    else:
        con.execute("create or replace temp table oa(id varchar, author_pos integer, inst_id varchar, ror varchar, "
                    "name varchar, country varchar, type varchar)")
    stage["paper_institutions"] = (
        "select distinct oa.id, oa.inst_id, oa.author_pos from oa join point_index pi on pi.id = oa.id "
        "where oa.inst_id is not null")
    stage["paper_looked_up"] = "select distinct oa.id from oa join point_index pi on pi.id = oa.id"
    mode = lambda c: f"mode({c}) filter (where {c} is not null)"
    stage["institutions"] = (
        f"select oa.inst_id, {mode('oa.ror')} ror, {mode('oa.name')} name, {mode('oa.country')} country, "
        f"{mode('oa.type')} type, count(distinct oa.id)::bigint n from oa join point_index pi on pi.id = oa.id "
        f"where oa.inst_id is not null group by oa.inst_id")
    if rf_files:
        stage["cite_refs"] = (
            f"select distinct r.citing_id, r.cited_id from read_parquet({_plist(rf_files)}, union_by_name=true) r "
            f"join point_index a on a.id = r.citing_id join point_index b on b.id = r.cited_id "
            f"where r.citing_id <> r.cited_id")
    else:
        stage["cite_refs"] = "select null::varchar citing_id, null::varchar cited_id where false"

    for name, sel in stage.items():
        con.execute(f"create or replace temp table _new_{name} as {sel}")

    sc_ = lambda q: con.execute(q).fetchall()[0][0]
    n_points = sc_("select count(*) from point_index")
    sh = lambda x: round(x / n_points, 4) if n_points else None
    meta = {
        "n_points": n_points,
        "comment_share": sh(sc_("select count(*) from _new_paper_comments where comment is not null and trim(comment) <> ''")),
        "venue_share": sh(sc_("select count(*) from _new_paper_comments where kind in ('accepted','workshop')")),
        "code_share": sh(sc_("select count(*) from _new_paper_comments where code_url is not null")),
        "inst_looked_up_share": sh(sc_("select count(*) from _new_paper_looked_up")),
        "inst_known_share": sh(sc_("select count(distinct id) from _new_paper_institutions")),
        "n_institutions_30": sc_("select count(*) from _new_institutions where n >= 30"),
        "n_cite_refs": sc_("select count(*) from _new_cite_refs"),
        "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    mdf = pd.DataFrame([(k, str(v)) for k, v in meta.items()], columns=["key", "value"])
    con.register("meta_df", mdf)
    con.execute("create or replace temp table _new_extra_meta as select * from meta_df")

    con.execute("begin")
    try:
        for name in TABLES:
            con.execute(f"drop table if exists {name}")
            con.execute(f"create table {name} as select * from _new_{name}")
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    counts = {n: sc_(f"select count(*) from {n}") for n in TABLES}
    con.close()
    return meta, counts, time.time() - t0


def report(meta, counts, secs):
    for k, v in meta.items():
        print(f"{k}: {v}")
    print("rows: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"took {secs:.1f}s")


def selftest():
    import shutil
    scratch = pathlib.Path("/private/tmp/claude-501/-Users-chandu/5ba53b84-cad4-41dd-bad3-762609d50f86/scratchpad")
    scratch.mkdir(parents=True, exist_ok=True)
    d = pathlib.Path(tempfile.mkdtemp(prefix="load_extra_", dir=scratch))
    raw = d / "raw"
    for s in ("comments", "openalex", "refs"):
        (raw / s).mkdir(parents=True)

    def pq(path, df):
        c = duckdb.connect(); c.register("t", df); c.execute(f"copy t to '{path}' (format parquet)")

    ids = ["a1", "a2", "a3", "a4", "a5"]
    def mkdb(name, with_comment, with_venue):
        p = d / name
        c = duckdb.connect(str(p))
        c.execute("create table point_index(row int, id varchar)")
        c.executemany("insert into point_index values (?,?)", list(enumerate(ids)))
        c.execute("create table papers(id varchar, title varchar" + (", comment varchar" if with_comment else "") + ")")
        c.executemany("insert into papers values (?,?" + (",?" if with_comment else "") + ")",
                      [(i, "t") + (((["Accepted at NeurIPS 2025. Code: https://github.com/x/y", None, "submitted to ICML 2026",
                                      None, None])[k],) if with_comment else ()) for k, i in enumerate(ids)])
        c.execute("create table s2_meta(id varchar" + (", s2_venue varchar" if with_venue else "") + ")")
        c.executemany("insert into s2_meta values (?" + (",?" if with_venue else "") + ")",
                      [(i,) + (((["Nature", "arXiv.org", "ArXiv", "Journal of Things", ""])[k],) if with_venue else ())
                       for k, i in enumerate(ids)])
        c.close()
        return p

    pq(raw / "comments/00000.parquet", pd.DataFrame({"id": ["a2", "a3", "a4", "zz"],
        "comment": ["Accepted to CVPR 2024", "ignored, papers has one", None, "x"]}))
    pq(raw / "openalex/00000.parquet", pd.DataFrame({
        "id": ["a1", "a1", "a1", "a2", "a3", "a9"], "author_pos": [0, 0, 1, 0, 0, 0],
        "inst_id": ["I1", "I1", "I2", "I1", None, "I1"], "ror": ["r1", "r1", "r2", "r1", None, "r1"],
        "name": ["Uni One", "Uni One", "Uni Two", "Uni One", None, "Uni One"], "country": ["US", "US", "DE", "US", None, "US"],
        "type": ["education"] * 3 + ["education", None, "education"]}))
    pq(raw / "refs/00000.parquet", pd.DataFrame({"citing_id": ["a1", "a1", "a1", "a2", "a1"],
        "cited_id": ["a2", "a2", "a1", "q9", "a3"]}))
    pq(raw / "refs/done_00001.parquet", pd.DataFrame({"citing_id": ["a4"], "cited_id": ["a5"]}))

    for with_comment, with_venue in ((True, True), (False, False)):
        p = mkdb(f"t_{with_comment}.duckdb", with_comment, with_venue)
        r1 = load(raw, p); r2 = load(raw, p)
        assert r1[1] == r2[1], (r1[1], r2[1])
        assert {k: v for k, v in r1[0].items() if k != "loaded_at"} == {k: v for k, v in r2[0].items() if k != "loaded_at"}
        c = duckdb.connect(str(p), read_only=True)
        pcs = {r[0]: r for r in c.execute("select id, comment, venue, venue_source, kind, code_url from paper_comments").fetchall()}
        assert len(pcs) == 5
        assert c.execute("select count(*) from cite_refs").fetchall()[0][0] == 2  # a1->a2 (deduped), a1->a3
        assert sorted(c.execute("select citing_id||'>'||cited_id from cite_refs").fetchall()) == [("a1>a2",), ("a1>a3",)]
        assert c.execute("select count(*) from paper_institutions").fetchall()[0][0] == 3  # (a1,I1,0),(a1,I2,1),(a2,I1,0)
        assert c.execute("select count(*) from paper_looked_up").fetchall()[0][0] == 3  # a1,a2,a3; a9 outside
        assert c.execute("select n from institutions where inst_id='I1'").fetchall()[0][0] == 2
        assert c.execute("select n from institutions where inst_id='I2'").fetchall()[0][0] == 1
        assert c.execute("select count(*) from paper_comments where venue ilike 'arxiv%'").fetchall()[0][0] == 0
        if with_comment:
            assert pcs["a1"][2:5] == ("NeurIPS", "comment", "accepted") and pcs["a1"][5] == "https://github.com/x/y"
            assert pcs["a2"][1:3] == ("Accepted to CVPR 2024", "CVPR")  # raw backfill
            assert pcs["a3"][2:5] == ("ICML", "comment", "submitted")
            assert pcs["a4"][2:5] == ("Journal of Things", "s2", "accepted")
            assert pcs["a5"][2] is None  # empty s2 venue
        else:
            assert pcs["a2"][2:5] == ("CVPR", "comment", "accepted")
            assert pcs["a1"][1] is None
        em = dict(c.execute("select key, value from extra_meta").fetchall())
        assert em["n_points"] == "5" and em["n_cite_refs"] == "2" and em["inst_known_share"] == "0.4"
        c.close()
    # s2 fallback sets kind accepted
    p = mkdb("t_s2.duckdb", True, True)
    c = duckdb.connect(str(p)); c.execute("update papers set comment = null"); c.close()
    shutil.rmtree(raw / "comments")  # missing input folder
    load(raw, p)
    c = duckdb.connect(str(p), read_only=True)
    r = c.execute("select venue, venue_source, kind from paper_comments where id='a1'").fetchall()[0]
    assert r == ("Nature", "s2", "accepted"), r
    assert c.execute("select count(*) from paper_comments where venue_source='s2'").fetchall()[0][0] == 2  # a1, a4 (Journal of Things)
    c.close()
    # all inputs missing
    shutil.rmtree(raw)
    raw.mkdir()
    load(raw, p)
    c = duckdb.connect(str(p), read_only=True)
    assert c.execute("select count(*) from cite_refs").fetchall()[0][0] == 0
    assert c.execute("select count(*) from paper_comments").fetchall()[0][0] == 5
    c.close()
    shutil.rmtree(d, ignore_errors=True)
    print("ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    report(*load(a.raw))


if __name__ == "__main__":
    main()
