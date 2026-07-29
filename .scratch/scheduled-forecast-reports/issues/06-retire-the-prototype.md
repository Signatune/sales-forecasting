# Retire the weekly forecast prototype

Status: done
Blocked by: 03

## Resolution note

Done. `prototype_weekly_forecast.py`, `prototype_weekly_forecast.html` and
`NOTES-prototype-weekly-forecast.md` are deleted.

The ticket's placement table was checked line by line before deleting and holds:
every verdict from the notes is recorded in `CONTEXT.md`, the PRD, ADR 0010 or
tickets 02/03. No reference to the prototype survives outside `.scratch/`.

The one genuinely open thread — whether this report should become what someone
bakes off, which needs a Service Level buffer over the point forecasts — remains
recorded in this ticket and in ADR 0012/0013. Until it exists, the page says in
plain type that these are expected Demand and not bake quantities.

## Parent

`.scratch/scheduled-forecast-reports/PRD.md`

## What to do

Delete, once ticket 03 has carried variant C's design into `report_render.py`:

- `prototype_weekly_forecast.py`
- `prototype_weekly_forecast.html`
- `NOTES-prototype-weekly-forecast.md`

They are marked throwaway in their own headers and the question they existed to
answer is answered: C won.

Check first that nothing in the notes is left unrecorded elsewhere. At the time
of writing, everything is placed:

| From the notes | Now lives in |
|---|---|
| C wins, chart-first | ticket 03 |
| Simple split, show the trend | superseded — raw logged variety forecasts, PRD |
| Only logged forecasts, six days out minimum | ADR 0010, the strict origin rule |
| Service sales only, leave room for preorders | ticket 03, the reserved block |
| "Last week" is not the current week | `CONTEXT.md`, Weekday Baseline |
| A missing baseline is not a 100% jump | ticket 02 |
| Under 3% is flat | ticket 02, `CONTEXT.md` |
| Top-down ≠ bottom-up | ticket 03, the footnote |
| No Service Level buffer is applied | PRD, out of scope |

The one genuinely open thread the notes raised is **whether this report should
become what someone bakes off** — which needs a Service Level buffer over the
point forecasts. It is now safe to delete the file: the thread is recorded, and
it is narrower than it was.

**What is settled.** ADR 0012 records that the 95% Service Level is *asserted*
from operating knowledge rather than derived from measured costs — `Co` is small
because leftovers sell Day-old, and `Cu` is not separable in the data because the
baked varieties are zero-priced modifiers whose revenue belongs to a parent item.
So nobody needs to price a Stockout before this can move, and nobody should
re-open that question without new evidence of the kind ADR 0012 names.

**What remains, as its own effort.** Two pieces, in this order:

1. ~~**Confirm the data supports the target.**~~ **Done.** `residuals.py` is the
   lead-3 rolling-origin replay this needed; `backtest.py` never produced such a
   pool and the evaluator that did was retired in `1d62ef4` (ADR 0012 records the
   correction). The pool is 1,657 daily residuals back to 2022 — one per open day,
   not one per lead span — and **the Service Level Ceiling is 97%, so 95% sits
   below it.** But that holds only over the full pool: at the 26-week warm-up the
   retired evaluator used, 95% costs ±16% of the bake in estimation noise and sits
   *above* the ceiling. ADR 0013 widens the pool accordingly.
2. **Turn point forecasts into a Bake-to Quantity.** An analysis read over the
   log — `models.p95_buffer` over the lead-3 `wheat_bagels` total (ADR 0001,
   ADR 0006) — buffering the Poolish total once and splitting by expected share,
   never summing per-variety quantiles. Then `models.coverage` over the
   accumulated log to check whether a nominal 95% delivers 95% in the shop, which
   ADR 0012 names as the real feedback loop.

   **Three constraints step 1 hands it.** *Residuals come from
   `residuals.residual_pool`, not the log* — the log is nine origins deep and
   cannot carry a three-year pool for years yet; the replay resolves Targets
   top-down exactly as the engine does, so switching later will not move the
   numbers. *Pass `through` explicitly* — the current day's Sales are still
   accumulating, and scoring a part-day total manufactures a residual near −0.9.
   *Coverage will read low against nominal for reasons that are not the buffer's
   fault*: the tail of the pool is calendar events (Patriots' Day, Yom Kippur,
   Christmas Eve), so the days a 95% buffer misses will cluster on holidays the
   model has no feature for. Before concluding a nominal 95% under-delivers,
   check whether the uncovered days are those days.

Until that exists, the report shows point Demand Forecasts and is **not** a bake
sheet. Ticket 03's page must not imply otherwise.
