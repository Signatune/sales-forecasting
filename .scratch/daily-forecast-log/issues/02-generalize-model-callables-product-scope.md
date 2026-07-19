# Generalize the model callables with a Product scope

Status: ready-for-agent

## Parent

`.scratch/daily-forecast-log/PRD.md`

## What to build

The daily engine forecasts a **Forecast Target** — a summed series relabeled with
the Target name — but `model_comparison.ewma_forecast` and `ets_forecast` are
hard-wired to `forecast.FORECAST_PRODUCTS` and emit per-variety rows. Generalize
them (and the shared helpers `_in_scope_history`, `_same_weekday_reduce`, and the
ETS per-series path) to accept an optional **Product scope**, so the engine can
point a model at exactly one series (`[target_name]`).

- The scope parameter defaults to `forecast.FORECAST_PRODUCTS`, so every existing
  caller and the whole comparison behave exactly as before.
- With a scope of `[target_name]` against a frame whose only Product is that
  Target's summed series, each callable fits and forecasts that one series.
- Keep **one definition of each model** — no parallel per-series forecaster.

This is the "reuse via generalization, not a new seam" decision from the PRD.

## Acceptance criteria

- [ ] `ewma_forecast` and `ets_forecast` accept a Product scope, defaulting to the
      current `FORECAST_PRODUCTS`
- [ ] Called with the default scope, both produce byte-for-byte the same output as
      today (existing `tests/test_model_comparison.py` stays green unchanged)
- [ ] Called with a single-element scope against a matching frame, each forecasts
      exactly that series
- [ ] `tests/test_model_comparison.py` gains coverage that the scope parameter
      forecasts the scoped series and that the default is unchanged
- [ ] No model arithmetic is duplicated into a new function

## Blocked by

- (none)
