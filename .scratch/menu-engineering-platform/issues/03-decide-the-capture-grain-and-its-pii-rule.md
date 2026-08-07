# Decide the capture grain and its PII rule

Type: grilling
Status: open

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
