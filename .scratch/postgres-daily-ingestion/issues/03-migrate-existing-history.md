# Migrate the existing history into Postgres

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`

## What to build

The roughly ten years of history already pulled from Toast lives in
`data/raw/*.json` and `data/sales_history.parquet`. ADR 0003 says it moves into
Postgres once, by hand, reusing the rate-limit cost already paid rather than
re-pulling from Toast — the Analytics API's lookback cap makes a re-pull
expensive, and there is no reason to spend it twice.

A one-time migration loads the saved raw responses into the raw `jsonb` table and
the canonical Sales rows into the Sales table. It is run once by a person, not on
a schedule, and re-running it must not duplicate or corrupt what is already
there.

The bar is that Postgres tells the same story the parquet file does. Not
"approximately" — the same Products, the same dates, the same quantities.

Demoable: after the migration, a query against Postgres and a read of
`sales_history.parquet` return identical Sales.

## Acceptance criteria

- [ ] Every raw response under `data/raw/` is present in the raw table, with its restaurant, business date and capture time preserved
- [ ] Canonical Sales in Postgres match `sales_history.parquet` exactly: same row count, same Product set, same date range, same quantities
- [ ] Re-running the migration leaves the database unchanged rather than duplicating rows
- [ ] The comparison that proves the match is reproducible, not a one-off eyeballing
- [ ] Nothing in this ticket re-contacts the Toast API

## Blocked by

- `02-postgres-schema.md`
