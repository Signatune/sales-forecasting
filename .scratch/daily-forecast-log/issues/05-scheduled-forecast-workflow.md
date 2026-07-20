# Scheduled forecast workflow, gated on the daily capture

Status: done — integration tests unrun (see the resolution note)

## Resolution note

Built as `daily_forecast.py` (mirroring `daily_capture.py`'s shape:
`run_daily_forecast(conn, sales, as_of)` plus a `main` with `connect` /
`load_sales` / `now` seams) and `.github/workflows/daily-forecast.yml`, with
`docs/scheduled-forecast.md` alongside `docs/scheduled-capture.md`.

Three judgement calls worth recording:

- **A separate workflow on `workflow_run`, not a second job in
  `daily-capture.yml`.** Both were allowed by the ticket. A separate workflow
  gets its own concurrency group and its own `workflow_dispatch`, so a missed
  morning can be re-forecast *without* re-pulling Toast — which a `needs:`-gated
  second job could not offer. It is guarded on
  `github.event.workflow_run.conclusion == 'success'` so a failed capture skips
  the morning rather than forecasting off an incomplete history.
- **`main` catches `ValueError` as well as `RuntimeError` / `psycopg.Error`.**
  The engine raises `ValueError` for configuration mistakes (an unknown Product
  in a Target, a hyperparameter a model does not take, an unrunnable model).
  Those messages already say what to fix, so they are printed as the same clear
  one-line failure rather than buried in a traceback.
- **The `forecast` extra already existed** — `experiment` was repurposed in an
  earlier ticket — so this only had to install `.[forecast]` in the workflow.

**The two `TestAgainstPostgres` tests are unrun.** As with ticket 04, `pgserver`
publishes no Windows wheel and there was no local Postgres or Docker on the
machine this was built on, so they skipped. The unit layer (eight tests driving
`main` through fake `connect` / `load_sales` seams) did run and passes, and the
whole path was additionally driven end to end against a stub connection with
both models configured — logging the config version, the row count, and a group
Target correctly summing its two members. What is unconfirmed is narrowly the
database round trip: that `run_daily_forecast` reads a real `forecast_configs`
row and that a same-morning re-run inserts nothing.

## Parent

`.scratch/daily-forecast-log/PRD.md`

## What to build

The single, config-driven compute job (ADR 0006): a GitHub Actions workflow that
runs the engine each morning **after** the day's Sales capture, so the forecast
sees the just-closed day.

- **Ordering.** Run after `daily-capture.yml` succeeds — either a second job in
  that workflow gated with `needs:`, or a separate workflow triggered on the
  capture workflow's completion. Do not schedule it on a bare clock time that could
  race the capture.
- **A thin entry point** that wires it together: read the active config
  (`db.read_active_config`), run `run_forecasts(config, sales, as_of)` on the Sales
  from `sales_history.load_sales_history()`, and write the log with the write-once
  writer. `as_of` is "today" in the restaurants' timezone, consistent with how the
  capture computes its window. It logs which config version it ran, how many rows
  it wrote, and exits non-zero (visibly) on a Toast/DB/model failure.
- **`statsmodels` install.** Repurpose the `experiment` extra in `pyproject.toml`
  into a `forecast` extra and have this workflow install `.[forecast]`, so
  Holt-Winters runs in production while the base/dev install and the default test
  run stay light (ETS still imported lazily; ETS tests still `importorskip`).
- Reuse the capture workflow's secret and concurrency conventions
  (`DATABASE_URL` session pooler; a concurrency group so a manual run and the
  scheduled run never write at once).

## Acceptance criteria

- [x] The forecast job runs only after the capture has written the day's Sales
- [x] The entry point reads the active config, runs the engine, and writes the
      write-once log; it logs the config version and the row count
- [x] A failure exits non-zero and is visible in the Actions tab
- [x] `statsmodels` is installed for the job via a `forecast` extra; the default
      `pytest` run still passes without it
- [x] The job is hand-runnable (`workflow_dispatch` or equivalent) for a missed day
- [x] A concurrency group prevents overlapping writes

## Blocked by

- `03-forecast-engine-run-forecasts.md`
- `04-db-read-config-and-write-once-forecasts.md`
