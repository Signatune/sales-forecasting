# How modifiers are accounted for in menu engineering analysis

Research note. Question: when computing an item's price, cost, and revenue for
menu engineering (Kasavana & Smith-style Stars/Plowhorses/Puzzles/Dogs
analysis), how are POS modifiers (add-ons, substitutions, size variants)
handled? Is there a standard practice?

## The core matrix doesn't mention modifiers at all

The original Kasavana & Smith (1982) framework is defined purely in terms of
a menu item's **contribution margin** and **sales volume (popularity)**,
with no modifier concept built in:

- **Contribution Margin** = Selling Price − Portion (food) Cost
- **Popularity threshold** = (1 ÷ number of menu items) × 0.70 — an item is
  "popular" if its sales-mix share is at least 70% of an equal share
- **Sales Mix %** = units sold of item ÷ total units sold
- Four quadrants: **Stars** (high margin, high popularity), **Plowhorses**
  (low margin, high popularity), **Puzzles** (high margin, low popularity),
  **Dogs** (low margin, low popularity)

Source: [meez — Menu Engineering Matrix](https://www.getmeez.com/blog/menu-engineering-matrix)

The matrix assumes one clean (price, cost) pair per menu item. It was
designed before POS modifier systems existed, so it has nothing to say about
an item sold with variable add-ons — that problem is left to the data layer
underneath it. I could not get a working fetch of the original Kasavana &
Smith text or the FIU hospitality-review paper covering it (both 403'd), but
three independent secondary descriptions of the formula agree on the above,
so I'm treating it as settled.

## The POS layer keeps modifiers and items as separate rows, revenue attaches to the item

Toast's Product Mix (PMIX) report — the report type this repo's own
`toast_client.py`/`toast_orders.py` pull from — treats items and modifiers as
distinct report rows, not a single blended row per sale:

- Item Details and Modifier Details are separate transaction-level reports;
  modifiers show up as **expandable sub-rows under items**, not merged in.
- **All revenue is booked to the base item's revenue category — "no revenue
  will be reported for the MODIFIER category"** even though the modifier is
  still shown for reporting/analysis. This matches what this repo's own
  `docs/toast-analytics-api.md` observed independently: `netSalesAmount` on
  modifier rows is "usually `0.0` because most modifiers are free-of-charge
  line items" — quantity is the meaningful signal on a modifier row, not
  revenue.
- Toast does offer a report-level toggle — "Calculate item prices including
  modifiers" (default) vs "excluding modifiers" — but this only changes how
  an item's *average price* is displayed in the report; it doesn't change
  where the underlying revenue is recorded.

Sources: [Toast — Product Mix (PMIX) Report Overview](https://support.toasttab.com/en/article/Product-Mix-PMIX-Report-Overview), [Toast — Menu Report Overview](https://support.toasttab.com/en/article/Menu-Report-Overview-1492794696577)

So at the POS-reporting layer, the standard practice is: **the item carries
the revenue, the modifier carries a cost obligation that has to be
reattached to the item by a separate costing step.** That reattachment step
is where recipe-costing tools do the actual work.

## The standard reattachment practice: map each modifier to a recipe/ingredient, scale by quantity, roll into the item's realized cost

This is documented consistently by both **xtraCHEF** (Toast's own
acquired recipe-costing product) and **MarginEdge** (the tool this repo
already integrates with via its MCP server) — this looks to be the
de facto industry pattern, not one vendor's idiosyncrasy:

- **Map, don't average blindly.** Each POS modifier is mapped to an existing
  Product or Prep Recipe, with an explicit quantity/UOM: "indicate a number
  and UOM (Unit of Measure) for the portion being consumed by your
  modifier." The same modifier can be mapped to a *different* quantity
  depending on which parent menu item it's attached to (xtraCHEF explicitly
  supports "modifiers have different measurements depending on the menu
  item it is applied to").
- **Additive and subtractive modifiers are both handled**, with subtraction
  as an explicit flag rather than a negative quantity hack: "If the modifier
  ... removes an ingredient, you can ... select the Subtract slider. Rather
  than adding the additional cost of this ingredient ... it will now
  subtract that cost from your Recipe." xtraCHEF's worked example: a $6.00
  burger becomes $7.25 with a mapped "Add Bacon" modifier (+$1.25) or $5.50
  with a mapped "No Cheese" modifier (−$0.50).
- **Nested modifiers** (a modifier that itself has sub-modifiers) are
  supported and also deplete inventory accordingly.
- **The resulting per-order cost, not the static recipe cost, feeds the
  margin report.** "This mapping step unlocks advanced profit margin
  reporting that considers both your sales and food costs" — until
  modifiers are mapped, the profitability reports are explicitly
  incomplete/unpopulated.

Sources: [xtraCHEF — Recipe / Product Mix Mapping](https://support.toasttab.com/en/article/xtraCHEF-Recipe-Product-Mix-Mapping), [xtraCHEF — Variance Analysis and Product Mix Reports](https://support.toasttab.com/en/article/xtraCHEF-Recipe-Reporting)

MarginEdge's own Menu Analysis report (fetched via search cache only — the
help-center pages 403'd on direct `WebFetch`, so treat this one with a bit
more caution than the xtraCHEF quotes above) appears to follow the same
pattern, and separates the two cost concepts by name rather than blending
them silently:

- "Modifiers are mapped to a product/recipe ... a scale is set based on how
  much of the unit of measurement is used."
- The Menu Analysis report exposes **"Average Cost"** (the base recipe cost,
  *excluding* modifiers) alongside a distinct **"modifier cost"** column, a
  **"total cost"** column (the two summed), **"total revenue,"** and a
  **"theoretical cost %"** — i.e. it deliberately keeps the un-modified
  recipe cost visible as its own figure rather than only surfacing a
  post-modifier blend.

Source: [MarginEdge — Menu Analysis FAQs](https://help.marginedge.com/hc/en-us/articles/10278355596947-Menu-Analysis-FAQs) (via search cache, direct fetch blocked), [MarginEdge — Mapping Modifiers for PMIX](https://help.marginedge.com/hc/en-us/articles/360037899473-Mapping-Modifiers-for-PMIX) (via search cache, direct fetch blocked)

## Size variants are usually not "modifiers" at all — they're separate recipes

This isn't from an external source — it's what this repo's own
`kendall_square_recipes_bom.csv` already shows empirically: `12oz Black Iced
Tea` and `20oz Black Iced Tea` are two entirely separate `MENU`-category
recipes with their own `recipeCost`/`menuPrice`, not one recipe with a
"size" modifier bolted on. That lines up with the general practice above —
a size variant changes the *base* recipe's yield/cost, so it's cleanest
modeled as its own priced item, and the modifier-mapping machinery above is
reserved for genuine optional add-ons/substitutions layered on top of a
fixed base item.

## Bottom line for a menu-engineering pass on this data

1. **Don't run Kasavana & Smith straight off the static recipe cost.**
   `recipeCost` in `kendall_square_recipes_bom.csv` is the cost of the *base*
   recipe only; it excludes whatever modifiers were actually attached to
   each sale.
2. **Standard practice is a two-part cost:** base recipe cost (fixed) +
   modifier cost (variable, driven by attach rate) = realized cost per unit
   sold. The item's contribution margin for the matrix should use the
   *realized*, modifier-inclusive cost and revenue, not the theoretical
   recipe cost — otherwise a Plowhorse whose margin looks fine on paper but
   is routinely sold with a costly free-of-charge modifier will misclassify
   as more profitable than it actually is.
3. **Revenue for a modifier itself is usually $0** in the POS fact (Toast
   books it to the parent item), matching what `normalize.py` already
   assumes for bagel modifiers in this repo — so "modifier revenue" isn't a
   real quantity to chase; what matters is modifier **cost** and **attach
   rate**, applied against the parent item's already-recorded revenue.
4. There is no standard practice for folding modifier cost variance into the
   matrix's *popularity* axis — popularity is still just units of the parent
   item sold, unaffected by which modifiers rode along.
