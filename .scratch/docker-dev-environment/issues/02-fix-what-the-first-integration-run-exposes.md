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

**Status:** ready-for-agent

- [ ] Every failure from the first run is triaged into one of: test bug, db
      module bug, or genuine pipeline bug
- [ ] Test bugs and db module bugs are fixed
- [ ] Genuine pipeline bugs are filed as separate tickets and referenced here,
      with the affected tests left failing or explicitly and visibly marked —
      never silently skipped
- [ ] The full suite passes inside the container
- [ ] The integration tests are still confirmed executed, not skipped, in that
      green run
- [ ] The triage is written up in this ticket: what failed, why, and what was
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
