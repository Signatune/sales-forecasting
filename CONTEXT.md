# Sales Forecasting

Forecasts demand for products sold in a deli and bakery, producing an aggregate sales forecast derived from the sum of each product's projected demand.

## Where the data lives

A managed Postgres database is the single source of truth for Sales history (ADR 0003). Raw Toast responses land as `jsonb` in one table, and the canonical Sales fact — one row per `(date, restaurant, source_type, source_name, quantity)` — in another; the `product_sales` view rolls that fact up through the Product mapping to the `(product, date, quantity)` frame the forecast reads (ADR 0005). Every reader goes through `sales_history.load_sales_history()`, which reads that view; nothing forecasts from a file.

Each day's Sales are captured by a scheduled GitHub Actions job (`.github/workflows/daily-capture.yml`) that runs `daily_capture.py` — no laptop has to be awake. The daily capture pulls the **Orders API only**, over a 3-day trailing business-date window, and upserts by `(date, restaurant, source)`, so voids and back-office corrections Toast allows after a day closes are picked up on the next run (ADR 0004).

Behind that capture, a second scheduled job (`.github/workflows/daily-forecast.yml`, running `daily_forecast.py`) forecasts the day: it reads the active configuration from `forecast_configs` — which Forecast Targets to run, how far ahead, and each model's hyperparameters — runs every configured model over each Target's summed series, and appends the results to the write-once `forecasts` log (ADR 0006). It is gated on the capture's success so it always sees the just-closed day. A logged row is frozen at what was predicted that morning; nothing bake-specific (lead, buffering, the Poolish total) is stored there, since all of it is derivable at read time.

Reading that log is a third scheduled job (`.github/workflows/scheduled-reports.yml`, running `scheduled_reports.py`), gated on the forecast the way the forecast is gated on the capture. It asks `report_configs` which Scheduled Reports fire on today's weekday, and for each requires a Forecast Origin of **today** under the referenced configuration — refusing loudly rather than rendering a stale window (ADR 0010). Qualifying reports become a PDF, one page per model that covers the whole Report Window, which is uploaded to Drive and announced in Google Chat with a card linking to it; a webhook cannot carry an attachment, which is what forces that shape. The pages carry point Demand Forecasts only: no Service Level buffer is applied anywhere on this path, and the page says so. See `docs/reports.md`.

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

**Forecast Origin**:
The day a Demand Forecast was made on, seeing only the Sales available that
morning. Every logged forecast belongs to exactly one Origin, and a forecast's
lead is its target date minus its Origin.
_Avoid_: Run date, as-of (informally), forecast date

**Scheduled Report**:
A standing subscription naming one forecast configuration, the weekdays it
should be delivered on, and what its pages say. Reports are data rather than
schedule code: a daily job asks which Scheduled Reports want delivering today.
_Avoid_: Job, digest, subscription

**Report Window**:
The span of target dates a Scheduled Report covers — the day after its Forecast
Origin through the end of the referenced configuration's horizon. The window is
never configured; it falls out of the day a report is scheduled on. A report
scheduled on Saturdays against a seven-day horizon therefore covers Sunday
through Saturday, and the same report moved to Wednesdays would cover Thursday
through Wednesday.
_Avoid_: Forecast week, reporting period, upcoming week

**Settled Sales**:
Sales for a date old enough that the capture's trailing window has closed over
it, so no further Toast correction is expected. Distinct from captured Sales,
which include the most recent days that are still revisable.
_Avoid_: Final sales, closed sales, actuals (alone)

**Weekday Baseline**:
The mean Settled Sales for a given weekday across the four most recent weeks,
used as the reference a Demand Forecast is called up, down or flat against. A
weekday average rather than the single preceding week, so one unusual day cannot
flip the direction. A move smaller than 3% is called flat.
_Avoid_: Last week, previous week, comparison

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
The probability that Demand is met without a Stockout, chosen as a target (currently 95%). It sets how much buffer the Poolish quantity carries above expected Demand: a lost sale is treated as far costlier than a leftover, so the total is forecast at that upper quantile rather than at the mean. The target is asserted from operating knowledge rather than derived from measured costs (ADR 0012), and must sit below the Service Level Ceiling.
_Avoid_: Fill rate, coverage

**Residual Pool**:
The set of past relative errors — actual Demand over forecast Demand, minus one — that a Service Level buffer reads its quantile from, collected at the Poolish lead from a single model's own replayed forecasts. Relative rather than absolute so one pool serves every weekday and every year (ADR 0002, ADR 0013).
_Avoid_: Error history, residuals (alone), training window

**Service Level Ceiling**:
The highest Service Level a Residual Pool can actually estimate, above which a higher target buys noise rather than service. A property of the pool and of what its tail is made of, not of cost — currently 96-97% (ADR 0012).
_Avoid_: Maximum service level, cap

**Day-old**:
A baked bagel unsold on its bake day and sold at a discount afterwards. Day-old sell-through is what makes a leftover cheap, and so is much of why the Service Level sits as high as it does: a leftover is partly recovered, a Stockout is not.
_Avoid_: Stale, surplus, waste
