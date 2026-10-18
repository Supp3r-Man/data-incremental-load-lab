"""Seeded synthetic source table that changes every day. All data is fictional."""
import random

import duckdb

DAY = 86400  # updated_at is an integer number of seconds; day d starts at d * DAY
COLS = ["name", "status", "amount", "created_day", "updated_at", "is_deleted"]
DDL = ("CREATE TABLE src(id INTEGER PRIMARY KEY, name VARCHAR, status VARCHAR, amount INTEGER, "
       "created_day INTEGER, updated_at BIGINT, is_deleted BOOLEAN)")
STATUSES = ["active", "paused", "trial"]
RATES = dict(ins=60, upd=40, silent=10, soft=8, hard=8, late=15)


class Sim:
    """Day 0 creates n0 rows. Every later day applies: new rows, ordinary updates (updated_at bumped),
    silent updates (a buggy writer forgot to bump updated_at), soft deletes (flag + bump), hard deletes
    (row removed), and late-arriving rows (inserted today but stamped 1-6 days in the past)."""

    def __init__(self, seed=7, n0=1000, **rates):
        self.seed, self.rng = seed, random.Random(seed)
        self.con = duckdb.connect()
        self.con.execute(DDL)
        self.n0, self.next_id, self.log = n0, 1, []
        self.rates = {**RATES, **rates}

    def _row(self, created_day, ts):
        i = self.next_id
        self.next_id += 1
        return (i, f"acct-{i:06d}", self.rng.choice(STATUSES), self.rng.randint(100, 99999), created_day, ts, False)

    def _insert(self, rows):
        if rows:
            self.con.executemany("INSERT INTO src VALUES (?,?,?,?,?,?,?)", rows)

    def step(self, day):
        r, k, x = self.rng, self.rates, self.con.execute
        ev = dict(insert=0, update=0, silent=0, soft=0, hard=0, late=0)
        if day == 0:
            ev["insert"] = self.n0
            self._insert([self._row(0, r.randint(0, DAY - 1)) for _ in range(self.n0)])
            self.log.append(ev)
            return ev
        pool = [i for (i,) in x("SELECT id FROM src WHERE NOT is_deleted ORDER BY id").fetchall()]
        r.shuffle(pool)

        def take(n):
            return [pool.pop() for _ in range(min(n, len(pool)))]

        ts = lambda: day * DAY + r.randint(0, DAY - 1)
        for i in take(k["upd"]):
            x("UPDATE src SET status=?, amount=amount+?, updated_at=? WHERE id=?", [r.choice(STATUSES), r.randint(1, 50), ts(), i])
            ev["update"] += 1
        for i in take(k["silent"]):
            x("UPDATE src SET amount=amount+? WHERE id=?", [r.randint(1, 50), i])
            ev["silent"] += 1
        for i in take(k["soft"]):
            x("UPDATE src SET is_deleted=true, updated_at=? WHERE id=?", [ts(), i])
            ev["soft"] += 1
        for i in take(k["hard"]):
            x("DELETE FROM src WHERE id=?", [i])
            ev["hard"] += 1
        self._insert([self._row(day, ts()) for _ in range(k["ins"])])
        ev["insert"] = k["ins"]
        late = []
        for _ in range(k["late"]):
            lag = r.choice([1, 1, 2, 2]) if r.random() < 0.8 else r.randint(4, 6)
            cd = day - min(lag, day)
            late.append(self._row(cd, cd * DAY + r.randint(0, DAY - 1)))
        self._insert(late)
        ev["late"] = len(late)
        self.log.append(ev)
        return ev
