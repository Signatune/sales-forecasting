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
