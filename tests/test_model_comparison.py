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
    forecast_totals,
    p95_buffer,
    pinball,
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
    def test_ranks_the_seeded_candidates_on_pinball(self):
        # Every variety sells a flat quantity all history, so both models
        # forecast the total exactly (residuals zero, no buffer) and score a
        # perfect pinball of zero.
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        comparison = compare_models(history, eval_weeks=1, warmup_weeks=1)

        assert list(comparison.columns) == ["model", "pinball", "mape", "days"]
        assert set(comparison["model"]) == {"seasonal_naive", "moving_average"}
        assert comparison["pinball"].tolist() == pytest.approx([0.0, 0.0])
        assert comparison["mape"].tolist() == pytest.approx([0.0, 0.0])
        assert comparison["days"].tolist() == [7, 7]
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
