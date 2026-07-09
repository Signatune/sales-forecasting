# Bagel Forecast Pilot

Status: ready-for-agent

## Purpose

Prove out a Sales ingestion → Demand Forecast pipeline end-to-end on a small, real Product family (bagel varieties) before investing in scaling it to the full menu. The pilot validates two things at once: that we can reliably pull and normalize Sales data from the Toast Analytics API, and that a simple forecasting approach produces a plausible Demand Forecast from it.

This is a plumbing-and-shape pilot, not an accuracy project. The bar is "the pipeline runs end-to-end and the forecast is in the right ballpark," not a tuned, production-accurate model.

## Scope

- **Products in scope**: the bagel varieties sold as distinct Products (exact list to be confirmed once we see what Toast exposes per-item).
- **Data source**: Toast Analytics API. We have API credentials but have not yet explored the actual response shape — that exploration is part of this work, not a precondition.
- **Out of scope for this pilot**:
  - The rest of the menu (Category-wide rollup beyond the bagel family).
  - Stockout correction. Toast does not report whether a Product was 86'd on a given day, so this pilot treats Sales as a direct proxy for Demand. Distinguishing true Demand from Stockout-suppressed Sales would require pulling in MarginEdge data — deferred to a future spec.
  - Any scheduling, deployment, or database. This runs as local Python scripts/notebook against local files.

## Requirements

### 1. Ingestion

- `toast_client.py`: authenticates against the Toast Analytics API and pulls daily, per-Product Sales data for the bagel family. Pull the full history Toast makes available (paginate/loop if the API caps how far back a single request can go).
- Raw API responses are saved to `data/raw/` (timestamped) so normalization logic can be rebuilt/debugged without re-hitting the API.
- `normalize.py`: transforms the raw Toast response into canonical Sales records — one row per (Product, Date, Quantity) — using this project's domain vocabulary (`Product`, `Sales`; see `CONTEXT.md`). Written to `data/sales_history.parquet`.
- Fail loudly on auth errors or an unexpected response shape. Do not silently swallow or paper over surprises in the data — for this pilot, surfacing "Toast changed its shape" is valuable information.

### 2. Forecasting

- `forecast.py` reads `data/sales_history.parquet` and produces a **Demand Forecast** per bagel Product for a **2–7 day horizon**, at **daily granularity**.
- **Model**: seasonal-naive (same-weekday historical average). For a target date, forecast a Product's Demand as the average of that Product's Sales on the same weekday across the trailing history pulled in step 1.
- Sum the per-Product Demand Forecasts for the target dates into a family-level **Sales Forecast** for the bagel family.
- Output written to `data/demand_forecast.parquet`.

### 3. Backtest

- `backtest.py` (or a notebook section) holds out the most recent ~2–4 weeks of actual Sales, generates forecasts as if those days were in the future using only data prior to them, and compares forecast vs. actual per Product and for the family rollup.
- Report an error metric (e.g. MAPE) per Product and for the family total.
- Also compute a trailing N-day moving average (no seasonality) over the same holdout period as a naive baseline, so the backtest shows whether the seasonal-naive model actually beats a dumber baseline — this baseline is a comparison artifact only, not a candidate for the shipped model.

### 4. Inspection

- A notebook (`notebooks/exploration.ipynb`) for visually eyeballing forecast-vs-actual charts per Product before trusting any number.

## Data flow

```
Toast Analytics API
  → raw JSON (data/raw/)
  → normalize.py
  → data/sales_history.parquet  (canonical Sales records)
  → forecast.py (seasonal-naive model)
  → data/demand_forecast.parquet  (per-Product Demand Forecast)
  → summed → family-level Sales Forecast
  → backtest.py compares vs. held-out actuals
```

## Testing

- Unit test `normalize.py` against a saved sample raw Toast response (captured once we've seen the real shape), to lock down the transformation and catch future shape drift.
- The backtest (Requirement 3) serves as the accuracy/validation check — no separate accuracy test suite for this pilot.
- Manual visual sanity check via the notebook before treating any forecast number as trustworthy.

## Known simplifications / explicit non-goals

- No Stockout correction (Sales used as a Demand proxy). Revisit once MarginEdge data is available.
- Single Product family (bagels) only; no attempt at full-menu scale or performance in this pilot.
- No scheduling/automation, no database, no deployment — local scripts and files only.
- No tuning beyond the seasonal-naive baseline; exponential smoothing / trend+seasonality models are deferred to a future iteration once the pipeline is proven.

## Open questions (to resolve during implementation, not before)

- Exact list of bagel varieties as distinct Toast Products (depends on how Toast's menu structure maps to our `Product` concept).
- Whether Toast's API imposes a lookback cap on a single historical pull.
