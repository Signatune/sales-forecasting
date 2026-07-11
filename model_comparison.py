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

The second, smaller target is the **bake split**: two days out, with the Poolish
already made and fixed, how to divide it into a Bake-to Quantity per variety
(compare_split_models). A split candidate emits a share rather than a quantity,
carries no buffer of its own — the Service Level buffer lives once, in the
Poolish total, because quantiles do not add (ADR 0001) — and is scored on WAPE
per variety, so a miss on sesame is not weighted as though it mattered more than
a larger absolute miss on everything.
"""
import datetime as dt
import sys
from typing import Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

import backtest
import forecast
from backtest import mape

Quantity = Union[float, pd.Series]

# A candidate model: prior Sales and an as_of, out a per-Product Demand Forecast.
Model = Callable[[pd.DataFrame, dt.date], pd.DataFrame]

# A split candidate: prior Sales and an as_of, out a per-variety expected
# share of the horizon's total — (product, date, share), not a quantity.
SplitModel = Callable[[pd.DataFrame, dt.date], pd.DataFrame]

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

# A Bake-to Quantity is decided ~2 days ahead — a day later than the Poolish
# itself, because shaping and baking happen after the Poolish is already made.
SPLIT_LEAD = 2

# The Service Level the Poolish total is buffered and scored at.
SERVICE_LEVEL = 0.95

# Trailing-window seasonal-naive: same-weekday mean over only the last N weeks.
# 8 weeks is ~two months — enough same-weekday observations (8) to average
# without one odd week swinging the forecast, but short enough that a Sale from
# over a year ago, ~8% higher on the downtrend, no longer drags the forecast up.
# A tuning choice; the equal-weight incumbent is this with an unbounded window.
TRAILING_WINDOW_WEEKS = 8

# EWMA seasonal-naive: same-weekday mean with exponentially-decaying weights.
# A 3-week half-life halves a same-weekday Sale's weight every 3 observations, so
# the forecast tracks recent Demand closely while still smoothing across roughly
# a quarter of history. Shorter than the trailing window's hard 8-week cutoff
# because the decay tapers the old Sales' influence rather than truncating it.
EWMA_HALFLIFE_WEEKS = 3


# --- Pure-pandas candidate models ------------------------------------------
#
# Three point-forecast candidates, each the same
# (sales, as_of) -> DataFrame[product, date, forecast_quantity] callable as
# forecast.forecast_demand: a per-variety Demand Forecast over
# forecast.FORECAST_PRODUCTS for forecast.target_dates(as_of), read from the
# leak-free forecast.history_before cutoff. They exist to expose the incumbent's
# structural high bias — it averages every same-weekday Sale with equal weight,
# so on Demand trending down ~8%/yr it keeps forecasting the higher past. Each of
# these lets recent Sales pull the forecast down toward where Demand is now. They
# emit POINT forecasts only; the evaluator buffers to P95 and scores pinball@95.


def _in_scope_history(sales: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """Leak-free Sales strictly before as_of, narrowed to the three baked
    varieties — the shared front of every candidate below. history_before
    applies no Product scope (callers select what they emit), so each candidate
    must, exactly as forecast_demand and moving_average_forecast do."""
    history = forecast.history_before(sales, as_of)
    return history[history["product"].isin(forecast.FORECAST_PRODUCTS)]


def _demand_forecast_frame(records: List[dict]) -> pd.DataFrame:
    """Shape point-forecast records into the Demand Forecast contract: exactly
    the columns, dtypes and ordering forecast.forecast_demand returns, so a
    candidate's output is indistinguishable from the incumbent's downstream."""
    frame = pd.DataFrame(records, columns=["product", "date", "forecast_quantity"])
    # Nanoseconds, matching forecast.forecast_demand — see the note there.
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    frame["forecast_quantity"] = frame["forecast_quantity"].astype(float)
    return frame.sort_values(["date", "product"], ignore_index=True)


def _same_weekday_reduce(
    history: pd.DataFrame, as_of: dt.date, reducer: Callable[[pd.Series], float]
) -> pd.DataFrame:
    """Reduce each variety's same-weekday Sales, in date order, to one Demand
    Forecast per target date — the shared body of the same-weekday candidates.

    `reducer` is the only thing that varies between them (a trailing-window mean,
    an EWMA, ...); it receives that variety's same-weekday quantities oldest-first
    and returns the point forecast. A variety that never sold on a target's
    weekday yields no row, exactly as the incumbent — no evidence to reduce.
    """
    records = []
    for target in forecast.target_dates(as_of):
        weekday = target.dayofweek
        for product in forecast.FORECAST_PRODUCTS:
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


def trailing_window_forecast(
    sales: pd.DataFrame, as_of: dt.date, weeks: int = TRAILING_WINDOW_WEEKS
) -> pd.DataFrame:
    """Same-weekday mean over only the last `weeks` observations of each variety.

    Like forecast.forecast_demand it averages a variety's recorded Sales on the
    target's weekday — but only the most recent `weeks` of them, dropping older
    same-weekday Sales entirely. On a declining series that trims the high, stale
    tail the equal-weight incumbent keeps averaging in, so the forecast sits
    closer to where Demand is now. A variety with no Sales on the target's
    weekday yields no row, exactly as the incumbent — no evidence to average.
    """
    history = _in_scope_history(sales, as_of)
    return _same_weekday_reduce(history, as_of, lambda s: s.tail(weeks).mean())


def ewma_forecast(
    sales: pd.DataFrame, as_of: dt.date, halflife: float = EWMA_HALFLIFE_WEEKS
) -> pd.DataFrame:
    """Recency-weighted same-weekday mean: a variety's same-weekday Sales in date
    order reduced by an exponentially-weighted mean whose weight halves every
    `halflife` observations (pandas ewm, adjust=True).

    The most recent same-weekday Sale counts most and older ones fade smoothly,
    so — unlike the incumbent's equal weighting — a declining series is forecast
    below its all-history same-weekday mean, tracking the downtrend rather than
    the (higher) distant past. A variety that never sold on the target's weekday
    yields no row, mirroring the incumbent.
    """
    history = _in_scope_history(sales, as_of)
    return _same_weekday_reduce(
        history, as_of, lambda s: s.ewm(halflife=halflife, adjust=True).mean().iloc[-1]
    )


def seasonal_trend_forecast(sales: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """A same-weekday level plus a fitted linear drift.

    For each variety, fit one ordinary-least-squares line to its whole Sales
    history against calendar day (numpy.polyfit degree 1) — that slope is the
    ~8%/yr downtrend the equal-weight incumbent is blind to. The per-weekday
    seasonal level is the mean of the de-trended residuals on that weekday, and a
    target's forecast is the line extrapolated to the target date plus its
    weekday's seasonal level. Because the line is projected forward past all
    history, a declining series forecasts below its backward-looking same-weekday
    mean. A variety with under two distinct Sales dates (no slope to fit) or a
    weekday it never sold on (no seasonal level) yields no row, as the incumbent.
    """
    history = _in_scope_history(sales, as_of)
    records = []
    for product in forecast.FORECAST_PRODUCTS:
        variety = history[history["product"] == product].sort_values("date")
        if variety["date"].nunique() < 2:
            continue
        origin = variety["date"].min()
        day_index = (variety["date"] - origin).dt.days.to_numpy(dtype=float)
        quantity = variety["quantity"].to_numpy(dtype=float)
        slope, intercept = np.polyfit(day_index, quantity, 1)
        residual = quantity - (intercept + slope * day_index)
        seasonal = pd.Series(residual).groupby(
            variety["date"].dt.dayofweek.to_numpy()
        ).mean()
        for target in forecast.target_dates(as_of):
            weekday = target.dayofweek
            if weekday not in seasonal.index:
                continue
            t = (target - origin).days
            level = intercept + slope * t + seasonal.loc[weekday]
            records.append(
                {"product": product, "date": target, "forecast_quantity": float(level)}
            )
    return _demand_forecast_frame(records)


# The pilot's two models, then the pure-pandas candidates. The evaluator treats
# every entry identically — a later ticket drops ETS in here the same way.
POOLISH_CANDIDATES: Dict[str, Model] = {
    "seasonal_naive": forecast.forecast_demand,
    "moving_average": backtest.moving_average_forecast,
    "trailing_window": trailing_window_forecast,
    "ewma": ewma_forecast,
    "seasonal_trend": seasonal_trend_forecast,
}


def pinball_losses(actual: pd.Series, forecast: pd.Series, level: float) -> pd.Series:
    """The pinball (quantile) loss of each single day, un-averaged.

    Per day: level * (actual - forecast) when actual >= forecast (an
    under-forecast), else (1 - level) * (forecast - actual) (an over-forecast).
    At level=0.95 an under-forecast is penalised level / (1 - level) = 19x as
    hard as an equal-magnitude over-forecast — the asymmetry that makes this the
    right metric for a Stockout-averse bake decision, where MAPE/MAE would not
    distinguish the two directions.

    Public in its un-averaged form because the day-to-day spread is what says
    whether one model's lower mean is evidence or noise: two candidates' daily
    losses are paired day-by-day and their difference tested (see
    inspection_page.recommendation). A mean alone cannot answer that.
    """
    diff = actual - forecast
    return diff.where(diff >= 0, other=0.0) * level + (-diff).where(
        diff < 0, other=0.0
    ) * (1 - level)


def pinball(actual: pd.Series, forecast: pd.Series, level: float) -> float:
    """Mean pinball loss at a Service Level — the headline score for the Poolish
    total, the daily losses averaged over the days scored."""
    return float(pinball_losses(actual, forecast, level).mean())


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


def coverage(actual: pd.Series, quantity: pd.Series) -> float:
    """The share of days a quantity actually covered Demand — the *realised*
    Service Level, read against the 95% the buffer targets.

    A day the bake exactly met Demand counts as covered: nothing was left over,
    but nobody was turned away either, and it is a Stockout the buffer exists to
    prevent. Pinball says how costly the misses were; this says how often they
    happened, which is the number the baker can check against the promise.

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


# --- Holt-Winters / ETS candidate (opt-in; needs the statsmodels extra) ----

# A weekly-seasonal ETS needs at least two full 7-day cycles to estimate a
# seasonal component; a variety with less history than this falls back to a
# same-weekday mean rather than failing to fit.
_ETS_MIN_OBSERVATIONS = 2 * 7


def _same_weekday_means(recorded: pd.Series) -> pd.Series:
    """Mean quantity by weekday (0=Mon .. 6=Sun) of a date-indexed Sales
    series — the same-weekday mean the incumbent forecasts on, reused here both
    to fill regularization gaps and as the ETS fallback."""
    return recorded.groupby(recorded.index.dayofweek).mean()


def _regular_daily_series(history: pd.DataFrame, product: str):
    """One variety's Sales as a gap-free daily series, or None if it never sold.

    normalize.py emits no row for a day a variety didn't sell, so a variety's
    recorded history is irregular — but ExponentialSmoothing with
    seasonal_periods=7 needs a regular daily index. We reindex to the continuous
    daily range the recorded days span (all strictly before as_of, because
    `history` is already history_before, so this never crosses the cutoff) and
    fill each inserted gap with that variety's mean Sales on the *same weekday*.
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
    zero-Sales holiday and the ~8%/yr downtrend are safe — multiplicative terms
    choke on non-positive values. Forecasts far enough to reach the furthest
    target, then maps forecast dates (series_end + 1, +2, ...) onto the targets.
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


def ets_forecast(sales: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """A per-variety Holt-Winters / ETS Demand Forecast — one point per target
    date — the classic seasonal reference model.

    Conforms to the same (sales, as_of) -> [product, date, forecast_quantity]
    seam as forecast.forecast_demand, so compare_models scores it with no
    special casing. statsmodels is imported *lazily* here so importing this
    module never requires it: ETS is opt-in via candidates_with_ets() and lives
    in the `experiment` extra, not the test-required deps.

    Emits a POINT forecast only — never its native prediction interval. Every
    candidate is buffered to its P95 the same way, through p95_buffer, so pinball
    measures forecast quality and not interval machinery (ADR 0002).

    Robustness: a variety with fewer than two weekly cycles of history, or one
    whose fit fails to converge, falls back to a same-weekday mean rather than
    raising; a variety with no history at all yields no row (as the incumbent
    does) and simply drops out of that day's wheat total. Negative point
    forecasts (a steep additive downtrend extrapolated out) are floored at zero.
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    history = _in_scope_history(sales, as_of)
    targets = forecast.target_dates(as_of)

    records = []
    for product in forecast.FORECAST_PRODUCTS:
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


def statsmodels_available() -> bool:
    """True when statsmodels (the `experiment` extra) can be imported. The ETS
    candidate is registered only when this holds, so a dev-only install can
    still import this module and run compare_models on the default candidates."""
    import importlib.util

    return importlib.util.find_spec("statsmodels") is not None


def candidates_with_ets() -> Dict[str, Model]:
    """The opt-in registry the notebook evaluates: POOLISH_CANDIDATES plus the
    ETS candidate — but only when statsmodels is installed.

    ETS is kept OUT of the default POOLISH_CANDIDATES on purpose. compare_models
    defaults to POOLISH_CANDIDATES, so importing this module and running the
    default comparison never touches statsmodels; the test suite passes on a
    dev-only install. A machine with the `experiment` extra gets ETS ranked
    alongside the others by evaluating this registry instead. On a dev-only
    install this returns the defaults unchanged, so nothing calls statsmodels.
    """
    if statsmodels_available():
        return {**POOLISH_CANDIDATES, "ets": ets_forecast}
    return dict(POOLISH_CANDIDATES)


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
) -> Tuple[pd.Timestamp, pd.Timestamp]:
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


def _replay_at_lead(model, sales: pd.DataFrame, days: List[pd.Timestamp], lead: int):
    """Yield (day, the rows the model emitted for that day) for each day, every
    call made from the origin `lead` days back.

    The single home of the rolling-origin walk both bake targets replay on. For
    a target day D the origin is D - lead, so history_before(D - lead) inside
    the model guarantees it never sees D or anything after it — the leak-freeness
    the whole comparison rests on, in one place rather than one copy per target.
    `model` is anything shaped (sales, as_of) -> frame with a `date` column: a
    Model, a SplitModel, or a Model composed with wheat_total.
    """
    for day in days:
        day = pd.Timestamp(day)
        as_of = (day - pd.Timedelta(days=lead)).date()
        emitted = model(sales, as_of)
        yield day, emitted[emitted["date"] == day]


def forecast_totals(
    model: Model, sales: pd.DataFrame, days: List[pd.Timestamp], lead: int
) -> pd.DataFrame:
    """Each day's wheat-total point forecast at a fixed lead, every one made
    from only the Sales strictly before its origin (_replay_at_lead).

    Returns (date, forecast_quantity); a day the model forecast no variety for
    yields NaN, so it drops out of scoring rather than counting as zero.
    """
    rows = []
    for day, total in _replay_at_lead(
        lambda s, as_of: wheat_total(model(s, as_of)), sales, days, lead
    ):
        quantity = total["forecast_quantity"]
        rows.append(
            {
                "date": day,
                "forecast_quantity": float(quantity.iloc[0])
                if len(quantity)
                else float("nan"),
            }
        )
    return pd.DataFrame(rows, columns=["date", "forecast_quantity"])


def variety_actuals(sales: pd.DataFrame, days: List[pd.Timestamp]) -> pd.DataFrame:
    """Each baked variety's actual Sales on each day — a variety with no Sales
    that day counting as zero, because on an open day the shop did bake it and
    a Bake-to Quantity was still owed for it.

    The grain actual_totals sums up from, so the two cannot disagree about what
    a day's Wheat Dough Demand was.
    """
    in_scope = sales[sales["product"].isin(forecast.FORECAST_PRODUCTS)]
    rows = []
    for day in days:
        day = pd.Timestamp(day)
        for product in forecast.FORECAST_PRODUCTS:
            actual = in_scope.loc[
                (in_scope["date"] == day) & (in_scope["product"] == product),
                "quantity",
            ].sum()
            rows.append({"product": product, "date": day, "actual": float(actual)})
    return pd.DataFrame(rows, columns=["product", "date", "actual"])


def actual_totals(
    sales: pd.DataFrame, days: List[pd.Timestamp]
) -> pd.DataFrame:
    """The actual wheat-total Sales on each day: the three baked varieties
    summed, an absent variety counting as zero for that day."""
    return (
        variety_actuals(sales, days)
        .groupby("date", as_index=False)["actual"]
        .sum()
        .sort_values("date", ignore_index=True)
    )


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


def buffered_totals(
    model: Model,
    sales: pd.DataFrame,
    eval_days: List[pd.Timestamp],
    warmup_days: List[pd.Timestamp],
    lead: int = POOLISH_LEAD,
    level: float = SERVICE_LEVEL,
) -> pd.DataFrame:
    """One candidate's whole rolling-origin replay, day by day: what the shop
    actually sold, what the model forecast from `lead` days back, and the P95
    Poolish quantity that point forecast buffers to.

    (date, actual, forecast_quantity, buffered_quantity) — one row per
    evaluation day the model forecast at all; a day it declined drops out rather
    than counting as zero. The buffer's relative residuals are collected from
    `warmup_days` alone, so the P95 a day is scored on never saw that day; with
    no warmup days there is nothing to buffer from and the point forecast stands
    unbuffered.

    Both the scores (_score_candidate) and the inspection charts read this one
    frame, so what a chart draws is what the score was taken from — the two
    cannot tell the baker different stories about the same day.
    """
    residuals = _relative_residuals(model, sales, warmup_days, lead)

    replay = (
        forecast_totals(model, sales, eval_days, lead)
        .merge(actual_totals(sales, eval_days), on="date")
        .dropna(subset=["forecast_quantity"])
        .reset_index(drop=True)
    )
    replay["buffered_quantity"] = (
        replay["forecast_quantity"]
        if residuals.empty
        else p95_buffer(replay["forecast_quantity"], residuals, level)
    )
    return replay[["date", "actual", "forecast_quantity", "buffered_quantity"]]


def _score_candidate(
    model: Model,
    sales: pd.DataFrame,
    eval_days: List[pd.Timestamp],
    warmup_days: List[pd.Timestamp],
    lead: int,
    level: float,
) -> Dict[str, float]:
    """One candidate's Poolish-total scores, reduced from its replay: pinball at
    the Service Level on the P95-buffered total, the coverage that P95 realised
    against it, and MAPE on the unbuffered point forecast as a familiar sanity
    column."""
    replay = buffered_totals(model, sales, eval_days, warmup_days, lead, level)

    positive = replay[replay["actual"] > 0]
    return {
        "pinball": pinball(replay["actual"], replay["buffered_quantity"], level),
        "coverage": coverage(replay["actual"], replay["buffered_quantity"]),
        "mape": mape(positive["actual"], positive["forecast_quantity"]),
        "days": len(replay),
    }


def window_days(
    sales: pd.DataFrame,
    eval_weeks: int = EVAL_WEEKS,
    warmup_weeks: int = WARMUP_WEEKS,
) -> Tuple[List[pd.Timestamp], List[pd.Timestamp]]:
    """The open days a comparison scores on, and the open days its buffer takes
    its residuals from: the last `eval_weeks` weeks, and the `warmup_weeks`
    immediately before them. Public so the inspection page charts exactly the
    days compare_models scored, rather than re-deriving a window that could
    drift from it."""
    eval_start, eval_end = evaluation_window(sales, eval_weeks)
    warmup_end = eval_start - pd.Timedelta(days=1)
    warmup_start = warmup_end - pd.Timedelta(days=warmup_weeks * 7 - 1)
    return (
        _open_days(sales, eval_start, eval_end),
        _open_days(sales, warmup_start, warmup_end),
    )


def compare_models(
    sales: pd.DataFrame,
    candidates: Optional[Dict[str, Model]] = None,
    eval_weeks: int = EVAL_WEEKS,
    warmup_weeks: int = WARMUP_WEEKS,
    lead: int = POOLISH_LEAD,
    level: float = SERVICE_LEVEL,
) -> pd.DataFrame:
    """Replay every candidate over the recent evaluation window and rank them on
    pinball@level for the Poolish total.

    One row per candidate — its pinball@level, the coverage its P95 realised,
    MAPE, and the day count — sorted best (lowest pinball) first. The window is
    the last `eval_weeks` weeks; the buffer's residuals come from the
    `warmup_weeks` immediately before it, so no scored day feeds its own buffer.
    Each day is forecast once, at `lead` days back, from only prior Sales.
    """
    candidates = candidates if candidates is not None else POOLISH_CANDIDATES

    eval_days, warmup_days = window_days(sales, eval_weeks, warmup_weeks)

    rows = [
        {"model": name, **_score_candidate(model, sales, eval_days, warmup_days, lead, level)}
        for name, model in candidates.items()
    ]
    return pd.DataFrame(
        rows, columns=["model", "pinball", "coverage", "mape", "days"]
    ).sort_values("pinball", ignore_index=True)


# --- Bake split: the second bake target ------------------------------------
#
# The lead-2 Bake-to Quantity per variety: the fixed Poolish divided across
# everything/plain/sesame by expected share (ADR 0001). A split candidate is a
# SplitModel — (sales, as_of) -> DataFrame[product, date, share] — emitting a
# share, not a quantity, because the quantity is not the candidate's to choose:
# the Poolish is already made and fixed by the time this decision is taken.
# bake_to_quantities turns those shares into Bake-to Quantities by allocating
# that fixed Poolish, and the result is scored on WAPE per variety.
#
# Two consequences of ADR 0001 hold everywhere below. No second quantile buffer
# is applied — the Service Level buffer lives once, in the Poolish total, and
# quantiles do not add. And the shares always sum to 1, so the split spends the
# Poolish exactly; it can misallocate between varieties but can never conjure
# dough that was not made.
#
# Not routed through compare_models: a SplitModel emits a share where a Model
# emits a forecast_quantity, and the two bake targets are scored on different
# metrics at different leads (ADR 0002). compare_split_models below reuses the
# evaluator's machinery instead — evaluation_window, _open_days, _replay_at_lead
# and wape — so the two comparisons cannot drift apart on the window, the open
# days, the leak-free cutoff, or the metric.


def _split_frame(records: List[dict]) -> pd.DataFrame:
    """Shape share records into the split contract: (product, date, share),
    matching _demand_forecast_frame's dtype and ordering convention."""
    frame = pd.DataFrame(records, columns=["product", "date", "share"])
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    frame["share"] = frame["share"].astype(float)
    return frame.sort_values(["date", "product"], ignore_index=True)


def constant_recent_share(
    sales: pd.DataFrame, as_of: dt.date, weeks: int = TRAILING_WINDOW_WEEKS
) -> pd.DataFrame:
    """Each variety's share of the recent total, held flat across every
    target date regardless of weekday. The mix is fairly stable (~45/29/27),
    so this is the near-non-race baseline a smarter split method must beat.

    Recent means the calendar `weeks` before as_of, leak-free via
    _in_scope_history — a cutoff on the date, not on the count of same-weekday
    observations trailing_window_forecast tails, since a share is taken over
    every recent day at once rather than per weekday. A variety with no Sales
    in that window gets no row rather than a fabricated zero share.
    """
    history = _in_scope_history(sales, as_of)
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(weeks=weeks)
    recent = history[history["date"] >= cutoff]
    totals = recent.groupby("product")["quantity"].sum()
    total = totals.sum()
    if total <= 0:
        return _split_frame([])

    records = [
        {"product": product, "date": target, "share": float(quantity / total)}
        for target in forecast.target_dates(as_of)
        for product, quantity in totals.items()
    ]
    return _split_frame(records)


def same_weekday_share(sales: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """Each variety's share of the total, conditioned on the target's
    weekday — a Saturday's mix need not match a Tuesday's. Leak-free over all
    history before as_of, mirroring the incumbent's same-weekday averaging; a
    weekday never observed at all yields no row for that target.
    """
    history = _in_scope_history(sales, as_of)
    if history.empty:
        return _split_frame([])

    weekday = history["date"].dt.dayofweek
    by_product = history.groupby([weekday, history["product"]])["quantity"].sum()
    by_weekday = history.groupby(weekday)["quantity"].sum()

    records = []
    for target in forecast.target_dates(as_of):
        weekday = target.dayofweek
        total = by_weekday.get(weekday, 0.0)
        if total <= 0:
            continue
        for product in forecast.FORECAST_PRODUCTS:
            quantity = by_product.get((weekday, product))
            if quantity is None:
                continue
            records.append(
                {"product": product, "date": target, "share": float(quantity / total)}
            )
    return _split_frame(records)


def per_variety_recency_share(
    sales: pd.DataFrame, as_of: dt.date, halflife: float = EWMA_HALFLIFE_WEEKS
) -> pd.DataFrame:
    """Each variety's recency-weighted point forecast (ewma_forecast),
    normalized per date to sum to 1 — the only split candidate that reacts to
    a variety's own recent trend rather than a fixed or weekday-only ratio.

    Normalizing is what makes this a split rather than three forecasts: the
    point forecasts are only ever read against each other, so whatever level
    error they share divides out, and what survives is their ratio. A date where
    every variety's point forecast is non-positive yields no row — nothing to
    normalize by.
    """
    points = ewma_forecast(sales, as_of, halflife=halflife)
    if points.empty:
        return _split_frame([])

    totals = points.groupby("date")["forecast_quantity"].transform("sum")
    positive = points[totals > 0].copy()
    positive["share"] = positive["forecast_quantity"] / totals[totals > 0]
    return _split_frame(positive[["product", "date", "share"]].to_dict("records"))


# The pilot's three split methods. The evaluator treats every entry
# identically, so a later candidate drops into SPLIT_CANDIDATES without
# special-casing, exactly as POOLISH_CANDIDATES.
SPLIT_CANDIDATES: Dict[str, SplitModel] = {
    "constant_recent_share": constant_recent_share,
    "same_weekday_share": same_weekday_share,
    "per_variety_recency_share": per_variety_recency_share,
}


def bake_to_quantities(
    model: SplitModel, sales: pd.DataFrame, days: List[pd.Timestamp], lead: int
) -> pd.DataFrame:
    """Each day's Bake-to Quantity per variety at a fixed lead: the candidate's
    expected share of that day, allocating the fixed Poolish.

    The Poolish it allocates is the day's *realised* wheat total. That is a
    deliberate scoring choice, not a leak. The shares are leak-free — they come
    from _replay_at_lead, so no candidate's history reaches its own target day —
    and the realised total enters only here, at scoring time, identically for
    every candidate, never fed back into a model. Holding the base fixed is what
    makes this a comparison of *split* quality: allocate a forecast total
    instead and each candidate's WAPE would carry whatever error the Poolish
    model made, which is the other target's score, already measured by
    compare_models.

    Read the resulting WAPE as split error, then — as the error a bake would
    actually see: on the day, the base is the P95-buffered Poolish, so real
    Bake-to Quantities sit above these; and because the shares sum to 1 against
    a base equal to the total, each candidate's signed errors across the three
    varieties cancel to zero by construction. A variety the model gave no share
    for yields no row.
    """
    poolish = actual_totals(sales, days).set_index("date")["actual"]
    rows = [
        {
            "product": row.product,
            "date": day,
            "forecast_quantity": float(row.share) * poolish.get(day, 0.0),
        }
        for day, shares in _replay_at_lead(model, sales, days, lead)
        for row in shares.itertuples(index=False)
    ]
    return pd.DataFrame(rows, columns=["product", "date", "forecast_quantity"])


def _score_split_candidate(
    model: SplitModel, sales: pd.DataFrame, eval_days: List[pd.Timestamp], lead: int
) -> pd.DataFrame:
    """One row per variety: WAPE over eval_days for this split candidate. A
    day the candidate gave no share for drops that variety's row from the
    comparison, exactly as an undeclared forecast does for the Poolish total.
    """
    merged = variety_actuals(sales, eval_days).merge(
        bake_to_quantities(model, sales, eval_days, lead),
        on=["product", "date"],
        how="left",
    )
    rows = []
    for product in forecast.FORECAST_PRODUCTS:
        scored = merged[merged["product"] == product].dropna(
            subset=["forecast_quantity"]
        )
        rows.append(
            {
                "product": product,
                "wape": wape(scored["actual"], scored["forecast_quantity"]),
                "days": len(scored),
            }
        )
    return pd.DataFrame(rows, columns=["product", "wape", "days"])


def compare_split_models(
    sales: pd.DataFrame,
    candidates: Optional[Dict[str, SplitModel]] = None,
    eval_weeks: int = EVAL_WEEKS,
    lead: int = SPLIT_LEAD,
) -> pd.DataFrame:
    """Replay every split candidate over the recent evaluation window and score
    each on WAPE per variety — compare_models' opposite number for the second
    bake target, sharing its window, its open days, and its leak-free walk.

    One row per (candidate, variety): its WAPE and the day count, sorted by
    model then product. The window is the last `eval_weeks` weeks; each day is
    forecast once, at `lead` days back, from only prior Sales.

    No warmup window, unlike compare_models: with no buffer on the split there
    are no residuals to pool, so there is nothing a scored day could feed.
    """
    candidates = candidates if candidates is not None else SPLIT_CANDIDATES

    eval_start, eval_end = evaluation_window(sales, eval_weeks)
    eval_days = _open_days(sales, eval_start, eval_end)

    rows = [
        {"model": name, **row}
        for name, model in candidates.items()
        for row in _score_split_candidate(model, sales, eval_days, lead).to_dict(
            "records"
        )
    ]
    return pd.DataFrame(rows, columns=["model", "product", "wape", "days"]).sort_values(
        ["model", "product"], ignore_index=True
    )


def _format_split_report(comparison: pd.DataFrame) -> str:
    pivot = comparison.pivot(index="model", columns="product", values="wape")
    products = [p for p in forecast.FORECAST_PRODUCTS if p in pivot.columns]
    lines = [
        f"Bake split — {pivot.index.nunique()} candidates, "
        "WAPE per variety (lower is better)",
        "",
        f"{'model':26}" + "".join(f"{p:>14}" for p in products),
    ]
    for model in pivot.index:
        cells = "".join(f"{pivot.loc[model, p] * 100:13.1f}%" for p in products)
        lines.append(f"{model:26}{cells}")
    lines += [
        "",
        "WAPE is each variety's total absolute error over its total actual Sales. Each "
        "method splits the",
        "day's realised wheat total, so these rank split quality with the Poolish "
        "model's own error held out;",
        "a real bake splits the P95-buffered Poolish, so its Bake-to Quantities sit "
        "above these. No second",
        "quantile buffer is applied — the buffer lives once, in the Poolish total "
        "(ADR 0001).",
    ]
    return "\n".join(lines)


def _format_report(comparison: pd.DataFrame, level: float) -> str:
    pct = int(round(level * 100))
    lines = [
        f"Poolish total — {len(comparison)} candidates, "
        f"ranked by pinball@{pct} (lower is better)",
        "",
        f"{'model':20} {f'pinball@{pct}':>12} {'coverage':>10} {'MAPE':>8} {'days':>6}",
    ]
    for row in comparison.itertuples():
        lines.append(
            f"{row.model:20} {row.pinball:12.2f} {row.coverage * 100:9.1f}% "
            f"{row.mape:7.1f}% {row.days:6}"
        )
    lines += [
        "",
        f"pinball@{pct} scores each model's P95-buffered wheat-total forecast; it "
        f"penalises under-",
        "forecasting 19x as hard as over. Coverage is how often that P95 quantity "
        f"actually covered",
        f"Demand — the realised Service Level, against the {pct}% target. MAPE is on "
        "the unbuffered point",
        "forecast, a sanity column only.",
    ]
    return "\n".join(lines)


def main() -> None:
    sales = pd.read_parquet(SALES_HISTORY_PATH)
    comparison = compare_models(
        sales, eval_weeks=EVAL_WEEKS, warmup_weeks=WARMUP_WEEKS
    )
    print(_format_report(comparison, SERVICE_LEVEL))
    print()
    split_comparison = compare_split_models(sales, eval_weeks=EVAL_WEEKS)
    print(_format_split_report(split_comparison))


if __name__ == "__main__":
    sys.exit(main())
