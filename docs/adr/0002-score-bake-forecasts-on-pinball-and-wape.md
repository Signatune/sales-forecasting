# Score bake-forecast candidates on pinball loss and WAPE, not MAPE

When comparing forecasting models for the baking decision, we score the two
targets on different metrics, and neither is the MAPE the pilot backtest used:

- **Poolish total: pinball loss at the 95% Service Level.** The bake decision is
  asymmetric — a Stockout costs a lost high-margin sale, a leftover costs cheap
  ingredients — so the right target is an upper quantile, not the mean. Pinball
  loss is the metric that rewards hitting the 95th percentile: it penalises
  under-forecasting 19× as hard as over-forecasting. A symmetric metric (MAPE,
  MAE) would crown the model that centres its guess, which is not what we bake.
- **Bake split: WAPE per variety.** The split is a mean-share problem with no
  asymmetry (the buffer already lives in the Poolish total). WAPE is chosen over
  MAPE because MAPE is undefined when an actual is zero and, on a 35× volume
  spread across varieties, over-weights misses on the smallest one.

## Every candidate buffers the same way: relative residuals

To score models on pinball we need each to emit a 95th-percentile quantity, not
just a point forecast. If each model derived its quantile by a different
mechanism (empirical same-weekday percentiles for the naive models, native
prediction intervals for ETS/SARIMA), pinball would partly measure the *interval
machinery* rather than the forecast. So every candidate uses one uniform
mechanism: **P95 = point forecast × (1 + the 95th-percentile relative residual)**,
where relative residuals ((actual − forecast) / forecast) are collected from that
model's own prior rolling-origin forecasts.

Relative rather than absolute residuals so the buffer scales with volume — a
big-swing Sunday gets a larger absolute buffer than a quiet Tuesday — capturing
weekday-varying spread without slicing the residual pool seven ways. This assumes
spread grows roughly in proportion to volume, which holds visibly in this data.

## Evaluation is rolling-origin over the recent ~26 weeks

Each day in the last ~26 weeks is forecast from only prior data (Poolish at lead
3, split at lead 2). Recent, because Demand is trending down ~8%/yr and we want
the model that is best *now*; ~26 weeks, because a 28-day holdout gives only ~4
of each weekday — too few to rank ~6 models without a single odd day flipping the
winner.
