"""Locks the seasonal-naive Demand Forecast and the family Sales Forecast.

The model is simple enough that its edges are the interesting part: which
history a forecast is allowed to see (ticket 03 replays past dates, so this
must not leak the future), which Products are in scope, and what happens when
a Product has never sold on the weekday being forecast.

Fixtures here are synthetic. normalize.py's tests already pin the real Toast
shape; these pin the arithmetic.
"""
import datetime as dt

import pandas as pd
import pytest

from forecast import (
    FORECAST_PRODUCTS,
    SKIPPED_PRODUCTS,
    forecast_demand,
    roll_up_sales_forecast,
    sparse_weekday_counts,
    unexpected_products,
)

# 2026-07-10 is a Friday. Targets are as_of+2..as_of+7 == Sun 12th..Fri 17th.
AS_OF = dt.date(2026, 7, 10)


def sales(records) -> pd.DataFrame:
    """A Sales history frame shaped like normalize.py's output."""
    df = pd.DataFrame(records, columns=["product", "date", "quantity"])
    df["date"] = pd.to_datetime(df["date"])
    df["quantity"] = df["quantity"].astype(float)
    return df


class TestSeasonalNaiveAverage:
    def test_averages_sales_on_the_same_weekday(self):
        """Three prior Sundays for one Product -> that Sunday's mean."""
        history = sales([
            ("plain", "2026-06-21", 10.0),  # Sunday
            ("plain", "2026-06-28", 20.0),  # Sunday
            ("plain", "2026-07-05", 60.0),  # Sunday
            ("plain", "2026-06-22", 999.0),  # Monday: must not leak in
        ])

        result = forecast_demand(history, AS_OF)
        sunday = result[result["date"] == pd.Timestamp("2026-07-12")]

        assert len(sunday) == 1
        assert sunday["forecast_quantity"].iloc[0] == pytest.approx(30.0)

    def test_each_weekday_is_averaged_independently(self):
        history = sales([
            ("plain", "2026-07-05", 10.0),  # Sunday
            ("plain", "2026-07-06", 40.0),  # Monday
            ("plain", "2026-06-29", 20.0),  # Monday
        ])

        result = forecast_demand(history, AS_OF).set_index("date")

        assert result.loc[pd.Timestamp("2026-07-12"), "forecast_quantity"] == 10.0
        assert result.loc[pd.Timestamp("2026-07-13"), "forecast_quantity"] == 30.0

    def test_absent_days_are_skipped_not_counted_as_zero(self):
        """A Product that sold on one of two Sundays forecasts its recorded
        mean, not half of it. See the ticket's 'recorded days only' decision."""
        history = sales([
            ("plain", "2026-06-28", 8.0),  # Sunday, sold
            ("sesame", "2026-06-28", 1.0),  # pins 2026-07-05 as an open day
            ("sesame", "2026-07-05", 1.0),  # Sunday, plain absent entirely
        ])

        result = forecast_demand(history, AS_OF)
        plain_sunday = result[
            (result["product"] == "plain")
            & (result["date"] == pd.Timestamp("2026-07-12"))
        ]

        assert plain_sunday["forecast_quantity"].iloc[0] == pytest.approx(8.0)


class TestHorizon:
    def test_covers_two_through_seven_days_out(self):
        history = sales([("plain", f"2026-06-{day:02d}", 5.0) for day in range(1, 29)])

        dates = sorted(forecast_demand(history, AS_OF)["date"].unique())

        assert [pd.Timestamp(d).date() for d in dates] == [
            dt.date(2026, 7, 12),
            dt.date(2026, 7, 13),
            dt.date(2026, 7, 14),
            dt.date(2026, 7, 15),
            dt.date(2026, 7, 16),
            dt.date(2026, 7, 17),
        ]

    def test_excludes_tomorrow_and_today(self):
        history = sales([("plain", f"2026-06-{day:02d}", 5.0) for day in range(1, 29)])

        dates = set(forecast_demand(history, AS_OF)["date"])

        assert pd.Timestamp(AS_OF) not in dates
        assert pd.Timestamp("2026-07-11") not in dates


class TestHistoryCutoff:
    """Ticket 03 backtests by replaying a past as_of. Sales on or after that
    date are the future and must not reach the model."""

    def test_ignores_sales_on_or_after_as_of(self):
        """as_of is a Friday, and so is the last target date. Both the partial
        as_of day and the target's own actuals must stay out of the average."""
        history = sales([
            ("plain", "2026-07-03", 10.0),  # Friday, before as_of: the only input
            ("plain", "2026-07-10", 500.0),  # Friday == as_of, day not yet over
            ("plain", "2026-07-17", 900.0),  # Friday, the target date itself
        ])

        result = forecast_demand(history, AS_OF).set_index("date")
        friday = result.loc[pd.Timestamp("2026-07-17"), "forecast_quantity"]

        assert friday == pytest.approx(10.0)  # not 255.0, 470.0 or 900.0

    def test_a_past_as_of_sees_only_its_own_past(self):
        history = sales([
            ("plain", "2026-06-07", 10.0),  # Sunday
            ("plain", "2026-06-14", 20.0),  # Sunday
            ("plain", "2026-06-21", 90.0),  # Sunday, after the earlier as_of
        ])

        early = forecast_demand(history, dt.date(2026, 6, 19)).set_index("date")
        late = forecast_demand(history, dt.date(2026, 7, 10)).set_index("date")

        # 2026-06-21 is a Sunday, 2 days after the early as_of.
        assert early.loc[pd.Timestamp("2026-06-21"), "forecast_quantity"] == 15.0
        assert late.loc[pd.Timestamp("2026-07-12"), "forecast_quantity"] == 40.0


class TestProductScope:
    def test_skipped_products_are_not_forecast(self):
        history = sales([
            ("plain", "2026-07-05", 10.0),
            ("cinnamon raisin", "2026-07-05", 10.0),
            ("pumpernickel", "2026-07-05", 10.0),
        ])

        products = set(forecast_demand(history, AS_OF)["product"])

        assert products == {"plain"}
        assert {"cinnamon raisin", "pumpernickel"} <= set(SKIPPED_PRODUCTS)

    def test_forecast_scope_is_the_three_baked_varieties(self):
        assert set(FORECAST_PRODUCTS) == {"everything", "plain", "sesame"}

    def test_gluten_free_varieties_are_skipped_not_forecast(self):
        """Bought in frozen, never baked — a recorded decision, not a warning on
        every run. Their Sales stay in the history (see normalize.py)."""
        for variety in ("gluten-free everything", "gluten-free plain"):
            assert variety in SKIPPED_PRODUCTS
            assert variety not in FORECAST_PRODUCTS

    def test_gluten_free_sales_are_not_forecast_but_are_a_known_decision(self):
        history = sales([
            ("plain", "2026-07-05", 10.0),
            ("gluten-free plain", "2026-07-05", 5.0),
            ("gluten-free everything", "2026-07-05", 3.0),
        ])

        products = set(forecast_demand(history, AS_OF)["product"])

        assert products == {"plain"}  # gluten-free left the forecast scope
        assert unexpected_products(history) == []  # but not as an unexpected variety

    def test_skipped_and_forecast_products_do_not_overlap(self):
        assert not set(FORECAST_PRODUCTS) & set(SKIPPED_PRODUCTS)

    def test_unexpected_products_flags_an_unclassified_variety(self):
        history = sales([
            ("plain", "2026-07-05", 10.0),
            ("cinnamon raisin", "2026-07-05", 10.0),
            ("asiago", "2026-07-05", 10.0),
        ])

        assert unexpected_products(history) == ["asiago"]

    def test_unexpected_products_is_empty_for_a_known_history(self):
        history = sales([(p, "2026-07-05", 1.0) for p in FORECAST_PRODUCTS])

        assert unexpected_products(history) == []


class TestZeroObservationGuard:
    """A Product with no record on a target weekday is omitted, not zeroed and
    not NaN — inventing a zero would contradict recorded-only averaging."""

    def test_product_is_omitted_on_a_weekday_it_never_sold(self):
        history = sales([("plain", "2026-07-05", 10.0)])  # Sunday only

        result = forecast_demand(history, AS_OF)

        assert set(result["date"]) == {pd.Timestamp("2026-07-12")}
        assert not result["forecast_quantity"].isna().any()

    def test_omission_does_not_produce_nan_in_the_rollup(self):
        history = sales([
            ("plain", "2026-07-05", 10.0),  # Sunday only
            ("sesame", "2026-07-05", 4.0),
            ("sesame", "2026-07-06", 6.0),  # Monday
        ])

        family = roll_up_sales_forecast(forecast_demand(history, AS_OF))

        assert not family["forecast_quantity"].isna().any()
        by_date = family.set_index("date")["forecast_quantity"]
        assert by_date[pd.Timestamp("2026-07-12")] == 14.0  # both Products
        assert by_date[pd.Timestamp("2026-07-13")] == 6.0  # sesame alone

    def test_sparse_weekday_counts_reports_thin_and_missing_weekdays(self):
        history = sales([
            ("plain", "2026-07-05", 10.0),  # one Sunday only
            ("sesame", "2026-06-28", 1.0),
            ("sesame", "2026-07-05", 1.0),  # two Sundays
        ])

        sparse = sparse_weekday_counts(history, AS_OF, min_observations=2)

        assert ("plain", "Sunday", 1) in sparse
        assert ("plain", "Monday", 0) in sparse
        assert ("sesame", "Sunday", 2) not in sparse


class TestFamilyRollup:
    def test_sums_demand_forecasts_across_products_per_date(self):
        demand = pd.DataFrame({
            "product": ["plain", "sesame", "plain", "sesame"],
            "date": pd.to_datetime(
                ["2026-07-12", "2026-07-12", "2026-07-13", "2026-07-13"]
            ),
            "forecast_quantity": [10.0, 5.0, 20.0, 2.5],
        })

        family = roll_up_sales_forecast(demand)

        assert list(family.columns) == ["date", "forecast_quantity"]
        assert len(family) == 2
        assert family["forecast_quantity"].tolist() == [15.0, 22.5]

    def test_one_row_per_date_sorted(self):
        demand = pd.DataFrame({
            "product": ["plain", "plain"],
            "date": pd.to_datetime(["2026-07-13", "2026-07-12"]),
            "forecast_quantity": [1.0, 2.0],
        })

        family = roll_up_sales_forecast(demand)

        assert family["date"].is_monotonic_increasing
        assert family["date"].is_unique

    def test_empty_demand_forecast_rolls_up_to_an_empty_frame(self):
        empty = forecast_demand(sales([]), AS_OF)

        family = roll_up_sales_forecast(empty)

        assert family.empty
        assert list(family.columns) == ["date", "forecast_quantity"]


class TestOutputShape:
    def test_columns_and_dtypes(self):
        history = sales([("plain", "2026-07-05", 10.0)])

        result = forecast_demand(history, AS_OF)

        assert list(result.columns) == ["product", "date", "forecast_quantity"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["forecast_quantity"].dtype == float

    def test_rows_are_sorted_by_date_then_product(self):
        history = sales([
            ("sesame", "2026-07-05", 1.0),
            ("plain", "2026-07-05", 1.0),
            ("sesame", "2026-07-06", 1.0),
            ("plain", "2026-07-06", 1.0),
        ])

        result = forecast_demand(history, AS_OF)

        assert result[["date", "product"]].values.tolist() == [
            [pd.Timestamp("2026-07-12"), "plain"],
            [pd.Timestamp("2026-07-12"), "sesame"],
            [pd.Timestamp("2026-07-13"), "plain"],
            [pd.Timestamp("2026-07-13"), "sesame"],
        ]

    def test_rejects_a_frame_that_is_not_a_sales_history(self):
        with pytest.raises(ValueError, match="columns"):
            forecast_demand(pd.DataFrame({"item": ["plain"]}), AS_OF)

    def test_rejects_a_nan_quantity_rather_than_averaging_it(self):
        """A NaN Sales quantity would survive the zero-observation guard, become
        a NaN Demand Forecast, and silently poison the family Sales Forecast."""
        history = sales([
            ("plain", "2026-07-05", 10.0),
            ("plain", "2026-06-28", float("nan")),
        ])

        with pytest.raises(ValueError, match="NaN"):
            forecast_demand(history, AS_OF)
