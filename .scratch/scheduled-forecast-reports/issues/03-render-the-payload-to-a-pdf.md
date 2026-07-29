# Render a payload to a PDF, one page per model

Status: ready-for-agent
Blocked by: 02

## Parent

`.scratch/scheduled-forecast-reports/PRD.md`

## What to build

A new module `report_render.py` and a `report` extra in `pyproject.toml`
(`weasyprint`), following how the `forecast` extra keeps `statsmodels` off the
base install.

```
render_chart(days)      -> str   # an <svg> element
render_html(payload)    -> str   # the whole document, one <section> per model
render_pdf(payload)     -> bytes
```

WeasyPrint, not a headless browser: the chart is emitted as SVG **from Python**,
so the test suite can assert on it directly rather than through a browser. No
JavaScript runs anywhere in this path.

### The page

Variant C of the prototype is the design being carried over — chart-first, the
bar chart carrying the answer. Read `prototype_weekly_forecast.html` for the
layout and palette, then delete the prototype (ticket 06).

Per model, in order — headline model first, then alphabetical (ADR 0011):

- a header naming the report, the Report Window, the Forecast Origin, the config
  version, and the model this page belongs to
- the bar chart of daily point Demand Forecasts across the window
- per day: the forecast, the direction against its Weekday Baseline, and the
  baseline itself. "No baseline" where there is none — never a percentage
- the variety split as raw logged forecasts, with their sum shown next to the
  headline and the top-down footnote: *the headline is fit to the summed series,
  so it is not the varieties added up*
- a reserved, labelled block: preorders and catering are not included in these
  figures and are coming to this report later. It holds its space so adding them
  later does not reflow the page
- where models were omitted for incomplete coverage, a line naming them

Letter portrait. Page breaks via `break-before: page` on each model section —
WeasyPrint's paged-media support is the reason it was chosen over a plotting
library.

The document says **"vs typical for this weekday"**, not "vs last week". The
baseline is a four-week mean (`CONTEXT.md`, Weekday Baseline) and mislabelling it
would misrepresent what the arrows mean.

**These are point Demand Forecasts, not Bake-to Quantities, and the page must say
so.** No Service Level buffer is applied anywhere in this feature (ADR 0012 fixes
the target at 95%, but applying it is ticket 06's follow-on effort). A page that
reads like a bake sheet would have someone baking to the *mean* — stocking out
roughly half the time — which is the exact failure the Service Level exists to
prevent. One plain line near the totals, not a footnote in small type:

> These are expected Demand, not bake quantities. Baking to these numbers means
> running out about half the time.

## Tests

A new `tests/test_report_render.py`:

- `render_chart` output parses as XML and has one bar per day in the window
- a day at "no baseline" renders no percentage anywhere in its markup
- the top-down footnote appears whenever the varieties' sum differs from the
  headline
- the headline model's section precedes the others, and the rest are alphabetical
- omitted models are named in the output
- `render_pdf` returns bytes starting `%PDF-` with as many pages as qualifying
  models — `importorskip` weasyprint so the suite still runs on a `dev`-only
  install, exactly as the ETS tests do for `statsmodels`
