# Backtest against held-out actuals, with inspection notebook

Status: ready-for-agent
Blocked by: 02

## Parent

`.scratch/bagel-forecast-pilot/PRD.md`

## What to build

Answer the pilot's accuracy question: is the seasonal-naive forecast in the right ballpark, and does it beat a dumber baseline? Hold out the most recent ~2–4 weeks of actual Sales, generate forecasts for those days using only data prior to them, and compare forecast vs. actual — per Product and for the family rollup — reporting an error metric (MAPE). Run a trailing-moving-average (no seasonality) baseline over the same holdout as the comparison bar; the baseline is a measurement artifact, not a candidate model.

Include the pilot's human demo surface: a notebook charting forecast-vs-actual per Product over the holdout period, so a person can eyeball plausibility (weekend peaks in the right place, magnitudes sane) before trusting any number.

## Acceptance criteria

- [ ] Backtest holds out the most recent ~2–4 weeks of actuals and forecasts them using only prior data (no leakage from the holdout into the model)
- [ ] MAPE reported per Product and for the family-level Sales Forecast
- [ ] Trailing-moving-average baseline computed over the same holdout, reported side-by-side with the seasonal-naive results
- [ ] Notebook charts forecast vs. actual per Product across the holdout period
- [ ] A short written conclusion (in the notebook or this ticket's Comments): does seasonal-naive beat the baseline, and does the forecast look plausible to a human?

## Blocked by

- `02-demand-forecast-and-family-rollup.md`

## Comments

### Inherited from ticket 02 (2026-07-10)

Ticket 02 narrowed forecast scope to five Products; `cinnamon raisin` and
`pumpernickel` are in `sales_history.parquet` but not forecast (see
`forecast.SKIPPED_PRODUCTS`).

**The family MAPE must compare like with like.** The family-level Sales Forecast
sums five Products. Scoring it against an actual family total that sums all
seven would charge the model for ~5.8 units/day it never tried to forecast.
Filter actuals to `forecast.FORECAST_PRODUCTS` before computing the family MAPE.

`forecast.forecast_demand(sales, as_of)` already refuses to see Sales on or
after `as_of`, so replaying a past `as_of` over the full history is leak-free —
`tests/test_forecast.py::TestHistoryCutoff` pins this. The backtest does not
need to pre-trim the history it passes in.

Note also that MAPE is undefined where an actual is zero. `gluten-free plain`
records no Sales on 41 of 854 open days, and `roll_up_sales_forecast` omits a
Product entirely on a weekday it never sold, so both zero actuals and absent
forecast rows are reachable in the holdout.
