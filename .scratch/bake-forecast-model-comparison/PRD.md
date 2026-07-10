# Bake Forecast Model Comparison

Status: ready-for-agent

## Problem Statement

The bagel pilot proved a Sales-ingestion → Demand-Forecast pipeline end-to-end
with a single seasonal-naive model, but never asked whether that model is any
good for the decision it feeds. The baker needs two numbers: how much Poolish to
make ~3 days ahead, and how many of each variety to bake ~2 days ahead. The
current model averages every same-weekday Sale with equal weight, which on a
Demand series trending down ~8%/yr is structurally biased high — and it was only
ever scored on MAPE, a metric that ignores the fact that a Stockout hurts far
more than a leftover. The baker has no evidence about which forecasting approach
actually produces the best bake-to numbers, or by how much.

## Solution

Compare a set of candidate forecasting models against the loss functions the
baking decision actually cares about, on the backfilled Sales history, and
recommend the best one for each of the two bake targets. Model the bake as two
stages (see `docs/adr/0001-two-stage-poolish-bake-forecast.md`):

- **Poolish (lead ~3 days):** forecast the *total* Wheat Dough Demand — the sum
  of `everything`, `plain`, `sesame` — and take its 95% Service Level quantile as
  the Poolish quantity to make. The buffer lives here, at the total.
- **Bake split (lead ~2 days):** forecast each variety's expected share and
  divide the fixed Poolish across the three by that share.

Score the Poolish total on pinball loss at 95%, and the split on WAPE per
variety (see `docs/adr/0002-score-bake-forecasts-on-pinball-and-wape.md`).
Evaluate every candidate on a rolling-origin replay over the recent ~26 weeks,
and publish a comparison table plus inspection charts. The output is a written
recommendation, not a shipped model — `forecast.py` is left untouched until a
follow-up promotes the winners.

## User Stories

1. As a baker, I want a recommended Poolish quantity ~3 days ahead, so that I can
   make one pre-ferment batch large enough to cover demand at a 95% Service Level.
2. As a baker, I want a recommended Bake-to Quantity per variety ~2 days ahead,
   so that I can split the fixed Poolish across `everything`, `plain`, and
   `sesame` in the proportions demand will actually take.
3. As a baker, I want the Poolish quantity to carry a deliberate buffer above
   expected Demand, so that I only stock out roughly 1 day in 20 rather than half
   the time.
4. As a baker, I want the buffer applied once to the total rather than to each
   variety, so that I am not told to make more dough than one 95% batch, which I
   physically cannot bake into three separate 95% piles.
5. As a shop owner, I want to know which forecasting model produces the best
   bake-to numbers and by how much, so that I can decide whether it is worth
   replacing the current seasonal-naive model.
6. As a shop owner, I want the comparison to include the model we already run, so
   that any recommended change is measured against the incumbent, not a strawman.
7. As a shop owner, I want a dumb moving-average baseline in the comparison, so
   that I can see whether the seasonal models earn their complexity at all.
8. As an analyst, I want recency-weighted and trend-aware candidates included, so
   that the comparison can reveal the high-bias problem the equal-weight average
   has on a declining series.
9. As an analyst, I want a classic Holt-Winters/ETS model included, so that I
   know whether a textbook seasonal method beats the simple recency-weighted ones
   or whether it does not earn its dependency.
10. As an analyst, I want every candidate to derive its 95% quantity by the same
    mechanism, so that the pinball comparison measures forecast quality and not
    whose interval math I trusted.
11. As an analyst, I want the buffer to scale with volume, so that a high-swing
    Sunday gets a larger absolute buffer than a quiet Tuesday without slicing the
    residual pool seven ways.
12. As an analyst, I want each model scored on the recent ~26 weeks, so that the
    winner is the model that is best now given the downtrend, not best on average
    over two years.
13. As an analyst, I want every day scored from only data strictly before it, so
    that no model is flattered by seeing actuals it would not have had.
14. As an analyst, I want the two bake targets scored on different metrics
    (pinball for the buffered total, WAPE for the split), so that each score
    matches the decision it informs.
15. As an analyst, I want the split scored on WAPE rather than MAPE, so that a
    miss on the smallest variety is not weighted as if it mattered more than a
    larger absolute miss on a big one, and zero-Sales days do not blow up the
    metric.
16. As a baker, I want charts of forecast vs actual for the Poolish total across
    the holdout, so that I can eyeball whether weekend peaks land in the right
    place and magnitudes are sane before trusting any number.
17. As a baker, I want a chart of how often each model's Poolish quantity actually
    covered demand, so that I can confirm the model hits close to the 95% target
    rather than over- or under-shooting it.
18. As a baker, I want to see the split accuracy per variety, so that I can trust
    that the everything/plain/sesame proportions are right, not just the total.
19. As an analyst, I want a single comparison table ranking all candidates per
    target, so that the recommendation is legible at a glance.
20. As an analyst, I want a written conclusion naming the winner per target and
    the margin, so that the follow-up promotion ticket has a clear mandate.
21. As a maintainer, I want the gluten-free varieties dropped from forecasting
    but kept in the Sales history, so that they are available later without
    polluting a comparison for products we buy in frozen and never bake.
22. As a maintainer, I want the day-restricted varieties (`cinnamon raisin`,
    `pumpernickel`) to stay skipped exactly as the pilot skips them, so that this
    effort does not silently re-scope them.
23. As a maintainer, I want each candidate model to have the same callable shape
    as the existing forecast, so that the evaluator treats them uniformly and I
    can drop a new candidate in without special-casing.
24. As a maintainer, I want the winners left un-promoted and `forecast.py`
    untouched, so that this effort is a reversible experiment and shipping is a
    separate, reviewed step.

## Implementation Decisions

- **New module for candidate models.** A module holding each candidate as a
  callable with the exact signature and output shape of
  `forecast.forecast_demand` / `backtest.moving_average_forecast`:
  `(sales, as_of) -> DataFrame[product, date, forecast_quantity]`. It reuses
  `forecast.history_before` (the leak-free cutoff) and `forecast.target_dates`
  (the horizon) rather than redefining either. Candidates for the point forecast:
  seasonal-naive/all-history (the incumbent, imported from `forecast`),
  trailing-window seasonal-naive, EWMA/recency-weighted seasonal-naive,
  seasonal-plus-trend, and Holt-Winters/ETS via statsmodels. The moving-average
  baseline is reused from `backtest`.
- **The Poolish total is a pseudo-Product.** Rather than a second model shape,
  the total Wheat Dough series is produced by aggregating the three forecast
  varieties per date into one synthetic Product (e.g. a wheat-total row), so the
  same model callables forecast the total exactly as they forecast a variety.
  This keeps a single model-callable seam for both bake targets.
- **The split is expected-share allocation.** Per-variety point forecasts at lead
  2 give each variety's share; the fixed Poolish is divided by those shares. No
  second quantile buffer is applied to the split — the buffer already lives in
  the Poolish total (ADR 0001; quantiles do not add).
- **Uniform relative-residual buffering.** A pure transform turns any model's
  point forecast into its P95: collect relative residuals
  ((actual − forecast) / forecast) from that model's own prior rolling-origin
  forecasts, take their 95th percentile `q`, and return
  `point_forecast × (1 + q)`. Same mechanism for naive and ETS alike, and the
  multiplicative form makes the absolute buffer grow with volume (ADR 0002).
- **Pure metric functions.** `pinball(actual, forecast, level)` and
  `wape(actual, forecast)` live beside the existing `mape` (kept as a familiar
  sanity column). Pinball penalises under-forecasting `level/(1−level)` = 19× as
  hard as over-forecasting at 95%.
- **Rolling-origin evaluator.** A top-level comparison function analogous to
  `backtest.compare`: replay each candidate from a series of past origins across
  the recent ~26 weeks, scoring the Poolish total at lead 3 and the split at lead
  2, each origin forecasting from only prior data. It emits one comparison frame
  (per-candidate pinball@95 on the total, WAPE per variety on the split, MAPE
  alongside) that both the printed report and the notebook consume.
- **Scope constants.** `FORECAST_PRODUCTS` narrows to `everything`, `plain`,
  `sesame`; `gluten-free everything` / `gluten-free plain` move out of the
  forecast set but remain in the Sales history; `cinnamon raisin` /
  `pumpernickel` stay skipped. Whether this edits `forecast.FORECAST_PRODUCTS`
  directly or the experiment declares its own scope is left to implementation,
  but the experiment must not silently disagree with `forecast.py`.
- **Dependency.** `statsmodels` is added for ETS. It belongs in an
  experiment/notebook extra unless the evaluator itself imports it; the existing
  test suite must still run on a `dev`-only install.
- **Deliverable is a recommendation.** The effort ends with the comparison table,
  the notebook charts, a written conclusion naming a winner per target and the
  margin, and a follow-up ticket to promote the winners into `forecast.py`. No
  model is shipped here; `forecast.py` is not modified.

## Testing Decisions

- **Test external behavior, not internals.** As in the existing suites, feed a
  synthetic Sales frame (the `sales()` helper shape) into a public function and
  assert on the returned DataFrame, using numbers worked by hand rather than
  recomputed the way the code does. Do not assert on private grouping helpers or
  intermediate structures.
- **Each candidate model (Seam 1).** Pin each model's point arithmetic the way
  `TestMovingAverageBaseline` does: a small hand-built history and the exact
  forecast expected — e.g. a recency-weighted model must weight recent
  same-weekday Sales above old ones, and a declining series must forecast below
  the equal-weight mean. Also pin the shared contract: same columns/dtypes as a
  Demand Forecast, leak-freeness (no Sales on or after `as_of`, mirroring
  `TestHistoryCutoff`), and the wheat-total pseudo-Product summing the three
  varieties per date.
- **Metric functions (Seam 2).** Pin `pinball` and `wape` with hand-worked
  actuals/forecasts, including the asymmetry (an under-forecast scores 19× an
  equal over-forecast at 95%) and WAPE's behavior on a zero actual, beside the
  existing `TestMape`.
- **P95 buffer (Seam 3).** Feed a known set of relative residuals and assert the
  resulting P95 multiplier and quantity; confirm a wider residual spread yields a
  larger buffer and that the buffer scales with the point forecast.
- **Evaluator (Seam 4).** Follow `TestCompare` / `TestReplayOrigins`: assert the
  origins cover the ~26-week window with each holdout day forecast once at the
  intended lead, that no origin's forecast sees its own target date (the step-day
  fixture pattern), and that the comparison frame carries every candidate's score
  per target. A `TestMain`-style test that the printed report names each
  candidate and both targets.
- **Prior art.** `tests/test_backtest.py` (rolling-origin bookkeeping, the
  moving-average model, `mape`) and `tests/test_forecast.py` (model arithmetic,
  history cutoff, product scope, output shape) are the direct templates.

## Out of Scope

- **Shipping a model.** `forecast.py` is not modified; promoting the winners is a
  separate follow-up ticket.
- **Staffing / revenue forecasting.** A separate decision at a ~2-week lead, at
  the aggregate level, on a mean (not a quantile) — a different effort with a
  different loss function.
- **Ingredient / recipe ordering.** Rolling the bake forecast up into flour and
  topping quantities against supplier lead times — backlog.
- **Gluten-free and day-restricted varieties.** Not forecast here; their Sales
  stay in the history for later use.
- **Stockout correction.** Still no MarginEdge data; Sales remains the Demand
  proxy, as in the pilot.
- **ML / feature-based models.** Gradient-boosted or regression models with
  engineered features are excluded as overkill for three series with a clean
  weekly cycle.
- **Scheduling, deployment, database.** Local scripts, files, and a notebook
  only.

## Further Notes

- The incumbent is expected to lose to a recency-weighted seasonal model: its
  equal-weight same-weekday average cannot see the ~8%/yr downtrend and so
  forecasts high. Confirming that empirically is a primary goal.
- ETS may not earn its dependency. If recency-weighted seasonal-naive matches it
  within noise, the recommendation should say so and `statsmodels` can be dropped
  from the promoted path.
- The split may be a non-race: the mix is fairly stable (~45/29/27), so a
  constant recent share could be un-beatable, collapsing the split sub-comparison
  to a one-liner. That is a finding worth stating, not a failure.
- Open tuning questions to resolve during implementation: the trailing-window and
  EWMA spans, and the exact origin spacing for the lead-2 vs lead-3 replays.
