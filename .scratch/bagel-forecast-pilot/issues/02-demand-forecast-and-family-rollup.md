# Produce the Demand Forecast and family Sales Forecast

Status: ready-for-human
Blocked by: 01

## Parent

`.scratch/bagel-forecast-pilot/PRD.md`

## What to build

Running the forecast against the ingested Sales history produces a Demand Forecast for each bagel Product — daily granularity, covering 2–7 days out — plus the summed family-level Sales Forecast. Demoable: real forecast numbers for next week's bagels, per variety and for the family as a whole.

Model is seasonal-naive: a Product's forecast Demand for a target date is the average of its Sales on that same weekday across the trailing history. No stockout correction — per the PRD, Sales is the Demand proxy for this pilot.

## Acceptance criteria

- [x] A single command reads the normalized Sales history and writes a per-Product Demand Forecast for each of the next 2–7 days
- [x] Forecast uses the seasonal-naive (same-weekday historical average) model
- [x] The family-level Sales Forecast is the sum of the per-Product Demand Forecasts, produced alongside them
- [x] Output is persisted in the same local-file style as the Sales history and is inspectable (readable columns: Product, Date, forecast quantity)
- [x] Handles a Product with sparse history (e.g. a new variety with few matching weekdays) without crashing — a clear warning or documented behavior is fine

## Blocked by

- `01-ingest-bagel-sales-history.md`

## Comments

### Decisions taken during implementation (2026-07-10)

**Scope: five Products, not seven.** `cinnamon raisin` and `pumpernickel` are
day-restricted varieties (Wednesdays / Thursdays only) and are out of forecast
scope for now. They stay in `sales_history.parquet` — the exclusion lives in
`forecast.py`, so re-including them is a move between two constants. The family
Sales Forecast is therefore a sum over five Products and understates all-bagel
Sales by roughly 5.8 units/day; `forecast.py` prints this caveat on every run.

**Averaging: recorded days only.** A Product's same-weekday average is taken
over the days it has a Sales record, not over every calendar day in the window.
Absent days are skipped rather than counted as zero.

This choice is only safe because of the scope decision above. The two skipped
varieties never sold on some weekdays at all (pumpernickel has zero Sunday,
Monday and Tuesday records), so recorded-only averaging would have produced
`NaN` for them and poisoned the family sum on three of the six forecast days.
For the five Products that remain, every weekday carries at least 109
observations, so no `NaN` can arise. The residual gap between recorded-only and
zero-filled averaging is under 1% for four of them, and 5% for the low-volume
`gluten-free plain` (absent on 41 of 854 open days).

It also means the seven all-store-closed days in the history (July 4ths,
Thanksgivings) are ignored for free — they carry no rows for any Product, so
they never drag down the weekday they land on.

**Zero-observation guard.** If a forecast Product ever has no record on a target
weekday, it is omitted from that date with a loud warning rather than forecast
as zero or as `NaN`. Unreachable for the current five; a safety net if scope
changes.

**Second output file.** The PRD's data flow names only
`data/demand_forecast.parquet` and describes the family rollup as "summed"
without a file. `forecast.py` also persists `data/sales_forecast.parquet`,
because ticket 03 consumes the family rollup and `CONTEXT.md` treats a Demand
Forecast (per-Product) and a Sales Forecast (aggregate) as distinct concepts.
