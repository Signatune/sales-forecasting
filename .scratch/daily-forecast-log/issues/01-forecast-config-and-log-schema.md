# Postgres schema: `forecast_configs` and write-once `forecasts`

Status: done

## Parent

`.scratch/daily-forecast-log/PRD.md`

## What to build

Two new tables in `schema.sql`, following the file's existing conventions
(idempotent `IF NOT EXISTS` / `CREATE OR REPLACE`, RLS enabled with no policies).

- **`forecast_configs`** — one row per version of the whole configuration:
  - `version` bigint generated identity, primary key
  - `created_at` timestamptz default `now()`
  - `is_active` boolean
  - `config` jsonb — the config document `{ horizon_days, models, targets }`
- **`forecasts`** — the write-once Demand Forecast log:
  - `as_of` date
  - `config_version` bigint referencing `forecast_configs (version)`
  - `model` text
  - `target` text
  - `target_date` date
  - `forecast_quantity` double precision
  - `created_at` timestamptz default `now()`
  - primary key `(as_of, config_version, model, target, target_date)` — the key
    the write-once insert conflicts on

There is deliberately **no `lead` column**: lead is `target_date - as_of`, derived
at read time (ADR 0006).

## Acceptance criteria

- [X] Both tables are created by applying `schema.sql`, and re-applying it changes
      nothing (idempotent) — `test_apply_schema_is_idempotent` re-applies over the
      new tables and passes against a real Postgres
- [X] Both tables have RLS enabled with no policies, matching the rest of the
      schema (private; the Data API's `anon`/`authenticated` roles get no access)
      — asserted by `test_new_tables_have_rls_enabled_and_no_policies`
- [X] `forecasts`' primary key is `(as_of, config_version, model, target,
      target_date)` and `config_version` is a foreign key to `forecast_configs`
      — FK asserted by `test_forecast_rows_reference_a_config_version`, write-once
      key by `test_forecasts_write_once_conflicts_on_the_key`
- [X] The schema comments explain the write-once intent and why lead is not stored
- [X] `python db.py apply-schema` applies cleanly against a fresh database —
      `db.apply_schema` runs against the throwaway Postgres the DB tests boot

## Verification note

The DB-integration tests for the two new tables were added to `tests/test_db.py`
under the existing `TEST_DATABASE_URL`-gated pattern. They run by default against
a throwaway local Postgres (`pytest`, via the ephemeral-Postgres default in
`conftest.py`), and pass: `python -m pytest tests/test_db.py` → 20 passed.

## Blocked by

- (none)
