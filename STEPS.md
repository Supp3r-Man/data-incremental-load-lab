# STEPS

**Part A** is what I did, in order. **Part B** is how you run it.

---

## Part A: steps taken

1. **Picked the question:** which incremental-load strategy gives the same table as a full reload, and what does each cost? Decided to answer it by measurement, against a ground truth, instead of by argument.
2. **Wrote the simulator** (`incload/sim.py`): seeded, fictional accounts, six kinds of daily change: new rows, ordinary updates, silent updates (value changes, `updated_at` does not), soft deletes (flag plus bump), hard deletes (row removed), late-arriving rows (stamped 1-6 days old).
3. **Wrote the strategies** (`incload/loads.py`) as small functions sharing one staging step and one upsert: full reload, watermark (upsert, plus an append-only variant for contrast), watermark with a lookback window, lookback plus a key anti-join sweep, and hash-diff merge with and without delete detection.
4. **Defined the ground truth** as the source table right now, and `diff()` as four counts against it: missed, stale, ghost, duplicate.
5. **Defined cost** as rows scanned and rows written per run, and kept the baselines fair: the watermark baseline upserts by key and advances from the data, not the clock, and the full reload runs in a transaction.
6. **Added atomicity:** `run()` wraps each load in a transaction with the watermark update inside it, with injectable crash points (`mid`, `end`) and an `atomic=False` switch to show the failure.
7. **Added partition overwrite** for backfills, partitioned on the immutable `created_day`.
8. **Ran the simulation** (`python -m incload demo`) and looked at the numbers before writing any seeded assertions, so the assertions describe what the data shows (orderings, zeros) rather than copying exact counts.
9. **Wrote 45 tests** in five files: simulator (6), strategy behaviour on tiny hand-built tables (16), atomicity (7), partition overwrite (6), seeded comparison with the ground truth (10).
10. **Mutation-checked** 8 behaviours with a script that copies the repo, applies one literal replace, and requires pytest to fail. All 8 killed on the first run.
11. **Wrote the README and this file**, then committed locally. Pushed to both accounts; CI passed on Python 3.11 and 3.13.

Problems and choices along the way:
- DuckDB's `executemany` rejects an empty list, so a simulator config with no late rows crashed; the first test run caught it and `_insert` now skips empty batches.
- My first test file had 49 tests; I cut redundant ones (a duplicate late-row check, an ids-unique check) to stay within 45 rather than pad.
- The first crash demo used the same crash point for both strategies, which made the non-transactional watermark load look identical to the transactional one; I moved its crash to after the writes, before the watermark moves.
- The key sweep scans more rows than a full reload when keys are counted as rows. I kept that number and said so in the README instead of switching to a metric that flatters it.
- `-x` in the mutation script reports the first failing test, not how many tests catch each break.

---

## Part B: steps for you

1. Needs Python 3.11 or newer.
2. From the repo folder: `python -m pip install duckdb pytest`
3. Tests: `python -m pytest` (45 tests, about 10 seconds).
4. Measured tables: `python -m incload demo`. Try `--seed 3 --days 30`; the ordering should hold, the exact counts will change.
5. To see a strategy break: in `incload/loads.py` change `def run(env, fn, atomic=True` to `atomic=False` and run the tests; the atomicity tests fail.
6. To add a strategy: write `fn(env, ctx) -> Stats`, add a `Strategy(...)` line to `STRATEGIES`, and it appears in the demo table and the rerun-safety and crash tests.
