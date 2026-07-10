"""Pure scoring and buffering primitives for the bake-forecast model
comparison: pinball loss at a Service Level, WAPE, and the P95
relative-residual buffer transform every candidate model shares.

These are the metric/buffer seam only — no I/O, no model callables, no
rolling-origin replay. A follow-up ticket (03+) builds the rolling-origin
evaluator on top of these, analogous to backtest.compare.
"""
from typing import Union

import pandas as pd

Quantity = Union[float, pd.Series]


def pinball(actual: pd.Series, forecast: pd.Series, level: float) -> float:
    """Mean pinball (quantile) loss at a Service Level.

    Per point: level * (actual - forecast) when actual >= forecast (an
    under-forecast), else (1 - level) * (forecast - actual) (an
    over-forecast). At level=0.95 an under-forecast is penalised
    level / (1 - level) = 19x as hard as an equal-magnitude over-forecast —
    the asymmetry that makes this the right metric for a Stockout-averse bake
    decision, where MAPE/MAE would not distinguish the two directions.
    """
    diff = actual - forecast
    loss = diff.where(diff >= 0, other=0.0) * level + (-diff).where(
        diff < 0, other=0.0
    ) * (1 - level)
    return float(loss.mean())


def wape(actual: pd.Series, forecast: pd.Series) -> float:
    """Weighted absolute percentage error: total absolute error over total
    actual. Unlike MAPE, an individual zero actual does not blow up the
    metric — it still contributes its absolute error to the numerator, but
    the denominator is the total across the series, not that one row.

    Undefined — NaN, not zero or an error — when the total actual is zero
    (including the empty-comparison case), mirroring backtest.mape's
    convention for its degenerate case.
    """
    total_actual = actual.sum()
    if actual.empty or total_actual == 0:
        return float("nan")
    return float((actual - forecast).abs().sum() / total_actual)


def p95_buffer(
    point_forecast: Quantity, relative_residuals: pd.Series, level: float = 0.95
) -> Quantity:
    """Buffer a point forecast to its P{level} using a model's own prior
    relative residuals ((actual - forecast) / forecast).

    Takes the `level`-th percentile `q` of relative_residuals (pandas'
    default linear interpolation between closest ranks) and returns
    point_forecast * (1 + q). Multiplicative so the absolute buffer grows
    with volume — a high-swing Sunday gets a bigger buffer than a quiet
    Tuesday from the same residual pool.

    point_forecast may be a scalar or a pd.Series; the same multiplier is
    applied to every element, so buffering scales linearly with the point
    forecast.
    """
    q = relative_residuals.quantile(level, interpolation="linear")
    return point_forecast * (1 + q)
