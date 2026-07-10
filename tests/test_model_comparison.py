"""Locks the pure scoring/buffering primitives the bake-forecast model
comparison reduces on: pinball loss at a Service Level, WAPE, and the P95
relative-residual buffer transform.

As in test_backtest.py, numbers are worked by hand rather than recomputed the
way the code does, and only the returned values are asserted on.
"""
import pandas as pd
import pytest

from model_comparison import p95_buffer, pinball, wape


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
