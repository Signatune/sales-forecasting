# Bake-split sub-comparison

Status: ready-for-agent

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The second, smaller forecast target: the lead-2 split of the fixed Poolish across
the three varieties by expected share, scored on WAPE per variety (no second
quantile buffer — the buffer lives in the Poolish total; see
`docs/adr/0001-two-stage-poolish-bake-forecast.md`).

Candidate split methods, each reusing the `compare_models` evaluator and the WAPE
metric:

- **Constant recent share** — each variety's share of the recent total, held flat.
- **Same-weekday share** — each variety's share conditioned on weekday.
- **Per-variety recency level** — per-variety recency-weighted forecast,
  normalized to sum to the Poolish total.

The mix is fairly stable (~45/29/27), so this may be a near-non-race where a
constant recent share is hard to beat — a finding worth stating, not a failure.

## Acceptance criteria

- [ ] The three split methods are scored on WAPE per variety over the recent ~26 weeks at lead 2, via `compare_models`
- [ ] The split allocates the fixed Poolish by expected share, with no second quantile buffer
- [ ] Each method respects the leak-free history cutoff
- [ ] A comparison table (or the report) shows WAPE per variety for each split method
- [ ] Split arithmetic pinned with hand-worked synthetic history

## Blocked by

- `03-rolling-origin-evaluator-poolish-total.md`
