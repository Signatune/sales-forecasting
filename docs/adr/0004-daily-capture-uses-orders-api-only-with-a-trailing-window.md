# Daily capture pulls Orders API only, over a 3-day trailing window

The original backfill pulled both Toast Orders and Analytics data because
Analytics enforces a strict lookback-rate cap (~14 months of modifier-level
history per hour) that made Orders the practical way to reach 10 years of
history fast; `normalize.py` then preferred Analytics wherever it reached, on
the assumption it was the more authoritative source. On 2026-07-07 the
Orders-derived aggregation was reconciled live against Analytics
`quantitySold` and matched exactly (see `toast_orders.py`). Going forward
there's no need for two sources of the same numbers, and Analytics' rate caps
make it a worse fit for a job that runs every day indefinitely.

The daily job pulls only from the Orders API. Each run re-pulls a 3-day
trailing window of business dates — not just the newest one — and upserts by
`(Product, Date)`, so back-office corrections and voids Toast allows after a
business date closes are picked up automatically instead of relying on
someone noticing and re-running a backfill by hand.

## Consequences

If Toast's Orders-API counting semantics ever silently drift from Analytics,
there's no automatic cross-check going forward — a periodic manual
reconciliation would need to be reintroduced if that risk materializes.
`toast_client.py` (Analytics) stays in the repo for any future backfill or
reconciliation need but is no longer part of the daily path.
