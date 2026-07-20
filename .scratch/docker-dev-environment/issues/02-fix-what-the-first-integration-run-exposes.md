# 02 — Fix what the first integration run exposes

**What to build:** The test suite is green inside the container — including the
DB integration tests that, until ticket 01, had never executed anywhere.

Because these tests have never run, the failures they produce may be in the
tests themselves or in the database code they exercise. Both are in scope to
fix. What is **not** in scope is making a failure go away without understanding
it:

- No loosening an assertion to match observed behavior
- No re-skipping a test that now finally runs
- No `xfail` used as a silencer

If the run reveals a genuine bug in pipeline behavior — forecasting, capture,
schema, or the Sales model — that is **reported and filed as its own ticket**,
not fixed inline. This effort changes what provisions the test database, not
what the pipeline does.

A larger-than-expected set of failures here is a sign this effort is working,
not a sign it went wrong. This ticket makes previously-invisible risk visible;
report what it finds plainly.

**Blocked by:** 01 — Compose environment that runs the suite against Postgres 17.

**Status:** done

- [x] Every failure from the first run is triaged into one of: test bug, db
      module bug, or genuine pipeline bug
- [x] Test bugs and db module bugs are fixed
- [x] Genuine pipeline bugs are filed as separate tickets and referenced here,
      with the affected tests left failing or explicitly and visibly marked —
      never silently skipped — *none found; no ticket filed*
- [x] The full suite passes inside the container
- [x] The integration tests are still confirmed executed, not skipped, in that
      green run
- [x] The triage is written up in this ticket: what failed, why, and what was
      done about it

## The first run (recorded by ticket 01, 2026-07-20)

`docker compose run --rm test pytest -v -rs`, against Postgres 17.10 in the
compose environment. Tally:

**246 collected — 236 passed, 8 skipped, 2 errors.**

The PRD's "205" predates the current suite; **246 is the re-baselined total.**

Seven of the eight skips are the `importorskip("statsmodels")` sites in
`test_models.py` and `test_forecast_engine.py`. The image installs `.[dev]`,
not `.[forecast]`, so this is the default-install total, which is the number
ticket 02 should be measured against. (An earlier build of the image included
`[forecast]` and scored 243 passed / 1 skipped — recorded here only so the
discrepancy isn't mistaken for a regression.)

The four `TestAgainstPostgres` classes were confirmed collected and executed —
not skipped — from the verbose per-test output, 37 tests in total:

| Module | TestAgainstPostgres tests | Outcome |
| --- | --- | --- |
| `tests/test_db.py` | 25 | all passed |
| `tests/test_daily_capture.py` | 6 | all passed |
| `tests/test_migrate.py` | 4 | 3 passed, 1 skipped |
| `tests/test_daily_forecast.py` | 2 | 2 errors |

That the DB integration tests overwhelmingly passed on their first-ever
execution is the notable result — 34 of 37 green.

### The migration skip is deliberate and not OS-related

`test_migrate.py::TestAgainstPostgres::test_full_history_view_matches_regenerated_parquet`
skipped with **"no raw history checked out"** — the pre-migration history is
gitignored and lives only on the machine that ran the migration. This is a
data-availability skip, not the `pgserver`-on-Windows silent skip this effort
exists to eliminate. **Nothing skipped for want of a database.**

### The two errors — one root cause, to triage

Both are errors **at fixture setup**, not assertion failures, so neither test
body ran:

- `test_daily_forecast.py::TestAgainstPostgres::test_logs_the_active_configs_forecasts`
- `test_daily_forecast.py::TestAgainstPostgres::test_a_rerun_the_same_morning_writes_nothing_new`

Both die on the same statement at `tests/test_daily_forecast.py:204`, which
inserts an explicit `version` into `forecast_configs`:

```
psycopg.errors.GeneratedAlways: cannot insert a non-DEFAULT value into
column "version"
DETAIL: Column "version" is an identity column defined as GENERATED ALWAYS.
```

`schema.sql` declares `forecast_configs.version` as `GENERATED ALWAYS AS
IDENTITY`; the fixture writes `CONFIG["version"]` into it directly. First read
is that this is a **test bug** — the fixture should let the identity column
assign the version and read it back, rather than dictating it — but confirming
that against ADR 0006's intent for `config_version` is ticket 02's job, not
ticket 01's. Note the sibling suite in `test_db.py` inserts configs without
this problem, which is the obvious place to compare against.

## Triage (2026-07-20)

**One failure, one root cause, one verdict: test bug.** Ticket 01's first read
was correct and ADR 0006 confirms it.

### What failed

Both `test_daily_forecast.py::TestAgainstPostgres` tests, at fixture setup, on
the same `INSERT` — `psycopg.errors.GeneratedAlways` on
`forecast_configs.version`.

### Why

`schema.sql:142` declares `version bigint GENERATED ALWAYS AS IDENTITY PRIMARY
KEY`. The fixture wrote `CONFIG["version"]` (a hard-coded `3`) into it, which
Postgres rejects outright.

The fixture was not merely using the wrong SQL — it was asserting a contract the
design does not have. `db.read_active_config` (`db.py:303-329`) stamps the
**row's** version over whatever the stored JSON document carries, and its
docstring says why: the row's value "is the identity `forecasts.config_version`
references." A config document's own `version` key is therefore never
authoritative; the database assigns it. The fixture dictating `3` inverted that.

Two further points confirm the verdict rather than resting on the schema alone:

- `test_db.py`'s `_insert_config` helper (`tests/test_db.py:278`) — the sibling
  suite ticket 01 pointed at — already inserts without `version` and reads the
  generated value back with `RETURNING version`. The correct pattern was
  already in the repo, in a suite covering the same table.
- The `TRUNCATE` in the fixture does not `RESTART IDENTITY`, so the sequence
  advances across tests. Even a fixture that guessed the first version right
  would break on the second test in the class. Hard-coding a version was never
  going to hold.

### What was done

`tests/test_daily_forecast.py` only — no production code touched, consistent
with "this effort changes what provisions the test database, not what the
pipeline does."

- The fixture inserts `(is_active, config)` and takes the assigned version from
  `RETURNING version`, matching `test_db.py:278`'s `_insert_config`. Reading it
  back in the same statement keeps the assertions off `read_active_config`, the
  reader under test, without a second query.
- `test_logs_the_active_configs_forecasts` asserts against that version instead
  of the literal `3`.

No assertion was loosened: the test still asserts the full `counts` dict and the
exact logged rows.

`CONFIG` still carries `"version": 3`, which the identity now overrides — so on
any run where the assigned version is not itself 3, a pass also demonstrates
that `read_active_config`'s override happens. That is a side benefit, not a
guarantee: since the fixture's `TRUNCATE` does not `RESTART IDENTITY`, the
sequence will pass through 3 on some runs, and the demonstration lapses for
those. Ticket 06's fixture consolidation is the place to make it deliberate if
it is worth pinning down.

### No pipeline bugs found

Nothing was filed. The single failure was in test code; the db module and the
pipeline behaved correctly throughout. The honest summary is that the database
code survived its first-ever execution intact — the one thing that broke was a
test that had never run.

### The green run

`docker compose run --rm test pytest -v -rs`, Postgres 17.10:

**246 collected — 238 passed, 8 skipped, 0 errors.** (Was 236/8/2.)

All 37 `TestAgainstPostgres` tests confirmed collected and executed from the
verbose per-test output, not inferred from the exit code:

| Module | Tests | Outcome |
| --- | --- | --- |
| `tests/test_db.py` | 25 | all passed |
| `tests/test_daily_capture.py` | 6 | all passed |
| `tests/test_migrate.py` | 4 | 3 passed, 1 skipped |
| `tests/test_daily_forecast.py` | 2 | **both now pass** |

The 8 skips are unchanged and both kinds are accounted for: 7
`importorskip("statsmodels")` sites (the image installs `.[dev]`, not
`.[forecast]`) and the `test_migrate.py` "no raw history checked out"
data-availability skip. **Nothing skipped for want of a database.**
