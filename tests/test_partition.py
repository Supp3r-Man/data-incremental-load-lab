from functools import partial

import duckdb
import pytest
from helpers import ZERO, again, bump, hard, loaded, master, put

from incload.loads import Crash, diff, fingerprint, partition_overwrite, run


def po(lo, hi, col="created_day"):
    return partial(partition_overwrite, lo=lo, hi=hi, col=col)


def test_backfill_repairs_late_rows_inside_the_range():
    c = master()
    put(c, 6, 10)
    s, e, _ = loaded(c, "watermark")
    put(c, 7, 8)  # late: stamped day 8, below the watermark (day 10)
    again(s, e)
    assert diff(e)["missed"] == 1
    run(e, po(8, 8))
    assert diff(e) == ZERO


def test_backfill_removes_hard_deleted_rows_inside_the_range_only():
    c = master()
    put(c, 6, 3)
    s, e, _ = loaded(c, "watermark")
    hard(c, 3)  # created day 0
    hard(c, 6)  # created day 3
    run(e, po(3, 9))
    assert diff(e)["ghost"] == 1  # id 3 is outside the range and stays a ghost
    run(e, po(0, 0))
    assert diff(e)["ghost"] == 0


def test_backfill_is_idempotent():
    c = master()
    s, e, _ = loaded(c, "watermark")
    bump(c, 1, 1)
    first = run(e, po(0, 0))
    fp = fingerprint(e)
    second = run(e, po(0, 0))
    assert fingerprint(e) == fp and first == second and diff(e) == ZERO


def test_backfill_scans_and_writes_only_the_partition():
    c = master()
    put(c, 6, 4)
    s, e, _ = loaded(c, "watermark")
    st = run(e, po(4, 4))
    assert (st.scanned, st.written) == (1, 1)


def test_partitioning_by_a_mutable_column_hits_the_primary_key_and_rolls_back():
    c = master()
    s, e, _ = loaded(c, "watermark")
    bump(c, 1, 5)  # source row moved to day 5; target still holds the day-0 copy
    fp = fingerprint(e)
    with pytest.raises(duckdb.Error):
        run(e, po(5, 5, col="updated_day"))
    assert fingerprint(e) == fp
    run(e, po(0, 0))  # the immutable key repairs it
    assert diff(e) == ZERO


def test_crash_during_backfill_keeps_the_old_partition():
    c = master()
    s, e, _ = loaded(c, "watermark")
    fp = fingerprint(e)
    with pytest.raises(Crash):
        run(e, po(0, 0), crash_at="mid")
    assert fingerprint(e) == fp
