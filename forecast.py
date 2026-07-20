"""The seasonal-naive Demand Forecast model, and the helpers every model shares.

    forecast_demand(sales, as_of)
      -> DataFrame[product, date, forecast_quantity]

This module no longer writes a parquet, and is no longer an entry point. Since
the daily forecast log became the single source of truth for forecasts (ADR
0006), each morning's Demand Forecasts are produced by `daily_forecast.py` and
appended to the `forecasts` table; the file-based outputs it used to write —
`data/demand_forecast.parquet` and `data/sales_forecast.parquet` — were retired
with `main()`, the way ticket 07 retired the file-based ingestion path.

What remains is a library. `forecast_demand` is still a *model callable* — the
seasonal-naive baseline `backtest.py` scores the candidates against — and
`history_before`, `target_dates` and `FORECAST_PRODUCTS` are the definitions
every other model reaches for, so that the leak-free cutoff and what counts as
a target date exist once (see models.py, forecast_engine.py).

`main()` was the only production caller of `unexpected_products`,
`sparse_weekday_counts` and `roll_up_sales_forecast`, which now run only under
test. They are kept because they record decisions the code still depends on —
which varieties are knowingly not forecast (SKIPPED_PRODUCTS), and the
mean-only family rollup ADR 0001 names — and because run_forecasts' docstring
sends callers to sparse_weekday_counts to find the gaps the log won't show.
Note what that costs: nothing now warns about a Product in the Sales history
classified neither way. forecast_engine._target_series catches the inverse (a
Target naming a Product the history lacks), so a *new* variety no longer
announces itself; whatever picks up the analysis layer should re-run that check.

The model is seasonal-naive: a Product's forecast Demand for a target date is
the mean of its Sales on that same weekday across the trailing history. No
stockout correction — per the PRD, Sales is the Demand proxy for this pilot.

Two things about the averaging are load-bearing.

It averages over the days a Product has a Sales record, not over every calendar
day. normalize.py emits no row for a Product that sold nothing, so an absent day
is real information — but counting it as a zero and skipping it give different
answers, and this module skips. That is only sound for the Products in
FORECAST_PRODUCTS, each of which sells nearly every open day; it would badly
overstate a day-restricted variety, which is why cinnamon raisin and
pumpernickel are skipped (see SKIPPED_PRODUCTS). It also means the days both
locations were closed (July 4ths, Thanksgivings) drop out for free: no Product
has a row on them.

And a Demand Forecast may only see Sales strictly before its as_of date. Ticket
03 backtests by replaying a past as_of over the same history, so a leak of the
target week's actuals would silently flatter the error metric.
"""
import datetime as dt
from typing import Dict, List, Tuple

import pandas as pd

# The three baked bagel varieties the forecast covers — everything, plain and
# sesame, which share one Wheat Dough. Each sells on essentially every open day,
# which is what licenses the recorded-days-only averaging above.
FORECAST_PRODUCTS: Tuple[str, ...] = (
    "everything",
    "plain",
    "sesame",
)

# In the Sales history, deliberately not forecast. Listed so they are a recorded
# decision rather than a warning on every run. Their Sales stay in the Sales
# history; re-including one is a move between these two constants.
SKIPPED_PRODUCTS: Dict[str, str] = {
    "gluten-free everything": "bought in frozen, not baked — kept in Sales history for later",
    "gluten-free plain": "bought in frozen, not baked — kept in Sales history for later",
    "cinnamon raisin": "sold Wednesdays only; no Sales on most weekdays",
    "pumpernickel": "sold Thursdays only; no Sales on most weekdays",
}

# The forecast covers days as_of+2 .. as_of+7 inclusive: far enough out to be
# actionable for ordering, near enough that a same-weekday mean is plausible.
HORIZON_DAYS = (2, 7)

# Below this many same-weekday observations, a Product's forecast for that
# weekday is a coin flip. Warned about, not withheld.
MIN_OBSERVATIONS = 4

_SALES_COLUMNS = ("product", "date", "quantity")


def _validate_sales(sales: pd.DataFrame) -> None:
    missing = [c for c in _SALES_COLUMNS if c not in sales.columns]
    if missing:
        raise ValueError(
            f"Sales history is missing columns {missing} — expected "
            f"{list(_SALES_COLUMNS)}, got {list(sales.columns)}"
        )
    # A NaN quantity would average into a NaN Demand Forecast, slip past the
    # zero-observation guard (which only tests for an absent group), and sum
    # into a NaN family Sales Forecast. normalize.py never emits one; if that
    # changes, say so rather than quietly forecasting NaN.
    if sales["quantity"].isna().any():
        raise ValueError(
            "Sales history contains NaN quantities — refusing to forecast from "
            "it. normalize.py should emit a quantity for every Sales record"
        )


def history_before(sales: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """Sales strictly before as_of. Sales on as_of itself are excluded: the day
    is not over, so its total would understate the weekday.

    Product scope is not applied here — callers select the Products they emit,
    so filtering here too would be a second, silent gate on the same decision.

    Public because backtest.py's baseline model must cut its history at exactly
    the same place this one does, or the two are not comparable.
    """
    _validate_sales(sales)
    return sales[sales["date"] < pd.Timestamp(as_of)]


def target_dates(
    as_of: dt.date, horizon: Tuple[int, int] = HORIZON_DAYS
) -> List[pd.Timestamp]:
    """The dates a forecast made on as_of covers: as_of + first .. as_of + last
    inclusive, for the `(first, last)` lead range `horizon`.

    Public for the same reason as history_before: the backtest's baseline and
    its replay both key off the horizon, and a second definition of it would be
    free to drift. `horizon` is a parameter, not just the module constant,
    because the daily forecast engine's horizon is configuration — a day-count
    `N` covering `as_of+1 .. as_of+N` (ADR 0006) — and it must reach the same
    definition of a target date rather than reimplement it.
    """
    first, last = horizon
    return [pd.Timestamp(as_of) + pd.Timedelta(days=n) for n in range(first, last + 1)]


def _by_product_weekday(history: pd.DataFrame):
    """Group Sales by (Product, weekday). The single definition of what counts
    as one same-weekday observation — forecast_demand() averages these groups
    and sparse_weekday_counts() counts them, so they must not drift apart."""
    return history.groupby([history["product"], history["date"].dt.dayofweek])[
        "quantity"
    ]


def forecast_demand(sales: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """A per-Product Demand Forecast for each target date in the horizon.

    Each Demand Forecast is the mean of that Product's recorded Sales on the
    target's weekday, across all history before as_of. A Product with no Sales
    at all on that weekday yields no row rather than a zero or a NaN — there is
    no evidence to average, and a fabricated zero would flow into the family
    Sales Forecast as if it were one. sparse_weekday_counts() surfaces the gaps.
    """
    weekday_means = _by_product_weekday(history_before(sales, as_of)).mean()

    records = []
    for target in target_dates(as_of):
        for product in FORECAST_PRODUCTS:
            mean = weekday_means.get((product, target.dayofweek))
            if mean is None:
                continue
            records.append(
                {"product": product, "date": target, "forecast_quantity": float(mean)}
            )

    forecast = pd.DataFrame(records, columns=["product", "date", "forecast_quantity"])
    # Pinned to nanoseconds, not left to pd.to_datetime's inference: from pandas
    # 3.0 it infers microseconds from a column of Timestamps, and a Demand
    # Forecast whose date dtype drifts by pandas version is one that merges
    # against a Sales history — and round-trips through parquet — differently
    # than it used to.
    forecast["date"] = pd.to_datetime(forecast["date"]).astype("datetime64[ns]")
    forecast["forecast_quantity"] = forecast["forecast_quantity"].astype(float)
    return forecast.sort_values(["date", "product"], ignore_index=True)


def roll_up_sales_forecast(demand_forecast: pd.DataFrame) -> pd.DataFrame:
    """The family-level Sales Forecast: per-Product Demand Forecasts summed by
    date. Covers FORECAST_PRODUCTS only, so it understates all-bagel Sales by
    whatever SKIPPED_PRODUCTS sell."""
    if demand_forecast.empty:
        return pd.DataFrame(columns=["date", "forecast_quantity"])
    return (
        demand_forecast.groupby("date", as_index=False)["forecast_quantity"]
        .sum()
        .sort_values("date", ignore_index=True)
    )


def sparse_weekday_counts(
    sales: pd.DataFrame,
    as_of: dt.date,
    min_observations: int = MIN_OBSERVATIONS,
) -> List[Tuple[str, str, int]]:
    """(Product, weekday, count) for every forecast Product whose same-weekday
    history is thinner than min_observations — including the zero-observation
    case, where forecast_demand() emits no row at all."""
    observed = _by_product_weekday(history_before(sales, as_of)).size()

    sparse = []
    for target in target_dates(as_of):
        for product in FORECAST_PRODUCTS:
            count = int(observed.get((product, target.dayofweek), 0))
            if count < min_observations:
                sparse.append((product, target.day_name(), count))
    return sparse


def unexpected_products(sales: pd.DataFrame) -> List[str]:
    """Products in the Sales history that are neither forecast nor knowingly
    skipped — a new bagel variety must not vanish from the family total just
    because nobody told this module about it."""
    _validate_sales(sales)
    classified = set(FORECAST_PRODUCTS) | set(SKIPPED_PRODUCTS)
    return sorted(set(sales["product"]) - classified)
