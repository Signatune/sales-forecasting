"""Score the seasonal-naive Demand Forecast against held-out actual Sales.

    .venv/bin/python backtest.py

Holds out the last HOLDOUT_DAYS of the Sales history, forecasts those days as
if they were still in the future, and reports MAPE per Product and for the
family-level Sales Forecast — beside the same numbers for a trailing moving
average, the dumber bar the seasonal-naive model has to clear. The baseline is
a measurement artifact, not a candidate model: nothing writes it anywhere.

Three decisions shape everything below.

**Origins, not one cutoff.** forecast.forecast_demand covers as_of+2..as_of+7,
so no single as_of reaches across a four-week holdout. Instead the backtest
replays a series of origins six days apart (replay_origins), each forecasting
the six days that follow it. Every holdout day is forecast exactly once, at a
lead time of 2..7 days — the same lead times a Friday ordering run would face.
A later origin does see the earlier holdout days, which is not a leak: by then
they have happened. What must never happen is a forecast seeing its own target
date, and forecast.history_before is what guarantees that.

**A zero actual has no MAPE.** The metric divides by the actual, and even a
Product that sells nearly every open day records the occasional zero-Sales day.
Those rows are carried in the comparison frame (the notebook charts them) but
excluded from the mean, and counted in `unscored_days` so the exclusion is
visible.

**Both models are scored on identical rows.** forecast_demand emits no row for
a Product on a weekday it never sold; the baseline, blind to weekday, always
emits one. Scoring each model on whatever rows it happened to produce would let
the seasonal-naive model duck exactly the days it finds hardest. So a row is
`comparable` only when both models forecast it, and `scored` only when it is
comparable *and* the actual is positive. The two are separate because they bite
at different levels: a zero actual has no percentage error but still belongs in
a family total, while a Product only one model forecast belongs in neither.
"""
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

import forecast

SALES_HISTORY_PATH = forecast.SALES_HISTORY_PATH

# Four weeks: long enough to cover every weekday four times, short enough that
# the model still trains on two years of history.
HOLDOUT_DAYS = 28

# The baseline's window. Seven days so it averages exactly one of each weekday
# — the fairest version of "no seasonality", not a strawman.
TRAILING_DAYS = 7

_MODELS = ("seasonal_naive", "moving_average")


def holdout_window(
    sales: pd.DataFrame, holdout_days: int = HOLDOUT_DAYS
) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """The first and last date (inclusive) of the held-out period: the most
    recent holdout_days of the Sales history."""
    end = sales["date"].max()
    start = end - pd.Timedelta(days=holdout_days - 1)
    if sales["date"].min() >= start:
        raise ValueError(
            f"holding out {holdout_days} days would leave no Sales before "
            f"{start.date()} to forecast from — the history begins "
            f"{sales['date'].min().date()}"
        )
    return start, end


def replay_origins(
    holdout_start: pd.Timestamp, holdout_end: pd.Timestamp
) -> List[dt.date]:
    """The as_of dates to replay so that each holdout day is forecast exactly
    once. Spaced by the width of the forecast horizon, and offset so the first
    origin's earliest target is the first holdout day."""
    first_lead, last_lead = forecast.HORIZON_DAYS
    stride = last_lead - first_lead + 1

    origin = (holdout_start - pd.Timedelta(days=first_lead)).date()
    origins = []
    while pd.Timestamp(origin) + pd.Timedelta(days=first_lead) <= holdout_end:
        origins.append(origin)
        origin += dt.timedelta(days=stride)
    return origins


def moving_average_forecast(
    sales: pd.DataFrame, as_of: dt.date, trailing_days: int = TRAILING_DAYS
) -> pd.DataFrame:
    """The baseline: each Product's mean Sales over its last trailing_days
    recorded days before as_of, carried flat across the whole horizon.

    Deliberately drop-in shaped like forecast.forecast_demand — same signature,
    same Product scope, same columns — so replay() cannot treat one specially.
    Like the model, it averages recorded days rather than calendar days; a
    Product with no history at all before as_of yields no row.
    """
    history = forecast.history_before(sales, as_of).sort_values("date")

    trailing_means = {}
    for product in forecast.FORECAST_PRODUCTS:
        recorded = history.loc[history["product"] == product, "quantity"]
        if not recorded.empty:
            trailing_means[product] = float(recorded.tail(trailing_days).mean())

    records = [
        {"product": product, "date": target, "forecast_quantity": mean}
        for target in forecast.target_dates(as_of)
        for product, mean in trailing_means.items()
    ]

    baseline = pd.DataFrame(records, columns=["product", "date", "forecast_quantity"])
    baseline["date"] = pd.to_datetime(baseline["date"])
    baseline["forecast_quantity"] = baseline["forecast_quantity"].astype(float)
    return baseline.sort_values(["date", "product"], ignore_index=True)


def mape(actual: pd.Series, forecast_quantity: pd.Series) -> float:
    """Mean absolute percentage error. Undefined — NaN, not zero — over an
    empty comparison, and the caller is responsible for having dropped the
    zero actuals it would divide by."""
    if actual.empty:
        return float("nan")
    return float((100 * (actual - forecast_quantity).abs() / actual).mean())


def _actuals(sales: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """One row per (forecast Product, open day) in the holdout, with quantity 0
    where a Product has no Sales record.

    Open days are the dates *some* Product sold on. A day nobody sold on is a
    day both locations were closed, and inventing five zero-Sales rows for it
    would score the model on a day it was never asked about.
    """
    in_scope = sales[sales["product"].isin(forecast.FORECAST_PRODUCTS)]
    window = sales[(sales["date"] >= start) & (sales["date"] <= end)]

    grid = pd.MultiIndex.from_product(
        [
            sorted(set(in_scope["product"])),
            sorted(window["date"].unique()),
        ],
        names=["product", "date"],
    )
    recorded = in_scope.set_index(["product", "date"])["quantity"]
    return (
        recorded.reindex(grid, fill_value=0.0)
        .rename("actual")
        .reset_index()
    )


def _replay(model, sales: pd.DataFrame, origins: List[dt.date], name: str) -> pd.DataFrame:
    """Run a forecast model from each origin and stack the results. Origins do
    not overlap, so no (Product, date) pair is forecast twice."""
    frames = [model(sales, origin) for origin in origins]
    return (
        pd.concat(frames, ignore_index=True)
        .rename(columns={"forecast_quantity": name})
    )


def compare(
    sales: pd.DataFrame,
    holdout_days: int = HOLDOUT_DAYS,
    trailing_days: int = TRAILING_DAYS,
) -> pd.DataFrame:
    """Forecast-vs-actual over the holdout: one row per (Product, open day),
    carrying the actual Sales, both models' forecasts, and the two flags the
    score functions reduce on. This is also what the notebook charts; a NaN
    forecast means that model declined to forecast that day.
    """
    start, end = holdout_window(sales, holdout_days)
    origins = replay_origins(start, end)

    comparison = _actuals(sales, start, end)
    for name, model in (
        ("seasonal_naive", forecast.forecast_demand),
        ("moving_average", lambda s, o: moving_average_forecast(s, o, trailing_days)),
    ):
        comparison = comparison.merge(
            _replay(model, sales, origins, name), on=["product", "date"], how="left"
        )

    comparison["comparable"] = (
        comparison["seasonal_naive"].notna() & comparison["moving_average"].notna()
    )
    comparison["scored"] = comparison["comparable"] & (comparison["actual"] > 0)
    return comparison.sort_values(["date", "product"], ignore_index=True)


def _mape_scores(scored: pd.DataFrame, total_rows: int) -> Dict[str, float]:
    """Both models' MAPE over the same scored rows, and how many rows the
    metric could not use — reported so the exclusions are never invisible."""
    return {
        "scored_days": len(scored),
        "unscored_days": total_rows - len(scored),
        **{f"{m}_mape": mape(scored["actual"], scored[m]) for m in _MODELS},
    }


def product_scores(comparison: pd.DataFrame) -> pd.DataFrame:
    """MAPE per Product for both models, over the rows both could be scored on,
    plus the count of holdout days that fell out of that comparison."""
    return pd.DataFrame([
        {"product": product, **_mape_scores(group[group["scored"]], len(group))}
        for product, group in comparison.groupby("product")
    ])


def family_totals(comparison: pd.DataFrame) -> pd.DataFrame:
    """The family-level Sales Forecast against the family-level actual, per day.

    Every column sums the same basket: the Products both models forecast that
    day. Two exclusions are at work, and they are not the same exclusion.

    Skipped Products never enter — summing all seven bagel varieties into the
    actual would charge the model for the two it never tried to forecast, and
    _actuals has already dropped them.

    And a Product only *one* model forecast leaves the basket entirely, actual
    included. The seasonal-naive model omits a Product on a weekday it never
    sold, where the weekday-blind baseline still forecasts one; keeping that
    Product's actual would charge the seasonal-naive total for units it never
    bid on while the baseline's total covered them. A day with no comparable
    Product at all keeps its row, with NaN totals, so family_scores can count it.
    """
    basket = comparison[comparison["comparable"]]
    totals = basket.groupby("date", as_index=False).agg(
        actual=("actual", "sum"),
        seasonal_naive=("seasonal_naive", "sum"),
        moving_average=("moving_average", "sum"),
    )
    open_days = comparison[["date"]].drop_duplicates()
    return open_days.merge(totals, on="date", how="left").sort_values(
        "date", ignore_index=True
    )


def family_scores(comparison: pd.DataFrame) -> Dict[str, float]:
    """MAPE for the family-level Sales Forecast, over the days the family sold
    something and at least one Product was comparable. A NaN actual — no
    comparable Product that day — fails the > 0 test and is counted unscored."""
    family = family_totals(comparison)
    return _mape_scores(family[family["actual"] > 0], len(family))


def _format_report(
    comparison: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> str:
    products = product_scores(comparison)
    family = family_scores(comparison)

    lines = [
        f"backtest over {start.date()}..{end.date()} "
        f"({comparison['date'].nunique()} open days)",
        "",
        f"{'product':24} {'seasonal-naive':>15} {'moving-average':>15} "
        f"{'scored':>7} {'unscored':>9}",
    ]
    for row in products.itertuples():
        lines.append(
            f"{row.product:24} {row.seasonal_naive_mape:14.1f}% "
            f"{row.moving_average_mape:14.1f}% "
            f"{row.scored_days:7} {row.unscored_days:9}"
        )
    lines += [
        "",
        f"{'family Sales Forecast':24} {family['seasonal_naive_mape']:14.1f}% "
        f"{family['moving_average_mape']:14.1f}% "
        f"{family['scored_days']:7} {family['unscored_days']:9}",
        "",
        "MAPE is over days both models forecast and the actual was non-zero. "
        "The moving-average",
        "baseline is a comparison bar only — it is not written anywhere and is "
        "not a candidate model.",
    ]
    return "\n".join(lines)


def main(holdout_days: int = HOLDOUT_DAYS, trailing_days: int = TRAILING_DAYS) -> None:
    sales = pd.read_parquet(SALES_HISTORY_PATH)
    start, end = holdout_window(sales, holdout_days)
    print(_format_report(compare(sales, holdout_days, trailing_days), start, end))


if __name__ == "__main__":
    sys.exit(main())
