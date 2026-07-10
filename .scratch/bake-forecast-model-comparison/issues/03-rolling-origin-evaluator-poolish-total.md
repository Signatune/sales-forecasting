# Rolling-origin evaluator on the Poolish total

Status: ready-for-agent

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The tracer bullet: the machinery that turns candidate models into a scored
comparison, proven end-to-end on the headline target — the Poolish total.

- The uniform **model-callable seam**: a candidate is a
  `(sales, as_of) -> DataFrame[product, date, forecast_quantity]` callable, the
  same shape as `forecast.forecast_demand` and `backtest.moving_average_forecast`,
  reusing `forecast.history_before` and `forecast.target_dates` for cutoff and
  horizon.
- The **wheat-total pseudo-Product**: aggregate the three baked varieties per date
  into one synthetic total Product, so the same callables forecast the Poolish
  total exactly as they forecast a variety (Poolish is decided at the total; see
  `docs/adr/0001-two-stage-poolish-bake-forecast.md`).
- `compare_models`: replay each model from a series of past origins across the
  recent ~26 weeks, at lead 3 (the Poolish lead), each origin forecasting only
  from prior data. Buffer each point forecast to P95 (ticket 02), and score with
  pinball@95 (ticket 02).

Wire it with the two models we already have — the incumbent seasonal-naive
(imported from `forecast`) and the moving-average baseline (from `backtest`) — and
produce a printed comparison table ranking them by pinball@95 on the Poolish
total. That first table, with real numbers, is the demo.

## Acceptance criteria

- [ ] A model-callable seam every candidate conforms to, with the wheat-total pseudo-Product summing the three varieties per date
- [ ] `compare_models` replays over the recent ~26 weeks at lead 3, each holdout day forecast from only prior data (no origin sees its own target date)
- [ ] Point forecasts are buffered to P95 and scored with pinball@95 on the Poolish total
- [ ] A printed comparison table ranks the incumbent seasonal-naive vs the moving-average baseline
- [ ] Tests follow `TestCompare` / `TestReplayOrigins`: origin bookkeeping, leak-freeness (step-day fixture), and the pseudo-Product aggregation

## Blocked by

- `01-narrow-baked-bagel-scope.md`
- `02-pinball-wape-and-p95-buffer.md`
