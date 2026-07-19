# Sales Forecasting

Forecasts demand for products sold in a deli and bakery, producing an aggregate sales forecast derived from the sum of each product's projected demand.

## Where the data lives

A managed Postgres database is the single source of truth for Sales history (ADR 0003). Raw Toast responses land as `jsonb` in one table, and the canonical Sales fact — one row per `(date, restaurant, source_type, source_name, quantity)` — in another; the `product_sales` view rolls that fact up through the Product mapping to the `(product, date, quantity)` frame the forecast reads (ADR 0005). Every reader goes through `sales_history.load_sales_history()`, which reads that view; nothing forecasts from a file.

Each day's Sales are captured by a scheduled GitHub Actions job (`.github/workflows/daily-capture.yml`) that runs `daily_capture.py` — no laptop has to be awake. The daily capture pulls the **Orders API only**, over a 3-day trailing business-date window, and upserts by `(date, restaurant, source)`, so voids and back-office corrections Toast allows after a day closes are picked up on the next run (ADR 0004).

The Analytics client (`toast_client.py`) is off this daily path. It is kept for a future backfill or a manual reconciliation against the Orders numbers, not run automatically. The one-time load of the pre-existing history into Postgres lived in `migrate.py`; there is no file-based ingestion path any more.

## Language

**Product**:
A single forecastable item sold in the deli or bakery (e.g. a sourdough loaf, a turkey club sandwich).
_Avoid_: Item, SKU, good

**Category**:
A grouping of related Products (e.g. Bakery, Deli, Prepared Foods).
_Avoid_: Department, group, section

**Demand**:
The quantity of a Product customers would purchase if unconstrained by available stock.
_Avoid_: Sales, orders

**Sales**:
The quantity of a Product actually recorded as sold in a given period. May understate Demand during a Stockout.
_Avoid_: Demand, revenue

**Stockout**:
A period during which a Product has zero available stock, suppressing Sales below true Demand.
_Avoid_: Out of stock, shortage

**Demand Forecast**:
A projected quantity of Demand for a single Product over a future period.
_Avoid_: Prediction, estimate

**Sales Forecast**:
The aggregate projected revenue or units across all Products' Demand Forecasts for a future period.
_Avoid_: Forecast (alone), projection

**Forecast Target**:
A named group of one or more Products whose Sales are summed into a single series that a model is fit to and forecast. A lone Product is the degenerate one-member group, so there is no separate "single product" case. Distinct from a Category: a Category is a merchandising grouping, a Forecast Target is a forecasting unit chosen because its aggregated series forecasts more accurately. A Product may belong to several Targets or none, and forecasting the members of a Target separately and summing the results (bottom-up) is a read-time aggregation, not a Target of its own.
_Avoid_: Series, group (alone), aggregate

## Baking

**Wheat Dough**:
The single dough shared by the baked bagel varieties (`everything`, `plain`, `sesame`), which differ only by topping applied at shaping. Distinct from the gluten-free varieties, which are bought in frozen and not baked.
_Avoid_: Base, batter

**Poolish**:
The pre-ferment made in one batch for the whole Wheat Dough, decided ~3 days ahead of a bake. Its quantity caps how many bagels can be baked that day, across all varieties combined.
_Avoid_: Starter, pre-ferment (informally), sponge

**Bake-to Quantity**:
The number of a single variety to shape and bake on a given day, decided ~2 days ahead by splitting the fixed Poolish across varieties by expected share.
_Avoid_: Bake amount, production target

**Service Level**:
The probability that Demand is met without a Stockout, chosen as a target (currently 95%). It sets how much buffer the Poolish quantity carries above expected Demand: a lost sale is treated as far costlier than a leftover, so the total is forecast at that upper quantile rather than at the mean.
_Avoid_: Fill rate, coverage
