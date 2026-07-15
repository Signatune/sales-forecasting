# Migrate the existing history into Postgres

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`
`docs/adr/0005-canonical-sales-is-a-source-to-product-model.md`

## What to build

The history already pulled from Toast lives in `data/raw/*.json` and
`data/sales_history.parquet`. ADR 0003 says it moves into Postgres once, by hand,
reusing the rate-limit cost already paid rather than re-pulling from Toast — the
Analytics API's lookback cap makes a re-pull expensive, and there is no reason to
spend it twice.

A one-time migration loads, into the ADR 0005 schema:

- the saved raw responses into the raw `jsonb` table,
- the `products` and `product_sources` mapping, seeded from `normalize.py`'s
  `BAGEL_MODIFIER_NAMES`, and
- the canonical Sales fact — one row per `(date, restaurant, source_type,
  source_name, quantity)` — for **every configured modifier** in the history, not
  just the seven mapped bagels. `source_type` is `modifier` throughout; there is
  no item history to load (ADR 0005), items arrive later via the daily job.

It is run once by a person, not on a schedule, and re-running it must not
duplicate or corrupt what is already there.

The bar is that Postgres tells the same story the parquet file does — for the
seven bagel Products, the `product_sales` view must match
`sales_history.parquet` exactly: same Products, same dates, same quantities.

### Regenerate the parquet first — it is stale

`sales_history.parquet` was last written before the Orders-API backfill reached
back to 2016; it currently holds only 2024-03 onward (~4.5k rows), while
`normalize.py` over the raw files now produces the full 2016-07 → 2026-07 history
(~9.3k rows). Re-run normalization to regenerate the parquet from the full raw
history **before** comparing, so the parquet, the fact, and the view all tell the
same story. (Ticket 07 later untracks the parquet; until then the file-based
readers still use it, so it must be current.)

Demoable: after the migration, the `product_sales` view and a freshly regenerated
read of `sales_history.parquet` return identical Sales for the seven Products.

## How to load it — batch, don't drip

This is a bulk backfill of ~10 years across hundreds of raw files, so the
row-at-a-time path is the wrong tool. The daily helper (ticket 08) writes a
handful of days per run; the migration writes the whole fact at once. The
`supabase-postgres-best-practices` skill (`references/data-batch-inserts.md`)
flags the per-row path and measures 10-50x on the batched path. Follow it:

- **Canonical Sales fact — COPY into a staging table, then one upsert.**
  `ON CONFLICT` is still required for the "re-run changes nothing" criterion, but
  a per-row `INSERT ... ON CONFLICT` over the whole history is slow. Instead
  `COPY` all the fact rows into an unlogged/temp staging table in one stream,
  then run a single `INSERT INTO sales SELECT ... FROM staging ON CONFLICT
  (date, restaurant_guid, source_type, source_name) DO UPDATE SET quantity =
  EXCLUDED.quantity`. This keeps the atomic upsert semantics the daily job
  depends on (`references/data-upsert.md`) while paying one round trip instead of
  hundreds of thousands. Add a dedicated bulk helper for this; leave the daily
  `upsert_sales` for the daily job's small incremental writes.
- **Raw responses — batch the inserts too.** Shard each canonical-source file
  (`menu_week`, `orders_agg`) into one row per `(restaurant, business_date)` so
  the raw table matches how the daily job (ticket 05) stores captures and so a
  day can be re-normalized on its own. Don't `INSERT` one row per shard in its
  own transaction — COPY them or use multi-row `INSERT` batches (~1000
  rows/statement per the skill).
- **One transaction, so re-runs stay clean.** Wrap the load in a single
  transaction: a failure rolls back to nothing rather than leaving a half-loaded
  table that the "re-running changes nothing" criterion then has to reason about.
  Keep external work (reading/parsing files) outside the transaction so the write
  itself stays short (`references/lock-short-transactions.md`).

## Which raw files, and how they shard

Load the raw responses that actually back the canonical history — the
`menu_week` and `orders_agg` files `normalize.load_sales_rows()` reads (latest
capture per window). `daily_totals` (per-restaurant totals) and
`restaurants_information` are not canonical sources and are out of scope. Each
canonical file spans many business dates and both restaurants, so shard it into
one raw row per `(restaurant, business_date)`, capture time from the filename.

## Connection note

COPY and a single long-lived load session want the **Session pooler** connection
string (IPv4), not the transaction-mode pooler and not the IPv6-only direct host
— see the `DATABASE_URL` setup in `docs/postgres.md`. Transaction-mode pooling
breaks session-scoped features (temp tables, prepared statements) that a staged
COPY load relies on.

## Acceptance criteria

- [ ] `sales_history.parquet` is regenerated from the full raw history before the comparison, so it reflects 2016 → present rather than the stale 2024-onward slice
- [ ] Every canonical-source raw response (`menu_week`, `orders_agg`) is present in the raw table, sharded one row per `(restaurant, business_date)`, with capture time preserved
- [ ] `products` and `product_sources` are seeded so the mapping reproduces `BAGEL_MODIFIER_NAMES`
- [ ] The `sales` fact holds every configured modifier in the history at `(date, restaurant, source_type, source_name)` grain
- [ ] For the seven bagel Products, the `product_sales` view matches the regenerated `sales_history.parquet` exactly: same row count, same Product set, same date range, same quantities
- [ ] The fact is loaded via COPY-into-staging plus a single `ON CONFLICT` upsert, not a per-row loop; raw responses are inserted in batches, not one statement per shard
- [ ] Re-running the migration leaves the database unchanged rather than duplicating rows
- [ ] The comparison that proves the match is reproducible, not a one-off eyeballing
- [ ] Nothing in this ticket re-contacts the Toast API

## Blocked by

- `02-postgres-schema.md`
