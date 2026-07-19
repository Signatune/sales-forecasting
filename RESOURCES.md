# Resources

## Primary (high-trust)

- **Hyndman & Athanasopoulos, _Forecasting: Principles and Practice_ (3rd ed.),
  §5.9 "Evaluating distributional forecast accuracy"** —
  <https://otexts.com/fpp3/distaccuracy.html>
  The canonical, free, authoritative definition of the quantile score / pinball
  loss. Note: uses the doubled convention (`2p` / `2(1-p)`), 2× this repo's.
  _Trust: very high (standard forecasting text)._

## Repo (ground truth for our implementation)

- `models.py` — `pinball_losses()` / `pinball()`: the actual daily loss and the
  mean headline score, in our un-doubled convention.
- `models.py` — `p95_buffer()`: how a point forecast becomes the P95 quantity
  that pinball scores.
- `models.py` — `coverage()`: the realised Service Level, the companion number
  to pinball.
- `CONTEXT.md` — domain language (Demand, Poolish, Service Level, Stockout).

_Note: the standalone model comparison and its `inspection_page` report were
retired when the daily forecast log landed; these pure scoring functions moved
to `models.py` and the daily forecast engine + analysis layer reuse them._

## Candidate communities (not yet vetted with Mike)
- r/forecasting, Cross Validated (stats.stackexchange.com) for quantile-loss Qs.
