"""Load strategies over DuckDB. Every strategy is fn(env, ctx) -> Stats and runs inside run()'s transaction.

Cost model: scanned = source rows pulled (plus keys listed for a delete sweep); written = target rows
inserted or updated (plus rows removed by explicit delete detection). A truncate / partition drop is free.
"""
from dataclasses import dataclass
from functools import partial

from .sim import COLS, DAY

TGT_DDL = ("CREATE TABLE {t}(id INTEGER{pk}, name VARCHAR, status VARCHAR, amount INTEGER, created_day INTEGER, "
           "updated_at BIGINT, is_deleted BOOLEAN, row_hash VARCHAR)")
HASH = "md5(concat_ws('|', " + ", ".join(COLS) + "))"


class Crash(RuntimeError):
    """Injected failure, used to test atomicity."""


class Ctx:
    def __init__(self, crash_at=None):
        self.crash_at = crash_at

    def point(self, name):
        if name == self.crash_at:
            raise Crash(name)


@dataclass
class Stats:
    scanned: int = 0
    written: int = 0


class Env:
    """One target table (schema.tgt) plus a one-row watermark table (schema.meta), on its own connection."""

    def __init__(self, master, name, pk=True):
        self.con, self.s = master.cursor(), name
        self.t, self.m, self.prev_wm = f"{name}.tgt", f"{name}.meta", -1
        self.con.execute(f"CREATE SCHEMA {name}")
        self.con.execute(TGT_DDL.format(t=self.t, pk=" PRIMARY KEY" if pk else ""))
        self.con.execute(f"CREATE TABLE {self.m}(wm BIGINT)")
        self.con.execute(f"INSERT INTO {self.m} VALUES (-1)")

    def x(self, sql):
        return self.con.execute(sql).fetchone()[0]

    def wm(self):
        return self.x(f"SELECT wm FROM {self.m}")


def _stage(env, where="TRUE"):
    env.con.execute(f"CREATE OR REPLACE TEMP TABLE stage AS SELECT *, {HASH} AS row_hash FROM src WHERE {where}")
    return env.x("SELECT count(*) FROM stage")


def _upsert(env, ctx, changed_only):
    sets = ", ".join(f"{c}=s.{c}" for c in COLS + ["row_hash"])
    cond = " AND t.row_hash<>s.row_hash" if changed_only else ""
    u = env.x(f"UPDATE {env.t} AS t SET {sets} FROM stage s WHERE t.id=s.id{cond}")
    ctx.point("mid")
    i = env.x(f"INSERT INTO {env.t} SELECT * FROM stage s WHERE NOT EXISTS (SELECT 1 FROM {env.t} t WHERE t.id=s.id)")
    return u + i


def _delete_missing(env):
    return env.x(f"DELETE FROM {env.t} WHERE id NOT IN (SELECT id FROM src)")


def _advance(env):
    env.con.execute(f"UPDATE {env.m} SET wm = greatest(wm, coalesce((SELECT max(updated_at) FROM stage), wm))")


def full_reload(env, ctx):
    n = _stage(env)
    env.x(f"DELETE FROM {env.t}")
    ctx.point("mid")
    w = env.x(f"INSERT INTO {env.t} SELECT * FROM stage")
    ctx.point("end")
    return Stats(n, w)


def incremental(env, ctx, lookback=0, sweep=False, append=False):
    """Watermark load: pull updated_at > (high watermark - lookback days), upsert by key (or append)."""
    n = _stage(env, f"updated_at > {env.wm() - lookback * DAY}")
    if append:
        ctx.point("mid")
        w = env.x(f"INSERT INTO {env.t} SELECT * FROM stage")
    else:
        w = _upsert(env, ctx, changed_only=False)
    if sweep:
        n += env.x("SELECT count(*) FROM src")
        w += _delete_missing(env)
    ctx.point("end")
    _advance(env)
    return Stats(n, w)


def merge_hashdiff(env, ctx, detect_deletes=False):
    """Read the whole source, write only rows that are new or whose row hash differs."""
    n = _stage(env)
    w = _upsert(env, ctx, changed_only=True)
    if detect_deletes:
        w += _delete_missing(env)
    ctx.point("end")
    return Stats(n, w)


def partition_overwrite(env, ctx, lo, hi, col="created_day"):
    """Backfill: replace every target row whose partition value is in [lo, hi] with the source rows there."""
    expr = "created_day" if col == "created_day" else "(updated_at // 86400)"
    n = _stage(env, f"{expr} BETWEEN {lo} AND {hi}")
    env.x(f"DELETE FROM {env.t} WHERE {expr} BETWEEN {lo} AND {hi}")
    ctx.point("mid")
    w = env.x(f"INSERT INTO {env.t} SELECT * FROM stage")
    ctx.point("end")
    return Stats(n, w)


def run(env, fn, atomic=True, crash_at=None):
    """Run one load. atomic=True wraps it in one transaction (data and watermark commit together)."""
    c = env.con
    env.prev_wm = env.wm()
    if atomic:
        c.execute("BEGIN")
    try:
        st = fn(env, Ctx(crash_at))
        if atomic:
            c.execute("COMMIT")
        return st
    except BaseException:
        if atomic:
            c.execute("ROLLBACK")
        raise


def diff(env):
    """Compare the target with the source (ground truth = a full reload of the source right now)."""
    t, eq = env.t, " AND ".join(f"x.{c}=s.{c}" for c in COLS)
    return dict(
        missed=env.x(f"SELECT count(*) FROM src WHERE id NOT IN (SELECT id FROM {t})"),
        ghost=env.x(f"SELECT count(*) FROM {t} WHERE id NOT IN (SELECT id FROM src)"),
        stale=env.x(f"SELECT count(*) FROM src s WHERE id IN (SELECT id FROM {t}) AND NOT EXISTS "
                    f"(SELECT 1 FROM {t} x WHERE x.id=s.id AND {eq})"),
        dup=env.x(f"SELECT count(*) - count(DISTINCT id) FROM {t}"))


def fingerprint(env):
    cols = ", ".join(["id"] + COLS + ["row_hash"])
    return env.x(f"SELECT md5(string_agg(concat_ws('|', {cols}), ';' ORDER BY id, row_hash)) FROM {env.t}")


def rerun_safe(env, fn):
    """Replay the last window (restore the pre-run watermark) and check the target did not change."""
    before = fingerprint(env)
    env.con.execute(f"UPDATE {env.m} SET wm = {env.prev_wm}")
    run(env, fn)
    return fingerprint(env) == before


@dataclass
class Strategy:
    name: str
    fn: object
    pk: bool = True


STRATEGIES = [
    Strategy("full_reload", full_reload),
    Strategy("watermark_append", partial(incremental, append=True), pk=False),
    Strategy("watermark", incremental),
    Strategy("watermark_lookback3", partial(incremental, lookback=3)),
    Strategy("lookback3_sweep", partial(incremental, lookback=3, sweep=True)),
    Strategy("merge_hashdiff", merge_hashdiff),
    Strategy("merge_hashdiff_del", partial(merge_hashdiff, detect_deletes=True)),
]
