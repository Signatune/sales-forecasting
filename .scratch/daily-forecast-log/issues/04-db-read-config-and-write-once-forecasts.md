# `db.py`: read the active config, write forecasts write-once

Status: ready-for-agent

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
