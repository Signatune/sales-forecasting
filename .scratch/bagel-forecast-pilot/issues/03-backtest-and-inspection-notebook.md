# Backtest against held-out actuals, with inspection notebook

Status: ready-for-human
Blocked by: 02

## Parent

`.scratch/bagel-forecast-pilot/PRD.md`

## What to build

Answer the pilot's accuracy question: is the seasonal-naive forecast in the right ballpark, and does it beat a dumber baseline? Hold out the most recent ~2–4 weeks of actual Sales, generate forecasts for those days using only data prior to them, and compare forecast vs. actual — per Product and for the family rollup — reporting an error metric (MAPE). Run a trailing-moving-average (no seasonality) baseline over the same holdout as the comparison bar; the baseline is a measurement artifact, not a candidate model.

Include the pilot's human demo surface: a notebook charting forecast-vs-actual per Product over the holdout period, so a person can eyeball plausibility (weekend peaks in the right place, magnitudes sane) before trusting any number.

## Acceptance criteria

- [x] Backtest holds out the most recent ~2–4 weeks of actuals and forecasts them using only prior data (no leakage from the holdout into the model)
- [x] MAPE reported per Product and for the family-level Sales Forecast
- [x] Trailing-moving-average baseline computed over the same holdout, reported side-by-side with the seasonal-naive results
- [x] Notebook charts forecast vs. actual per Product across the holdout period
- [x] A short written conclusion (in the notebook or this ticket's Comments): does seasonal-naive beat the baseline, and does the forecast look plausible to a human?

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

### Conclusion (2026-07-10)

**Seasonal-naive beats the baseline, and the forecast is plausible.** Backtest
over 2026-06-12..2026-07-09, 24 open days, `.venv/bin/python backtest.py`:

| Product | seasonal-naive | moving-average | scored |
|---|---|---|---|
| everything | 14.3% | 35.4% | 24 |
| gluten-free everything | **65.8%** | **55.9%** | 24 |
| gluten-free plain | 64.2% | 99.9% | 24 |
| plain | 17.6% | 33.6% | 24 |
| sesame | 18.8% | 30.2% | 24 |
| **family Sales Forecast** | **12.5%** | **31.0%** | 24 |

The family Sales Forecast beats the baseline by better than 2×. Per Product it
wins on four of five. It **loses on `gluten-free everything`** (65.8% vs 55.9%),
which sells ~8 units/day: a two-unit miss is a 25% error there, so that MAPE is
measuring noise, not seasonality. Same caveat on `gluten-free plain`'s 64.2%.
Neither gluten-free number should be read as a statement about the model.

Plausible to a human: every per-Product panel peaks on the weekend and the
forecast peaks with it. Mean bias is +16 units/day on a mean actual of 445
(~3.6% over-forecast) — the safe direction, waste rather than a Stockout.

**Two things worth a look before anyone tunes for accuracy.** The gluten-free
lines are too low-volume for MAPE to say anything useful; a unit-error metric
would. And the model over-forecasts Mondays (mean actual 263, forecast 339) —
two of the three worst family days in the holdout are Mondays, both over. The
two-year Monday mean sits above recent Mondays, which a model with a trend term
would catch. Per the PRD, deferred.

### Notes for whoever picks this up (2026-07-10)

Three things a reader should know about how the backtest is built.

`forecast.forecast_demand` covers `as_of+2..as_of+7`, so no single `as_of`
spans a four-week holdout. `backtest.replay_origins` walks a series of origins
six days apart; each holdout day is forecast exactly once, at a lead time of
2..7 days. A later origin does see earlier holdout days — by then they have
happened. What never happens is a forecast seeing its own target date.

Both models are scored on **identical rows**. `forecast_demand` emits no row
for a Product on a weekday it never sold, while the weekday-blind baseline
always emits one; scoring each on whatever rows it produced would let the
seasonal-naive model duck exactly the days it finds hardest. Hence two flags,
which bite at different levels: a row is `comparable` when both models forecast
it, and `scored` when it is comparable *and* the actual is positive.

The distinction matters at the family level, and getting it wrong was a real
bug caught in review. A zero actual has no percentage error but still belongs
in a family total. A Product only *one* model forecast belongs in neither — if
its actual stays in the family sum while only the baseline's forecast covers
it, the seasonal-naive total is charged for units it never bid on. So
`family_totals` sums the comparable basket on every column, actual included.
Not triggered by the current history (all 24 holdout days are fully comparable,
so the 12.5% above is unaffected), but reachable, per ticket 02's note.

Ticket 02's `history_before` and `target_dates` were promoted from private to
public in `forecast.py`. The baseline has to cut its history at the same
instant the model does, and the replay has to key off the same horizon; a
second definition of either would have been free to drift.

The holdout happens to contain a **four-day closure** (Fri 2026-07-03 through
Mon 07-06, around a Saturday July 4th). Earlier years show single-day closures.
Nothing sells on those days, they carry no Sales record, and the backtest never
scores them — 24 open days out of 28.
