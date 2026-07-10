# Pinball, WAPE, and the P95 relative-residual buffer

Status: ready-for-agent

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The scoring and buffering primitives every candidate comparison will reduce on,
as pure functions verifiable on their own:

- `pinball(actual, forecast, level)` — pinball (quantile) loss at a Service Level.
  At 95% it penalises under-forecasting 19× as hard as over-forecasting. Lives
  beside the existing `mape`.
- `wape(actual, forecast)` — weighted absolute percentage error: total absolute
  error over total actual. Defined when an individual actual is zero (unlike
  MAPE), and not dominated by misses on the smallest variety.
- The P95 buffer transform — given a model's relative residuals
  ((actual − forecast) / forecast) from prior forecasts, take their 95th
  percentile `q` and return `point_forecast × (1 + q)`. The multiplicative form
  makes the absolute buffer grow with volume, so a high-swing Sunday gets a bigger
  buffer than a quiet Tuesday.

See `docs/adr/0002-score-bake-forecasts-on-pinball-and-wape.md` for why these
metrics and this buffering mechanism.

## Acceptance criteria

- [ ] `pinball` returns the quantile loss and its asymmetry is pinned (an under-forecast scores 19× an equal over-forecast at 95%)
- [ ] `wape` is the total-absolute-error-over-total-actual, with a zero-actual case pinned
- [ ] The buffer transform turns a point forecast into its P95 from a set of relative residuals; a wider residual spread yields a larger buffer, and the buffer scales with the point forecast
- [ ] All three are pure functions with hand-worked unit tests, in the style of `TestMape`

## Blocked by

- None — can start immediately.
