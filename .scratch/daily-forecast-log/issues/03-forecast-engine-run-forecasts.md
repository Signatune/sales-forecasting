# The forecast engine: `run_forecasts(config, sales, as_of)`

Status: ready-for-agent

## Parent

`.scratch/daily-forecast-log/PRD.md`

## What to build

A new module holding the primary seam: a pure function that turns the active
configuration, a Sales frame, and an `as_of` into the log rows for that morning.

    run_forecasts(config, sales, as_of)
      -> DataFrame[as_of, config_version, model, target, target_date, forecast_quantity]

Behaviour:

- **Target resolution.** For each Target in `config["targets"]`, sum its member
  Products' Sales per date into one series relabeled with the Target name. Sum of a
  one-member group is that Product itself — no special case. An unknown Product
  name raises a clear error (mirroring `forecast.unexpected_products`).
- **Run every model on every Target.** For each model in `config["models"]`, run
  the generalized callable (ticket 02) scoped to `[target_name]` against the
  relabeled series, with the model's configured hyperparameters.
- **Horizon.** Forecast `as_of+1 .. as_of+config["horizon_days"]` for every Target.
  No stored lead, no min-lead cutoff.
- **Leak-free.** Reuse `forecast.history_before` so no forecast sees Sales on or
  after `as_of`.
- **Stateless.** Holt-Winters re-fits from history each call; no model state is
  persisted or passed in. The function is a pure function of its arguments (ADR
  0006), so repeated calls with the same arguments are identical.
- Stamp every row with `config["version"]` as `config_version`.

The function does no database I/O — it takes a plain Sales frame and a plain config
and returns a frame. Wiring to Postgres is ticket 04/05.

## Acceptance criteria

- [ ] A group Target sums its members into one series before fitting (a hand-worked
      top-down total), keyed on the Target name, not its members
- [ ] A one-member Target reproduces the bare (scoped) model output
- [ ] Both configured models appear for each Target and each target date
- [ ] Output spans exactly `as_of+1 .. as_of+horizon_days`
- [ ] No forecast uses Sales on or after `as_of`
- [ ] An unknown Product name in a Target raises
- [ ] Two calls with identical `(config, sales, as_of)` return identical rows,
      including for Holt-Winters (statelessness)
- [ ] Output columns/dtypes are ready for the write path (dates as dates, quantity
      as float), tested with a synthetic Sales frame and config as in
      `tests/test_model_comparison.py`

## Blocked by

- `02-generalize-model-callables-product-scope.md`
