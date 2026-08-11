# Selection-grain capture, with PII excluded by an allowlist enforced at one extraction boundary

`daily_capture.py`'s guarantee against guest PII was, until now, a side
effect of aggregation: full Toast orders were reduced in memory to per-day
modifier-name quantities, and only that aggregate was ever written anywhere.
Menu engineering needs two things that aggregation destroys — the link from a
Modifier to the parent selection it rode on, and the selection's price — so
the grain has to get finer. That removes the free PII guarantee aggregation
was providing, and it has to be rebuilt deliberately.

## Decision

**Grain.** A new `selections` table, one row per Toast selection (order
line), with its Modifiers nested as `jsonb` — recursively, since
`toast_orders.py`'s `_count_modifiers` already recurses to arbitrary depth.
Modifiers are never queried independently of the selection they rode on for
any menu-engineering computation (Realized Cost is computed one selection at
a time), so there is no query pattern a relational `selection_modifiers`
table would serve better, and reconstructing a tree from a normalized table
would cost a recursive CTE for no benefit. `sales`/`product_sales` are
expected to become derived from `selections` rather than written
independently (ticket 04 decides the mechanics).

**Price.** Each selection keeps `price` (final, post-discount) and
`preDiscountPrice` (gross, pre-discount) — together these distinguish a
comped item (`price = 0`, `preDiscountPrice > 0`) from a genuinely cheap one
without a separate flag. `tax` is not captured; it plays no role in a
Kasavana & Smith contribution-margin calculation. Voided selections are
excluded entirely, matching `toast_orders.aggregate_modifier_rows`'s existing
treatment of voided orders/checks/selections.

**The PII rule, by construction.** A single extraction function — a sibling
to `aggregate_modifier_rows`, not a modification of it — is the only code
allowed to see a raw Toast order. Everything downstream (`daily_capture.py`,
`db.py`) only ever sees that function's output. It returns, per selection:
`restaurant_guid`, `business_date`, `order_guid`, `selection_guid`,
`source_name`, `quantity`, `price`, `preDiscountPrice`, and nested
`modifiers` (each: `guid`, `displayName`, `quantity`, nested `modifiers`).
Nothing else — no `customer`, `deliveryInfo`, `curbsidePickupInfo`, employee,
or table/guest-count field is ever read out of the raw order. This is an
allowlist, not a denylist: a field is only ever captured because it is named
here, so a new field Toast adds later is excluded by default rather than
leaking silently. Any selection or modifier whose item carries no real Toast
GUID is dropped rather than partially captured — the same rule
`normalize.is_configured_modifier` already applies to modifiers, extended to
selections, since an "open item" with no configured GUID is the one place
free text a guest or server typed could otherwise ride in as the "name."

**Volume.** At an estimated ~450 selections/day across both restaurants
(~250 orders/day, ~1.8 items/order), the new grain runs roughly 90–130 MB per
year of history — enough to matter against Supabase Free tier's 500 MB
ceiling, especially if backfill (ticket 07) reaches back years. Rather than
bound `selections` itself, `raw_toast_responses`'s retention is bounded to a
short rolling window (roughly ADR 0004's 3-day trailing re-pull, plus some
debugging slack) instead of growing forever: `selections` now serves the
replay-without-re-pulling-Toast role `raw_toast_responses` existed for, so
keeping both unboundedly would be redundant. `selections` itself stays
unbounded for now; if actual measured volume (tickets 06, 07) makes Free tier
too small, the choice is between capping backfill depth or moving to a paid
tier — deferred until real numbers exist.

## Consequences

`daily_capture.py`'s docstring claim that "no guest PII is persisted"
because "each day is aggregated in memory" no longer describes how the
guarantee is enforced — this ADR supersedes that mechanism while keeping the
same guarantee, now enforced by an explicit field allowlist at one function
boundary instead of by aggregation. ADR 0004's trailing-window re-pull and
upsert semantics are expected to carry over unchanged onto the new grain
(ticket 05 implements this and must preserve it explicitly, since the
primary key changes shape).

Bounding `raw_toast_responses`'s retention means the pre-ticket-03 replay
safety net (rerun normalization against a saved response without re-hitting
Toast) stops covering the full history and instead covers only the rolling
window; `selections` is now the thing that plays that role for
anything older.
