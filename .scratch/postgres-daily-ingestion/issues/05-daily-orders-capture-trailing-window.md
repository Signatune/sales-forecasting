# Daily capture: Orders only, upsert

Status: done
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0004-daily-capture-uses-orders-api-only-with-a-trailing-window.md`
`docs/adr/0005-canonical-sales-is-a-source-to-product-model.md`

## What to build

One command that captures a day's Sales end to end: pull the Toast Orders API for
both in-scope restaurants, store the raw responses as `jsonb`, normalize them to
the canonical Sales fact, and upsert. Capture and normalization happen in the
same run — a GitHub Actions runner is ephemeral, so there is no half-finished
state to hand to a second step later.

Writes go to the ADR 0005 `sales` fact at `(date, restaurant, source_type,
source_name, quantity)` grain — every configured modifier the day sold, not just
the mapped bagels — via the batched `upsert_sales` helper (ticket 08). The
`product_sales` view rolls those up to Products for the readers; this job does
not touch the mapping.

**Orders only.** The Orders-derived aggregation was reconciled live against
Analytics `quantitySold` on 2026-07-07 and matched exactly, so there is no reason
to pull the same numbers twice — and Analytics' lookback-rate cap makes it a poor
fit for a job that runs every day forever. `toast_client.py` is not on this path.

Note that raw orders carry guest PII, so orders are not stored as-pulled; they
are aggregated to per-day modifier quantity rows first, exactly as
`toast_orders.py` does today.

Demoable: run it, see the last three business dates' Sales in Postgres. Run it
again — nothing changes. Hand-corrupt one day's quantity in the database, run it
again, and the day is corrected back from Toast.

## Acceptance criteria

- [x] One command pulls Orders for today and the last complete business date across both in-scope restaurants, normalizes, and writes to Postgres — `python daily_capture.py`, over ADR 0004's 3-day trailing window
- [x] Raw responses are stored as `jsonb`, aggregated so no guest PII is persisted
- [x] Sales are upserted into the fact by `(date, restaurant, source_type, source_name)`: a second run is a no-op, and a changed quantity in Toast overwrites the stored one
- [x] The Analytics API is not called
- [x] The existing normalization rules still hold: only configured modifiers (those with a `modifierGuid`) count, unmapped bagel-looking modifiers are surfaced loudly, out-of-scope restaurants are excluded
- [x] A Toast or database failure exits non-zero with a clear message and leaves the stored history untouched
- [x] Tests cover the corrected records after re-checking the same day after a modification, the upsert-corrects-a-changed-day case, and the failure path

## Blocked by

- `02-postgres-schema.md`
- `08-batch-the-sales-upsert-helper.md` — the daily capture's write path; land the batched helper first so this is built on it, not on the row-at-a-time version
