# Batch the incremental Sales upsert helper

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0004-daily-capture-uses-orders-api-only-with-a-trailing-window.md`
`docs/adr/0005-canonical-sales-is-a-source-to-product-model.md`

## What to build

`db.upsert_sales` — the write path the daily capture (ticket 05) uses — calls
`cursor.executemany`, which is a round trip per row. The
`supabase-postgres-best-practices` skill (`references/data-batch-inserts.md`)
flags exactly this: individual statements carry per-statement overhead, and the
batched form is 10-50x faster. Ticket 03 already routes the one-time ~10-year
migration around this helper onto a dedicated COPY-into-staging bulk path; this
ticket fixes the helper that stays in the daily hot path.

Rewrite `upsert_sales` to write its rows in one batched statement rather than a
per-row loop, keeping everything else identical:

- **Same signature, same semantics.** It still takes the canonical Sales fact
  frame — `(date, restaurant, source_type, source_name, quantity)` per ADR 0005 —
  and still upserts with `ON CONFLICT (date, restaurant_guid, source_type,
  source_name) DO UPDATE SET quantity = EXCLUDED.quantity`
  (`references/data-upsert.md`) — a repeat write of a `(date, restaurant,
  source)` still replaces its quantity, which is what ADR 0004's 3-day trailing
  window depends on. Callers do not change.
- **One round trip, not N.** Use a single multi-row `INSERT ... VALUES (...),
  (...), ...` (psycopg's `executemany` does not collapse into one statement;
  build the batched statement, or use the driver's batch/`copy` helper). The
  daily job writes only a handful of days, so a single statement is plenty —
  no staging table needed here; that pattern belongs to ticket 03's bulk load.
- **Empty frame stays a no-op**, exactly as today.

This is a straight refactor: no behaviour changes, no numbers change. It exists
so the write path the scheduled job runs every day isn't the row-at-a-time
anti-pattern the skill calls out.

Demoable: the existing `upsert_sales` tests pass unchanged, and writing the same
`(date, restaurant, source)` twice with different quantities still reads back one
row carrying the second quantity — now in a single statement.

## Acceptance criteria

- [ ] `upsert_sales` writes its rows in a single batched statement, not a per-row `executemany` loop
- [ ] The upsert semantics are unchanged: `ON CONFLICT (date, restaurant_guid, source_type, source_name)` still replaces a repeated key's quantity, and an empty frame is still a no-op
- [ ] The function signature is unchanged and no caller is touched
- [ ] Existing `db` tests pass, including the replace-on-repeat case; a test asserts the batched write still upserts correctly

## Blocked by

- `02-postgres-schema.md`
