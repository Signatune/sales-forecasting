# Daily Forecast Log

Status: ready-for-agent

## Problem Statement

The shop has two candidate forecasting models — EWMA and Holt-Winters/ETS — that
competed well in an offline comparison, but nothing produces or records their
Demand Forecasts day to day. The owner wants **both** models to run every day and
their Demand Forecasts logged, so their accuracy can be tracked over time with
summary statistics — a live continuation of the comparison, not a one-off
backtest. Two constraints shape it:

1. **Don't hard-code a winner.** The comparison found the top models statistically
   tied; the owner wants the ongoing live record to keep informing that choice
   rather than freezing one model into the code now.
2. **Don't hard-code the bake situation.** Parameters like *how far ahead* to
   forecast, and *which Products or groups* to forecast, should be configuration
   the owner can change — eventually through a frontend for less-technical users —
   not constants edited in code with a new GitHub Actions workflow bolted on each
   time. In particular, "3 days out means Poolish" must not be encoded anywhere in
   the forecasting engine.

## Solution

A **config-driven daily forecast engine** that logs raw point Demand Forecasts to
Postgres for later analysis.

Each morning, after the day's Sales are captured, one scheduled job reads the
**active configuration** — a versioned JSON document naming the **Forecast
Targets** to forecast, the horizon (a day-count `N`), and each model's
hyperparameters — resolves each Target to a single summed Sales series, runs EWMA
and Holt-Winters across `as_of+1 .. as_of+N`, and appends the results to a
**write-once log**. A logged row is frozen at what was predicted with the data
available that morning.

Everything bake-specific — buffering to a Service Level, aggregating varieties to
the Poolish total, scoring on pinball/coverage — is left to a separate
**analysis layer** that reads the log; none of it lives in the engine or the
config. Lead is derived (`target_date - as_of`), so "Poolish = 3 days out" and
"staffing = 14 days out" are both just filters over the log, never stored
parameters.

See `docs/adr/0006-daily-forecasts-are-a-config-driven-write-once-log.md`. This
supersedes ticket 08 of the bake-forecast-model-comparison effort.

## User Stories

1. As a shop owner, I want EWMA and Holt-Winters to run every day, so that I have
   an ongoing forecast from each model rather than a one-time backtest.
2. As a shop owner, I want each day's Demand Forecasts recorded, so that I can
   later measure how accurate each model actually was against real Sales.
3. As an analyst, I want each logged forecast frozen at what was predicted with
   the data available that morning, so that my accuracy analysis scores forecasts
   and not hindsight.
4. As an analyst, I want a config change or a later Sales correction to never
   rewrite a past logged forecast, so that the historical record stays truthful.
5. As an analyst, I want to know exactly which configuration produced any logged
   forecast, so that a result is reproducible and attributable.
6. As a shop owner, I want to choose *which* Products or groups get forecast as
   configuration, so that I can change the forecast surface without code changes.
7. As a shop owner, I want to forecast a group of Products as one aggregated
   series (e.g. the wheat bagels together), so that I get the accuracy benefit of
   the smoother combined series.
8. As a shop owner, I want a single Product to be forecastable the same way as a
   group, so that there is no special case to reason about.
9. As an analyst, I want the same Product to be usable in several Forecast
   Targets, so that I can log both the wheat-bagel total and the individual
   varieties and compare top-down against bottom-up over time.
10. As an analyst, I want bottom-up totals derived at analysis time by summing
    member Targets, so that the engine never needs a separate bottom-up mode.
11. As a shop owner, I want to set how many days ahead to forecast, so that the
    horizon covers the furthest-out decision I care about.
12. As a shop owner, I want "how many days out means Poolish" to be a question I
    ask of the data, not a setting, so that no bake assumption is baked into the
    forecaster.
13. As a shop owner, I want both models run on every Target with the winner left
    to the data, so that I never pre-commit a Target to a model.
14. As an analyst, I want each model's hyperparameters (e.g. EWMA's half-life)
    configurable, so that I can retune without editing code.
15. As a shop owner, I want the daily forecast to run automatically after the
    day's Sales are captured, so that it always sees the just-closed day and no
    laptop has to be awake.
16. As a maintainer, I want one forecasting workflow that reads all configuration
    from the database, so that adding a Target is a data change, not another
    GitHub Actions workflow.
17. As a maintainer, I want the existing EWMA and ETS model definitions reused,
    so that there is one definition of each model, still under its existing tests.
18. As an analyst, I want Holt-Winters re-fit from history each run rather than
    carrying state between days, so that every logged forecast is reproducible
    from the Sales data alone and reflects trailing-window Sales corrections.
19. As a maintainer, I want the engine to be a pure function of Sales history,
    as_of, and config, so that it is testable without a database or a scheduler.
20. As a maintainer, I want `statsmodels` required only for the deployed forecast
    job, so that the base and dev installs stay light and the test suite still
    runs without it.
21. As a shop owner, I want the config and results stored in the database, so that
    a future frontend can read and edit them directly without a bespoke API
    server.
22. As a security-conscious owner, I want the new tables closed to the public Data
    API until a frontend deliberately needs them, so that business data is not
    exposed by default.
23. As an analyst, I want the operational bake number (how much Poolish to make)
    to be a view over the log that reuses the existing buffer logic, so that
    retiring the old parquet path loses no capability.
24. As a maintainer, I want `forecast.py`'s parquet outputs retired once the log
    exists, so that there is a single source of truth for forecasts.
25. As a maintainer, I want ticket 08 formally superseded, so that no one later
    hard-wires a single model per its now-obsolete plan.

## Implementation Decisions

- **Primary seam — a pure engine function.** `run_forecasts(config, sales,
  as_of)` returns a DataFrame of log rows `(as_of, config_version, model, target,
  target_date, forecast_quantity)`. It takes a plain `(product, date, quantity)`
  Sales frame (as `sales_history.load_sales_history()` returns) and a plain config
  object — no database — so all forecasting behavior reduces to one testable seam.
- **Forecast Target resolution.** For each Target, the engine sums its member
  Products' Sales per date into one series relabeled with the Target name, then
  runs each configured model on that single series. Sum-of-one is the lone-Product
  case; there is no separate code path. An unknown Product name in the config is a
  loud error (mirroring `forecast.unexpected_products`).
- **Reuse the model callables via a Product-scope parameter.** `ewma_forecast` and
  `ets_forecast` (and their shared helpers) take a **required** Product scope: the
  caller always names what to forecast, so there is no default set of Products a
  model forecasts. The engine points them at `[target_name]` against the relabeled
  summed series. One definition of each model remains; no parallel per-series
  forecaster is written. (Ticket 02 update: the standalone model comparison was
  retired, so these live in `models.py`, not the old `model_comparison.py`; with no
  comparison callers left there is no longer a default scope to preserve.)
- **Horizon is a day-count `N`; lead is derived.** The engine forecasts
  `as_of+1 .. as_of+N` for every Target. There is no stored lead and no min-lead
  cutoff; downstream analysis filters on `target_date - as_of`. Leak-freeness is
  the existing `forecast.history_before` cutoff (Sales strictly before `as_of`).
- **Global configuration.** One horizon and one model set (with hyperparameters)
  apply to every Target. Stored as a versioned JSON document, e.g.
  `{ "horizon_days": N, "models": { "ewma": {"halflife_weeks": 3},
  "holt_winters": {} }, "targets": { "wheat_bagels": ["everything","plain",
  "sesame"], "turkey_club": ["turkey club"] } }`. Structured to allow per-Target
  overrides later, but none are built now.
- **Stateless ETS.** Holt-Winters re-fits from history each run; no fitted model
  state is persisted between days (ADR 0006). This is what keeps `run_forecasts`
  pure and every logged row reproducible.
- **Schema — two new tables.** `forecast_configs (version, created_at, is_active,
  config jsonb)`, one row per configuration version. `forecasts (as_of,
  config_version, model, target, target_date, forecast_quantity, created_at)` with
  primary key `(as_of, config_version, model, target, target_date)`. Both
  RLS-enabled with no policies, as the rest of `schema.sql` is. Schema application
  stays idempotent (`IF NOT EXISTS` / `CREATE OR REPLACE`).
- **Write-once persistence.** A new `db.py` writer inserts forecast rows with
  `ON CONFLICT (…) DO NOTHING`, so a same-morning retry fills gaps and never
  overwrites, and a repeat under a new config version adds rows rather than
  clobbering. A new `db.py` reader returns the active configuration.
- **Scheduled compute, gated on capture.** A GitHub Actions workflow runs the
  engine after the daily-capture job succeeds (so it sees the just-closed day),
  reads the active config, runs `run_forecasts`, and writes the log. It installs
  the `forecast` extra. No on-demand trigger and no API server.
- **`statsmodels` moves to a `forecast` extra.** Repurposed from the `experiment`
  extra; the scheduled workflow installs `.[forecast]`. ETS is still imported
  lazily; the base/dev install and the default test run do not require it.
- **Supersede ticket 08 and retire the parquet path.** `forecast.py` stops writing
  `demand_forecast.parquet` / `sales_forecast.parquet`; its seasonal-naive model
  function survives as a callable. Ticket 08 is closed/re-scoped as superseded.
- **Analysis layer is a follow-on.** The views that join the log to actual Sales
  and reduce it with the existing pure `pinball` / `wape` / `coverage` /
  `p95_buffer` functions — including the Poolish bake number — are tracked
  separately, not built in this effort.

## Testing Decisions

- **Test external behavior, not internals.** As in the existing suites, feed a
  synthetic Sales frame (the `sales()` helper shape) and a synthetic config into a
  public function and assert on the returned DataFrame, using numbers worked by
  hand rather than recomputed the way the code does. Do not assert on private
  grouping or aggregation helpers.
- **The engine (primary seam).** Test `run_forecasts(config, sales, as_of)`
  directly: a Target that is a group sums its members into one series before
  fitting (top-down, a hand-worked total); a one-member Target reproduces the bare
  model; the horizon spans `as_of+1 .. as_of+N`; both configured models appear per
  Target per target date; no forecast sees Sales on or after `as_of` (the
  leak-free cutoff, mirroring `TestHistoryCutoff`); rows key on the Target, not its
  members; an unknown Product name raises. Prior art:
  `tests/test_models.py` and `tests/test_backtest.py` (synthetic frames, leak-free
  cutoffs, model arithmetic).
- **Model-callable generalization.** `tests/test_models.py` asserts the required
  Product-scope parameter forecasts exactly the scoped series (done in ticket 02).
- **Database functions (existing integration seam).** Test the write-once writer
  and the active-config reader with the ephemeral-Postgres pattern already in
  `tests/test_db.py`: a repeat write of the same key does not overwrite, a write
  under a new `config_version` adds rows, and a config round-trips. ETS is not
  required for these.
- **Stateless-ETS confirmation.** A test that two `run_forecasts` calls with the
  same `(config, sales, as_of)` produce identical rows for the Holt-Winters model
  documents the reproducibility the write-once log depends on.

## Out of Scope

- **The analysis layer.** The forecast-vs-actual views, summary statistics, and
  the operational Poolish bake number (buffer over the lead-3 wheat-bagel total)
  are an immediate follow-on effort, not this one.
- **The frontend.** Editing config through a UI, and the scoped RLS policies / auth
  that exposing the tables to the Data API would require, come later; interim
  editing is via SQL / the Supabase table editor.
- **On-demand compute.** No "run a forecast now" trigger and no bespoke API
  server; compute is scheduled-only.
- **Per-Target config overrides and nested groups.** Configuration is global and
  groups are flat for v1.
- **Baseline models in the log.** Only EWMA and Holt-Winters are logged; the naive
  `seasonal_naive` / `moving_average` comparison is considered already answered.
- **Staffing / revenue forecasting, ingredient ordering, stockout correction,
  ML/feature-based models.** As in the prior efforts, out of scope; Sales remains
  the Demand proxy.

## Further Notes

- The `forecasts` table is small even append-only — a handful of Targets × 2
  models × an `N`-day horizon × 365 days is well within Postgres's comfort.
- Because a group Target and its member Targets can both be logged, the top-down
  vs bottom-up accuracy question becomes a standing analysis-time query rather than
  a one-off decision.
- Respects ADR 0001 (two-stage Poolish/bake; the buffer lives once, at the total),
  ADR 0004 (trailing-window revisions, which stateless refits absorb), and ADR
  0005 (the source-to-Product model the Sales frame comes from).
