import duckdb

from incload.loads import STRATEGIES, Env, run
from incload.sim import DAY, DDL

ZERO = dict(missed=0, ghost=0, stale=0, dup=0)


def master(n=5):
    c = duckdb.connect()
    c.execute(DDL)
    for i in range(1, n + 1):
        put(c, i, 0, i * 10)
    return c


def put(c, id, day, off=0, amount=100, created=None, deleted=False):
    c.execute("INSERT INTO src VALUES (?,?,?,?,?,?,?)",
              [id, f"acct-{id:06d}", "active", amount, day if created is None else created, day * DAY + off, deleted])


def bump(c, id, day, off=0):  # ordinary update: new value and new updated_at
    c.execute("UPDATE src SET amount=amount+7, updated_at=? WHERE id=?", [day * DAY + off, id])


def silent(c, id):  # value changes, updated_at does not
    c.execute("UPDATE src SET amount=amount+7 WHERE id=?", [id])


def soft(c, id, day, off=0):
    c.execute("UPDATE src SET is_deleted=true, updated_at=? WHERE id=?", [day * DAY + off, id])


def hard(c, id):
    c.execute("DELETE FROM src WHERE id=?", [id])


def strat(name):
    return next(s for s in STRATEGIES if s.name == name)


def loaded(c, name):
    s = strat(name)
    e = Env(c, name, s.pk)
    return s, e, run(e, s.fn)


def again(s, e):
    return run(e, s.fn)
