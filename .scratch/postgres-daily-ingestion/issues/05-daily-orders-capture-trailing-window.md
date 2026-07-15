# Daily capture: Orders only, 3-day trailing window, upsert

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0004-daily-capture-uses-orders-api-only-with-a-trailing-window.md`

## What to build

One command that captures a day's Sales end to end: pull the Toast Orders API for
both in-scope restaurants, store the raw responses as `jsonb`, normalize them to
canonical Sales, and upsert. Capture and normalization happen in the same run —
a GitHub Actions runner is ephemeral, so there is no half-finished state to hand
to a second step later.

Two decisions from ADR 0004 shape it:

**Orders only.** The Orders-derived aggregation was reconciled live against
Analytics `quantitySold` on 2026-07-07 and matched exactly, so there is no reason
to pull the same numbers twice — and Analytics' lookback-rate cap makes it a poor
fit for a job that runs every day forever. `toast_client.py` is not on this path.

**A 3-day trailing window, not just yesterday.** Toast lets back-office
corrections and voids land after a business date has closed. Re-pulling the last
three business dates every run and upserting by `(Product, Date)` means those
corrections are picked up on their own, instead of depending on someone noticing
and re-running a backfill by hand. This is the whole reason the window exists —
if the job only ever wrote new days, a correction would sit uncaught forever.

Note that raw orders carry guest PII, so orders are not stored as-pulled; they
are aggregated to per-day modifier quantity rows first, exactly as
`toast_orders.py` does today.

Demoable: run it, see the last three business dates' Sales in Postgres. Run it
again — nothing changes. Hand-corrupt one day's quantity in the database, run it
again, and the day is corrected back from Toast.

## Acceptance criteria

- [ ] One command pulls Orders for the trailing 3 business dates across both in-scope restaurants, normalizes, and writes to Postgres
- [ ] Raw responses are stored as `jsonb`, aggregated so no guest PII is persisted
- [ ] Sales are upserted by `(Product, Date)`: a second run is a no-op, and a changed quantity in Toast overwrites the stored one
- [ ] The Analytics API is not called
- [ ] The existing normalization rules still hold: only configured modifiers (those with a `modifierGuid`) count, unmapped bagel-looking modifiers are surfaced loudly, out-of-scope restaurants are excluded
- [ ] A Toast or database failure exits non-zero with a clear message and leaves the stored history untouched
- [ ] Tests cover the trailing window, the upsert-corrects-a-changed-day case, and the failure path

## Blocked by

- `02-postgres-schema.md`
