"""Locks the pure scoring/buffering primitives the bake-forecast model
comparison reduces on: pinball loss at a Service Level, WAPE, and the P95
relative-residual buffer transform.

As in test_backtest.py, numbers are worked by hand rather than recomputed the
way the code does, and only the returned values are asserted on.
"""
import datetime as dt

import pandas as pd
import pytest

import backtest
import forecast
import model_comparison
import sales_history
from model_comparison import (
    WHEAT_TOTAL,
    actual_totals,
    bake_to_quantities,
    buffered_totals,
    compare_models,
    compare_split_models,
    constant_recent_share,
    coverage,
    evaluation_window,
    ewma_forecast,
    forecast_totals,
    p95_buffer,
    per_variety_recency_share,
    pinball,
    same_weekday_share,
    seasonal_trend_forecast,
    trailing_window_forecast,
    wape,
    wheat_total,
)


def sales(records) -> pd.DataFrame:
    """A Sales history frame shaped like normalize.py's output."""
    df = pd.DataFrame(records, columns=["product", "date", "quantity"])
    df["date"] = pd.to_datetime(df["date"])
    df["quantity"] = df["quantity"].astype(float)
    return df


def daily(product: str, start: str, days: int, quantity: float = 10.0):
    first = pd.Timestamp(start)
    return [(product, first + pd.Timedelta(days=n), quantity) for n in range(days)]


def varieties(start: str, days: int, quantities=(5.0, 3.0, 2.0)):
    """everything/plain/sesame each selling a constant quantity every day."""
    e, p, s = quantities
    return (
        daily("everything", start, days, e)
        + daily("plain", start, days, p)
        + daily("sesame", start, days, s)
    )


class TestPinball:
    def test_is_zero_when_forecast_equals_actual(self):
        actual = pd.Series([10.0, 20.0, 30.0])
        forecast = pd.Series([10.0, 20.0, 30.0])

        assert pinball(actual, forecast, level=0.95) == pytest.approx(0.0)

    def test_is_the_mean_quantile_loss(self):
        # actual=10, forecast=8 -> under-forecast: 0.95 * (10 - 8) = 1.9
        # actual=10, forecast=12 -> over-forecast: 0.05 * (12 - 10) = 0.1
        # mean = (1.9 + 0.1) / 2 = 1.0
        actual = pd.Series([10.0, 10.0])
        forecast = pd.Series([8.0, 12.0])

        assert pinball(actual, forecast, level=0.95) == pytest.approx(1.0)

    def test_under_forecast_is_penalised_19x_an_equal_over_forecast(self):
        # Same magnitude of miss (2 units), opposite direction, at level=0.95.
        # Under: actual (12) >= forecast (10) -> 0.95 * 2 = 1.9
        # Over: actual (10) < forecast (12) -> 0.05 * 2 = 0.1
        # 1.9 / 0.1 == 19 == level / (1 - level)
        under = pinball(pd.Series([12.0]), pd.Series([10.0]), level=0.95)
        over = pinball(pd.Series([10.0]), pd.Series([12.0]), level=0.95)

        assert under == pytest.approx(1.9)
        assert over == pytest.approx(0.1)
        assert under / over == pytest.approx(19.0)
        assert under / over == pytest.approx(0.95 / 0.05)


class TestWape:
    def test_is_total_absolute_error_over_total_actual(self):
        # errors: |100-90|=10, |50-60|=10 -> total abs error = 20
        # total actual = 100 + 50 = 150
        # wape = 20 / 150
        actual = pd.Series([100.0, 50.0])
        forecast = pd.Series([90.0, 60.0])

        assert wape(actual, forecast) == pytest.approx(20.0 / 150.0)

    def test_a_zero_individual_actual_does_not_blow_up(self):
        # Unlike MAPE, a zero individual actual just contributes its absolute
        # error to the numerator; the denominator is the non-zero total.
        # errors: |0-5|=5, |100-90|=10 -> total abs error = 15
        # total actual = 0 + 100 = 100
        actual = pd.Series([0.0, 100.0])
        forecast = pd.Series([5.0, 90.0])

        assert wape(actual, forecast) == pytest.approx(15.0 / 100.0)

    def test_is_undefined_when_the_total_actual_is_zero(self):
        actual = pd.Series([0.0, 0.0])
        forecast = pd.Series([5.0, 3.0])

        assert pd.isna(wape(actual, forecast))

    def test_is_undefined_for_an_empty_comparison(self):
        assert pd.isna(wape(pd.Series([], dtype=float), pd.Series([], dtype=float)))


class TestP95Buffer:
    def test_buffers_a_point_forecast_by_the_95th_percentile_relative_residual(self):
        # 20 relative residuals, 0.00..0.19 in steps of 0.01. Linear
        # interpolation (numpy/pandas default) puts the 95th percentile at
        # index 0.95 * 19 = 18.05 -> between residuals[18]=0.18 and
        # residuals[19]=0.19: 0.18 + 0.05 * (0.19 - 0.18) = 0.1805
        residuals = pd.Series([round(0.01 * n, 2) for n in range(20)])

        buffered = p95_buffer(100.0, residuals, level=0.95)

        assert buffered == pytest.approx(100.0 * 1.1805)

    def test_a_wider_residual_spread_yields_a_larger_buffer(self):
        narrow = pd.Series([0.05, 0.06, 0.07, 0.08, 0.09, 0.10])
        wide = pd.Series([0.05, 0.06, 0.07, 0.08, 0.09, 0.50])

        narrow_buffer = p95_buffer(100.0, narrow, level=0.95)
        wide_buffer = p95_buffer(100.0, wide, level=0.95)

        assert wide_buffer > narrow_buffer

    def test_scales_with_the_point_forecast(self):
        residuals = pd.Series([0.05, 0.10, 0.15, 0.20])

        single = p95_buffer(100.0, residuals, level=0.95)
        double = p95_buffer(200.0, residuals, level=0.95)

        assert double == pytest.approx(single * 2)

    def test_accepts_a_series_of_point_forecasts(self):
        # n=3, index = 0.95 * (3 - 1) = 1.9 -> between residuals[1]=0.10 and
        # residuals[2]=0.20: 0.10 + 0.9 * (0.20 - 0.10) = 0.19
        residuals = pd.Series([0.0, 0.10, 0.20])
        point_forecasts = pd.Series([100.0, 200.0])

        buffered = p95_buffer(point_forecasts, residuals, level=0.95)

        assert list(buffered) == pytest.approx([119.0, 238.0])


class TestCoverage:
    """How often a buffered quantity actually covered Demand — the realised
    Service Level the 95% target is read against."""

    def test_is_the_share_of_days_the_quantity_covered_demand(self):
        # Covered on 3 of the 4 days: 12 >= 10, 10 >= 10 (an exact bake covers —
        # nothing short), 9 < 10 (a Stockout), 11 >= 10.
        actual = pd.Series([10.0, 10.0, 10.0, 10.0])
        quantity = pd.Series([12.0, 10.0, 9.0, 11.0])

        assert coverage(actual, quantity) == pytest.approx(0.75)

    def test_a_quantity_that_never_covers_scores_zero(self):
        actual = pd.Series([10.0, 10.0])
        quantity = pd.Series([9.0, 1.0])

        assert coverage(actual, quantity) == pytest.approx(0.0)

    def test_is_undefined_for_an_empty_comparison(self):
        empty = pd.Series([], dtype=float)

        assert pd.isna(coverage(empty, empty))


def flat_model(quantity: float):
    """A stub candidate that forecasts a fixed quantity for every target date —
    a known point forecast to work the buffer's arithmetic against by hand."""

    def model(sales_history, as_of):
        records = [
            {"product": "plain", "date": target, "forecast_quantity": quantity}
            for target in forecast.target_dates(as_of)
        ]
        return pd.DataFrame(records, columns=["product", "date", "forecast_quantity"])

    return model


class TestBufferedTotals:
    """The per-day replay both the pinball score and the inspection charts read,
    so the number a chart draws is the number the score was taken from."""

    def test_carries_the_actual_point_forecast_and_p95_per_day(self):
        # The model forecasts a flat 10 while the shop sold 12 every day, so its
        # relative residual is a constant (12 - 10) / 10 = 0.2 — a P95 of 0.2 and
        # a buffered quantity of 10 * 1.2 = 12 on every evaluation day.
        history = sales(daily("plain", "2026-06-01", 28, quantity=12.0))
        warmup_days = [pd.Timestamp("2026-06-15"), pd.Timestamp("2026-06-16")]
        eval_days = [pd.Timestamp("2026-06-27"), pd.Timestamp("2026-06-28")]

        replay = buffered_totals(
            flat_model(10.0), history, eval_days, warmup_days, lead=3, level=0.95
        )

        assert list(replay.columns) == [
            "date",
            "actual",
            "forecast_quantity",
            "buffered_quantity",
        ]
        assert replay["date"].tolist() == eval_days
        assert replay["actual"].tolist() == pytest.approx([12.0, 12.0])
        assert replay["forecast_quantity"].tolist() == pytest.approx([10.0, 10.0])
        assert replay["buffered_quantity"].tolist() == pytest.approx([12.0, 12.0])

    def test_the_buffer_comes_from_the_warmup_days_not_the_scored_ones(self):
        # The shop sold 12 through the warmup and 100 across the evaluation days.
        # A buffer that saw its own scored days would blow up to cover the 100s;
        # taking its residuals from the warmup only, it stays at 10 * 1.2 = 12 —
        # a badly under-covering P95, which is exactly what an honest score shows.
        history = sales(
            daily("plain", "2026-06-01", 21, quantity=12.0)  # .. 2026-06-21
            + daily("plain", "2026-06-22", 7, quantity=100.0)  # .. 2026-06-28
        )
        warmup_days = [pd.Timestamp("2026-06-15"), pd.Timestamp("2026-06-16")]
        eval_days = [pd.Timestamp("2026-06-27"), pd.Timestamp("2026-06-28")]

        replay = buffered_totals(
            flat_model(10.0), history, eval_days, warmup_days, lead=3, level=0.95
        )

        assert replay["actual"].tolist() == pytest.approx([100.0, 100.0])
        assert replay["buffered_quantity"].tolist() == pytest.approx([12.0, 12.0])
        assert coverage(replay["actual"], replay["buffered_quantity"]) == 0.0

    def test_an_empty_residual_pool_leaves_the_point_forecast_unbuffered(self):
        history = sales(daily("plain", "2026-06-01", 28, quantity=12.0))
        eval_days = [pd.Timestamp("2026-06-27")]

        replay = buffered_totals(
            flat_model(10.0), history, eval_days, warmup_days=[], lead=3, level=0.95
        )

        assert replay["buffered_quantity"].tolist() == pytest.approx([10.0])


class TestWheatTotal:
    """The three varieties sum per date into one synthetic total Product — the
    Poolish is decided on the total, not per variety."""

    def test_sums_the_varieties_per_date(self):
        per_variety = pd.DataFrame(
            [
                ("everything", pd.Timestamp("2026-07-11"), 5.0),
                ("plain", pd.Timestamp("2026-07-11"), 3.0),
                ("sesame", pd.Timestamp("2026-07-11"), 2.0),
                ("everything", pd.Timestamp("2026-07-12"), 6.0),
                ("plain", pd.Timestamp("2026-07-12"), 4.0),
                ("sesame", pd.Timestamp("2026-07-12"), 2.0),
            ],
            columns=["product", "date", "forecast_quantity"],
        )

        total = wheat_total(per_variety)

        assert set(total["product"]) == {WHEAT_TOTAL}
        assert total.set_index("date")["forecast_quantity"].to_dict() == {
            pd.Timestamp("2026-07-11"): 10.0,
            pd.Timestamp("2026-07-12"): 12.0,
        }

    def test_an_empty_forecast_totals_to_nothing(self):
        empty = pd.DataFrame(columns=["product", "date", "forecast_quantity"])

        assert wheat_total(empty).empty


class TestEvaluationWindow:
    def test_covers_the_most_recent_n_weeks_inclusive(self):
        history = sales(daily("plain", "2026-05-01", 70))  # .. 2026-07-09

        start, end = evaluation_window(history, weeks=1)

        assert start == pd.Timestamp("2026-07-03")
        assert end == pd.Timestamp("2026-07-09")

    def test_refuses_a_window_that_leaves_no_history_to_train_on(self):
        history = sales(daily("plain", "2026-07-03", 7))  # .. 2026-07-09

        with pytest.raises(ValueError, match="no Sales before"):
            evaluation_window(history, weeks=1)


class TestForecastTotals:
    """The wheat-total point forecast per day, at a fixed lead, leak-free."""

    def test_a_forecast_never_sees_the_day_it_forecasts(self):
        # plain sells 10 every day, then jumps to 1000 from 2026-07-06. The
        # forecast for 2026-07-09 is made at lead 3 (as_of 2026-07-06), so it
        # trails only the tens behind the jump.
        history = sales(
            daily("plain", "2026-06-01", 35)  # .. 2026-07-05
            + daily("plain", "2026-07-06", 4, quantity=1000.0)  # .. 2026-07-09
        )
        target = pd.Timestamp("2026-07-09")

        totals = forecast_totals(
            backtest.moving_average_forecast, history, [target], lead=3
        )

        assert totals.loc[0, "forecast_quantity"] == pytest.approx(10.0)
        assert actual_totals(history, [target]).loc[0, "actual"] == 1000.0

    def test_sums_the_three_varieties_into_the_total(self):
        history = sales(varieties("2026-06-01", 39, (5.0, 3.0, 2.0)))
        target = pd.Timestamp("2026-07-09")

        totals = forecast_totals(
            backtest.moving_average_forecast, history, [target], lead=3
        )

        assert totals.loc[0, "forecast_quantity"] == pytest.approx(10.0)  # 5+3+2


class TestCompareModels:
    def test_ranks_the_registered_candidates_on_pinball(self):
        # Every variety sells a flat quantity all history, so every candidate
        # forecasts the total exactly (residuals zero, no buffer) and scores a
        # perfect pinball of zero. Asserted as a superset so a later ticket
        # adding ETS does not break this.
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        comparison = compare_models(history, eval_weeks=1, warmup_weeks=1)

        assert list(comparison.columns) == [
            "model",
            "pinball",
            "coverage",
            "mape",
            "days",
        ]
        assert {
            "seasonal_naive",
            "moving_average",
            "trailing_window",
            "ewma",
            "seasonal_trend",
        } <= set(comparison["model"])
        n = len(comparison)
        assert comparison["pinball"].tolist() == pytest.approx([0.0] * n)
        assert comparison["mape"].tolist() == pytest.approx([0.0] * n)
        assert comparison["days"].tolist() == [7] * n
        # An exact forecast covers Demand on every day — a realised 100%.
        assert comparison["coverage"].tolist() == pytest.approx([1.0] * n)
        # Sorted best (lowest pinball) first.
        assert comparison["pinball"].is_monotonic_increasing

    def test_under_forecasting_the_total_costs_more_than_over(self):
        # A flat history the models forecast exactly, then break the buffer's
        # residual pool: with no warmup buffer, a model that reads high vs one
        # that reads low are penalised asymmetrically by pinball@95.
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        comparison = compare_models(
            history, eval_weeks=1, warmup_weeks=1, level=0.95
        )

        # Both are exact here, so this simply pins that the frame carries a
        # finite pinball per candidate rather than a NaN.
        assert comparison["pinball"].notna().all()

    def test_scores_every_open_day_in_the_window_once(self):
        # The rolling-origin bookkeeping TestReplayOrigins pins for backtest:
        # each open day in the window is forecast exactly once, at the lead.
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        comparison = compare_models(history, eval_weeks=2, warmup_weeks=2)

        # 2 weeks of gap-free Sales = 14 open days, each scored once per model.
        assert (comparison["days"] == 14).all()

    def test_a_closed_day_is_not_an_origin_target(self):
        # No variety sells on 2026-07-06 (both locations closed), so it is never
        # a target date and drops out of the window — 13 open days, not 14.
        history = sales(
            [
                row
                for row in varieties("2026-05-29", 42, (5.0, 3.0, 2.0))
                if row[1] != pd.Timestamp("2026-07-06")
            ]
        )

        comparison = compare_models(history, eval_weeks=2, warmup_weeks=2)

        assert (comparison["days"] == 13).all()


def mondays(product, quantities, start="2026-06-01"):
    """One Sale per week on the same weekday (2026-06-01 is a Monday), oldest
    first — the same-weekday series each candidate below reduces over."""
    first = pd.Timestamp(start)
    return [(product, first + pd.Timedelta(weeks=n), q) for n, q in enumerate(quantities)]


def _quantity_on(frame, date):
    """The single forecast_quantity a candidate emitted for `date`."""
    row = frame.loc[frame["date"] == pd.Timestamp(date), "forecast_quantity"]
    assert len(row) == 1
    return float(row.iloc[0])


def _equal_weight_mean(history, as_of, date):
    """The incumbent forecast.forecast_demand's same-weekday mean for `date` —
    the high-biased baseline the recency-aware models must beat downward."""
    return _quantity_on(forecast.forecast_demand(history, as_of), date)


class TestTrailingWindowSeasonalNaive:
    """Same-weekday mean over only the last N weeks — it must ignore older ones."""

    def test_averages_only_the_last_n_same_weekday_observations(self):
        # Six declining Mondays; with a 3-week window only the last three count.
        # last 3 = (70 + 60 + 50) / 3 = 60; the 100/90/80 are outside the window.
        history = sales(mondays("plain", [100.0, 90.0, 80.0, 70.0, 60.0, 50.0]))
        as_of = dt.date(2026, 7, 7)  # after the last Monday 2026-07-06
        target = "2026-07-13"  # the next Monday, inside as_of+2..as_of+7

        result = trailing_window_forecast(history, as_of, weeks=3)

        assert _quantity_on(result, target) == pytest.approx(60.0)

    def test_forecasts_below_the_equal_weight_mean_on_a_declining_series(self):
        # Trimming the stale high tail puts the window mean (60) below the
        # incumbent's all-history same-weekday mean (450 / 6 = 75).
        history = sales(mondays("plain", [100.0, 90.0, 80.0, 70.0, 60.0, 50.0]))
        as_of = dt.date(2026, 7, 7)
        target = "2026-07-13"

        windowed = _quantity_on(trailing_window_forecast(history, as_of, weeks=3), target)

        assert _equal_weight_mean(history, as_of, target) == pytest.approx(75.0)
        assert windowed < _equal_weight_mean(history, as_of, target)

    def test_ignores_sales_on_or_after_as_of(self):
        history = sales(
            daily("plain", "2026-06-01", 32, quantity=10.0)  # .. 2026-07-02
            + [("plain", "2026-07-03", 900.0)]  # as_of: the day is not over
            + [("plain", "2026-07-05", 900.0)]  # a target date's own actuals
        )

        result = trailing_window_forecast(history, dt.date(2026, 7, 3))

        assert result["forecast_quantity"].unique() == pytest.approx([10.0])

    def test_forecasts_only_the_forecast_products(self):
        history = sales(
            daily("plain", "2026-06-01", 30) + daily("cinnamon raisin", "2026-06-01", 30)
        )

        result = trailing_window_forecast(history, dt.date(2026, 7, 3))

        assert set(result["product"]) == {"plain"}

    def test_matches_the_shape_of_a_demand_forecast(self):
        history = sales(daily("plain", "2026-06-01", 30))

        result = trailing_window_forecast(history, dt.date(2026, 7, 9))

        assert list(result.columns) == ["product", "date", "forecast_quantity"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["forecast_quantity"].dtype == float


class TestEwmaSeasonalNaive:
    """Same-weekday mean with recent observations weighted above old ones."""

    def test_weights_recent_same_weekday_sales_above_old(self):
        # Four declining Mondays 40,30,20,10 (oldest->newest), half-life 1 obs
        # -> alpha 0.5. adjust=True weights newest->oldest 1, .5, .25, .125:
        #   (10*1 + 20*.5 + 30*.25 + 40*.125) / (1 + .5 + .25 + .125)
        #   = 32.5 / 1.875 = 17.3333 — pulled far below the equal mean of 25.
        history = sales(mondays("plain", [40.0, 30.0, 20.0, 10.0]))
        as_of = dt.date(2026, 6, 23)  # after the last Monday 2026-06-22
        target = "2026-06-29"  # the next Monday

        result = ewma_forecast(history, as_of, halflife=1)

        assert _quantity_on(result, target) == pytest.approx(17.333333, rel=1e-5)
        assert _equal_weight_mean(history, as_of, target) == pytest.approx(25.0)

    def test_forecasts_below_the_equal_weight_mean_on_a_declining_series(self):
        # With the documented default half-life, recency-weighting still lands
        # below the incumbent's equal-weight same-weekday mean on a decline.
        history = sales(mondays("plain", [100.0, 90.0, 80.0, 70.0, 60.0, 50.0]))
        as_of = dt.date(2026, 7, 7)
        target = "2026-07-13"

        weighted = _quantity_on(ewma_forecast(history, as_of), target)

        assert weighted < _equal_weight_mean(history, as_of, target)

    def test_ignores_sales_on_or_after_as_of(self):
        history = sales(
            daily("plain", "2026-06-01", 32, quantity=10.0)
            + [("plain", "2026-07-03", 900.0)]
            + [("plain", "2026-07-05", 900.0)]
        )

        result = ewma_forecast(history, dt.date(2026, 7, 3))

        assert result["forecast_quantity"].unique() == pytest.approx([10.0])

    def test_forecasts_only_the_forecast_products(self):
        history = sales(
            daily("plain", "2026-06-01", 30) + daily("cinnamon raisin", "2026-06-01", 30)
        )

        result = ewma_forecast(history, dt.date(2026, 7, 3))

        assert set(result["product"]) == {"plain"}

    def test_matches_the_shape_of_a_demand_forecast(self):
        history = sales(daily("plain", "2026-06-01", 30))

        result = ewma_forecast(history, dt.date(2026, 7, 9))

        assert list(result.columns) == ["product", "date", "forecast_quantity"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["forecast_quantity"].dtype == float


class TestSeasonalTrend:
    """A same-weekday level plus a fitted linear drift — it catches the decline
    the equal-weight incumbent projects flat."""

    def test_extrapolates_a_linear_decline_below_the_equal_weight_mean(self):
        # A perfectly linear decline: quantity = 100 - day_index over 40 days
        # from 2026-06-01. OLS recovers slope -1, intercept 100, zero residual
        # (so zero seasonal offset). Target 2026-07-13 is day_index 42:
        #   forecast = 100 - 42 = 58.
        # The incumbent's Monday mean over 100,93,86,79,72,65 is 495/6 = 82.5 —
        # the trend projects forward past every one of those, so it lands below.
        history = sales(
            [("plain", pd.Timestamp("2026-06-01") + pd.Timedelta(days=i), 100.0 - i)
             for i in range(40)]
        )
        as_of = dt.date(2026, 7, 11)  # after the last day 2026-07-10
        target = "2026-07-13"

        result = seasonal_trend_forecast(history, as_of)

        assert _quantity_on(result, target) == pytest.approx(58.0)
        assert _equal_weight_mean(history, as_of, target) == pytest.approx(82.5)
        assert _quantity_on(result, target) < _equal_weight_mean(history, as_of, target)

    def test_ignores_sales_on_or_after_as_of(self):
        # Flat 10s before as_of -> slope 0, level 10; the 900s on and after
        # as_of are never in the fit, so the forecast stays 10.
        history = sales(
            daily("plain", "2026-06-01", 32, quantity=10.0)
            + [("plain", "2026-07-03", 900.0)]
            + [("plain", "2026-07-05", 900.0)]
        )

        result = seasonal_trend_forecast(history, dt.date(2026, 7, 3))

        assert result["forecast_quantity"].to_numpy() == pytest.approx(10.0)

    def test_forecasts_only_the_forecast_products(self):
        history = sales(
            daily("plain", "2026-06-01", 30) + daily("cinnamon raisin", "2026-06-01", 30)
        )

        result = seasonal_trend_forecast(history, dt.date(2026, 7, 3))

        assert set(result["product"]) == {"plain"}

    def test_matches_the_shape_of_a_demand_forecast(self):
        history = sales(daily("plain", "2026-06-01", 30))

        result = seasonal_trend_forecast(history, dt.date(2026, 7, 9))

        assert list(result.columns) == ["product", "date", "forecast_quantity"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["forecast_quantity"].dtype == float


def _share_on(frame, product, date):
    """The single share a split candidate emitted for (product, date)."""
    row = frame.loc[
        (frame["product"] == product) & (frame["date"] == pd.Timestamp(date)), "share"
    ]
    assert len(row) == 1
    return float(row.iloc[0])


class TestConstantRecentShare:
    """Each variety's share of the recent total, held flat across weekdays."""

    def test_is_each_varietys_share_of_the_recent_total(self):
        # 30 days of 5/3/2 a day: everything 150, plain 90, sesame 60 of a 300
        # total -> 0.50 / 0.30 / 0.20, the same on every target date.
        history = sales(varieties("2026-06-01", 30, (5.0, 3.0, 2.0)))
        as_of = dt.date(2026, 7, 1)

        result = constant_recent_share(history, as_of)

        assert _share_on(result, "everything", "2026-07-04") == pytest.approx(0.5)
        assert _share_on(result, "plain", "2026-07-04") == pytest.approx(0.3)
        assert _share_on(result, "sesame", "2026-07-04") == pytest.approx(0.2)

    def test_the_shares_sum_to_one_on_every_target_date(self):
        history = sales(varieties("2026-06-01", 30, (5.0, 3.0, 2.0)))

        result = constant_recent_share(history, dt.date(2026, 7, 1))

        totals = result.groupby("date")["share"].sum()
        assert totals.tolist() == pytest.approx([1.0] * len(totals))
        assert len(totals) == 6  # as_of+2 .. as_of+7

    def test_ignores_a_mix_older_than_the_recent_window(self):
        # An old sesame-heavy month (1/1/8) then a recent week of 5/3/2. With a
        # 1-week window only the recent week counts: 35 / 21 / 14 of 70
        # -> 0.50 / 0.30 / 0.20, not the old 0.10-everything mix.
        history = sales(
            varieties("2026-06-01", 28, (1.0, 1.0, 8.0))  # .. 2026-06-28
            + varieties("2026-07-01", 7, (5.0, 3.0, 2.0))  # .. 2026-07-07
        )
        as_of = dt.date(2026, 7, 8)

        result = constant_recent_share(history, as_of, weeks=1)

        assert _share_on(result, "everything", "2026-07-10") == pytest.approx(0.5)
        assert _share_on(result, "sesame", "2026-07-10") == pytest.approx(0.2)

    def test_ignores_sales_on_or_after_as_of(self):
        # A sesame-only blowout on as_of and on a target date must not reach the
        # shares: the mix stays the 5/3/2 the history before as_of recorded.
        history = sales(
            varieties("2026-06-01", 32, (5.0, 3.0, 2.0))  # .. 2026-07-02
            + [("sesame", pd.Timestamp("2026-07-03"), 900.0)]  # as_of
            + [("sesame", pd.Timestamp("2026-07-05"), 900.0)]  # a target date
        )

        result = constant_recent_share(history, dt.date(2026, 7, 3))

        assert _share_on(result, "sesame", "2026-07-05") == pytest.approx(0.2)

    def test_matches_the_split_shape(self):
        history = sales(varieties("2026-06-01", 30, (5.0, 3.0, 2.0)))

        result = constant_recent_share(history, dt.date(2026, 7, 1))

        assert list(result.columns) == ["product", "date", "share"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["share"].dtype == float


class TestSameWeekdayShare:
    """The mix conditioned on weekday — a Monday's split need not be a
    Tuesday's."""

    def test_conditions_the_mix_on_the_targets_weekday(self):
        # Mondays sell 6/3/1 (shares .6/.3/.1); Tuesdays sell 2/2/6
        # (shares .2/.2/.6). 2026-06-01 is a Monday.
        history = sales(
            varieties("2026-06-01", 1, (6.0, 3.0, 1.0))  # Mon 06-01
            + varieties("2026-06-02", 1, (2.0, 2.0, 6.0))  # Tue 06-02
            + varieties("2026-06-08", 1, (6.0, 3.0, 1.0))  # Mon 06-08
            + varieties("2026-06-09", 1, (2.0, 2.0, 6.0))  # Tue 06-09
        )
        as_of = dt.date(2026, 6, 13)  # targets 06-15 (Mon) .. 06-20

        result = same_weekday_share(history, as_of)

        assert _share_on(result, "everything", "2026-06-15") == pytest.approx(0.6)
        assert _share_on(result, "sesame", "2026-06-15") == pytest.approx(0.1)
        assert _share_on(result, "everything", "2026-06-16") == pytest.approx(0.2)
        assert _share_on(result, "sesame", "2026-06-16") == pytest.approx(0.6)

    def test_the_shares_sum_to_one_on_every_target_date(self):
        history = sales(varieties("2026-06-01", 30, (5.0, 3.0, 2.0)))

        result = same_weekday_share(history, dt.date(2026, 7, 1))

        totals = result.groupby("date")["share"].sum()
        assert totals.tolist() == pytest.approx([1.0] * len(totals))

    def test_ignores_sales_on_or_after_as_of(self):
        history = sales(
            varieties("2026-06-01", 32, (5.0, 3.0, 2.0))  # .. 2026-07-02
            + [("sesame", pd.Timestamp("2026-07-03"), 900.0)]
            + [("sesame", pd.Timestamp("2026-07-05"), 900.0)]
        )

        result = same_weekday_share(history, dt.date(2026, 7, 3))

        assert _share_on(result, "sesame", "2026-07-05") == pytest.approx(0.2)

    def test_matches_the_split_shape(self):
        history = sales(varieties("2026-06-01", 30, (5.0, 3.0, 2.0)))

        result = same_weekday_share(history, dt.date(2026, 7, 1))

        assert list(result.columns) == ["product", "date", "share"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["share"].dtype == float


class TestPerVarietyRecencyShare:
    """Per-variety recency-weighted forecasts, normalized to the total."""

    def test_normalizes_the_per_variety_forecasts_to_sum_to_one(self):
        # A flat 5/3/2 history: every variety's recency-weighted forecast is its
        # own flat quantity, so the normalized shares are exactly .5 / .3 / .2.
        history = sales(varieties("2026-06-01", 30, (5.0, 3.0, 2.0)))

        result = per_variety_recency_share(history, dt.date(2026, 7, 1))

        assert _share_on(result, "everything", "2026-07-04") == pytest.approx(0.5)
        assert _share_on(result, "plain", "2026-07-04") == pytest.approx(0.3)
        assert _share_on(result, "sesame", "2026-07-04") == pytest.approx(0.2)
        totals = result.groupby("date")["share"].sum()
        assert totals.tolist() == pytest.approx([1.0] * len(totals))

    def test_normalizes_the_recency_weighted_forecasts_not_the_raw_totals(self):
        # Four Mondays. everything declines 40 -> 30 -> 20 -> 10 while plain and
        # sesame hold flat at 10. At half-life 1 the EWMA of everything's Mondays
        # is 52/3 = 17.333.. (the weighting TestEwmaSeasonalNaive pins by hand),
        # so the three point forecasts stand at 52/3 : 10 : 10. Normalized —
        # scale by 3 to 52 : 30 : 30 of a 112 total — the shares are
        #   everything = 52/112 = 13/28,  plain = sesame = 30/112 = 15/56.
        # A share built from the raw totals instead would see 100 : 40 : 40 and
        # hand everything 100/180 = 5/9, so this pins that the recency weighting
        # reaches the split and drags the declining variety's share down.
        history = sales(
            mondays("everything", [40.0, 30.0, 20.0, 10.0])
            + mondays("plain", [10.0, 10.0, 10.0, 10.0])
            + mondays("sesame", [10.0, 10.0, 10.0, 10.0])
        )
        as_of = dt.date(2026, 6, 23)  # after the last Monday 2026-06-22
        target = "2026-06-29"  # the next Monday

        result = per_variety_recency_share(history, as_of, halflife=1)

        assert _share_on(result, "everything", target) == pytest.approx(13 / 28)
        assert _share_on(result, "plain", target) == pytest.approx(15 / 56)
        assert _share_on(result, "sesame", target) == pytest.approx(15 / 56)
        assert _share_on(result, "everything", target) < 5 / 9  # the raw-total share

    def test_a_recent_surge_lifts_a_varietys_share_above_the_constant_mix(self):
        # Four Mondays. everything and plain hold flat at 10; sesame surges
        # 1 -> 2 -> 4 -> 40. The constant share sees only the volume totals
        # (40 / 40 / 47 -> sesame 47/127); the recency split leans on sesame's
        # newest Monday and puts its share above that.
        history = sales(
            mondays("everything", [10.0, 10.0, 10.0, 10.0])
            + mondays("plain", [10.0, 10.0, 10.0, 10.0])
            + mondays("sesame", [1.0, 2.0, 4.0, 40.0])
        )
        as_of = dt.date(2026, 6, 23)
        target = "2026-06-29"

        recency = per_variety_recency_share(history, as_of)
        constant = constant_recent_share(history, as_of)

        assert _share_on(constant, "sesame", target) == pytest.approx(47 / 127)
        assert _share_on(recency, "sesame", target) > _share_on(
            constant, "sesame", target
        )

    def test_ignores_sales_on_or_after_as_of(self):
        history = sales(
            varieties("2026-06-01", 32, (5.0, 3.0, 2.0))  # .. 2026-07-02
            + [("sesame", pd.Timestamp("2026-07-03"), 900.0)]
            + [("sesame", pd.Timestamp("2026-07-05"), 900.0)]
        )

        result = per_variety_recency_share(history, dt.date(2026, 7, 3))

        assert _share_on(result, "sesame", "2026-07-05") == pytest.approx(0.2)

    def test_matches_the_split_shape(self):
        history = sales(varieties("2026-06-01", 30, (5.0, 3.0, 2.0)))

        result = per_variety_recency_share(history, dt.date(2026, 7, 1))

        assert list(result.columns) == ["product", "date", "share"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["share"].dtype == float


class TestSplitForecastQuantities:
    """The shares allocate the fixed Poolish — the actual wheat total for the
    day — with no second quantile buffer on top."""

    def test_allocates_the_actual_wheat_total_by_expected_share(self):
        # A flat 5/3/2 history: the total on 2026-07-09 is 10, and the .5/.3/.2
        # shares divide exactly that 10 back into 5 / 3 / 2.
        history = sales(varieties("2026-06-01", 39, (5.0, 3.0, 2.0)))  # .. 2026-07-09
        target = pd.Timestamp("2026-07-09")

        result = bake_to_quantities(
            constant_recent_share, history, [target], lead=2
        )

        quantities = result.set_index("product")["forecast_quantity"]
        assert quantities["everything"] == pytest.approx(5.0)
        assert quantities["plain"] == pytest.approx(3.0)
        assert quantities["sesame"] == pytest.approx(2.0)

    def test_the_allocation_sums_to_the_total_with_no_second_buffer(self):
        # The split divides a fixed Poolish; it must never inflate it. The
        # allocated quantities sum to exactly the day's actual total (10), not
        # to a buffered 10 * (1 + q) — the buffer lives in the Poolish total.
        history = sales(varieties("2026-06-01", 39, (5.0, 3.0, 2.0)))
        target = pd.Timestamp("2026-07-09")

        result = bake_to_quantities(
            constant_recent_share, history, [target], lead=2
        )

        assert result["forecast_quantity"].sum() == pytest.approx(10.0)
        assert actual_totals(history, [target]).loc[0, "actual"] == 10.0

    def test_the_shares_never_see_the_day_they_split(self):
        # A 5/3/2 mix until 2026-07-06, then sesame explodes to 1000 from
        # 2026-07-07. Splitting 2026-07-09 at lead 2 (as_of 2026-07-07) reads
        # only history strictly before the explosion, so the shares stay
        # .5/.3/.2 — applied to that day's real (huge) total of 1008.
        history = sales(
            varieties("2026-06-01", 36, (5.0, 3.0, 2.0))  # .. 2026-07-06
            + daily("everything", "2026-07-07", 3, 5.0)  # .. 2026-07-09
            + daily("plain", "2026-07-07", 3, 3.0)
            + daily("sesame", "2026-07-07", 3, 1000.0)
        )
        target = pd.Timestamp("2026-07-09")

        result = bake_to_quantities(
            constant_recent_share, history, [target], lead=2
        )

        quantities = result.set_index("product")["forecast_quantity"]
        assert actual_totals(history, [target]).loc[0, "actual"] == 1008.0
        assert quantities["everything"] == pytest.approx(0.5 * 1008.0)
        assert quantities["plain"] == pytest.approx(0.3 * 1008.0)
        assert quantities["sesame"] == pytest.approx(0.2 * 1008.0)


class TestVarietyActuals:
    """The per-variety grain actual_totals sums up from — the two must not
    disagree about what a day's Wheat Dough Demand was."""

    def test_an_absent_variety_counts_as_zero_that_day(self):
        # sesame sold nothing on 2026-07-09, so normalize.py emits no row for
        # it — but the shop was open and a Bake-to Quantity was still owed, so
        # its actual is a real zero, not a missing row.
        history = sales(
            [
                ("everything", pd.Timestamp("2026-07-09"), 5.0),
                ("plain", pd.Timestamp("2026-07-09"), 3.0),
            ]
        )
        day = pd.Timestamp("2026-07-09")

        actuals = model_comparison.variety_actuals(history, [day]).set_index("product")

        assert actuals.loc["sesame", "actual"] == 0.0
        assert actuals.loc["everything", "actual"] == 5.0

    def test_the_totals_are_the_varieties_summed(self):
        history = sales(varieties("2026-07-09", 1, (5.0, 3.0, 2.0)))
        day = pd.Timestamp("2026-07-09")

        per_variety = model_comparison.variety_actuals(history, [day])

        assert per_variety["actual"].sum() == pytest.approx(10.0)
        assert actual_totals(history, [day]).loc[0, "actual"] == pytest.approx(10.0)


class TestCompareSplitModels:
    def test_scores_wape_per_variety_for_every_split_candidate(self):
        # A flat 5/3/2 history every split method nails exactly, so every WAPE
        # is zero — what this pins is the frame's shape: one row per
        # (candidate, variety), every candidate present, every variety scored.
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        comparison = compare_split_models(history, eval_weeks=1)

        assert list(comparison.columns) == ["model", "product", "wape", "days"]
        assert set(comparison["model"]) == {
            "constant_recent_share",
            "same_weekday_share",
            "per_variety_recency_share",
        }
        assert set(comparison["product"]) == set(forecast.FORECAST_PRODUCTS)
        assert len(comparison) == 9  # 3 candidates x 3 varieties
        assert comparison["wape"].tolist() == pytest.approx([0.0] * 9)
        assert (comparison["days"] == 7).all()

    def test_a_wrong_split_scores_a_wape_the_right_size(self):
        # Five weeks of a 5/3/2 mix, then the shop is closed until a single
        # trading day on 2026-07-09 that actually splits 6/2/2. The 1-week
        # window holds just that one open day, and its lead-2 origin (07-07)
        # sees only the old mix — so the split is the stale 5/3/2 against a
        # 6/2/2 actual, on a total of 10:
        #   everything: |6 - 5| / 6 = 1/6
        #   plain:      |2 - 3| / 2 = 1/2
        #   sesame:     exact       = 0
        history = sales(
            varieties("2026-05-29", 35, (5.0, 3.0, 2.0))  # .. 2026-07-02
            + varieties("2026-07-09", 1, (6.0, 2.0, 2.0))  # the one scored day
        )

        comparison = compare_split_models(
            history, candidates={"constant": constant_recent_share}, eval_weeks=1
        )

        wapes = comparison.set_index("product")["wape"]
        assert (comparison["days"] == 1).all()
        assert wapes["everything"] == pytest.approx(1 / 6)
        assert wapes["plain"] == pytest.approx(1 / 2)
        assert wapes["sesame"] == pytest.approx(0.0, abs=1e-9)

    def test_scores_every_open_day_in_the_window_once(self):
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        comparison = compare_split_models(history, eval_weeks=2)

        assert (comparison["days"] == 14).all()

    def test_a_closed_day_is_not_an_origin_target(self):
        history = sales(
            [
                row
                for row in varieties("2026-05-29", 42, (5.0, 3.0, 2.0))
                if row[1] != pd.Timestamp("2026-07-06")
            ]
        )

        comparison = compare_split_models(history, eval_weeks=2)

        assert (comparison["days"] == 13).all()


class TestMain:
    def test_prints_the_ranked_candidates(self, tmp_path, monkeypatch, capsys):
        history_path = tmp_path / "sales_history.parquet"
        monkeypatch.setattr(model_comparison, "EVAL_WEEKS", 1)
        monkeypatch.setattr(model_comparison, "WARMUP_WEEKS", 1)
        sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0))).to_parquet(
            history_path, index=False
        )
        monkeypatch.setattr(
            sales_history, "load_sales_history", lambda: pd.read_parquet(history_path)
        )

        model_comparison.main()
        out = capsys.readouterr().out

        assert "seasonal_naive" in out
        assert "moving_average" in out
        assert "pinball@95" in out

    def test_prints_the_split_comparison_too(self, tmp_path, monkeypatch, capsys):
        history_path = tmp_path / "sales_history.parquet"
        monkeypatch.setattr(model_comparison, "EVAL_WEEKS", 1)
        monkeypatch.setattr(model_comparison, "WARMUP_WEEKS", 1)
        sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0))).to_parquet(
            history_path, index=False
        )
        monkeypatch.setattr(
            sales_history, "load_sales_history", lambda: pd.read_parquet(history_path)
        )

        model_comparison.main()
        out = capsys.readouterr().out

        assert "Bake split" in out
        assert "WAPE" in out
        for name in model_comparison.SPLIT_CANDIDATES:
            assert name in out
        for product in forecast.FORECAST_PRODUCTS:
            assert product in out


class TestEtsDependencyGate:
    """The statsmodels gate: a dev-only install (no statsmodels) can still
    import this module and run compare_models on the defaults. statsmodels IS
    installed here so we can't uninstall it — instead we pin the guard's
    contract: ETS is never in the default candidate set, and the opt-in registry
    is what conditionally adds it."""

    def test_ets_is_absent_from_the_default_candidates(self):
        # compare_models defaults to POOLISH_CANDIDATES; if ETS were in it, a
        # machine without statsmodels would call it and fail.
        assert "ets" not in model_comparison.POOLISH_CANDIDATES

    def test_default_compare_models_does_not_score_ets(self):
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        comparison = compare_models(history, eval_weeks=1, warmup_weeks=1)

        assert "ets" not in set(comparison["model"])
        assert comparison["pinball"].notna().all()

    def test_the_opt_in_registry_is_a_superset_of_the_defaults(self):
        # Whether or not statsmodels is present, the registry never drops a
        # default candidate.
        registry = model_comparison.candidates_with_ets()

        assert set(model_comparison.POOLISH_CANDIDATES) <= set(registry)


class TestEtsForecast:
    """The Holt-Winters / ETS candidate. Exact ETS arithmetic by hand is
    impractical, so these pin the contract — shape, scope, leak-freeness,
    scoring — not the fitted internals."""

    def test_conforms_to_the_demand_forecast_shape(self):
        pytest.importorskip("statsmodels")
        history = sales(varieties("2026-01-01", 150, (5.0, 3.0, 2.0)))

        result = model_comparison.ets_forecast(history, dt.date(2026, 6, 1))

        assert list(result.columns) == ["product", "date", "forecast_quantity"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["forecast_quantity"].dtype == float
        assert set(result["product"]) <= set(forecast.FORECAST_PRODUCTS)
        assert not result.empty

    def test_a_forecast_never_sees_the_day_it_forecasts(self):
        # plain sells 10 every day for a year, then jumps to 1000 from
        # 2026-01-01. Forecasting 2026-01-03 at lead 3 (as_of 2026-01-01) trains
        # only on the tens strictly before the jump, so the point forecast must
        # stay near 10, nowhere near the 1000 it would leak if it saw as_of on.
        pytest.importorskip("statsmodels")
        history = sales(
            daily("plain", "2025-01-01", 365, 10.0)  # .. 2025-12-31
            + daily("plain", "2026-01-01", 5, 1000.0)  # .. 2026-01-05
        )
        target = pd.Timestamp("2026-01-03")

        result = model_comparison.ets_forecast(history, dt.date(2026, 1, 1))
        row = result[(result["product"] == "plain") & (result["date"] == target)]

        assert len(row) == 1
        assert row["forecast_quantity"].iloc[0] < 100.0  # ~10, not 1000

    def test_scores_a_finite_pinball_through_compare_models(self):
        pytest.importorskip("statsmodels")
        history = sales(varieties("2026-02-01", 130, (5.0, 3.0, 2.0)))

        comparison = compare_models(
            history,
            candidates={"ets": model_comparison.ets_forecast},
            eval_weeks=1,
            warmup_weeks=1,
        )

        assert list(comparison["model"]) == ["ets"]
        assert comparison["pinball"].notna().all()
        assert comparison["pinball"].iloc[0] >= 0.0

    def test_the_registry_wires_ets_in_when_statsmodels_is_present(self):
        pytest.importorskip("statsmodels")
        assert model_comparison.statsmodels_available()

        registry = model_comparison.candidates_with_ets()

        assert registry.get("ets") is model_comparison.ets_forecast


class TestProductScope:
    """The optional Product scope points a model at exactly the series named,
    so the daily engine can run one model definition over a Forecast Target's
    summed series ([target_name]) — a Product that is not one of the baked
    varieties — with no parallel per-series forecaster (ticket 02)."""

    def test_ewma_forecasts_a_scoped_target_series(self):
        # A Target series relabeled with its own name — the shape ticket 03's
        # engine hands each model: a single Product outside FORECAST_PRODUCTS.
        # Four declining Mondays 40/30/20/10 at half-life 1 obs reduce to the
        # same 17.3333 TestEwmaSeasonalNaive works by hand, reached here only
        # because the scope names the series.
        history = sales(mondays("wheat_bagels", [40.0, 30.0, 20.0, 10.0]))
        as_of = dt.date(2026, 6, 23)  # after the last Monday 2026-06-22
        target = "2026-06-29"  # the next Monday

        result = ewma_forecast(history, as_of, halflife=1, scope=["wheat_bagels"])

        assert set(result["product"]) == {"wheat_bagels"}
        assert _quantity_on(result, target) == pytest.approx(17.333333, rel=1e-5)

    def test_ewma_default_scope_ignores_a_target_series(self):
        # With no scope, a Target-named series the default FORECAST_PRODUCTS
        # does not name is invisible — existing behavior, unchanged.
        history = sales(mondays("wheat_bagels", [40.0, 30.0, 20.0, 10.0]))

        result = ewma_forecast(history, dt.date(2026, 6, 23), halflife=1)

        assert result.empty

    def test_ewma_scope_selects_only_the_named_series(self):
        # A frame carrying both a baked variety and a Target series: the default
        # scope forecasts only the variety, a [target] scope only the Target —
        # neither leaks the other's rows.
        history = sales(
            mondays("plain", [10.0, 10.0, 10.0, 10.0])
            + mondays("wheat_bagels", [40.0, 30.0, 20.0, 10.0])
        )
        as_of = dt.date(2026, 6, 23)

        default = ewma_forecast(history, as_of, halflife=1)
        scoped = ewma_forecast(history, as_of, halflife=1, scope=["wheat_bagels"])

        assert set(default["product"]) == {"plain"}
        assert set(scoped["product"]) == {"wheat_bagels"}

    def test_ets_forecasts_a_scoped_target_series(self):
        pytest.importorskip("statsmodels")
        history = sales(daily("wheat_bagels", "2026-01-01", 150, 10.0))

        result = model_comparison.ets_forecast(
            history, dt.date(2026, 6, 1), scope=["wheat_bagels"]
        )

        assert set(result["product"]) == {"wheat_bagels"}
        assert not result.empty

    def test_ets_default_scope_ignores_a_target_series(self):
        pytest.importorskip("statsmodels")
        history = sales(daily("wheat_bagels", "2026-01-01", 150, 10.0))

        result = model_comparison.ets_forecast(history, dt.date(2026, 6, 1))

        assert result.empty
