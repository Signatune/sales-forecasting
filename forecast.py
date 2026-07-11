"""Seasonal-naive Demand Forecast per bagel Product, and the family Sales Forecast.

    .venv/bin/python forecast.py

Reads the canonical Sales history written by normalize.py and writes, for each
of the next 2..7 days, a Demand Forecast per Product and the summed family-level
Sales Forecast:

    data/sales_history.parquet
      -> data/demand_forecast.parquet   (product, date, forecast_quantity)
      -> data/sales_forecast.parquet    (date, forecast_quantity)

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
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

SALES_HISTORY_PATH = Path(__file__).parent / "data" / "sales_history.parquet"
DEMAND_FORECAST_PATH = Path(__file__).parent / "data" / "demand_forecast.parquet"
SALES_FORECAST_PATH = Path(__file__).parent / "data" / "sales_forecast.parquet"

# The three baked bagel varieties the forecast covers — everything, plain and
# sesame, which share one Wheat Dough. Each sells on essentially every open day,
# which is what licenses the recorded-days-only averaging above.
FORECAST_PRODUCTS: Tuple[str, ...] = (
    "everything",
    "plain",
    "sesame",
)

# In the Sales history, deliberately not forecast. Listed so they are a recorded
# decision rather than a warning on every run. Their Sales stay in
# sales_history.parquet; re-including one is a move between these two constants.
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


def target_dates(as_of: dt.date) -> List[pd.Timestamp]:
    """The dates a forecast made on as_of covers. Public for the same reason as
    history_before: the backtest's baseline and its replay both key off the
    horizon, and a second definition of it would be free to drift."""
    first, last = HORIZON_DAYS
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


def _skipped_daily_mean(sales: pd.DataFrame) -> float:
    """Mean units/day the skipped Products sell, so the family Sales Forecast's
    caveat carries a magnitude rather than just a name."""
    skipped = sales[sales["product"].isin(SKIPPED_PRODUCTS)]
    if skipped.empty or sales.empty:
        return 0.0
    return skipped["quantity"].sum() / sales["date"].nunique()


def main(as_of: Optional[dt.date] = None) -> None:
    as_of = as_of or dt.date.today()
    sales = pd.read_parquet(SALES_HISTORY_PATH)

    for product in unexpected_products(sales):
        print(
            f"WARNING: Sales history contains Product {product!r}, which is "
            "neither in FORECAST_PRODUCTS nor SKIPPED_PRODUCTS — it is missing "
            "from the family Sales Forecast"
        )
    for product, weekday, count in sparse_weekday_counts(sales, as_of):
        detail = (
            "omitted from that date" if count == 0 else f"averaged over {count} days"
        )
        print(
            f"WARNING: {product!r} has {count} recorded {weekday} Sales before "
            f"{as_of} — {detail}"
        )

    demand = forecast_demand(sales, as_of)
    if demand.empty:
        raise ValueError(
            "produced zero Demand Forecast records — refusing to write an empty "
            f"Demand Forecast. Sales history holds {sorted(set(sales['product']))}, "
            f"none of which is in FORECAST_PRODUCTS {list(FORECAST_PRODUCTS)}"
        )
    family = roll_up_sales_forecast(demand)

    DEMAND_FORECAST_PATH.parent.mkdir(parents=True, exist_ok=True)
    demand.to_parquet(DEMAND_FORECAST_PATH, index=False)
    family.to_parquet(SALES_FORECAST_PATH, index=False)

    print(
        f"wrote {len(demand)} Demand Forecast records for "
        f"{demand['product'].nunique()} Products, "
        f"{demand['date'].min().date()}..{demand['date'].max().date()} "
        f"-> {DEMAND_FORECAST_PATH}"
    )
    print(f"wrote {len(family)} family Sales Forecast records -> {SALES_FORECAST_PATH}")
    print(
        f"\nnote: family Sales Forecast excludes {', '.join(sorted(SKIPPED_PRODUCTS))} "
        f"(~{_skipped_daily_mean(sales):.1f} units/day)\n"
    )
    for row in family.itertuples():
        print(f"  {row.date:%a %Y-%m-%d}  {row.forecast_quantity:8.1f}")


if __name__ == "__main__":
    sys.exit(main())
