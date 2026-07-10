# Narrow the baked-bagel forecast scope

Status: ready-for-agent

## Parent

`.scratch/bake-forecast-model-comparison/PRD.md`

## What to build

The forecast covers only the three baked varieties — `everything`, `plain`,
`sesame` — which share one Wheat Dough. The two gluten-free varieties are bought
in frozen and never baked, so they leave the forecast scope entirely; their Sales
stay in `sales_history.parquet` for later use. `cinnamon raisin` and
`pumpernickel` remain skipped exactly as before.

This is a prefactor that makes every later ticket's scope correct from the start.
Per the developer's decision, the change lives in the shipped `forecast.py`
scope: the model is unchanged, only which Products it covers. Update `forecast.py`
and `backtest.py` (and their tests) so nothing silently disagrees about which
Products are in scope, and so the family Sales Forecast and its caveat reflect
the three baked varieties.

Demoable: running the forecast produces Demand Forecasts for the three baked
varieties only, with the gluten-free varieties absent from the family total and
their Sales still present in the history.

## Acceptance criteria

- [ ] The gluten-free varieties are no longer forecast; their Sales remain in `sales_history.parquet`
- [ ] `everything`, `plain`, `sesame` are the forecast scope; `cinnamon raisin` / `pumpernickel` stay skipped with their existing reasons
- [ ] The family Sales Forecast and its printed caveat reflect the narrowed scope
- [ ] `forecast.py` and `backtest.py` tests updated to the three-variety scope and passing
- [ ] No module silently disagrees about which Products are in scope

## Blocked by

- None — can start immediately.
