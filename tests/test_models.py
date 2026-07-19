"""Locks the forecasting model definitions and the pure scoring/buffering
primitives in models.py: EWMA and Holt-Winters / ETS, and pinball loss at a
Service Level, WAPE, coverage, and the P95 relative-residual buffer.

As in test_backtest.py, numbers are worked by hand rather than recomputed the
way the code does, and only the returned values are asserted on. Every model
call names its Product `scope` explicitly — there is no default set of Products
a model forecasts.
"""
import datetime as dt

import pandas as pd
import pytest

import forecast
import models
from models import (
    coverage,
    ets_forecast,
    ewma_forecast,
    p95_buffer,
    pinball,
    pinball_losses,
    wape,
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


def mondays(product, quantities, start="2026-06-01"):
    """One Sale per week on the same weekday (2026-06-01 is a Monday), oldest
    first — the same-weekday series each model below reduces over."""
    first = pd.Timestamp(start)
    return [(product, first + pd.Timedelta(weeks=n), q) for n, q in enumerate(quantities)]


def _quantity_on(frame, date):
    """The single forecast_quantity a model emitted for `date`."""
    row = frame.loc[frame["date"] == pd.Timestamp(date), "forecast_quantity"]
    assert len(row) == 1
    return float(row.iloc[0])


def _equal_weight_mean(history, as_of, date):
    """The incumbent forecast.forecast_demand's same-weekday mean for `date` —
    the high-biased baseline the recency-aware models must beat downward."""
    return _quantity_on(forecast.forecast_demand(history, as_of), date)


class TestPinballLosses:
    """The un-averaged per-day loss the analysis layer pairs and tests — kept
    public so the day-to-day spread, not just the mean, is available."""

    def test_is_the_per_day_quantile_loss_un_averaged(self):
        # Day 1 under-forecast: actual (12) >= predicted (10) -> 0.95 * 2 = 1.9
        # Day 2 over-forecast:  actual (10) <  predicted (12) -> 0.05 * 2 = 0.1
        actual = pd.Series([12.0, 10.0])
        predicted = pd.Series([10.0, 12.0])

        losses = pinball_losses(actual, predicted, level=0.95)

        assert losses.tolist() == pytest.approx([1.9, 0.1])
        # The mean of these is exactly what pinball() reports.
        assert pinball(actual, predicted, level=0.95) == pytest.approx(1.0)


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

        result = ewma_forecast(history, as_of, scope=["plain"], halflife=1)

        assert _quantity_on(result, target) == pytest.approx(17.333333, rel=1e-5)
        assert _equal_weight_mean(history, as_of, target) == pytest.approx(25.0)

    def test_forecasts_below_the_equal_weight_mean_on_a_declining_series(self):
        # With the documented default half-life, recency-weighting still lands
        # below the incumbent's equal-weight same-weekday mean on a decline.
        history = sales(mondays("plain", [100.0, 90.0, 80.0, 70.0, 60.0, 50.0]))
        as_of = dt.date(2026, 7, 7)
        target = "2026-07-13"

        weighted = _quantity_on(ewma_forecast(history, as_of, scope=["plain"]), target)

        assert weighted < _equal_weight_mean(history, as_of, target)

    def test_ignores_sales_on_or_after_as_of(self):
        history = sales(
            daily("plain", "2026-06-01", 32, quantity=10.0)
            + [("plain", "2026-07-03", 900.0)]
            + [("plain", "2026-07-05", 900.0)]
        )

        result = ewma_forecast(history, dt.date(2026, 7, 3), scope=["plain"])

        assert result["forecast_quantity"].unique() == pytest.approx([10.0])

    def test_scope_selects_only_the_named_products(self):
        # A frame with a baked variety and an unrelated Product: the scope, not
        # any built-in default, decides what is forecast.
        history = sales(
            daily("plain", "2026-06-01", 30) + daily("cinnamon raisin", "2026-06-01", 30)
        )

        result = ewma_forecast(
            history, dt.date(2026, 7, 3), scope=forecast.FORECAST_PRODUCTS
        )

        assert set(result["product"]) == {"plain"}

    def test_matches_the_shape_of_a_demand_forecast(self):
        history = sales(daily("plain", "2026-06-01", 30))

        result = ewma_forecast(history, dt.date(2026, 7, 9), scope=["plain"])

        assert list(result.columns) == ["product", "date", "forecast_quantity"]
        assert result["date"].dtype == "datetime64[ns]"
        assert result["forecast_quantity"].dtype == float


class TestProductScope:
    """The Product scope is required and points a model at exactly the series
    named, so the daily engine runs one model definition over a Forecast
    Target's summed series ([target_name]) — a Product that is not one of the
    baked varieties — with no parallel per-series forecaster and no default set
    of Products baked into the model."""

    def test_ewma_forecasts_a_scoped_target_series(self):
        # A Target series relabeled with its own name — the shape the engine
        # hands each model: a single Product outside FORECAST_PRODUCTS. Four
        # declining Mondays 40/30/20/10 at half-life 1 obs reduce to the same
        # 17.3333 the bare model works by hand, reached here only because the
        # scope names the series.
        history = sales(mondays("wheat_bagels", [40.0, 30.0, 20.0, 10.0]))
        as_of = dt.date(2026, 6, 23)  # after the last Monday 2026-06-22
        target = "2026-06-29"  # the next Monday

        result = ewma_forecast(history, as_of, scope=["wheat_bagels"], halflife=1)

        assert set(result["product"]) == {"wheat_bagels"}
        assert _quantity_on(result, target) == pytest.approx(17.333333, rel=1e-5)

    def test_ewma_scope_selects_only_the_named_series(self):
        # A frame carrying both a baked variety and a Target series: a [variety]
        # scope forecasts only the variety, a [target] scope only the Target —
        # neither leaks the other's rows.
        history = sales(
            mondays("plain", [10.0, 10.0, 10.0, 10.0])
            + mondays("wheat_bagels", [40.0, 30.0, 20.0, 10.0])
        )
        as_of = dt.date(2026, 6, 23)

        variety = ewma_forecast(history, as_of, scope=["plain"], halflife=1)
        target = ewma_forecast(history, as_of, scope=["wheat_bagels"], halflife=1)

        assert set(variety["product"]) == {"plain"}
        assert set(target["product"]) == {"wheat_bagels"}

    def test_ewma_requires_a_scope(self):
        # There is no default set of Products: a caller must say what to forecast.
        history = sales(mondays("plain", [10.0, 10.0]))

        with pytest.raises(TypeError):
            ewma_forecast(history, dt.date(2026, 6, 23))

    def test_ets_forecasts_a_scoped_target_series(self):
        pytest.importorskip("statsmodels")
        history = sales(daily("wheat_bagels", "2026-01-01", 150, 10.0))

        result = ets_forecast(history, dt.date(2026, 6, 1), scope=["wheat_bagels"])

        assert set(result["product"]) == {"wheat_bagels"}
        assert not result.empty

    def test_ets_requires_a_scope(self):
        history = sales(daily("wheat_bagels", "2026-01-01", 20, 10.0))

        with pytest.raises(TypeError):
            ets_forecast(history, dt.date(2026, 6, 1))


class TestHorizon:
    """The `(first, last)` lead range a model covers is a parameter, defaulting
    to the incumbent forecast.HORIZON_DAYS. The daily engine's horizon is
    configuration — `as_of+1 .. as_of+N` — so it must be able to ask for a range
    the module constant does not describe."""

    def test_defaults_to_the_incumbent_horizon(self):
        history = sales(daily("plain", "2026-06-01", 30))
        as_of = dt.date(2026, 7, 9)

        result = ewma_forecast(history, as_of, scope=["plain"])

        assert list(result["date"]) == forecast.target_dates(as_of)

    def test_ewma_covers_the_requested_lead_range(self):
        history = sales(daily("plain", "2026-06-01", 30))
        as_of = dt.date(2026, 7, 9)

        result = ewma_forecast(history, as_of, scope=["plain"], horizon=(1, 3))

        assert list(result["date"]) == [
            pd.Timestamp("2026-07-10"),
            pd.Timestamp("2026-07-11"),
            pd.Timestamp("2026-07-12"),
        ]

    def test_ets_covers_the_requested_lead_range(self):
        pytest.importorskip("statsmodels")
        history = sales(daily("plain", "2026-01-01", 150, 10.0))
        as_of = dt.date(2026, 6, 1)

        result = ets_forecast(history, as_of, scope=["plain"], horizon=(1, 3))

        assert list(result["date"]) == [
            pd.Timestamp("2026-06-02"),
            pd.Timestamp("2026-06-03"),
            pd.Timestamp("2026-06-04"),
        ]


class TestEtsForecast:
    """The Holt-Winters / ETS model. Exact ETS arithmetic by hand is
    impractical, so these pin the contract — shape, scope, leak-freeness — not
    the fitted internals."""

    def test_conforms_to_the_demand_forecast_shape(self):
        pytest.importorskip("statsmodels")
        history = sales(varieties("2026-01-01", 150, (5.0, 3.0, 2.0)))

        result = ets_forecast(
            history, dt.date(2026, 6, 1), scope=forecast.FORECAST_PRODUCTS
        )

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

        result = ets_forecast(history, dt.date(2026, 1, 1), scope=["plain"])
        row = result[(result["product"] == "plain") & (result["date"] == target)]

        assert len(row) == 1
        assert row["forecast_quantity"].iloc[0] < 100.0  # ~10, not 1000

    def test_a_short_history_falls_back_without_raising(self):
        # Fewer than two weekly cycles: ETS cannot fit a seasonal component, so
        # it backs off to a same-weekday mean rather than raising. plain sells a
        # flat 10, so the fallback forecast is 10.
        pytest.importorskip("statsmodels")
        history = sales(daily("plain", "2026-06-01", 10, quantity=10.0))

        result = ets_forecast(history, dt.date(2026, 6, 15), scope=["plain"])

        assert not result.empty
        assert result["forecast_quantity"].unique() == pytest.approx([10.0])
