# Postgres schema: `forecast_configs` and write-once `forecasts`

Status: ready-for-agent

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

- [ ] Both tables are created by applying `schema.sql`, and re-applying it changes
      nothing (idempotent)
- [ ] Both tables have RLS enabled with no policies, matching the rest of the
      schema (private; the Data API's `anon`/`authenticated` roles get no access)
- [ ] `forecasts`' primary key is `(as_of, config_version, model, target,
      target_date)` and `config_version` is a foreign key to `forecast_configs`
- [ ] The schema comments explain the write-once intent and why lead is not stored
- [ ] `python db.py apply-schema` applies cleanly against a fresh database

## Blocked by

- (none)
