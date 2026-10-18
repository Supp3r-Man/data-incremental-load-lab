import pytest

from incload.bench import simulate


@pytest.fixture(scope="module")
def sm():
    return simulate(seed=7, days=20).summary()


def bad(r):
    return r["missed"] + r["stale"] + r["ghost"] + r["dup"]


def test_full_reload_equals_ground_truth(sm):
    assert bad(sm["full_reload"]) == 0


def test_hashdiff_merge_with_delete_detection_equals_ground_truth(sm):
    assert bad(sm["merge_hashdiff_del"]) == 0


def test_plain_watermark_misses_late_rows_hard_deletes_and_silent_updates(sm):
    r = sm["watermark"]
    assert r["missed"] > 0 and r["ghost"] > 0 and r["stale"] > 0 and r["dup"] == 0


def test_lookback_recovers_most_late_rows_but_not_all_and_not_deletes(sm):
    assert 0 < sm["watermark_lookback3"]["missed"] < sm["watermark"]["missed"] / 2
    assert sm["watermark_lookback3"]["ghost"] > 0


def test_lookback_plus_key_sweep_fixes_deletes_but_still_misses_late_and_silent(sm):
    r = sm["lookback3_sweep"]
    assert r["ghost"] == 0 and r["missed"] > 0 and r["stale"] > 0


def test_hashdiff_without_delete_detection_only_fails_on_hard_deletes(sm):
    r = sm["merge_hashdiff"]
    assert (r["missed"], r["stale"], r["dup"]) == (0, 0, 0) and r["ghost"] > 0


def test_append_only_watermark_duplicates_and_is_not_rerun_safe(sm):
    assert sm["watermark_append"]["dup"] > 0 and not sm["watermark_append"]["rerun_safe"]
    assert all(r["rerun_safe"] for n, r in sm.items() if n != "watermark_append")


def test_cost_watermark_is_cheap_hashdiff_scans_everything_but_writes_little(sm):
    full, wm, mg = sm["full_reload"], sm["watermark"], sm["merge_hashdiff"]
    assert wm["scanned_day"] < full["scanned_day"] / 5
    assert mg["scanned_day"] == full["scanned_day"] and mg["written_day"] < full["written_day"] / 5
    assert sm["watermark_lookback3"]["written_day"] > wm["written_day"]


@pytest.mark.parametrize("seed", [1, 2])
def test_ordering_holds_across_seeds(seed):
    sm = simulate(seed=seed, days=10, n0=300).summary()
    assert bad(sm["full_reload"]) == 0 and bad(sm["merge_hashdiff_del"]) == 0
    assert bad(sm["watermark"]) > bad(sm["lookback3_sweep"]) >= 0 and sm["watermark"]["ghost"] > 0
