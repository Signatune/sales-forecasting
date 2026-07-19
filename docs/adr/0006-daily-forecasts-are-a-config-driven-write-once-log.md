# Daily Demand Forecasts are a config-driven, write-once log rather than a single promoted model

The bagel pilot shipped one seasonal-naive model that writes Demand Forecasts to
parquet, run by hand and unscheduled. The bake-forecast-model-comparison effort
then built EWMA and Holt-Winters/ETS candidates and scored them on a rolling
backtest — and concluded the top seasonal models are *statistically tied* (0.8
standard errors apart) and that ETS *does not earn its `statsmodels` dependency*.
That left ticket 08 ("promote the winner into `forecast.py`") stuck: the backtest
could not, on 178 days, separate the candidates with confidence.

Rather than promote one model, the system now runs **EWMA and Holt-Winters every
morning** and records each one's point Demand Forecasts to a Postgres log,
**frozen at what was predicted with the data available that morning**. Accumulating
a live forecast-vs-actual record is how the "which model, by how much" question
actually gets answered — with real evidence that grows over time, rather than one
backtest. The two are not both operational answers; they are two horses in a log.

The forecast surface is **configuration, not code**. A versioned JSON document
holds which Forecast Targets to forecast, how far ahead (a horizon day-count `N`),
and each model's hyperparameters — so the surface can change without a deploy, and
later from a frontend, without adding a GitHub Actions workflow per change.

Load-bearing decisions:

- **The engine logs only raw point Demand Forecasts.** Buffering to a Service
  Level, aggregating to the Poolish total, and pinball/coverage scoring are
  *analysis-time reads* over the log joined to actual Sales — not stored outputs
  and not config knobs. This is what keeps "3 days out means Poolish" out of the
  forecasting code: lead is derived (`target_date - as_of`), never stored, so a
  Poolish or staffing decision is a filter over the log.
- **A Forecast Target is a group of one or more Products**, summed top-down into
  one series a model is fit to; a lone Product is the one-member case. Forecasting
  members separately and summing (bottom-up) is a read-time aggregation, never an
  engine mode (see `CONTEXT.md`).
- **The engine is stateless; Holt-Winters/ETS re-fits from history each run.** A
  forecast is a pure function of `(Sales history, as_of, config)`. No fitted model
  state is persisted between days. This keeps every logged row reproducible from
  the data alone, and lets each morning's run reflect ADR 0004's trailing-window
  Sales corrections rather than baking a since-revised actual into carried-over
  state. At this data scale a daily refit is milliseconds, so the incremental
  (warm-started) alternative would trade reproducibility and correction-handling
  for a performance win that is not needed.
- **The log is write-once**, keyed `(as_of, config_version, model, target,
  target_date)` with `ON CONFLICT DO NOTHING`. A config change or a Sales revision
  never rewrites a past forecast; provenance is the `config_version` stamp.
- **Compute stays scheduled.** One GitHub Actions workflow reads the active config
  and runs everything after the daily capture, so each morning's forecast sees the
  just-closed day. No bespoke API server: a future frontend rides Supabase's Data
  API over these tables.

This supersedes ticket 08
(`.scratch/bake-forecast-model-comparison/issues/08-promote-poolish-and-split-winners.md`).

## Consequences

- `forecast.py`'s parquet outputs (`demand_forecast.parquet`,
  `sales_forecast.parquet`) are retired the way ticket 07 retired the file-based
  ingestion path. The seasonal-naive *model function* remains a callable; only its
  role as the parquet-writing entry point goes away. The operational bake number
  becomes an analysis view that reuses the existing `p95_buffer` over the lead-3
  wheat-bagel Forecast Target total (ADR 0001).
- `statsmodels` becomes **required for the forecast job**, reversing ticket 08's
  choice to keep it off the production path. It is installed via a `forecast`
  extra in the scheduled workflow; the base and `dev` installs stay light and the
  test suite still runs without it (ETS tests `importorskip` it).
- The existing model callables (`ewma_forecast`, `ets_forecast`) are generalized
  to accept a Product scope so the engine can point them at a Target's summed
  series — one definition of each model, still under `test_model_comparison.py`.
- A new scheduled workflow is gated on the capture job so forecasts run after the
  day's Sales land.
- The `forecast_configs` and `forecasts` tables are RLS-enabled with no policies
  (private, as the rest of the schema is) until a frontend genuinely needs scoped
  Data API access.
