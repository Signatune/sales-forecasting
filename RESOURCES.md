# Resources

## Primary (high-trust)

- **Hyndman & Athanasopoulos, _Forecasting: Principles and Practice_ (3rd ed.),
  §5.9 "Evaluating distributional forecast accuracy"** —
  <https://otexts.com/fpp3/distaccuracy.html>
  The canonical, free, authoritative definition of the quantile score / pinball
  loss. Note: uses the doubled convention (`2p` / `2(1-p)`), 2× this repo's.
  _Trust: very high (standard forecasting text)._

## Repo (ground truth for our implementation)

- `model_comparison.py:245` — `pinball_losses()` / `pinball()`: the actual daily
  loss and the mean headline score, in our un-doubled convention.
- `model_comparison.py:304` — `p95_buffer()`: how a point forecast becomes the
  P95 quantity that pinball scores.
- `model_comparison.py:288` — `coverage()`: the realised Service Level, the
  companion number to pinball.
- `inspection_page.py` — how the score is rendered into the ranked model report.
- `CONTEXT.md` — domain language (Demand, Poolish, Service Level, Stockout).

## Candidate communities (not yet vetted with Mike)
- r/forecasting, Cross Validated (stats.stackexchange.com) for quantile-loss Qs.
