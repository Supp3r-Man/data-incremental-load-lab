import re

from incload.sim import DAY, Sim


def snapshot(sim):
    return sim.con.execute("SELECT * FROM src ORDER BY id").fetchall()


def run_sim(seed, days=5, **kw):
    s = Sim(seed, n0=100, **kw)
    for d in range(days + 1):
        s.step(d)
    return s


def test_same_seed_gives_identical_source():
    assert snapshot(run_sim(3)) == snapshot(run_sim(3))


def test_every_event_kind_occurs():
    s = run_sim(1, days=10)
    for kind in ("insert", "update", "silent", "soft", "hard", "late"):
        assert sum(e[kind] for e in s.log) > 0, kind


def test_late_rows_are_stamped_before_the_day_they_arrive():
    s = Sim(5, n0=100, ins=0, upd=0, silent=0, soft=0, hard=0, late=20)
    for d in range(4):
        s.step(d)
    before = s.con.execute("SELECT max(id) FROM src").fetchone()[0]
    s.step(4)
    ts = [t for (t,) in s.con.execute("SELECT updated_at FROM src WHERE id > ?", [before]).fetchall()]
    assert len(ts) == 20 and all(t < 4 * DAY for t in ts) and any(t < 3 * DAY for t in ts)


def test_silent_update_changes_value_but_not_updated_at():
    s = Sim(5, n0=50, ins=0, upd=0, silent=5, soft=0, hard=0, late=0)
    s.step(0)
    before = snapshot(s)
    s.step(1)
    after = snapshot(s)
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(changed) == 5 and all(a[5] == b[5] and a[3] != b[3] for a, b in changed)


def test_soft_delete_keeps_row_and_bumps_updated_at_hard_delete_removes_it():
    s = Sim(5, n0=50, ins=0, upd=0, silent=0, soft=3, hard=2, late=0)
    s.step(0)
    s.step(1)
    assert s.con.execute("SELECT count(*) FROM src").fetchone()[0] == 48
    rows = s.con.execute("SELECT updated_at FROM src WHERE is_deleted").fetchall()
    assert len(rows) == 3 and all(t >= DAY for (t,) in rows)


def test_data_is_fictional_no_card_ssn_or_email_shapes():
    s = run_sim(2, days=3)
    pats = [r"\b\d{13,19}\b", r"\b\d{3}-\d{2}-\d{4}\b", r"@"]
    for row in snapshot(s):
        text = " ".join(map(str, row))
        assert not any(re.search(p, text) for p in pats), text
