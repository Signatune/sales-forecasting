# Sales Forecasting

Forecasts demand for products sold in a deli and bakery, producing an aggregate sales forecast derived from the sum of each product's projected demand.

## Language

**Product**:
A single forecastable item sold in the deli or bakery (e.g. a sourdough loaf, a turkey club sandwich).
_Avoid_: Item, SKU, good

**Category**:
A grouping of related Products (e.g. Bakery, Deli, Prepared Foods).
_Avoid_: Department, group, section

**Demand**:
The quantity of a Product customers would purchase if unconstrained by available stock.
_Avoid_: Sales, orders

**Sales**:
The quantity of a Product actually recorded as sold in a given period. May understate Demand during a Stockout.
_Avoid_: Demand, revenue

**Stockout**:
A period during which a Product has zero available stock, suppressing Sales below true Demand.
_Avoid_: Out of stock, shortage

**Demand Forecast**:
A projected quantity of Demand for a single Product over a future period.
_Avoid_: Prediction, estimate

**Sales Forecast**:
The aggregate projected revenue or units across all Products' Demand Forecasts for a future period.
_Avoid_: Forecast (alone), projection

## Baking

**Wheat Dough**:
The single dough shared by the baked bagel varieties (`everything`, `plain`, `sesame`), which differ only by topping applied at shaping. Distinct from the gluten-free varieties, which are bought in frozen and not baked.
_Avoid_: Base, batter

**Poolish**:
The pre-ferment made in one batch for the whole Wheat Dough, decided ~3 days ahead of a bake. Its quantity caps how many bagels can be baked that day, across all varieties combined.
_Avoid_: Starter, pre-ferment (informally), sponge

**Bake-to Quantity**:
The number of a single variety to shape and bake on a given day, decided ~2 days ahead by splitting the fixed Poolish across varieties by expected share.
_Avoid_: Bake amount, production target

**Service Level**:
The probability that Demand is met without a Stockout, chosen as a target (currently 95%). It sets how much buffer the Poolish quantity carries above expected Demand: a lost sale is treated as far costlier than a leftover, so the total is forecast at that upper quantile rather than at the mean.
_Avoid_: Fill rate, coverage
