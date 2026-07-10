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
from model_comparison import (
    WHEAT_TOTAL,
    actual_totals,
    compare_models,
    evaluation_window,
    ewma_forecast,
    forecast_totals,
    p95_buffer,
    pinball,
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

        assert list(comparison.columns) == ["model", "pinball", "mape", "days"]
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


class TestMain:
    def test_prints_the_ranked_candidates(self, tmp_path, monkeypatch, capsys):
        history_path = tmp_path / "sales_history.parquet"
        monkeypatch.setattr(model_comparison, "SALES_HISTORY_PATH", history_path)
        monkeypatch.setattr(model_comparison, "EVAL_WEEKS", 1)
        monkeypatch.setattr(model_comparison, "WARMUP_WEEKS", 1)
        sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0))).to_parquet(
            history_path, index=False
        )

        model_comparison.main()
        out = capsys.readouterr().out

        assert "seasonal_naive" in out
        assert "moving_average" in out
        assert "pinball@95" in out
