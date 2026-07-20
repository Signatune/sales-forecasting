# `db.py`: read the active config, write forecasts write-once

Status: done — integration tests unrun (see the resolution note)

## Resolution note

Built as `read_active_config` / `insert_forecasts` (plus the pure `forecast_rows`
half) in `db.py`, following the `sales_rows` / `upsert_sales` pair shape.

Two judgement calls worth recording:

- **`read_active_config` returns one document, not a `(version, config)` pair.**
  The row's `version` is stamped into the returned document under `"version"`,
  because that is exactly what `run_forecasts` reads (`config["version"]`), so
  the reader hands the engine something it can take directly rather than making
  every caller reassemble the two halves. The row's version wins over any
  `"version"` the stored document happens to carry.
- **`db.FORECAST_COLUMNS` re-spells the log's columns rather than importing
  `forecast_engine.LOG_COLUMNS`,** keeping `db.py` free of the engine; a unit
  test pins the two lists together so a rename on either side fails loudly
  instead of breaking the daily job silently.

**The acceptance criteria are covered by tests that have not been executed.**
The nine new `TestAgainstPostgres` tests skipped on the machine this was built
on: `pgserver` publishes no Windows wheel and there was no local Postgres or
Docker available, and the repo has no CI workflow that runs the suite. They
should run — and the round-trip, no-overwrite and new-config-version criteria be
confirmed — the first time the suite runs with `TEST_DATABASE_URL` pointed at a
throwaway Postgres (the planned Docker conversion would do it). The unit layer
(`TestForecastRows`, including the column-pinning check) did run and passes.

## Parent

`.scratch/daily-forecast-log/PRD.md`

## What to build

Two thin functions in `db.py`, matching the module's existing style:

- **`read_active_config(conn)`** — return the active configuration (its `version`
  and `config` document) from `forecast_configs`. Define "active" precisely
  (e.g. `is_active = true`, and if that can match more than one, the newest
  `version` wins); raise a clear error if there is no active config.
- **A write-once forecasts writer** — insert the rows `run_forecasts` produces with
  `ON CONFLICT (as_of, config_version, model, target, target_date) DO NOTHING`, so
  a same-morning retry fills gaps without overwriting and a repeat under a new
  config version adds rows rather than clobbering. Follow the `upsert_sales` /
  `sales_rows` shape (a pure frame-to-rows helper plus the write), committing like
  the other writers.

## Acceptance criteria

- [ ] `read_active_config` round-trips a config document and returns its version;
      raises clearly when no active config exists
- [ ] The writer inserts new rows and returns how many were written
- [ ] A second write of the same `(as_of, config_version, model, target,
      target_date)` key does **not** overwrite the existing `forecast_quantity`
- [ ] A write of the same forecast under a **different** `config_version` inserts a
      new row (no clobber)
- [ ] The frame-to-rows mapping is a pure, separately-tested helper
- [ ] Tests use the ephemeral-Postgres pattern already in `tests/test_db.py`; they
      do not require `statsmodels`

## Blocked by

- `01-forecast-config-and-log-schema.md`
