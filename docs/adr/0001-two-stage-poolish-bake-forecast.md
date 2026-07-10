# Forecast the bake as two stages: a buffered Poolish total, then a split

The baking decision is not one forecast but two, at two lead times and two
grains, so we model it as two targets rather than as five independent
per-variety Demand Forecasts (as the original pilot did):

- **Poolish (lead ~3 days).** Forecast *total* Wheat Dough Demand — the sum of
  `everything`, `plain`, `sesame` — and take the **95th percentile** of that
  total as the quantity of Poolish to make. The Service Level buffer lives
  here, at the total, because the Poolish is one shared batch.
- **Bake split (lead ~2 days).** Forecast each variety's expected share and
  divide the already-fixed Poolish across the three by that share.

## Why not per-variety P95 summed

The Poolish is a hard cap: on bake day you split a fixed amount of dough, so you
cannot bake all three varieties to their individual 95th percentiles at once —
that needs more dough than a 95th-percentile *total* batch. Statistically the
same point: **quantiles do not add.** The 95th percentile of the total is less
than the sum of the three per-variety 95th percentiles, because the varieties do
not all peak on the same day. Summing per-variety quantiles would systematically
over-provision. So the buffer is applied once, to the total, and the split uses
expected (mean) shares only.

## Consequences

- The family Sales Forecast rollup in `forecast.py` (sum of per-Product Demand
  Forecasts) is valid only for *mean* forecasts. It must not be fed quantile
  forecasts — see `roll_up_sales_forecast`.
- Gluten-free varieties leave the forecast scope entirely (bought in frozen, not
  baked). `cinnamon raisin` / `pumpernickel` remain skipped as before.
- Staffing/revenue planning (a ~2-week-lead, aggregate, mean-based decision) and
  ingredient-recipe ordering are deliberately out of scope here; they have
  different lead times and loss functions and are tracked separately.
