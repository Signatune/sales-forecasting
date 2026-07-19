"""Forecasting model definitions and the pure scoring/buffer primitives.

One definition of each candidate model the shop runs — EWMA seasonal-naive and
Holt-Winters / ETS — plus the pure metrics they are judged on (pinball loss at a
Service Level, WAPE, coverage, and the P95 relative-residual buffer). Both halves
are reused, not re-implemented: the daily forecast engine points a model at one
Forecast Target's summed series, and the analysis layer reduces the logged
forecasts with the same scoring functions.

Every model is a `(sales, as_of, scope, ..., horizon) -> DataFrame[product,
date, forecast_quantity]` callable, plus whatever hyperparameters that model
takes (EWMA's `halflife`; ETS has none). `scope` is required — there is no
default set of Products a model forecasts. The caller always names what to
forecast: the baked varieties, or a lone `[target_name]` against a frame whose
only Product is that Target's summed series. `horizon` is the `(first, last)`
lead range to cover, defaulting to the incumbent forecast.HORIZON_DAYS; the
daily engine passes its configured `(1, horizon_days)` instead. A model reads
only Sales strictly before `as_of` (forecast.history_before), so a replayed past
origin never sees its own target.
"""
import datetime as dt
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd

import forecast

Quantity = Union[float, pd.Series]

# EWMA seasonal-naive: same-weekday mean with exponentially-decaying weights.
# A 3-week half-life halves a same-weekday Sale's weight every 3 observations, so
# the forecast tracks recent Demand closely while still smoothing across roughly
# a quarter of history.
EWMA_HALFLIFE_WEEKS = 3

# A weekly-seasonal ETS needs at least two full 7-day cycles to estimate a
# seasonal component; a series with less history than this falls back to a
# same-weekday mean rather than failing to fit.
_ETS_MIN_OBSERVATIONS = 2 * 7


# --- EWMA seasonal-naive ---------------------------------------------------


def _in_scope_history(
    sales: pd.DataFrame, as_of: dt.date, scope: Sequence[str]
) -> pd.DataFrame:
    """Leak-free Sales strictly before as_of, narrowed to the Product `scope` —
    the shared front of the model callables here. history_before applies no
    Product scope (callers select what they emit), so each model must, exactly
    as forecast_demand and backtest.moving_average_forecast do.

    `scope` is required and caller-supplied: the baked varieties, or a lone
    `[target_name]` when the engine points a model at one Forecast Target's
    summed series. The arithmetic below is the same whichever it is — only which
    Products it runs over changes.
    """
    history = forecast.history_before(sales, as_of)
    return history[history["product"].isin(scope)]


def _demand_forecast_frame(records: List[dict]) -> pd.DataFrame:
    """Shape point-forecast records into the Demand Forecast contract: exactly
    the columns, dtypes and ordering forecast.forecast_demand returns, so a
    model's output is indistinguishable from the incumbent's downstream."""
    frame = pd.DataFrame(records, columns=["product", "date", "forecast_quantity"])
    # Nanoseconds, matching forecast.forecast_demand — see the note there.
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    frame["forecast_quantity"] = frame["forecast_quantity"].astype(float)
    return frame.sort_values(["date", "product"], ignore_index=True)


def _same_weekday_reduce(
    history: pd.DataFrame,
    as_of: dt.date,
    reducer: Callable[[pd.Series], float],
    scope: Sequence[str],
    horizon: Tuple[int, int],
) -> pd.DataFrame:
    """Reduce each scoped Product's same-weekday Sales, in date order, to one
    Demand Forecast per target date.

    `reducer` is what the model contributes: it receives that Product's
    same-weekday quantities oldest-first and returns the point forecast (here an
    exponentially-weighted mean). A Product that never sold on a target's weekday
    yields no row — no evidence to reduce.

    `scope` is required; the engine passes a lone `[target_name]` so the same
    reduction runs over one Forecast Target series. `horizon` is the `(first,
    last)` lead range to cover.
    """
    records = []
    for target in forecast.target_dates(as_of, horizon):
        weekday = target.dayofweek
        for product in scope:
            same_weekday = history[
                (history["product"] == product)
                & (history["date"].dt.dayofweek == weekday)
            ].sort_values("date")["quantity"]
            if same_weekday.empty:
                continue
            records.append(
                {"product": product, "date": target,
                 "forecast_quantity": float(reducer(same_weekday))}
            )
    return _demand_forecast_frame(records)


def ewma_forecast(
    sales: pd.DataFrame,
    as_of: dt.date,
    scope: Sequence[str],
    halflife: float = EWMA_HALFLIFE_WEEKS,
    horizon: Tuple[int, int] = forecast.HORIZON_DAYS,
) -> pd.DataFrame:
    """Recency-weighted same-weekday mean: a Product's same-weekday Sales in date
    order reduced by an exponentially-weighted mean whose weight halves every
    `halflife` observations (pandas ewm, adjust=True).

    The most recent same-weekday Sale counts most and older ones fade smoothly,
    so — unlike an equal-weight same-weekday mean — a declining series is
    forecast below its all-history mean, tracking the downtrend rather than the
    (higher) distant past. A Product that never sold on the target's weekday
    yields no row.

    `scope` names the Products to forecast: the baked varieties, or a lone
    `[target_name]` for one Forecast Target's summed series. `horizon` is the
    `(first, last)` lead range to forecast, defaulting to the incumbent
    forecast.HORIZON_DAYS; the daily engine passes its configured
    `(1, horizon_days)`.
    """
    history = _in_scope_history(sales, as_of, scope)
    return _same_weekday_reduce(
        history,
        as_of,
        lambda s: s.ewm(halflife=halflife, adjust=True).mean().iloc[-1],
        scope,
        horizon,
    )


# --- Pure scoring / buffering primitives -----------------------------------
#
# The metrics the logged forecasts are judged on, reused by the analysis layer.
# Each is a pure function of two aligned Series; none reaches back into a model.


def pinball_losses(actual: pd.Series, predicted: pd.Series, level: float) -> pd.Series:
    """The pinball (quantile) loss of each single day, un-averaged.

    Per day: level * (actual - predicted) when actual >= predicted (an
    under-forecast), else (1 - level) * (predicted - actual) (an over-forecast).
    At level=0.95 an under-forecast is penalised level / (1 - level) = 19x as
    hard as an equal-magnitude over-forecast — the asymmetry that makes this the
    right metric for a Stockout-averse bake decision, where MAPE/MAE would not
    distinguish the two directions.

    Public in its un-averaged form because the day-to-day spread is what says
    whether one model's lower mean is evidence or noise: the analysis layer pairs
    two models' daily losses day-by-day and tests their difference. A mean alone
    cannot answer that.
    """
    diff = actual - predicted
    return diff.where(diff >= 0, other=0.0) * level + (-diff).where(
        diff < 0, other=0.0
    ) * (1 - level)


def pinball(actual: pd.Series, predicted: pd.Series, level: float) -> float:
    """Mean pinball loss at a Service Level — the daily losses averaged over the
    days scored."""
    return float(pinball_losses(actual, predicted, level).mean())


def wape(actual: pd.Series, predicted: pd.Series) -> float:
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
    return float((actual - predicted).abs().sum() / total_actual)


def coverage(actual: pd.Series, quantity: pd.Series) -> float:
    """The share of days a quantity actually covered Demand — the *realised*
    Service Level, read against the 95% a buffer targets.

    A day the bake exactly met Demand counts as covered: nothing was left over,
    but nobody was turned away either. Pinball says how costly the misses were;
    this says how often they happened.

    Undefined — NaN — for an empty comparison, as wape and backtest.mape are.
    """
    if actual.empty:
        return float("nan")
    return float((quantity >= actual).mean())


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


# --- Holt-Winters / ETS (opt-in; needs the statsmodels extra) --------------


def _same_weekday_means(recorded: pd.Series) -> pd.Series:
    """Mean quantity by weekday (0=Mon .. 6=Sun) of a date-indexed Sales
    series — the same-weekday mean reused here both to fill regularization gaps
    and as the ETS fallback."""
    return recorded.groupby(recorded.index.dayofweek).mean()


def _regular_daily_series(history: pd.DataFrame, product: str):
    """One Product's Sales as a gap-free daily series, or None if it never sold.

    normalize.py emits no row for a day a Product didn't sell, so a Product's
    recorded history is irregular — but ExponentialSmoothing with
    seasonal_periods=7 needs a regular daily index. We reindex to the continuous
    daily range the recorded days span (all strictly before as_of, because
    `history` is already history_before, so this never crosses the cutoff) and
    fill each inserted gap with that Product's mean Sales on the *same weekday*.
    That preserves the weekly cycle the seasonal component is about to estimate
    rather than puncturing it with a holiday zero; a weekday never observed at
    all backs off to the series mean.
    """
    recorded = (
        history.loc[history["product"] == product]
        .groupby("date")["quantity"]
        .sum()
        .sort_index()
    )
    if recorded.empty:
        return None

    full = pd.date_range(recorded.index.min(), recorded.index.max(), freq="D")
    series = recorded.reindex(full)

    weekday_means = _same_weekday_means(recorded)
    gap_index = series.index[series.isna()]
    if len(gap_index):
        series.loc[gap_index] = gap_index.dayofweek.map(weekday_means)
    return series.fillna(recorded.mean())


def _ets_points(
    series: pd.Series, targets: List[pd.Timestamp], smoother
) -> Dict[pd.Timestamp, Optional[float]]:
    """Fit a weekly-seasonal additive ETS on the regular series and read off each
    target date's point forecast.

    Additive trend and additive seasonal (not multiplicative) so both a
    zero-Sales holiday and a downtrend are safe — multiplicative terms choke on
    non-positive values. Forecasts far enough to reach the furthest target, then
    maps forecast dates (series_end + 1, +2, ...) onto the targets.
    """
    series_end = series.index.max()
    steps = max((max(targets) - series_end).days, 1)

    fitted = smoother(
        series.to_numpy(dtype=float),
        trend="add",
        seasonal="add",
        seasonal_periods=7,
        initialization_method="estimated",
    ).fit()
    forecasts = fitted.forecast(steps)
    by_date = {
        series_end + pd.Timedelta(days=i + 1): value
        for i, value in enumerate(forecasts)
    }
    return {target: by_date.get(target) for target in targets}


def ets_forecast(
    sales: pd.DataFrame,
    as_of: dt.date,
    scope: Sequence[str],
    horizon: Tuple[int, int] = forecast.HORIZON_DAYS,
) -> pd.DataFrame:
    """A per-Product Holt-Winters / ETS Demand Forecast — one point per target
    date — the classic seasonal reference model.

    Conforms to the same (sales, as_of, scope) -> [product, date,
    forecast_quantity] seam as ewma_forecast. statsmodels is imported *lazily*
    here so importing this module never requires it: ETS lives in the `forecast`
    extra, not the base or test-required deps, and is re-fit from history on
    every call (no fitted state is carried between days).

    Emits a POINT forecast only — never its native prediction interval — so a
    downstream buffer (p95_buffer) measures forecast quality, not interval
    machinery.

    Robustness: a Product with fewer than two weekly cycles of history, or one
    whose fit fails to converge, falls back to a same-weekday mean rather than
    raising; a Product with no history at all yields no row. Negative point
    forecasts (a steep additive downtrend extrapolated out) are floored at zero.

    `scope` names the Products to forecast: the baked varieties, or a lone
    `[target_name]` for one Forecast Target's summed series. `horizon` is the
    `(first, last)` lead range to forecast, as in ewma_forecast.
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    history = _in_scope_history(sales, as_of, scope)
    targets = forecast.target_dates(as_of, horizon)

    records = []
    for product in scope:
        series = _regular_daily_series(history, product)
        if series is None:
            continue

        points = None
        if len(series) >= _ETS_MIN_OBSERVATIONS:
            try:
                points = _ets_points(series, targets, ExponentialSmoothing)
            except Exception:
                points = None
        if points is None:
            weekday_means = _same_weekday_means(series)
            points = {t: weekday_means.get(t.dayofweek) for t in targets}

        for target in targets:
            value = points.get(target)
            if value is None or pd.isna(value):
                continue
            records.append(
                {
                    "product": product,
                    "date": target,
                    "forecast_quantity": max(float(value), 0.0),
                }
            )

    return _demand_forecast_frame(records)
