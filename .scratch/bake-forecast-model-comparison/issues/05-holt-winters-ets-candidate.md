# Holt-Winters / ETS candidate

Status: done

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The classic seasonal reference model — Holt-Winters / ETS via `statsmodels` —
conforming to the same model-callable seam and ranked alongside the pandas
candidates on pinball@95 for the Poolish total. Answers whether a textbook
seasonal method beats the simple recency-weighted ones, or whether it does not
earn its dependency.

`statsmodels` is a new dependency: add it as an experiment/notebook extra, not to
the test-required deps, so the existing suite still runs on a `dev`-only install.
The model still emits its P95 through the same uniform relative-residual buffer as
every other candidate (not its own prediction interval), so pinball compares
forecast quality, not interval machinery — see
`docs/adr/0002-score-bake-forecasts-on-pinball-and-wape.md`.

## Acceptance criteria

- [ ] An ETS/Holt-Winters candidate conforms to the model-callable seam and is scored by `compare_models`
- [ ] It derives its P95 through the shared relative-residual buffer, not a native interval
- [ ] It respects the leak-free history cutoff
- [ ] `statsmodels` is an experiment/notebook extra; the test suite still runs on a `dev`-only install
- [ ] The comparison table ranks ETS against the pandas candidates, incumbent, and baseline

## Blocked by

- `03-rolling-origin-evaluator-poolish-total.md`
