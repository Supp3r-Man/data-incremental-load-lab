import pytest
from helpers import ZERO, again, bump, hard, loaded, master, put

from incload.loads import STRATEGIES, Crash, Env, diff, fingerprint, run


@pytest.mark.parametrize("at", ["mid", "end"])
def test_atomic_full_reload_crash_leaves_target_untouched(at):
    c = master()
    s, e, _ = loaded(c, "full_reload")
    before = fingerprint(e)
    put(c, 6, 1)
    hard(c, 1)
    with pytest.raises(Crash):
        run(e, s.fn, crash_at=at)
    assert fingerprint(e) == before


def test_non_atomic_full_reload_crash_leaves_an_empty_target():
    c = master()
    s, e, _ = loaded(c, "full_reload")
    with pytest.raises(Crash):
        run(e, s.fn, atomic=False, crash_at="mid")
    assert e.x(f"SELECT count(*) FROM {e.t}") == 0


def test_atomic_watermark_crash_keeps_data_and_watermark_together():
    c = master()
    s, e, _ = loaded(c, "watermark")
    put(c, 6, 1)
    fp, wm = fingerprint(e), e.wm()
    with pytest.raises(Crash):
        run(e, s.fn, crash_at="end")
    assert fingerprint(e) == fp and e.wm() == wm


def test_rerun_after_an_atomic_crash_recovers():
    c = master()
    s, e, _ = loaded(c, "watermark")
    put(c, 6, 1)
    with pytest.raises(Crash):
        run(e, s.fn, crash_at="end")
    again(s, e)
    assert diff(e) == ZERO and e.wm() == 86400 + 0


def test_non_atomic_watermark_crash_leaves_partial_state_that_a_rerun_repairs():
    c = master()
    s, e, _ = loaded(c, "watermark")
    bump(c, 1, 1)
    put(c, 6, 1)
    wm = e.wm()
    with pytest.raises(Crash):
        run(e, s.fn, atomic=False, crash_at="mid")  # update applied, insert not, watermark not moved
    assert diff(e)["missed"] == 1 and diff(e)["stale"] == 0 and e.wm() == wm
    again(s, e)
    assert diff(e) == ZERO


def test_atomic_crash_mid_load_is_all_or_nothing_for_every_strategy():
    for s in STRATEGIES:
        c = master()
        _, e, _ = loaded(c, s.name)
        bump(c, 1, 1)
        put(c, 6, 1)
        hard(c, 2)
        fp, wm = fingerprint(e), e.wm()
        with pytest.raises(Crash):
            run(e, s.fn, crash_at="mid")
        assert fingerprint(e) == fp and e.wm() == wm, s.name
