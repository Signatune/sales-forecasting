# Readers read Sales from Postgres

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`

## What to build

Flip the loader from ticket 01 so the Sales history comes from Postgres instead
of the parquet file. Postgres becomes the single source of truth: the forecast,
the backtest, the model comparison and the inspection page all now run off the
database, and they need database connection setup they did not need before.

Because ticket 03 proved the migrated data matches the parquet file, this ticket
has a sharp test — the numbers must not move. Any difference in the Demand
Forecast or the backtest metrics after the switch is a bug in the switch, not a
new result.

Watch the `date` column's dtype: commit `e527755` had to pin the Demand Forecast
date to nanosecond precision because pandas and parquet disagreed. Postgres will
hand back timestamps of its own, so the loader is responsible for producing the
same dtype the rest of the code already expects.

Demoable: with the parquet file moved out of the way, `forecast.py` and
`backtest.py` run against Postgres and produce the same output as before.

## Acceptance criteria

- [ ] The shared loader reads canonical Sales from Postgres
- [ ] `forecast.py`, `backtest.py`, `model_comparison.py` and `inspection_page.py` all run with no parquet Sales history present
- [ ] Outputs match a pre-switch run: same Demand Forecast, same Sales Forecast, same backtest metrics
- [ ] The `date` dtype the loader returns matches what downstream code expects; no dtype regression
- [ ] A missing or unreachable database fails with a clear message, not a confusing pandas error
- [ ] Tests cover the loader against a database rather than mocking it away entirely

## Blocked by

- `01-single-sales-history-loader.md`
- `03-migrate-existing-history.md`
