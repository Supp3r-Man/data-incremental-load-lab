"""Seeded multi-day simulation: run every strategy against the same changing source and score it."""
from .loads import STRATEGIES, Crash, Env, diff, partition_overwrite, rerun_safe, run
from .sim import Sim


class Result:
    def __init__(self, sim, envs, cost, strategies, days):
        self.sim, self.envs, self.cost, self.strategies, self.days = sim, envs, cost, strategies, days
        self._summary = None

    def summary(self):
        if self._summary is None:
            out = {}
            for s in self.strategies:
                steady = self.cost[s.name][1:]  # day 0 is the initial load, excluded from per-day cost
                out[s.name] = dict(diff(self.envs[s.name]),
                                   scanned_day=sum(c.scanned for c in steady) / len(steady),
                                   written_day=sum(c.written for c in steady) / len(steady))
            for s in self.strategies:  # after scoring: this replays the last window
                out[s.name]["rerun_safe"] = rerun_safe(self.envs[s.name], s.fn)
            self._summary = out
        return self._summary

    def events(self):
        return {k: sum(e[k] for e in self.sim.log) for k in self.sim.log[0]}


def simulate(seed=7, days=20, strategies=STRATEGIES, **sim_kw):
    sim = Sim(seed, **sim_kw)
    envs = {s.name: Env(sim.con, s.name, s.pk) for s in strategies}
    cost = {s.name: [] for s in strategies}
    for d in range(days + 1):
        sim.step(d)
        for s in strategies:
            cost[s.name].append(run(envs[s.name], s.fn))
    return Result(sim, envs, cost, strategies, days)


def crash_demo(seed=7):
    """Crash mid-load on day 3, with and without a transaction. Returns rows for printing."""
    rows = []
    for name, point in (("full_reload", "mid"), ("watermark", "end")):
        s = next(x for x in STRATEGIES if x.name == name)
        for atomic in (True, False):
            sim = Sim(seed, n0=200)
            e = Env(sim.con, f"{name}_{'tx' if atomic else 'notx'}", s.pk)
            for d in range(3):
                sim.step(d)
                run(e, s.fn)
            sim.step(3)
            n0, wm0 = e.x(f"SELECT count(*) FROM {e.t}"), e.wm()
            try:
                run(e, s.fn, atomic=atomic, crash_at=point)
            except Crash:
                pass
            n1, wm1 = e.x(f"SELECT count(*) FROM {e.t}"), e.wm()
            run(e, s.fn)
            rows.append((name, "yes" if atomic else "no", n0, n1, wm0 == wm1, e.x(f"SELECT count(*) FROM {e.t}")))
    return rows


def backfill_demo(res, window=7):
    """Partition-overwrite (by immutable created_day) on the final state of two strategies."""
    out = []
    for name in ("watermark", "lookback3_sweep"):
        e = res.envs[name]
        for label, lo in (("last %d days" % window, res.days - window + 1), ("all days", 0)):
            before = sum(diff(e).values())
            st = run(e, lambda env, ctx, lo=lo: partition_overwrite(env, ctx, lo, res.days))
            out.append((name, label, before, sum(diff(e).values()), st.scanned, st.written))
    return out


def render(res):
    sm, ev, L = res.summary(), res.events(), []
    L.append(f"seed={res.sim.seed} days={res.days} initial_rows={res.sim.n0} per-day {res.sim.rates}")
    L.append("source events over the run: " + ", ".join(f"{k}={v}" for k, v in ev.items()))
    L.append(f"final source rows: {res.sim.con.execute('SELECT count(*) FROM src').fetchone()[0]}")
    L.append("")
    L.append(f"{'strategy':<21}{'missed':>7}{'stale':>7}{'ghost':>7}{'dup':>6}{'scan/day':>10}{'write/day':>10}  rerun-safe")
    for s in res.strategies:
        r = sm[s.name]
        L.append(f"{s.name:<21}{r['missed']:>7}{r['stale']:>7}{r['ghost']:>7}{r['dup']:>6}"
                 f"{r['scanned_day']:>10.0f}{r['written_day']:>10.0f}  {'yes' if r['rerun_safe'] else 'NO'}")
    L.append("missed = in source, absent from target | stale = in both, values differ | ghost = hard-deleted in source, still in target")
    L.append("dup = extra copies of a key | scan/write per day are means over days 1..N (day 0 is the initial load)")
    L.append("")
    L.append("crash mid-load on day 3: full_reload after its delete; watermark after its writes, before the watermark moves")
    L.append(f"{'strategy':<13}{'in a tx':>8}{'rows before':>12}{'rows after crash':>17}{'watermark kept':>15}{'rows after rerun':>17}")
    for n, a, b, c, w, d in crash_demo(res.sim.seed):
        L.append(f"{n:<13}{a:>8}{b:>12}{c:>17}{str(w):>15}{d:>17}")
    L.append("")
    L.append("backfill by partition overwrite (created_day), applied to the final state")
    L.append(f"{'strategy':<17}{'range':<13}{'bad before':>11}{'bad after':>10}{'scanned':>9}{'written':>9}")
    for n, lab, b, a, sc, w in backfill_demo(res):
        L.append(f"{n:<17}{lab:<13}{b:>11}{a:>10}{sc:>9}{w:>9}")
    return "\n".join(L)
