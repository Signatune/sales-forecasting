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
