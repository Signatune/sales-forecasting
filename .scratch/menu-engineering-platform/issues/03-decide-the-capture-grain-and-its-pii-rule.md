# Decide the capture grain and its PII rule

Type: grilling
Status: resolved

## Question

What does `daily_capture.py` persist, once it has to support menu engineering?

Today it pulls full orders, aggregates them in memory to per-day
modifier-name rows, and saves only that (`daily_capture.py:18-20`) —
deliberately, so no guest PII is stored. That aggregation is exactly what
destroys the two things menu engineering needs: the link from a Modifier to the
parent selection it rode on, and any price at all.

Decide:

1. **The grain.** Per-selection (order line) seems necessary — a Modifier has to
   point at its parent selection to compute Realized Cost. Is it one row per
   selection with modifiers nested as `jsonb`, or a selections table plus a
   selection_modifiers table? Nested Modifiers exist
   (`toast_orders.py:_count_modifiers` recurses) and must survive.

2. **Price.** Toast selections carry price. Menu engineering needs revenue per
   item. Decide exactly which price fields are kept (gross, net, discounts,
   voids) and how discounts and comps are treated — a comped item at $0 is not
   the same as a cheap item.

3. **The PII rule, by construction.** Aggregation is currently what guarantees
   no guest data lands. At a per-selection grain that guarantee is gone and must
   be replaced by an explicit allowlist of fields — never a denylist. Decide the
   allowlist, and where it is enforced so it cannot be bypassed. Note also that
   an order line can carry free text a guest or server typed; `normalize.py`
   already excludes non-GUID free text and that exclusion likely still applies.

4. **Volume.** Per-selection rows for a deli across two locations over years is
   a much larger table than today's daily aggregate. Sanity-check the order of
   magnitude against what Supabase's plan tolerates, and decide whether raw
   retention is bounded.

This touches ADR 0004 (Orders-API-only trailing window — the 3-day re-pull and
upsert semantics must still hold at the new grain) and the PII stance recorded
in `daily_capture.py`. Leave an ADR.

## Answer

1. **Grain.** A new `selections` table: one row per Toast selection (order
   line), with Modifiers nested as `jsonb`, recursively (modifiers are always
   read in the context of the one selection they rode on, so a relational
   `selection_modifiers` table would serve no query this needs). `sales` /
   `product_sales` are expected to become *derived* from `selections` rather
   than written independently — the mechanics are ticket 04's decision, not
   settled here.

2. **Price.** Keep `price` (net, post-discount) and `preDiscountPrice`
   (gross), which together distinguish a comp (`price = 0`,
   `preDiscountPrice > 0`) from a genuinely cheap item with no separate flag.
   `tax` is not captured — out of scope for the contribution-margin
   calculation. Voided selections are excluded entirely, matching
   `toast_orders.aggregate_modifier_rows`'s existing treatment.

3. **PII allowlist, enforced at one extraction-function boundary** (a
   sibling to `aggregate_modifier_rows`; nothing downstream touches a raw
   Toast order). Allowlisted: `restaurant_guid`, `business_date`,
   `order_guid`, `selection_guid`, `source_name`, `quantity`, `price`,
   `preDiscountPrice`, and nested `modifiers` (`guid`, `displayName`,
   `quantity`, nested `modifiers`). No `customer`, `deliveryInfo`,
   `curbsidePickupInfo`, employee, or table/guest-count field is ever read.
   A selection or modifier with no real Toast-configured GUID is dropped,
   not partially captured (extends `normalize.is_configured_modifier`'s
   existing rule to selections).

4. **Volume.** ~450 selections/day estimated (~90–130 MB/year) against
   Supabase Free tier's 500 MB. `raw_toast_responses`'s retention is bounded
   to a short rolling window (~ADR 0004's 3-day trailing re-pull plus some
   debugging slack), since `selections` now serves the replay role
   `raw_toast_responses` existed for. `selections` itself stays unbounded for
   now; capping backfill depth or moving off Free tier is deferred until
   tickets 06/07 produce real numbers.

Leaves ADR
[0016](../../../docs/adr/0016-selection-grain-capture-with-an-allowlisted-pii-boundary.md),
which supersedes the aggregation-based PII mechanism `daily_capture.py`'s
docstring describes today (ticket 05 updates the code and docstring) while
keeping the same guarantee. ADR 0004's trailing-window semantics are expected
to carry over unchanged at the new grain — ticket 05 must preserve that
explicitly, since the primary key shape changes.
