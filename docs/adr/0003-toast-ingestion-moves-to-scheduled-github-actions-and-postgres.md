# Move Toast ingestion to a scheduled GitHub Actions job writing to managed Postgres

`ingest.py` today is a manual, laptop-run command: a human types it, watches
the log, and it leaves behind timestamped raw JSON under `data/raw/` and a
`sales_history.parquet` that `normalize.py` fully rebuilds from all raw files
on every run. Getting each day's Sales automatically needs a trigger that
doesn't depend on a laptop being awake at the right time, and a store that
survives between runs — a GitHub Actions runner is ephemeral, so file-based
artifacts don't fit it without a separate persistence step of their own.

A GitHub Actions workflow on a daily cron schedule now performs both raw
capture and normalization in one run, writing directly to a managed Postgres
database: raw Toast responses as `jsonb` in one table (the replay/audit safety
net `data/raw/` used to serve — see ticket 01's `modifierGuid` bug, caught by
being able to rerun normalization against saved raw responses without
re-hitting Toast), and canonical `(Product, Date, Quantity)` Sales rows in
another. Postgres is the single source of truth. The existing history in
`data/raw/*.json` and `sales_history.parquet` is migrated into it once, by
hand, reusing the already-paid rate-limit cost rather than re-pulling from
Toast; after that migration, `data/raw/` is no longer tracked in the repo.

## Consequences

Downstream processing (`forecast.py`, `backtest.py`, `model_comparison.py`,
`inspection_page.py`) reads Sales from Postgres instead of a local parquet
file, and needs DB connection setup it didn't need before. Toast credentials
move from the local `.env` to GitHub Actions secrets.
