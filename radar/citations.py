"""Citation percentiles per paper and venue/code/citation/institution signals per topic and window.

Outputs (replaced atomically, in one transaction):
  paper_cites   (row, cit_pct)      percentile of citations among same-month papers, 0..100, ties = average rank
  topic_signals (level, cluster, win, n, venue_share, code_share, cit_median_pct, top10_share, top_insts, top_venues)

Windows follow radar.serve: win in (3, 6, 12) is the recent window of that many complete months ending at the
last complete month E (serve.month_window); win 0 is the whole snapshot, months 0..E. Topics are the
point_index.l0/l1/l2 primary assignment, as serve.py uses. Tables from radar.load_extra may be absent: those
signals are then NULL. Usage: python -m radar.citations [--selftest]
"""
import sys, time, json, pathlib, tempfile, os
import duckdb
from radar.harvest import DB
from radar.serve import month_window, ym_abs

WINDOWS = (0, 3, 6, 12)
SCRATCH = pathlib.Path("/private/tmp/claude-501/-Users-chandu/5ba53b84-cad4-41dd-bad3-762609d50f86/scratchpad")


def has(con, t):
    return con.execute("select count(*) from information_schema.tables where table_name = ?", [t]).fetchone()[0] > 0


def cols(con, t):
    return {r[0] for r in con.execute(f"describe {t}").fetchall()} if has(con, t) else set()


def last_complete_month(con):
    """Same rule as serve.py: min(latest submitted, latest harvested - 1 month) as an index from month0."""
    m0 = None
    if has(con, "map_meta"):
        r = con.execute("select month0 from map_meta order by created desc limit 1").fetchone()
        m0 = ym_abs(r[0]) if r and r[0] else None
    if m0 is not None and has(con, "papers"):
        ms, mh = con.execute("select max(submitted), max(harvested_at) from papers").fetchone()
        if ms is not None and mh is not None:
            return min(ym_abs(ms), ym_abs(mh) - 1) - m0
    return con.execute("select max(month) from point_index").fetchone()[0]


def build_cites(con):
    con.execute("drop table if exists _cites_new")
    con.execute("""
        create temp table _cites_new as
        with c as (
            select p.row, p.month, s.citations
            from point_index p left join s2_meta s on s.id = p.id
        ), r as (
            select row, citations,
                   rank() over w + (count(*) over (partition by month, citations) - 1) / 2.0 as avg_rank,
                   count(*) over (partition by month) as n_month
            from c where citations is not null
            window w as (partition by month order by citations)
        )
        select c.row,
               case when r.row is null then null
                    when r.n_month = 1 then 50
                    else cast(round(100 * (r.avg_rank - 1) / (r.n_month - 1)) as integer) end as cit_pct
        from c left join r on r.row = c.row order by c.row""")


def build_signals(con, E):
    ex_c, ex_i = has(con, "paper_comments"), has(con, "paper_institutions") and has(con, "institutions")
    cc = cols(con, "paper_comments")
    kind = "c.kind" if ex_c and "kind" in cc else "null::varchar"
    code = "c.code_url" if ex_c and "code_url" in cc else "null::varchar"
    venue = "c.venue" if ex_c and "venue" in cc else "null::varchar"
    join = "left join paper_comments c on c.id = p.id" if ex_c else ""
    con.execute("drop table if exists _base")
    con.execute(f"""create temp table _base as
        select p.row, p.id, p.month, p.l0, p.l1, p.l2, k.cit_pct,
               {kind} as kind, {code} as code_url, {venue} as venue
        from point_index p join _cites_new k on k.row = p.row {join}""")
    # one row per (paper, level, cluster, window) the paper falls in
    wins = [(w,) + ((0, E) if w == 0 else month_window(E, w)[:2]) for w in WINDOWS]
    con.execute("drop table if exists _win"); con.execute("create temp table _win (win int, lo int, hi int)")
    con.executemany("insert into _win values (?,?,?)", wins)
    con.execute("drop table if exists _pt")
    con.execute("""create temp table _pt as
        select b.row, b.id, l.level, case l.level when 0 then b.l0 when 1 then b.l1 else b.l2 end as cluster,
               w.win, b.cit_pct, b.kind, b.code_url, b.venue
        from _base b cross join (values (0), (1), (2)) l(level)
        join _win w on b.month between w.lo and w.hi
        where (case l.level when 0 then b.l0 when 1 then b.l1 else b.l2 end) >= 0""")
    for t in ("_agg", "_insts", "_vens", "_sig_new"): con.execute(f"drop table if exists {t}")
    ven = "null::double" if not ex_c else "avg(case when kind in ('accepted','workshop') then 1.0 else 0.0 end)"
    cod = "null::double" if not ex_c else "avg(case when code_url is not null then 1.0 else 0.0 end)"
    con.execute(f"""create temp table _agg as
        select level, cluster, win, count(*) n, {ven} venue_share, {cod} code_share,
               median(cit_pct) cit_median_pct,
               case when count(cit_pct) = 0 then null
                    else sum(case when cit_pct >= 90 then 1 else 0 end)::double / count(cit_pct) end top10_share
        from _pt group by level, cluster, win""")
    if ex_i:
        con.execute("""create temp table _insts as
            select level, cluster, win, '[' || string_agg(json_array(inst_id, name, n)::varchar, ',' order by n desc, name) || ']' j
            from (select *, row_number() over (partition by level, cluster, win order by n desc, name) rn
                  from (select t.level, t.cluster, t.win, i.inst_id, coalesce(i.name, '') name, count(distinct t.id) n
                        from _pt t join (select distinct id, inst_id from paper_institutions) a on a.id = t.id
                        join institutions i on i.inst_id = a.inst_id
                        group by 1, 2, 3, 4, 5)) where rn <= 5 group by level, cluster, win""")
    else:
        con.execute("create temp table _insts (level int, cluster int, win int, j varchar)")
    if ex_c and "venue" in cc:
        con.execute("""create temp table _vens as
            select level, cluster, win, '[' || string_agg(json_array(venue, n)::varchar, ',' order by n desc, venue) || ']' j
            from (select *, row_number() over (partition by level, cluster, win order by n desc, venue) rn
                  from (select level, cluster, win, venue, count(*) n from _pt where venue is not null and venue <> ''
                        group by 1, 2, 3, 4)) where rn <= 5 group by level, cluster, win""")
    else:
        con.execute("create temp table _vens (level int, cluster int, win int, j varchar)")
    con.execute("""create temp table _sig_new as
        select a.level, a.cluster, a.win, a.n, a.venue_share, a.code_share, a.cit_median_pct, a.top10_share,
               i.j top_insts, v.j top_venues
        from _agg a left join _insts i using (level, cluster, win) left join _vens v using (level, cluster, win)
        order by level, cluster, win""")


def run(con, log=print):
    t0 = time.time()
    E = last_complete_month(con)
    build_cites(con); log(f"paper_cites: {con.execute('select count(*) from _cites_new').fetchone()[0]:,} rows (E={E}) {time.time()-t0:.1f}s")
    t1 = time.time(); build_signals(con, E)
    con.execute("begin")
    try:
        for t, src in (("paper_cites", "_cites_new"), ("topic_signals", "_sig_new")):
            con.execute(f"drop table if exists {t}"); con.execute(f"create table {t} as select * from {src}")
        con.execute("commit")
    except Exception:
        con.execute("rollback"); raise
    log(f"topic_signals: {con.execute('select count(*) from topic_signals').fetchone()[0]:,} rows {time.time()-t1:.1f}s; total {time.time()-t0:.1f}s")


def fixture(con):
    con.execute("create table point_index (row integer, id text, month smallint, cat smallint, l0 smallint, l1 smallint, l2 smallint, x real, y real)")
    con.execute("create table s2_meta (id text, s2_id text, citations integer, influential integer, tldr text, fetched_at timestamp)")
    # month 35 = E (no papers table, so E = max(month)); topic 0 = rows 0-5, topic 1 = rows 6-10; l1 all topic 0; l2 none (-1)
    for r, c in enumerate([0, 0, 0, 1, 2, 2, 5, 10, 10, 50, None]):
        con.execute("insert into point_index values (?,?,35,0,?,0,-1,0,0)", [r, f"p{r:02d}", 0 if r < 6 else 1])
        con.execute("insert into s2_meta values (?,?,?,0,null,null)", [f"p{r:02d}", "s", c])


def selftest():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    p = SCRATCH / "citations_selftest.duckdb"; p.unlink(missing_ok=True)
    con = duckdb.connect(str(p)); fixture(con)
    run(con, log=lambda *_: None)   # extras tables absent: must not crash
    # hand-computed, n=10 non-null: average ranks 0->(1+2+3)/3=2, 1->4, 2->(5+6)/2=5.5, 5->7, 10->(8+9)/2=8.5, 50->10
    # pct = round(100*(avg-1)/9): 0->11.1->11, 1->33.3->33, 2->50, 5->66.7->67, 10->83.3->83, 50->100; null stays null
    exp = [11, 11, 11, 33, 50, 50, 67, 83, 83, 100, None]
    got = [r[0] for r in con.execute("select cit_pct from paper_cites order by row").fetchall()]
    assert got == exp, (got, exp)
    sg = {(r[0], r[1], r[2]): r for r in con.execute("select * from topic_signals").fetchall()}
    assert not any(k[0] == 2 for k in sg) and (0, 0, 0) in sg and (1, 0, 3) in sg
    assert sg[(0, 0, 3)][3] == 6 and sg[(0, 1, 3)][3] == 5 and sg[(1, 0, 12)][3] == 11
    assert all(sg[(0, 0, 3)][i] is None for i in (4, 5, 8, 9)), "absent extras give nulls"
    assert sg[(0, 0, 3)][6] == 22.0 and sg[(0, 0, 3)][7] == 0.0   # median(11,11,11,33,50,50)=22; none >= 90
    assert sg[(0, 1, 3)][6] == 83.0 and sg[(0, 1, 3)][7] == 0.25  # 67,83,83,100 -> median 83; 1 of 4 non-null >= 90
    # with extras: topic 1 = p06..p10
    con.execute("create table paper_comments (id text, comment text, venue text, venue_source text, kind text, year int, code_url text, pages int, withdrawn bool)")
    con.executemany("insert into paper_comments values (?,null,?,null,?,2026,?,null,false)", [
        ("p06", "NeurIPS", "accepted", "http://c"), ("p07", "WS", "workshop", None), ("p08", None, "preprint", "http://c"), ("p09", "NeurIPS", "accepted", None)])
    con.execute("create table paper_institutions (id text, inst_id text, author_pos int)")
    con.executemany("insert into paper_institutions values (?,?,?)", [("p06", "i1", 0), ("p06", "i1", 1), ("p06", "i2", 2), ("p07", "i1", 0), ("p09", "i1", 0)])
    con.execute("create table institutions (inst_id text, ror text, name text, country text, type text, n int)")
    con.executemany("insert into institutions values (?,null,?,null,null,0)", [("i1", "MIT"), ("i2", "CMU")])
    run(con, log=lambda *_: None)
    r = con.execute("select venue_share, code_share, top_insts, top_venues from topic_signals where level=0 and cluster=1 and win=3").fetchone()
    assert r[0] == 3 / 5 and r[1] == 2 / 5, r   # accepted/workshop: p06,p07,p09; code_url: p06,p08
    assert json.loads(r[2]) == [["i1", "MIT", 3], ["i2", "CMU", 1]], r[2]   # distinct papers, p06 counted once for i1
    assert json.loads(r[3]) == [["NeurIPS", 2], ["WS", 1]], r[3]
    con.close(); p.unlink()
    print("ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv: selftest()
    else:
        con = duckdb.connect(str(DB)); run(con); con.close()
