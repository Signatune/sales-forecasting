# Decide how the new grain relates to the existing sales fact

Type: grilling
Status: open
Blocked by: 03

## Question

Once a richer per-selection grain exists (ticket 03), what happens to `sales`
and `product_sales`?

ADR 0005 establishes the current model: `sales` is the fine-grained fact, one
row per `(date, restaurant_guid, source_type, source_name, quantity)`;
`product_sources` maps sources to Products many-to-one; `product_sales` rolls
the fact up to `(product, date, quantity)`, which is the only frame every
forecast reader consumes via `sales_history.load_sales_history()`.

The new grain is strictly finer than `sales`. So:

1. **Does `sales` become derived?** A view or materialized view over the new
   grain would make it impossible for the two to disagree, at some query cost.
   Keeping `sales` as an independently-written table risks silent divergence
   between the number the forecast sees and the number menu engineering sees —
   which would be worse than either being wrong alone.

2. **Does the forecast path change at all?** The stated goal is that it should
   not: `product_sales` keeps the same shape and the same numbers. Verify that
   is achievable, and say how it will be *proven* — a comparison of old vs. new
   `product_sales` output over the same dates is the obvious check.

3. **What does `product_sources` mean at the new grain?** It maps
   `(source_type, source_name)` to a Product, keyed on normalized names because
   Toast names are edited in place. At a per-selection grain, GUIDs are
   available. Decide whether the map stays name-keyed (and why), and whether the
   `item`/`modifier` `source_type` distinction survives.

4. **Does ADR 0005 get superseded, amended, or left standing?**

The bar: the forecast numbers must not move, and if they do, the map must know
exactly why before anything is built.
