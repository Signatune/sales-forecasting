"""Locks the daily forecast engine's primary seam: run_forecasts(config, sales,
as_of) -> the log rows for one morning.

As in test_models.py, a synthetic Sales frame and a synthetic config go in, only
the returned frame is asserted on, and the arithmetic is worked by hand rather
than recomputed the way the code does. Nothing here touches a database — the
engine is a pure function of its three arguments (ADR 0006), which is the whole
point of testing it at this seam.
"""
import datetime as dt

import pandas as pd
import pytest

import models
from forecast_engine import run_forecasts


def sales(records) -> pd.DataFrame:
    """A Sales history frame shaped like normalize.py's output."""
    df = pd.DataFrame(records, columns=["product", "date", "quantity"])
    df["date"] = pd.to_datetime(df["date"])
    df["quantity"] = df["quantity"].astype(float)
    return df


def daily(product: str, start: str, days: int, quantity: float = 10.0):
    first = pd.Timestamp(start)
    return [(product, first + pd.Timedelta(days=n), quantity) for n in range(days)]


def mondays(product, quantities, start="2026-06-01"):
    """One Sale per week on the same weekday (2026-06-01 is a Monday), oldest
    first — the same-weekday series EWMA reduces over."""
    first = pd.Timestamp(start)
    return [
        (product, first + pd.Timedelta(weeks=n), q) for n, q in enumerate(quantities)
    ]


def config(targets, horizon_days=7, model_config=None, version=1):
    """A configuration document shaped like the `forecast_configs.config` jsonb.
    EWMA only by default, so most tests run without the statsmodels extra."""
    return {
        "version": version,
        "horizon_days": horizon_days,
        "models": {"ewma": {"halflife_weeks": 1}} if model_config is None else model_config,
        "targets": targets,
    }


def _quantity_on(frame, model, target, target_date):
    """The single forecast_quantity logged for one (model, target, date)."""
    row = frame[
        (frame["model"] == model)
        & (frame["target"] == target)
        & (frame["target_date"] == dt.date.fromisoformat(target_date))
    ]["forecast_quantity"]
    assert len(row) == 1
    return float(row.iloc[0])


class TestTargetResolution:
    """A Forecast Target is a group of one or more Products summed into one
    series, keyed on the Target's name — a lone Product is the one-member case,
    not a separate code path (CONTEXT.md, ADR 0006)."""

    def test_a_group_target_is_summed_before_fitting(self):
        # plain and sesame together sell 40/30/20/10 over four Mondays
        # (30+10, 20+10, 10+10, 5+5), oldest first. Fitting the *summed* series
        # at half-life 1 observation -> alpha 0.5; adjust=True weights
        # newest->oldest 1, .5, .25, .125:
        #   (10*1 + 20*.5 + 30*.25 + 40*.125) / (1 + .5 + .25 + .125)
        #   = 32.5 / 1.875 = 17.3333
        # Summing the members' *forecasts* instead would be a different number,
        # so this pins top-down resolution, not a bottom-up rollup.
        history = sales(
            mondays("plain", [30.0, 20.0, 10.0, 5.0])
            + mondays("sesame", [10.0, 10.0, 10.0, 5.0])
        )
        as_of = dt.date(2026, 6, 23)  # after the last Monday, 2026-06-22

        result = run_forecasts(
            config({"wheat_bagels": ["plain", "sesame"]}), history, as_of
        )

        assert _quantity_on(
            result, "ewma", "wheat_bagels", "2026-06-29"
        ) == pytest.approx(17.333333, rel=1e-5)

    def test_rows_key_on_the_target_not_its_members(self):
        history = sales(
            mondays("plain", [30.0, 20.0, 10.0, 5.0])
            + mondays("sesame", [10.0, 10.0, 10.0, 5.0])
        )

        result = run_forecasts(
            config({"wheat_bagels": ["plain", "sesame"]}),
            history,
            dt.date(2026, 6, 23),
        )

        assert set(result["target"]) == {"wheat_bagels"}

    def test_a_one_member_target_reproduces_the_bare_model(self):
        # Sum-of-one is the Product itself, so a one-member Target must land on
        # exactly what the scoped model callable produces for that Product.
        history = sales(mondays("plain", [40.0, 30.0, 20.0, 10.0]))
        as_of = dt.date(2026, 6, 23)

        result = run_forecasts(config({"plain": ["plain"]}), history, as_of)
        bare = models.ewma_forecast(
            history, as_of, scope=["plain"], halflife=1, horizon=(1, 7)
        )

        assert _quantity_on(result, "ewma", "plain", "2026-06-29") == pytest.approx(
            float(bare.loc[bare["date"] == pd.Timestamp("2026-06-29"),
                           "forecast_quantity"].iloc[0])
        )

    def test_the_same_product_can_serve_several_targets(self):
        # Logging both the group total and a member lets the analysis layer
        # compare top-down against bottom-up later (PRD user story 9).
        history = sales(
            mondays("plain", [30.0, 20.0, 10.0, 5.0])
            + mondays("sesame", [10.0, 10.0, 10.0, 5.0])
        )

        result = run_forecasts(
            config({"wheat_bagels": ["plain", "sesame"], "plain": ["plain"]}),
            history,
            dt.date(2026, 6, 23),
        )

        assert set(result["target"]) == {"wheat_bagels", "plain"}

    def test_an_unknown_product_raises(self):
        history = sales(mondays("plain", [10.0, 10.0]))

        with pytest.raises(ValueError, match="cinnamon raisin"):
            run_forecasts(
                config({"wheat_bagels": ["plain", "cinnamon raisin"]}),
                history,
                dt.date(2026, 6, 23),
            )

    def test_a_target_with_no_members_raises(self):
        # Rather than logging nothing for it, which reads later exactly like a
        # model that failed to run.
        history = sales(mondays("plain", [10.0, 10.0]))

        with pytest.raises(ValueError, match="wheat_bagels"):
            run_forecasts(
                config({"wheat_bagels": []}), history, dt.date(2026, 6, 23)
            )


class TestHorizon:
    """The engine forecasts as_of+1 .. as_of+horizon_days for every Target.
    There is no stored lead and no min-lead cutoff — lead is derived at read
    time (ADR 0006)."""

    def test_spans_as_of_plus_one_through_horizon_days(self):
        history = sales(daily("plain", "2026-01-01", 150))
        as_of = dt.date(2026, 6, 1)

        result = run_forecasts(
            config({"plain": ["plain"]}, horizon_days=5), history, as_of
        )

        assert sorted(set(result["target_date"])) == [
            dt.date(2026, 6, 2),
            dt.date(2026, 6, 3),
            dt.date(2026, 6, 4),
            dt.date(2026, 6, 5),
            dt.date(2026, 6, 6),
        ]

    def test_a_target_date_with_no_evidence_yields_no_row(self):
        # The horizon is the span asked for, not a guaranteed row count. A
        # Target that has only ever sold on Mondays supports only the Monday in
        # the week ahead; the engine does not fabricate the other six, because a
        # made-up zero would be scored later as a confident forecast of nothing
        # rather than as the silence it is (as forecast.forecast_demand also
        # declines to do). The shop's real Targets sell every open day, so this
        # is the sparse-Target edge, not the normal case.
        history = sales(mondays("plain", [10.0, 10.0, 10.0, 10.0]))

        result = run_forecasts(
            config({"plain": ["plain"]}, horizon_days=7), history, dt.date(2026, 6, 23)
        )

        assert list(result["target_date"]) == [dt.date(2026, 6, 29)]

    def test_a_longer_horizon_reaches_further_out(self):
        history = sales(daily("plain", "2026-01-01", 150))
        as_of = dt.date(2026, 6, 1)

        result = run_forecasts(
            config({"plain": ["plain"]}, horizon_days=14), history, as_of
        )

        assert min(result["target_date"]) == dt.date(2026, 6, 2)
        assert max(result["target_date"]) == dt.date(2026, 6, 15)


class TestModelCoverage:
    """Both configured models run on every Target — the winner is left to the
    data, never pre-committed per Target (PRD user story 13)."""

    def test_every_model_appears_for_every_target_and_target_date(self):
        pytest.importorskip("statsmodels")
        history = sales(
            daily("plain", "2026-01-01", 150, 10.0)
            + daily("sesame", "2026-01-01", 150, 4.0)
        )
        as_of = dt.date(2026, 6, 1)

        result = run_forecasts(
            config(
                {"wheat_bagels": ["plain", "sesame"], "plain": ["plain"]},
                horizon_days=3,
                model_config={"ewma": {"halflife_weeks": 3}, "holt_winters": {}},
            ),
            history,
            as_of,
        )

        logged = set(
            zip(result["model"], result["target"], result["target_date"])
        )
        assert logged == {
            (model, target, as_of + dt.timedelta(days=lead))
            for model in ("ewma", "holt_winters")
            for target in ("wheat_bagels", "plain")
            for lead in (1, 2, 3)
        }

    def test_an_unconfigured_model_name_raises(self):
        history = sales(daily("plain", "2026-01-01", 60))

        with pytest.raises(ValueError, match="prophet"):
            run_forecasts(
                config({"plain": ["plain"]}, model_config={"prophet": {}}),
                history,
                dt.date(2026, 6, 1),
            )

    def test_a_hyperparameter_the_model_does_not_take_raises(self):
        # A misspelled key must not silently fall back to the code's default:
        # that would log a forecast under a config_version claiming a
        # hyperparameter which never reached the model, and provenance is the
        # whole point of the stamp (ADR 0006).
        history = sales(mondays("plain", [40.0, 30.0, 20.0, 10.0]))

        with pytest.raises(ValueError, match="halflife"):
            run_forecasts(
                config({"plain": ["plain"]}, model_config={"ewma": {"halflife": 5}}),
                history,
                dt.date(2026, 6, 23),
            )

    def test_holt_winters_takes_no_hyperparameters(self):
        # Its trend/seasonal/initialization choices are fixed so every logged
        # row was fit the same way; its config entry is `{}`.
        history = sales(daily("plain", "2026-01-01", 60))

        with pytest.raises(ValueError, match="seasonal"):
            run_forecasts(
                config(
                    {"plain": ["plain"]},
                    model_config={"holt_winters": {"seasonal": "mul"}},
                ),
                history,
                dt.date(2026, 6, 1),
            )

    def test_the_configured_hyperparameters_reach_the_model(self):
        # Same four declining Mondays under two half-lives. At 1 observation the
        # hand-worked answer is 17.3333 (see TestTargetResolution); a longer
        # half-life fades old Sales more slowly and so must land higher.
        history = sales(mondays("plain", [40.0, 30.0, 20.0, 10.0]))
        as_of = dt.date(2026, 6, 23)

        fast = run_forecasts(
            config({"plain": ["plain"]}, model_config={"ewma": {"halflife_weeks": 1}}),
            history,
            as_of,
        )
        slow = run_forecasts(
            config({"plain": ["plain"]}, model_config={"ewma": {"halflife_weeks": 10}}),
            history,
            as_of,
        )

        assert _quantity_on(fast, "ewma", "plain", "2026-06-29") == pytest.approx(
            17.333333, rel=1e-5
        )
        assert _quantity_on(slow, "ewma", "plain", "2026-06-29") > _quantity_on(
            fast, "ewma", "plain", "2026-06-29"
        )


class TestLeakFree:
    """Mirrors TestHistoryCutoff: a forecast may only see Sales strictly before
    as_of, so a replayed origin never flatters itself with its own target."""

    def test_no_forecast_uses_sales_on_or_after_as_of(self):
        # plain sells a flat 10 for a month, then spikes to 900 on as_of itself
        # and again after it. Every forecast must stay at 10.
        history = sales(
            daily("plain", "2026-06-01", 32, quantity=10.0)
            + [("plain", "2026-07-03", 900.0), ("plain", "2026-07-05", 900.0)]
        )

        result = run_forecasts(
            config({"plain": ["plain"]}), history, dt.date(2026, 7, 3)
        )

        assert result["forecast_quantity"].unique() == pytest.approx([10.0])


class TestStatelessness:
    """Holt-Winters re-fits from history every call and no state is carried
    between runs, so a logged row is reproducible from the Sales data alone
    (ADR 0006) — which is what the write-once log depends on."""

    def test_repeated_calls_return_identical_rows(self):
        pytest.importorskip("statsmodels")
        history = sales(daily("plain", "2026-01-01", 150, 10.0))
        settings = config(
            {"plain": ["plain"]},
            horizon_days=3,
            model_config={"ewma": {"halflife_weeks": 3}, "holt_winters": {}},
        )
        as_of = dt.date(2026, 6, 1)

        first = run_forecasts(settings, history, as_of)
        second = run_forecasts(settings, history, as_of)

        assert not first.empty
        assert "holt_winters" in set(first["model"])
        pd.testing.assert_frame_equal(first, second)


class TestLogRowShape:
    """The columns and dtypes the write path (ticket 04) inserts."""

    def test_has_the_log_columns_in_order(self):
        history = sales(daily("plain", "2026-01-01", 60))

        result = run_forecasts(
            config({"plain": ["plain"]}), history, dt.date(2026, 6, 1)
        )

        assert list(result.columns) == [
            "as_of",
            "config_version",
            "model",
            "target",
            "target_date",
            "forecast_quantity",
        ]

    def test_dates_are_dates_and_quantities_are_floats(self):
        history = sales(daily("plain", "2026-01-01", 60))
        as_of = dt.date(2026, 6, 1)

        result = run_forecasts(config({"plain": ["plain"]}), history, as_of)

        # Plain dt.date, not a Timestamp: the log's columns are `date`, and a
        # forecast for a day has no time of day to carry.
        assert all(type(value) is dt.date for value in result["as_of"])
        assert all(type(value) is dt.date for value in result["target_date"])
        assert set(result["as_of"]) == {as_of}
        assert result["forecast_quantity"].dtype == float

    def test_stamps_every_row_with_the_config_version(self):
        history = sales(daily("plain", "2026-01-01", 60))

        result = run_forecasts(
            config({"plain": ["plain"]}, version=7), history, dt.date(2026, 6, 1)
        )

        assert set(result["config_version"]) == {7}
