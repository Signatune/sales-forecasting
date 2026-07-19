# The forecast engine: `run_forecasts(config, sales, as_of)`

Status: done

## Resolution note

Built in a new `forecast_engine.py` as planned. One thing the plan did not
anticipate: the model callables took their target dates from
`forecast.target_dates(as_of)`, hard-wired to the incumbent
`HORIZON_DAYS = (2, 7)`, so there was no way to ask them for `as_of+1 ..
as_of+N`. Rather than reimplement target-date arithmetic in the engine (a second
definition free to drift from the backtest's), `target_dates` and both model
callables gained a `horizon` `(first, last)` parameter defaulting to
`forecast.HORIZON_DAYS`; the engine passes its configured `(1, horizon_days)`.
Existing callers are unaffected, and `tests/test_models.py` gains a
`TestHorizon` class covering the new parameter.

One acceptance criterion needs reading precisely. "Output spans exactly
`as_of+1 .. as_of+horizon_days`" holds for the horizon the engine *asks* each
model for, not as a guaranteed row count: a model emits no row for a target date
it has no evidence for (a Target that has never sold on a Tuesday yields no
Tuesday forecast), and the engine deliberately does not fabricate one — a
made-up zero would be scored later as a confident forecast of nothing rather
than as the silence it is, which is the same call `forecast.forecast_demand`
already makes. The shop's real Targets sell essentially every open day, so every
target date is covered in practice; the sparse-Target case is pinned by
`test_a_target_date_with_no_evidence_yields_no_row`.

Also worth recording: the config names EWMA's hyperparameter `halflife_weeks`
(the PRD's spelling, in the shop's terms) while the callable's parameter is
`halflife`. `forecast_engine.MODEL_RUNNERS` holds one small adapter per model that does
that mapping, so a config key is never blindly splatted into a callable. Three
config mistakes raise rather than logging a misleading row: an unknown model
name, a hyperparameter a model does not take (a typo'd `halflife` would
otherwise fall back to the code's default while the `config_version` stamp
claimed otherwise — provenance is the point of the stamp), and a Target with no
members.

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

- [x] A group Target sums its members into one series before fitting (a hand-worked
      top-down total), keyed on the Target name, not its members
- [x] A one-member Target reproduces the bare (scoped) model output
- [x] Both configured models appear for each Target and each target date
- [x] Output spans exactly `as_of+1 .. as_of+horizon_days`
- [x] No forecast uses Sales on or after `as_of`
- [x] An unknown Product name in a Target raises
- [x] Two calls with identical `(config, sales, as_of)` return identical rows,
      including for Holt-Winters (statelessness)
- [x] Output columns/dtypes are ready for the write path (dates as dates, quantity
      as float), tested with a synthetic Sales frame and config as in
      `tests/test_models.py`

## Blocked by

- `02-generalize-model-callables-product-scope.md`
