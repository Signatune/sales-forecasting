# Mission

## Who
Mike — building a sales/demand forecasting system for a deli & bakery. Comes to
this with a from-scratch stats background: forecasting and statistics concepts
should be built up from first principles, not assumed.

## Why this matters
The system picks between competing forecasting models (naïve, EWMA,
seasonal-trend, ETS…) and the choice is made on a single headline number:
**mean pinball@95** on the Poolish total. A model gets shipped — and real bake
decisions get made — because it scored lower here. Mike needs to *understand*
that number well enough to:

- trust the model the pipeline recommends (and defend the choice),
- read the `inspection_page` report without squinting,
- know when a lower score is real evidence vs. one lucky Saturday.

## Definition of progress
Mike can take a handful of days of (actual Demand, P95 quantity) and compute the
pinball@95 by hand, explain *why* it penalises a Stockout ~19× harder than a
leftover, and read the ranked model table in his own words.

## Grounding
Every lesson ties to the real code in this repo (`model_comparison.py`,
`inspection_page.py`) and the bakery's real units (bagels, Poolish, Demand,
Service Level — see `CONTEXT.md`).

_Status: provisional — inferred from the project + first request. Confirm/adjust
with Mike._
