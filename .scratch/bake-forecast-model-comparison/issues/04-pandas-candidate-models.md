# Pandas candidate models for the Poolish total

Status: ready-for-agent

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The pure-pandas candidate models for the Poolish total, each dropping into the
model-callable seam from ticket 03 and appearing ranked in the comparison table:

- **Trailing-window seasonal-naive** — same-weekday mean over the last N weeks
  only, rather than all history.
- **EWMA / recency-weighted seasonal-naive** — same-weekday mean with recent
  observations weighted above old ones.
- **Seasonal + trend** — a same-weekday level plus a fitted drift.

This is where the incumbent's structural high bias on the ~8%/yr downtrend should
surface: a recency-aware model should forecast below the equal-weight same-weekday
mean and score better on pinball@95. The trailing-window and EWMA spans are tuning
choices to settle here.

## Acceptance criteria

- [ ] Trailing-window, EWMA/recency-weighted, and seasonal+trend candidates conform to the model-callable seam and are scored by `compare_models`
- [ ] Each model's point arithmetic is pinned with hand-worked synthetic history (e.g. a recency-weighted model forecasts below the equal-weight mean on a declining series), in the style of `TestMovingAverageBaseline`
- [ ] Each candidate respects the leak-free history cutoff, mirroring `TestHistoryCutoff`
- [ ] The comparison table ranks all pandas candidates against the incumbent and the baseline on pinball@95

## Blocked by

- `03-rolling-origin-evaluator-poolish-total.md`
