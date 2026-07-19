# Scheduled forecast workflow, gated on the daily capture

Status: ready-for-agent

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

- [ ] The forecast job runs only after the capture has written the day's Sales
- [ ] The entry point reads the active config, runs the engine, and writes the
      write-once log; it logs the config version and the row count
- [ ] A failure exits non-zero and is visible in the Actions tab
- [ ] `statsmodels` is installed for the job via a `forecast` extra; the default
      `pytest` run still passes without it
- [ ] The job is hand-runnable (`workflow_dispatch` or equivalent) for a missed day
- [ ] A concurrency group prevents overlapping writes

## Blocked by

- `03-forecast-engine-run-forecasts.md`
- `04-db-read-config-and-write-once-forecasts.md`
