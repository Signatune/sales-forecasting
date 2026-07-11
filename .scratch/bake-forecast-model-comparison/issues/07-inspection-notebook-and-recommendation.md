# Inspection Page and written recommendation

Status: done

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The human-facing surface and the answer the whole effort exists to produce.

In an HTML file, chart the contenders so a person can eyeball
plausibility before trusting any number:

- Poolish total: forecast vs actual across the holdout, per candidate.
- Buffer coverage: how often each model's P95 Poolish quantity actually covered
  demand, against the 95% Service Level target.
- Split accuracy: WAPE per variety across the split methods.

Then write the conclusion: name the winning model for the Poolish total and for
the split, with the margin over the incumbent and over the moving-average
baseline, and a plain-language read on whether it is worth replacing the current
model.

Finally, file the follow-up **promotion ticket**: a new issue to promote the
Poolish and split winners into `forecast.py` (this effort ships nothing itself).

## Acceptance criteria

- [x] Page charts the Poolish total forecast vs actual per candidate
- [x] Page charts buffer coverage vs the 95% target
- [x] Page charts split accuracy (WAPE) per variety
- [x] A written conclusion names the winner per target, the margins, and a recommendation
- [x] A follow-up promotion ticket is filed to move the winners into `forecast.py`

## Comments

Built as `inspection_page.py` → `model_comparison.html` (generated, not
hand-written): three charts plus a conclusion whose every number is read off the
run. The verdict is a rule, not prose — candidates are compared on their *daily*
pinball losses, paired day by day, and a gap counts only past 2 standard errors.

The finding: `ewma` is the best dependency-free Poolish model (4.8% under the
incumbent) but the gap is 0.8 standard errors — the top seasonal models are tied
on 178 days of evidence. ETS did not earn `statsmodels` (3.2%, t = 1.8). The
seasonal models do beat the moving-average baseline decisively (43.1%, t = 3.4).
What separates `ewma` from the incumbent is calibration, not loss: the incumbent
over-covers at 98.3% against a 95% target and bakes ~36 more bagels of dough a
day for it. The split is a near non-race, as the PRD suspected —
`constant_recent_share` at 7.7% mean WAPE, 6.1% ahead of the runner-up.

Promotion filed as `08-promote-poolish-and-split-winners.md`, `ready-for-human`:
the loss numbers do not compel the swap, the dough numbers argue for it, and that
trade is the owner's call.

## Blocked by

- `04-pandas-candidate-models.md`
- `05-holt-winters-ets-candidate.md`
- `06-bake-split-sub-comparison.md`
