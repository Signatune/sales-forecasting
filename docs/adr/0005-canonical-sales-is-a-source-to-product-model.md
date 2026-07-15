# Canonical Sales is a source-to-product model, tracking every item and modifier

ADR 0003 described canonical Sales as a single table of pre-aggregated
`(Product, Date, Quantity)` rows, with the modifier-name-to-Product mapping
baked into `normalize.py` as the `BAGEL_MODIFIER_NAMES` dict. That is enough for
the seven bagel flavors we forecast today, but it bakes in two limits. It only
stores the things already mapped — the bagels — so everything else the business
sells is discarded at normalization time and cannot be recovered without
re-reading raw. And the mapping lives in code, so adding a Product, or moving a
modifier from one Product to another, means re-running normalization over the
whole history rather than changing a row.

The business sells far more than bagels, and Toast records what's sold at two
grains: menu **items** (the selection on a check) and **modifiers** (toppings,
preparations, and — for bagels specifically — the flavor itself, since there is
no per-flavor menu item). Both grains need to roll up to a canonical Product we
can aggregate and forecast, and the same Product can be fed by many sources
(several modifier spellings after in-place renames, an item and a modifier that
mean the same thing, two locations' variants).

So canonical Sales becomes a dimensional model:

- **`sales`** — the fact. One row per `(date, restaurant, source_type,
  source_name, quantity)`: every configured thing sold, at both Toast grains
  (`source_type` is `item` or `modifier`), per location. "Configured" means it
  carries a Toast GUID — free text a guest or server typed on a check is not a
  thing we sell and is excluded, exactly as `normalize.py` excludes it today.
- **`products`** and **`product_sources`** — the mapping. `product_sources` maps
  each source (`source_type`, `source_name`) to one `products` row; many sources
  to one Product. The mapping is now data, not code — `BAGEL_MODIFIER_NAMES`
  becomes the seed for these tables.
- **`product_sales`** — a view that rolls the fact up through the mapping to the
  `(product, date, quantity)` frame the forecast already reads, summed across
  locations and across a Product's sources.

Source names are matched in the same normalized (stripped, lower-cased) form
`normalize.py` matches on today, so a Product still spans every historical
spelling of its modifiers.

## The history we have is modifier-only

There is no item-level history to migrate. The Analytics reports were pulled
`group_by=MODIFIER`; `daily_totals` carry only per-restaurant daily totals; and
the Orders backfill aggregated each order's selections down to their modifiers
and did not keep the raw orders (they carry guest PII). Items exist in Toast and
the model has a place for them from day one, but the one-time migration (ticket
03) backfills only the **modifier** history — which we hold in full, back to
2016 — and items are populated **going forward** by the daily capture. Item
history is deliberately not re-pulled: that would spend the Analytics
lookback-rate budget ADR 0003 and ADR 0004 were built to avoid.

## Consequences

- **The forecast's numbers don't move.** `forecast.py`, `backtest.py`,
  `model_comparison.py` and `inspection_page.py` read the `product_sales` view,
  which returns the same `(product, date, quantity)` contract and reproduces
  today's seven-Product output exactly. The switch (ticket 04) is verified
  against a pre-change run.
- **Adding a Product is a data change, not a code change and not a re-pull.**
  Insert a `products` row, map sources to it, and any matching modifier history
  already in the fact rolls up immediately — the fact keeps every configured
  source, not just the mapped ones.
- **The daily job upserts the fact by its primary key** `(date, restaurant,
  source_type, source_name)`. ADR 0004's 3-day trailing window still replaces a
  re-pulled day's rows rather than accumulating duplicates; the replace-on-repeat
  key is just finer-grained than the old `(Product, Date)`.
- **Unmapped configured sources sit in the fact, untracked by any Product,**
  until someone maps them. `normalize.py`'s loud surfacing of a bagel-looking
  modifier that maps to no Product stays valuable, and becomes a query over the
  fact rather than a scan of raw.
- This **supersedes ADR 0003's** description of canonical Sales as a single
  pre-aggregated `(Product, Date, Quantity)` table; the raw-`jsonb` table and
  everything else in ADR 0003 and ADR 0004 stand.
