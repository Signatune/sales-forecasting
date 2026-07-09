# Produce the Demand Forecast and family Sales Forecast

Status: ready-for-agent
Blocked by: 01

## Parent

`.scratch/bagel-forecast-pilot/PRD.md`

## What to build

Running the forecast against the ingested Sales history produces a Demand Forecast for each bagel Product — daily granularity, covering 2–7 days out — plus the summed family-level Sales Forecast. Demoable: real forecast numbers for next week's bagels, per variety and for the family as a whole.

Model is seasonal-naive: a Product's forecast Demand for a target date is the average of its Sales on that same weekday across the trailing history. No stockout correction — per the PRD, Sales is the Demand proxy for this pilot.

## Acceptance criteria

- [ ] A single command reads the normalized Sales history and writes a per-Product Demand Forecast for each of the next 2–7 days
- [ ] Forecast uses the seasonal-naive (same-weekday historical average) model
- [ ] The family-level Sales Forecast is the sum of the per-Product Demand Forecasts, produced alongside them
- [ ] Output is persisted in the same local-file style as the Sales history and is inspectable (readable columns: Product, Date, forecast quantity)
- [ ] Handles a Product with sparse history (e.g. a new variety with few matching weekdays) without crashing — a clear warning or documented behavior is fine

## Blocked by

- `01-ingest-bagel-sales-history.md`
