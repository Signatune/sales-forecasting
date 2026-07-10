"""Compare candidate forecasting models for the bake decision.

    .venv/bin/python model_comparison.py

The bottom half of this module is the pure scoring/buffering seam — pinball
loss at a Service Level, WAPE, and the P95 relative-residual buffer — that
every candidate reduces on. The top half is the rolling-origin evaluator built
on it, analogous to backtest.compare but scoring the two bake targets on the
metrics they actually care about (see the two ADRs under docs/adr/).

The headline target is the **Poolish total**: the summed Wheat Dough Demand of
the three baked varieties, whose 95% Service Level quantile is how much Poolish
to make ~3 days ahead. Every candidate is a
`(sales, as_of) -> DataFrame[product, date, forecast_quantity]` callable — the
exact shape of forecast.forecast_demand and backtest.moving_average_forecast —
and its per-variety forecasts are summed per date into one synthetic wheat-total
Product (wheat_total). So the same callables that forecast a variety forecast
the total, and a new candidate drops into POOLISH_CANDIDATES without special
casing.

Three things keep the comparison honest. Every day in the recent ~26 weeks is
forecast from only prior data (forecast.history_before, at the Poolish lead of
3 days). Every candidate turns its point forecast into a P95 the same way — the
uniform relative-residual buffer (p95_buffer), with the buffer's own residuals
collected from a warmup window strictly before the evaluation window, so the
buffer never sees the days it is scored on either. And the total is scored on
pinball@95, which penalises under-forecasting 19x as hard as over — the
asymmetry a Stockout-averse bake decision turns on, and one MAPE cannot see.
"""
import datetime as dt
import sys
from typing import Callable, Dict, List, Union

import pandas as pd

import backtest
import forecast
from backtest import mape

Quantity = Union[float, pd.Series]

# A candidate model: prior Sales and an as_of, out a per-Product Demand Forecast.
Model = Callable[[pd.DataFrame, dt.date], pd.DataFrame]

SALES_HISTORY_PATH = forecast.SALES_HISTORY_PATH

# The synthetic Product the three baked varieties sum into per date — the
# Poolish is decided on this total, not per variety (ADR 0001).
WHEAT_TOTAL = "wheat total"

# Recent, because Demand trends down ~8%/yr and we want the model best *now*;
# ~26 weeks because a 4-week holdout gives too few of each weekday to rank
# ~6 models without one odd day flipping the winner (ADR 0002).
EVAL_WEEKS = 26

# The buffer's relative residuals come from this many weeks immediately before
# the evaluation window — enough same-lead forecasts to take a stable P95 from,
# and strictly earlier than any day the buffer is then scored on.
WARMUP_WEEKS = 26

# The Poolish is decided ~3 days ahead, so every origin forecasts its target
# from exactly three days back — the lead an ordering run actually faces.
POOLISH_LEAD = 3

# The Service Level the Poolish total is buffered and scored at.
SERVICE_LEVEL = 0.95

# The two models the pilot already ran, wired first so the tracer-bullet table
# has real numbers. Later tickets add the pandas candidates and ETS by dropping
# entries in here — the evaluator treats every entry identically.
POOLISH_CANDIDATES: Dict[str, Model] = {
    "seasonal_naive": forecast.forecast_demand,
    "moving_average": backtest.moving_average_forecast,
}


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


# --- Rolling-origin evaluator on the Poolish total -------------------------


def wheat_total(per_variety_forecast: pd.DataFrame) -> pd.DataFrame:
    """Sum a candidate's per-variety Demand Forecasts per date into the single
    synthetic wheat-total Product — the Poolish is decided on this total, not
    per variety (ADR 0001). Whatever varieties a model forecast that day are
    summed; a date the model declined entirely yields no row rather than a
    fabricated zero.

    Same columns as the input, so the total is just another Demand Forecast and
    the same downstream code scores it exactly as it would a variety.
    """
    if per_variety_forecast.empty:
        return pd.DataFrame(columns=["product", "date", "forecast_quantity"])
    total = per_variety_forecast.groupby("date", as_index=False)[
        "forecast_quantity"
    ].sum()
    total["product"] = WHEAT_TOTAL
    return total[["product", "date", "forecast_quantity"]].sort_values(
        "date", ignore_index=True
    )


def evaluation_window(
    sales: pd.DataFrame, weeks: int = EVAL_WEEKS
) -> tuple:
    """The first and last date (inclusive) of the evaluation period: the most
    recent `weeks` weeks of the Sales history. Refuses a window that leaves no
    earlier Sales to forecast from, mirroring backtest.holdout_window."""
    end = sales["date"].max()
    start = end - pd.Timedelta(days=weeks * 7 - 1)
    if sales["date"].min() >= start:
        raise ValueError(
            f"evaluating {weeks} weeks would leave no Sales before {start.date()} "
            f"to forecast from — the history begins {sales['date'].min().date()}"
        )
    return start, end


def _open_days(
    sales: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> List[pd.Timestamp]:
    """Dates in [start, end] on which some forecast variety sold — the days the
    Poolish total is asked about. A day no variety sold on is a day both
    locations were closed, and is not scored."""
    in_scope = sales[sales["product"].isin(forecast.FORECAST_PRODUCTS)]
    window = in_scope[(in_scope["date"] >= start) & (in_scope["date"] <= end)]
    return sorted(window["date"].unique())


def forecast_totals(
    model: Model, sales: pd.DataFrame, days: List[pd.Timestamp], lead: int
) -> pd.DataFrame:
    """Each day's wheat-total point forecast at a fixed lead, every one made
    from only the Sales strictly before its origin.

    For a target day D the origin is D - lead, so history_before(D - lead)
    inside the model guarantees the forecast never sees D (or anything after
    it). Returns (date, forecast_quantity); a day the model forecast no variety
    for yields NaN, so it drops out of scoring rather than counting as zero.
    """
    rows = []
    for day in days:
        day = pd.Timestamp(day)
        as_of = (day - pd.Timedelta(days=lead)).date()
        total = wheat_total(model(sales, as_of))
        match = total.loc[total["date"] == day, "forecast_quantity"]
        rows.append(
            {"date": day, "forecast_quantity": float(match.iloc[0]) if len(match) else float("nan")}
        )
    return pd.DataFrame(rows, columns=["date", "forecast_quantity"])


def actual_totals(
    sales: pd.DataFrame, days: List[pd.Timestamp]
) -> pd.DataFrame:
    """The actual wheat-total Sales on each day: the three baked varieties
    summed, an absent variety counting as zero for that day."""
    in_scope = sales[sales["product"].isin(forecast.FORECAST_PRODUCTS)]
    rows = []
    for day in days:
        day = pd.Timestamp(day)
        actual = in_scope.loc[in_scope["date"] == day, "quantity"].sum()
        rows.append({"date": day, "actual": float(actual)})
    return pd.DataFrame(rows, columns=["date", "actual"])


def _relative_residuals(
    model: Model, sales: pd.DataFrame, days: List[pd.Timestamp], lead: int
) -> pd.Series:
    """A model's own (actual - forecast) / forecast on the wheat total over the
    given days — the pool the P95 buffer takes its 95th percentile from. Days
    the model declined, or forecast zero for (a division by zero), drop out."""
    merged = forecast_totals(model, sales, days, lead).merge(
        actual_totals(sales, days), on="date"
    )
    usable = merged[merged["forecast_quantity"] > 0]
    return (usable["actual"] - usable["forecast_quantity"]) / usable[
        "forecast_quantity"
    ]


def _score_candidate(
    model: Model,
    sales: pd.DataFrame,
    eval_days: List[pd.Timestamp],
    warmup_days: List[pd.Timestamp],
    lead: int,
    level: float,
) -> Dict[str, float]:
    """One candidate's Poolish-total scores: pinball@level on its P95-buffered
    total, and MAPE on the unbuffered point forecast as a familiar sanity
    column. The buffer's residuals come from the warmup days only, so the P95 a
    day is scored on never saw that day."""
    residuals = _relative_residuals(model, sales, warmup_days, lead)

    scored = (
        forecast_totals(model, sales, eval_days, lead)
        .merge(actual_totals(sales, eval_days), on="date")
        .dropna(subset=["forecast_quantity"])
        .reset_index(drop=True)
    )
    if residuals.empty:
        buffered = scored["forecast_quantity"]
    else:
        buffered = p95_buffer(scored["forecast_quantity"], residuals, level)

    positive = scored[scored["actual"] > 0]
    return {
        "pinball": pinball(scored["actual"], buffered, level),
        "mape": mape(positive["actual"], positive["forecast_quantity"]),
        "days": len(scored),
    }


def compare_models(
    sales: pd.DataFrame,
    candidates: Dict[str, Model] = None,
    eval_weeks: int = EVAL_WEEKS,
    warmup_weeks: int = WARMUP_WEEKS,
    lead: int = POOLISH_LEAD,
    level: float = SERVICE_LEVEL,
) -> pd.DataFrame:
    """Replay every candidate over the recent evaluation window and rank them on
    pinball@level for the Poolish total.

    One row per candidate — its pinball@level, MAPE, and the day count — sorted
    best (lowest pinball) first. The window is the last `eval_weeks` weeks; the
    buffer's residuals come from the `warmup_weeks` immediately before it, so no
    scored day feeds its own buffer. Each day is forecast once, at `lead` days
    back, from only prior Sales.
    """
    candidates = candidates if candidates is not None else POOLISH_CANDIDATES

    eval_start, eval_end = evaluation_window(sales, eval_weeks)
    warmup_end = eval_start - pd.Timedelta(days=1)
    warmup_start = warmup_end - pd.Timedelta(days=warmup_weeks * 7 - 1)

    eval_days = _open_days(sales, eval_start, eval_end)
    warmup_days = _open_days(sales, warmup_start, warmup_end)

    rows = [
        {"model": name, **_score_candidate(model, sales, eval_days, warmup_days, lead, level)}
        for name, model in candidates.items()
    ]
    return pd.DataFrame(rows, columns=["model", "pinball", "mape", "days"]).sort_values(
        "pinball", ignore_index=True
    )


def _format_report(comparison: pd.DataFrame, level: float) -> str:
    pct = int(round(level * 100))
    lines = [
        f"Poolish total — {len(comparison)} candidates, "
        f"ranked by pinball@{pct} (lower is better)",
        "",
        f"{'model':20} {f'pinball@{pct}':>12} {'MAPE':>8} {'days':>6}",
    ]
    for row in comparison.itertuples():
        lines.append(
            f"{row.model:20} {row.pinball:12.2f} {row.mape:7.1f}% {row.days:6}"
        )
    lines += [
        "",
        f"pinball@{pct} scores each model's P95-buffered wheat-total forecast; it "
        f"penalises under-",
        "forecasting 19x as hard as over. MAPE is on the unbuffered point forecast, "
        "a sanity column only.",
    ]
    return "\n".join(lines)


def main() -> None:
    sales = pd.read_parquet(SALES_HISTORY_PATH)
    comparison = compare_models(
        sales, eval_weeks=EVAL_WEEKS, warmup_weeks=WARMUP_WEEKS
    )
    print(_format_report(comparison, SERVICE_LEVEL))


if __name__ == "__main__":
    sys.exit(main())
