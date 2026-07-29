# Scheduled Forecast Reports

Status: done

## Problem Statement

The `forecasts` log has been accumulating point Demand Forecasts every morning
since 2026-07-20 (ADR 0006), and nothing reads it. The owner cannot see what the
models expect for the coming week without opening a database client. A prototype
(`prototype_weekly_forecast.py`, three variants) answered what such a report
should *look* like; variant C, chart-first, won. This turns that answer into a
real, delivered artifact.

Three constraints shape it:

1. **Only logged forecasts.** The prototype re-ran the models in memory to reach
   days past the horizon and labelled them `extrapolated`. The real report does
   not do this: every number on the page was logged by a scheduled run, or the
   page is not produced.
2. **Cadence is data, not code.** The bagel report runs Saturdays, but other
   reports on other days must be addable without another workflow file. See
   ADR 0010.
3. **Delivery is Google Chat.** Which forces a specific shape: webhooks cannot
   upload attachments, so the PDF lands in Drive and the webhook posts a link.

## Solution

A **`report_configs` subscription table** and **one daily workflow** that reads
it.

Each row is a Scheduled Report: a foreign key to a `forecast_configs.version`,
the weekdays it fires on, and a `jsonb` document naming the report, its headline
model, its headline Forecast Target, the varieties forming the split, and a
symbolic delivery destination.

Each morning, after the daily forecast, the workflow asks which rows want
delivering today. For each, it requires a Forecast Origin of **today** under the
referenced config version — refusing loudly otherwise — reads that origin's
logged rows across the Report Window, renders a PDF, uploads it to Drive, and
posts a Google Chat card linking to it.

### The page

One page per qualifying model, headline model first, then alphabetical. Each page
carries, for the headline Forecast Target across the whole Report Window:

- a bar chart of the daily point Demand Forecasts (the chart-first shape that won
  the prototype), generated as SVG from Python
- each day's direction against its **Weekday Baseline** — the four-week
  same-weekday mean of Settled Sales — with moves under 3% called flat and a
  missing baseline rendered as "no baseline", never as a 100% jump
- the variety split as the **raw logged per-variety forecasts**, footnoted that
  they do not sum to the headline because the headline is fit to the summed
  series (top-down ≠ bottom-up)
- a reserved, labelled block for preorders and catering, stating that the figures
  exclude them

### Deliberately out of scope

- **Running accuracy statistics.** With the log nine days old, lead-6 and lead-7
  have zero scorable observations and the whole figure would rest on ~15 rows.
  Deferred until the log can support it; the metric will be WAPE (ADR 0002), not
  MAPE.
- **Service Level buffering.** The report shows point Demand Forecasts. Turning
  one into a Bake-to Quantity or a Poolish total is a separate read over the log
  (ADR 0006), and a bigger question than layout.
- **Preorders and catering data.** Space is reserved; the data is not modelled.
- **An interactive model toggle.** A PDF cannot toggle and a Chat webhook cannot
  carry interaction; flipping pages is the toggle (ADR 0011).

## Constraints discovered while designing

- **Only a Saturday origin can cover a Sunday–Saturday window** at
  `horizon_days: 7`, since a Friday origin reaches only the Friday of that week.
  There is no fallback origin — hence the strict same-day rule in ADR 0010.
- **Google Chat incoming webhooks cannot attach files.** Confirmed against
  Google's documentation: attachments require `media.upload` under OAuth user
  credentials, unreachable from a webhook's `key`/`token` authorization. Webhooks
  are also limited to 1 request/second per space and cannot receive interaction
  events.
- **Card images must be HTTPS-hosted PNG/JPG.** The Chat card therefore carries
  numbers and a link, not the chart.
- **Sales are not settled immediately.** ADR 0004's three-day trailing window
  means the most recent days are revisable, so the Weekday Baseline uses only
  Settled Sales.
