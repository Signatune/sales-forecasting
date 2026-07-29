# Postgres schema: `report_configs`, and reading the reports due today

Status: ready-for-agent

## Parent

`.scratch/scheduled-forecast-reports/PRD.md`

## What to build

One new table in `schema.sql`, following the file's existing conventions
(idempotent `IF NOT EXISTS`, RLS enabled with no policies), plus one read
function in `db.py`.

### `report_configs`

- `id` bigint generated identity, primary key
- `forecast_config_version` bigint **referencing `forecast_configs (version)`**
- `days_of_week` smallint[] — the weekdays this report fires on, `0 = Sunday` to
  match Postgres `EXTRACT(DOW)`
- `is_active` boolean, default false
- `created_at` timestamptz default `now()`
- `config` jsonb — `{ name, headline_model, target, varieties, delivery }`

The foreign key is the point of the table (ADR 0010): a report is defined by the
forecast configuration it reads, so a row can never reference a config that was
never used to forecast. Constrain `days_of_week` to be non-empty and its members
to `0..6` — an empty array is a report that silently never fires, which looks
exactly like one that is broken.

Unlike `forecast_configs`, this table is **edited in place**, not versioned. It
does not key anything: no logged row references a report. Rescheduling a report
is a change to that report, not a new report, and versioning it would accumulate
history nothing reads.

### `db.read_due_reports(conn, today)`

Returns the active reports whose `days_of_week` contains `today`'s weekday, as
plain dicts with the `config` document merged in alongside
`forecast_config_version`. Takes `today` as an argument rather than reading the
clock — the caller owns the restaurant-timezone date, exactly as
`daily_forecast.py` does.

## Tests

Under `tests/test_db.py`, alongside the existing `forecast_configs` tests:

- a report firing on today's weekday is returned; one firing only on other days
  is not
- an inactive report is never returned, whatever its days
- a report listing several weekdays is returned on each of them
- the `config` document round-trips, including `varieties` as a list
- inserting a row referencing a non-existent `forecast_config_version` fails
- an empty `days_of_week` is rejected by the constraint

## Seed row

Not part of this ticket's code, but the row this feature exists to serve:

```sql
INSERT INTO report_configs
  (forecast_config_version, days_of_week, is_active, config)
VALUES (1, '{6}', true, '{
  "name": "Bagel forecast",
  "headline_model": "ewma",
  "target": "wheat_bagels",
  "varieties": ["everything", "plain", "sesame"],
  "delivery": "bagel-team"
}'::jsonb);
```
