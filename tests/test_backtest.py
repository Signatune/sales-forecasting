"""Locks the backtest's holdout arithmetic, its baseline, and its MAPE.

A backtest that quietly scores the wrong rows is worse than no backtest, so
what is pinned here is mostly bookkeeping: which dates are held out, which
as_of dates are replayed over them, and which (Product, date) pairs are
scoreable at all. The error metric itself is three lines.

Fixtures are synthetic, and where a test needs a number it is one worked by
hand rather than recomputed the way the code does.
"""
import datetime as dt

import pandas as pd
import pytest

import backtest
from backtest import (
    compare,
    holdout_window,
    mape,
    moving_average_forecast,
    replay_origins,
)

# The real history ends on a Thursday; these fixtures end on 2026-07-09, too.
LAST_DAY = dt.date(2026, 7, 9)


def sales(records) -> pd.DataFrame:
    """A Sales history frame shaped like normalize.py's output."""
    df = pd.DataFrame(records, columns=["product", "date", "quantity"])
    df["date"] = pd.to_datetime(df["date"])
    df["quantity"] = df["quantity"].astype(float)
    return df


def daily(product: str, start: str, days: int, quantity: float = 10.0):
    first = pd.Timestamp(start)
    return [(product, first + pd.Timedelta(days=n), quantity) for n in range(days)]


class TestHoldoutWindow:
    def test_holds_out_the_most_recent_n_days_inclusive(self):
        history = sales(daily("plain", "2026-06-01", 39))  # .. 2026-07-09

        start, end = holdout_window(history, holdout_days=14)

        assert start == pd.Timestamp("2026-06-26")
        assert end == pd.Timestamp("2026-07-09")

    def test_refuses_a_holdout_that_leaves_no_history_to_train_on(self):
        history = sales(daily("plain", "2026-07-01", 9))  # .. 2026-07-09

        with pytest.raises(ValueError, match="no Sales"):
            holdout_window(history, holdout_days=14)


class TestReplayOrigins:
    """forecast_demand covers as_of+2..as_of+7, so origins step by six days:
    every holdout date is forecast exactly once, at a lead time of 2..7 days."""

    def test_first_origin_is_two_days_before_the_holdout(self):
        origins = replay_origins(pd.Timestamp("2026-06-26"), pd.Timestamp("2026-07-09"))

        assert origins[0] == dt.date(2026, 6, 24)

    def test_origins_cover_every_holdout_date_exactly_once(self):
        start, end = pd.Timestamp("2026-06-26"), pd.Timestamp("2026-07-09")

        forecast_days = [
            origin + dt.timedelta(days=lead)
            for origin in replay_origins(start, end)
            for lead in range(2, 8)
        ]

        holdout = [
            (start + pd.Timedelta(days=n)).date() for n in range((end - start).days + 1)
        ]
        assert sorted(set(forecast_days) & set(holdout)) == holdout
        assert len(forecast_days) == len(set(forecast_days))

    def test_no_origin_forecasts_past_the_holdout_unnecessarily(self):
        origins = replay_origins(pd.Timestamp("2026-06-26"), pd.Timestamp("2026-07-09"))

        assert origins == [
            dt.date(2026, 6, 24),
            dt.date(2026, 6, 30),
            dt.date(2026, 7, 6),
        ]


class TestMovingAverageBaseline:
    """The dumber bar: a trailing mean over recorded days, blind to weekday."""

    def test_averages_the_trailing_n_recorded_days_before_as_of(self):
        history = sales([
            ("plain", "2026-06-29", 100.0),  # 4th day back: outside a 3-day window
            ("plain", "2026-06-30", 10.0),
            ("plain", "2026-07-01", 20.0),
            ("plain", "2026-07-02", 30.0),
        ])

        result = moving_average_forecast(
            history, dt.date(2026, 7, 3), trailing_days=3
        )

        assert result["forecast_quantity"].unique() == pytest.approx([20.0])

    def test_the_same_flat_forecast_lands_on_every_target_date(self):
        """No seasonality: a Saturday and a Tuesday get the same number."""
        history = sales(daily("plain", "2026-06-01", 30, quantity=7.0))

        result = moving_average_forecast(history, LAST_DAY, trailing_days=7)

        assert len(result) == 6  # as_of+2 .. as_of+7
        assert result["forecast_quantity"].tolist() == [7.0] * 6

    def test_ignores_sales_on_or_after_as_of(self):
        history = sales([
            ("plain", "2026-07-02", 10.0),
            ("plain", "2026-07-03", 900.0),  # as_of: the day is not over
            ("plain", "2026-07-05", 900.0),  # a target date's own actuals
        ])

        result = moving_average_forecast(history, dt.date(2026, 7, 3), trailing_days=7)

        assert result["forecast_quantity"].unique() == pytest.approx([10.0])

    def test_forecasts_only_the_products_the_model_forecasts(self):
        history = sales([
            ("plain", "2026-07-02", 10.0),
            ("cinnamon raisin", "2026-07-02", 10.0),
        ])

        result = moving_average_forecast(history, dt.date(2026, 7, 3))

        assert set(result["product"]) == {"plain"}

    def test_matches_the_shape_of_a_demand_forecast(self):
        history = sales(daily("plain", "2026-06-01", 30))

        result = moving_average_forecast(history, LAST_DAY)

        assert list(result.columns) == ["product", "date", "forecast_quantity"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["forecast_quantity"].dtype == float


class TestMape:
    def test_is_the_mean_absolute_percentage_error(self):
        actual = pd.Series([100.0, 50.0])
        forecast = pd.Series([90.0, 60.0])  # 10% under, 20% over

        assert mape(actual, forecast) == pytest.approx(15.0)

    def test_is_undefined_for_an_empty_comparison(self):
        assert pd.isna(mape(pd.Series([], dtype=float), pd.Series([], dtype=float)))


class TestCompare:
    """One row per (forecast Product, open holdout day), carrying the actual
    alongside both models' forecasts."""

    def test_pairs_each_actual_with_both_forecasts(self):
        history = sales(daily("plain", "2026-06-01", 39, quantity=10.0))

        comparison = compare(history, holdout_days=7)

        assert set(comparison["product"]) == {"plain"}
        assert len(comparison) == 7
        assert comparison["actual"].tolist() == [10.0] * 7
        assert comparison["seasonal_naive"].tolist() == [10.0] * 7
        assert comparison["moving_average"].tolist() == [10.0] * 7

    def test_covers_only_open_days_in_the_holdout(self):
        """No Product has a row on a day both locations were closed, and the
        backtest must not invent a zero-Sales day out of one."""
        history = sales(
            daily("plain", "2026-06-01", 34)  # .. 2026-07-04
            + daily("plain", "2026-07-06", 4)  # 2026-07-05 closed
        )

        comparison = compare(history, holdout_days=7)

        assert pd.Timestamp("2026-07-05") not in set(comparison["date"])
        assert len(comparison) == 6

    def test_an_absent_sales_row_on_an_open_day_is_a_zero_actual(self):
        history = sales(
            daily("plain", "2026-06-01", 39)
            + daily("sesame", "2026-06-01", 38)  # sold nothing on the last day
        )

        comparison = compare(history, holdout_days=7)
        last_day = comparison[comparison["date"] == pd.Timestamp(LAST_DAY)]

        assert last_day.set_index("product")["actual"]["sesame"] == 0.0

    def test_a_zero_actual_is_not_scoreable(self):
        history = sales(
            daily("plain", "2026-06-01", 39) + daily("sesame", "2026-06-01", 38)
        )

        comparison = compare(history, holdout_days=7).set_index(["product", "date"])

        assert not comparison.loc[("sesame", pd.Timestamp(LAST_DAY)), "scored"]
        assert comparison.loc[("plain", pd.Timestamp(LAST_DAY)), "scored"]

    def test_a_missing_seasonal_naive_forecast_is_not_scoreable(self):
        """forecast_demand omits a Product on a weekday it never sold. The
        baseline, blind to weekday, still forecasts there — so scoring that row
        would compare the two models on different sets of days."""
        history = sales(
            # plain sells Wednesdays, sesame Mondays. 2026-07-08 is a Wednesday.
            [("plain", d, 10.0) for d in
             ("2026-06-03", "2026-06-10", "2026-06-17", "2026-06-24", "2026-07-01")]
            + [("sesame", d, 5.0) for d in
               ("2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29")]
            + [("plain", "2026-07-08", 10.0), ("sesame", "2026-07-08", 5.0)]
        )

        comparison = compare(history, holdout_days=7).set_index(["product", "date"])
        sesame = comparison.loc[("sesame", pd.Timestamp("2026-07-08"))]

        assert pd.isna(sesame["seasonal_naive"])  # sesame never sold on a Wednesday
        assert sesame["moving_average"] == pytest.approx(5.0)
        assert not sesame["scored"]
        assert comparison.loc[("plain", pd.Timestamp("2026-07-08")), "scored"]

    def test_a_forecast_never_sees_the_day_it_forecasts(self):
        """Sales step from 10 to 1000 on 2026-07-04, inside the holdout. The
        origin that forecasts that day sits behind the step; a later origin
        sits in front of it."""
        history = sales(
            daily("plain", "2026-06-01", 33, quantity=10.0)  # .. 2026-07-03
            + daily("plain", "2026-07-04", 6, quantity=1000.0)  # .. 2026-07-09
        )

        comparison = compare(history, holdout_days=8, trailing_days=7).set_index("date")

        # Origins are 2026-06-30 and 2026-07-06.
        step_day = comparison.loc[pd.Timestamp("2026-07-04")]
        assert step_day["actual"] == 1000.0
        assert step_day["seasonal_naive"] == 10.0  # forecast from 2026-06-30
        assert step_day["moving_average"] == 10.0

        # The second origin trails five ten-days and the 4th and 5th's thousands.
        assert comparison.loc[
            pd.Timestamp("2026-07-08"), "moving_average"
        ] == pytest.approx((5 * 10.0 + 2 * 1000.0) / 7)


class TestProductScores:
    def test_reports_both_models_mape_over_the_same_scored_days(self):
        history = sales(
            [("plain", "2026-06-%02d" % d, 10.0) for d in range(1, 31)]
            + [("plain", "2026-07-%02d" % d, 20.0) for d in range(1, 10)]
        )

        scores = backtest.product_scores(compare(history, holdout_days=7))
        plain = scores.set_index("product").loc["plain"]

        # Every holdout day's actual is 20; the seasonal-naive mean of a
        # trailing history that is mostly tens sits below the moving average's,
        # which has more of the recent twenties in it.
        assert plain["scored_days"] == 7
        assert plain["seasonal_naive_mape"] > plain["moving_average_mape"]

    def test_counts_the_days_it_could_not_score(self):
        history = sales(
            daily("plain", "2026-06-01", 39) + daily("sesame", "2026-06-01", 38)
        )

        scores = backtest.product_scores(compare(history, holdout_days=7))
        sesame = scores.set_index("product").loc["sesame"]

        assert sesame["scored_days"] == 6
        assert sesame["unscored_days"] == 1

    def test_a_product_with_nothing_scoreable_reports_no_mape(self):
        history = sales(
            daily("plain", "2026-06-01", 39) + [("sesame", "2026-06-01", 5.0)]
        )

        scores = backtest.product_scores(compare(history, holdout_days=7))
        sesame = scores.set_index("product").loc["sesame"]

        assert sesame["scored_days"] == 0
        assert pd.isna(sesame["seasonal_naive_mape"])


class TestFamilyTotals:
    """The family Sales Forecast sums FORECAST_PRODUCTS only, so the actual it
    is scored against must sum those same Products — see the ticket."""

    def test_family_actual_excludes_the_skipped_products(self):
        history = sales(
            daily("plain", "2026-06-01", 39, quantity=10.0)
            + daily("cinnamon raisin", "2026-06-01", 39, quantity=90.0)
        )

        family = backtest.family_totals(compare(history, holdout_days=7))

        assert family["actual"].tolist() == [10.0] * 7  # not 100.0

    def test_a_product_neither_model_can_be_scored_on_leaves_the_family_total(self):
        """The like-with-like rule again, one level down. On 2026-07-08 the
        seasonal-naive model declines to forecast sesame — it never sold on a
        Wednesday — but the weekday-blind baseline forecasts it anyway. Summing
        sesame's actual into the family total while only one model's forecast
        carries it would charge the model for a Product it never bid on, and
        hand the baseline a free unit."""
        history = sales(
            [("plain", d, 10.0) for d in
             ("2026-06-03", "2026-06-10", "2026-06-17", "2026-06-24", "2026-07-01")]
            + [("sesame", d, 5.0) for d in
               ("2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29")]
            + [("plain", "2026-07-08", 10.0), ("sesame", "2026-07-08", 5.0)]
        )

        family = backtest.family_totals(compare(history, holdout_days=7))
        day = family[family["date"] == pd.Timestamp("2026-07-08")].iloc[0]

        assert day["actual"] == 10.0  # plain alone, not 15.0
        assert day["seasonal_naive"] == 10.0
        assert day["moving_average"] == 10.0  # not 15.0

    def test_family_forecast_sums_the_products_forecast_that_day(self):
        history = sales(
            daily("plain", "2026-06-01", 39, quantity=10.0)
            + daily("sesame", "2026-06-01", 39, quantity=4.0)
        )

        family = backtest.family_totals(compare(history, holdout_days=7))

        assert family["seasonal_naive"].tolist() == [14.0] * 7
        assert family["moving_average"].tolist() == [14.0] * 7

    def test_family_mape_is_computed_over_the_family_totals(self):
        history = sales(
            daily("plain", "2026-06-01", 32, quantity=10.0)  # .. 2026-07-02
            + daily("plain", "2026-07-03", 7, quantity=11.0)  # holdout: 10% under
        )

        scores = backtest.family_scores(compare(history, holdout_days=7))

        assert scores["scored_days"] == 7
        assert scores["seasonal_naive_mape"] == pytest.approx(100 / 11, rel=1e-6)


class TestMain:
    def test_prints_both_models_side_by_side(self, tmp_path, monkeypatch, capsys):
        history_path = tmp_path / "sales_history.parquet"
        monkeypatch.setattr(backtest, "SALES_HISTORY_PATH", history_path)
        sales(daily("plain", "2026-05-01", 70)).to_parquet(history_path, index=False)

        backtest.main()
        out = capsys.readouterr().out

        assert "seasonal-naive" in out
        assert "moving-average" in out
        assert "plain" in out
        assert "family" in out.lower()
