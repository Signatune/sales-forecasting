# Postgres schema: `report_configs`, and reading the reports due today

Status: done

## Resolution note

Built as planned: `report_configs` in `schema.sql` with the foreign key to
`forecast_configs (version)`, and `db.read_due_reports(conn, today)`.

Two things the plan did not anticipate.

**`array_length` is the wrong function for "non-empty".** The obvious spelling
of the constraint, `array_length(days_of_week, 1) >= 1`, returns NULL rather
than 0 for `{}` — so the comparison is NULL and the CHECK *accepts* the one
value it exists to reject. The integration test caught it on the first real run.
The constraint uses `cardinality`, and rejects a NULL member explicitly as well:
`{NULL}` passes a length check and an array-containment check on its own.

**The weekday conversion earned its own function.** `db.postgres_weekday` maps a
python date onto Postgres' `EXTRACT(DOW)` convention — the two are off by one
*and* wrap at different ends of the week — so the mapping is unit-tested without
a database rather than buried in the query.

Two extra readers landed here rather than in a ticket of their own, because they
belong to `db.py`'s reader family and tickets 02/05 cannot be wired without
them: `read_forecast_config(conn, version)` (the referenced document stamped
with `version`, `is_active` and `active_version` — what lets a refusal name the
version a superseded row should be repointed at) and
`read_latest_forecasts(conn, config_version)` (the newest origin's rows only;
the payload builder discards every older one, and the log grows by one origin a
day forever).

Also: every other suite's `TRUNCATE` had to list `report_configs` alongside
`forecast_configs`, since Postgres refuses to truncate a referenced table on its
own.

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
