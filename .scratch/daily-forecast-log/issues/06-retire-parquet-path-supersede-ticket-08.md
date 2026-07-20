# Retire `forecast.py`'s parquet path and supersede ticket 08

Status: done

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

- [x] `forecast.py` no longer writes either parquet file; nothing in the repo reads
      them
- [x] `forecast.forecast_demand` and the shared helpers remain importable and their
      tests still pass
- [x] Ticket 08 is marked superseded with a pointer to ADR 0006 and this effort
- [x] Any docs/READMEs that referenced the parquet outputs are updated
- [x] No downstream reader (`backtest.py`, `model_comparison.py`,
      `inspection_page.py`, the new engine) breaks

## Notes

`main()`, both `*_PATH` constants and `_skipped_daily_mean` (which existed only
for `main()`'s printed caveat) are gone; `forecast.py` is a library with no
entry point, as `normalize.py` became under ticket 07. `roll_up_sales_forecast`,
`sparse_weekday_counts`, `unexpected_products` and `SKIPPED_PRODUCTS` were
*kept* though they now run only under test — they record scope decisions ADR
0001 and `forecast_engine.run_forecasts` still point at, and the ticket scoped
the removal to the parquet-writing output path. A module-docstring paragraph
says so, so they don't read as oversights, and flags the one thing this costs:
nothing warns any more about a Sales-history Product classified neither way.
Worth re-running that check when the analysis layer lands.

`model_comparison.py` and `inspection_page.py` no longer exist (retired in
ticket 02 of this effort), so the surviving downstream readers are `backtest.py`
and `models.py`. The only doc still describing the parquet outputs as live was
`.gitignore`'s header comment; the ADRs that mention them are left as the
historical record they are.

## Blocked by

- `05-scheduled-forecast-workflow.md`
