# Inspection notebook and written recommendation

Status: ready-for-agent

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The human-facing surface and the answer the whole effort exists to produce.

In `notebooks/exploration.ipynb`, chart the contenders so a person can eyeball
plausibility before trusting any number:

- Poolish total: forecast vs actual across the holdout, per candidate.
- Buffer coverage: how often each model's P95 Poolish quantity actually covered
  demand, against the 95% Service Level target.
- Split accuracy: WAPE per variety across the split methods.

Then write the conclusion: name the winning model for the Poolish total and for
the split, with the margin over the incumbent and over the moving-average
baseline, and a plain-language read on whether it is worth replacing the current
model. State explicitly if ETS did not earn its `statsmodels` dependency, and if
the split turned out to be a non-race.

Finally, file the follow-up **promotion ticket**: a new issue to promote the
Poolish and split winners into `forecast.py` (this effort ships nothing itself).

## Acceptance criteria

- [ ] Notebook charts the Poolish total forecast vs actual per candidate
- [ ] Notebook charts buffer coverage vs the 95% target
- [ ] Notebook charts split accuracy (WAPE) per variety
- [ ] A written conclusion names the winner per target, the margins, and a recommendation, and calls out whether ETS earned its dependency and whether the split was a non-race
- [ ] A follow-up promotion ticket is filed to move the winners into `forecast.py`

## Blocked by

- `04-pandas-candidate-models.md`
- `05-holt-winters-ets-candidate.md`
- `06-bake-split-sub-comparison.md`
