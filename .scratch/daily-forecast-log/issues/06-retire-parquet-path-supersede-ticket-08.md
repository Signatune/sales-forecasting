# Retire `forecast.py`'s parquet path and supersede ticket 08

Status: ready-for-agent

## Parent

`.scratch/daily-forecast-log/PRD.md`

## What to build

Once the log is producing daily Demand Forecasts, retire the old file-based
forecast output and formally close the now-obsolete promotion plan (ADR 0006).

- **Retire the parquet outputs.** `forecast.py` stops writing
  `demand_forecast.parquet` and `sales_forecast.parquet`, the way ticket 07 retired
  the file-based ingestion path. The seasonal-naive **model function**
  (`forecast.forecast_demand`) and the shared helpers (`history_before`,
  `target_dates`, `FORECAST_PRODUCTS`) survive — they are still imported by the
  comparison, the engine, and the generalized callables. Only the parquet-writing
  `main()` / output path goes away. Update the module docstring accordingly.
- **Supersede ticket 08.** Set
  `.scratch/bake-forecast-model-comparison/issues/08-promote-poolish-and-split-winners.md`
  to a closed/superseded state with a one-line pointer to ADR 0006 and this effort,
  so no one later hard-wires a single model per its plan. The operational Poolish
  bake number it described becomes part of the follow-on analysis layer (a view
  that reuses `p95_buffer` over the lead-3 wheat-bagel Target total), not a model
  shipped into `forecast.py`.

## Acceptance criteria

- [ ] `forecast.py` no longer writes either parquet file; nothing in the repo reads
      them
- [ ] `forecast.forecast_demand` and the shared helpers remain importable and their
      tests still pass
- [ ] Ticket 08 is marked superseded with a pointer to ADR 0006 and this effort
- [ ] Any docs/READMEs that referenced the parquet outputs are updated
- [ ] No downstream reader (`backtest.py`, `model_comparison.py`,
      `inspection_page.py`, the new engine) breaks

## Blocked by

- `05-scheduled-forecast-workflow.md`
