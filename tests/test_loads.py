import pytest
from helpers import ZERO, again, bump, hard, loaded, master, put, silent, soft, strat

from incload.loads import STRATEGIES, diff, rerun_safe


def test_full_reload_matches_source_and_scans_and_writes_everything():
    c = master()
    s, e, st = loaded(c, "full_reload")
    assert diff(e) == ZERO and (st.scanned, st.written) == (5, 5)


def test_full_reload_drops_hard_deleted_rows():
    c = master()
    s, e, _ = loaded(c, "full_reload")
    hard(c, 2)
    again(s, e)
    assert diff(e) == ZERO and e.x(f"SELECT count(*) FROM {e.t}") == 4


def test_watermark_first_run_loads_all_and_sets_watermark_to_max_updated_at():
    c = master()
    s, e, st = loaded(c, "watermark")
    assert diff(e) == ZERO and st.scanned == 5 and e.wm() == 50


def test_watermark_second_run_with_no_changes_scans_and_writes_nothing():
    c = master()
    s, e, _ = loaded(c, "watermark")
    st = again(s, e)
    assert (st.scanned, st.written) == (0, 0)


def test_watermark_picks_up_new_and_updated_rows_only():
    c = master()
    s, e, _ = loaded(c, "watermark")
    put(c, 6, 1)
    bump(c, 1, 1)
    st = again(s, e)
    assert (st.scanned, st.written) == (2, 2) and diff(e) == ZERO


def test_watermark_misses_a_late_arriving_row():
    c = master()
    s, e, _ = loaded(c, "watermark")
    put(c, 6, 2)
    again(s, e)
    put(c, 7, 1)  # arrives now but is stamped a day before the watermark
    again(s, e)
    assert diff(e)["missed"] == 1


def test_watermark_propagates_a_soft_delete():
    c = master()
    s, e, _ = loaded(c, "watermark")
    soft(c, 3, 1)
    again(s, e)
    assert diff(e) == ZERO and e.x(f"SELECT is_deleted FROM {e.t} WHERE id=3") is True


def test_watermark_never_sees_a_hard_delete():
    c = master()
    s, e, _ = loaded(c, "watermark")
    hard(c, 3)
    again(s, e)
    assert diff(e)["ghost"] == 1


def test_watermark_misses_an_update_that_did_not_bump_updated_at():
    c = master()
    s, e, _ = loaded(c, "watermark")
    silent(c, 2)
    again(s, e)
    assert diff(e)["stale"] == 1


def test_lookback_catches_a_row_two_days_late_but_not_five():
    c = master()
    put(c, 6, 10)
    s, e, _ = loaded(c, "watermark_lookback3")
    put(c, 7, 8)
    put(c, 8, 5)
    again(s, e)
    ids = {i for (i,) in e.con.execute(f"SELECT id FROM {e.t}").fetchall()}
    assert 7 in ids and 8 not in ids and diff(e)["missed"] == 1


def test_lookback_pays_for_its_window_by_rewriting_unchanged_rows_every_run():
    c = master()
    put(c, 6, 10)
    s, e, _ = loaded(c, "watermark_lookback3")
    st = again(s, e)
    assert (st.scanned, st.written) == (1, 1)  # row 6 is inside the window, re-read and re-written for nothing


def test_key_sweep_removes_hard_deleted_rows_and_counts_the_key_scan():
    c = master()
    s, e, _ = loaded(c, "lookback3_sweep")
    hard(c, 3)
    st = again(s, e)
    assert diff(e)["ghost"] == 0 and st.scanned >= 4 and st.written >= 1


def test_hashdiff_merge_catches_silent_update_and_writes_only_changed_rows():
    c = master()
    s, e, _ = loaded(c, "merge_hashdiff")
    silent(c, 2)
    st = again(s, e)
    assert (st.scanned, st.written) == (5, 1) and diff(e) == ZERO
    assert again(s, e).written == 0


def test_merge_needs_explicit_delete_detection_for_hard_deletes():
    for name, ghosts in (("merge_hashdiff", 1), ("merge_hashdiff_del", 0)):
        c = master()
        s, e, _ = loaded(c, name)
        hard(c, 3)
        again(s, e)
        assert diff(e)["ghost"] == ghosts, name


def test_append_only_watermark_duplicates_an_updated_row():
    c = master()
    s, e, _ = loaded(c, "watermark_append")
    bump(c, 1, 1)
    again(s, e)
    assert diff(e)["dup"] == 1


def test_rerunning_the_same_window_is_safe_for_every_strategy_except_append():
    for s in STRATEGIES:
        c = master()
        _, e, _ = loaded(c, s.name)
        bump(c, 1, 1)
        put(c, 6, 1)
        again(s, e)
        assert rerun_safe(e, s.fn) is (s.name != "watermark_append"), s.name
