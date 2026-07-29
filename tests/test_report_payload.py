"""The Scheduled Report decision layer (ticket 02 of scheduled-forecast-reports).

`report_payload.build_payload` is pure --- no database, no clock, no filesystem
--- so every rule below is exercised against small in-memory frames, without a
Postgres and without waiting for a Saturday.

`TODAY` is Saturday 2026-08-01 throughout, so the Report Window is Sunday
2026-08-02 through Saturday 2026-08-08 and the Settled Sales cutoff (three days
back, ADR 0004) falls on Wednesday 2026-07-29.
"""
import datetime as dt

import pandas as pd
import pytest

import report_payload

TODAY = dt.date(2026, 8, 1)  # a Saturday
SETTLED_THROUGH = dt.date(2026, 7, 29)  # TODAY - 3, the newest settled date

TARGET = "wheat_bagels"
VARIETIES = ["everything", "plain", "sesame"]
VERSION = 4

REPORT = {
    "id": 1,
    "forecast_config_version": VERSION,
    "name": "Bagel forecast",
    "headline_model": "ewma",
    "target": TARGET,
    "varieties": VARIETIES,
    "delivery": "bagel-team",
}


def config(
    horizon_days=7,
    is_active=True,
    active_version=VERSION,
    version=VERSION,
    models=("ewma", "holt_winters"),
):
    """The referenced `forecast_configs` document as db.py hands it over: the
    stored document with its `version`, its `is_active` flag, and the version
    that is active *now* stamped in."""
    return {
        "version": version,
        "is_active": is_active,
        "active_version": active_version,
        "horizon_days": horizon_days,
        "models": {name: {} for name in models},
        # The headline Target is the three varieties summed; each variety is
        # also forecast in its own right as a degenerate one-member Target,
        # which is what makes the split readable off the log (CONTEXT.md).
        "targets": {TARGET: list(VARIETIES), **{v: [v] for v in VARIETIES}},
    }


def log_frame(rows):
    """The `forecasts` rows as db.py reads them back: python dates, a float
    quantity."""
    return pd.DataFrame(
        rows,
        columns=[
            "as_of",
            "config_version",
            "model",
            "target",
            "target_date",
            "forecast_quantity",
        ],
    )


def full_log(
    origin=TODAY,
    version=VERSION,
    models=("ewma", "holt_winters"),
    horizon=7,
    headline=lambda day: 400.0,
    variety=lambda name, day: 100.0,
):
    """A complete origin: every model, every target date in the window, the
    headline Target and all three varieties."""
    rows = []
    for model in models:
        for lead in range(1, horizon + 1):
            date = origin + dt.timedelta(days=lead)
            rows.append((origin, version, model, TARGET, date, headline(date)))
            for name in VARIETIES:
                rows.append((origin, version, model, name, date, variety(name, date)))
    return log_frame(rows)


def without(log, **match):
    """`log` with the rows matching every keyword removed --- the hole a model
    that could not forecast a day leaves."""
    keep = pd.Series(False, index=log.index)
    for column, value in match.items():
        keep |= log[column] != value
    return log[keep].reset_index(drop=True)


def sold(date, total):
    """One day's Sales for the headline Target's members, `total` split evenly
    --- the baseline reads the members summed, so only the total matters."""
    return [(date, name, total / len(VARIETIES)) for name in VARIETIES]


def sales_frame(rows):
    """The `(product, date, quantity)` frame `sales_history.load_sales_history`
    returns: dates as datetime64, quantities as float."""
    return pd.DataFrame(
        {
            "product": [product for _, product, _ in rows],
            "date": pd.to_datetime([date for date, _, _ in rows]),
            "quantity": [float(quantity) for _, _, quantity in rows],
        }
    )


def flat_history(
    total=1000.0, start=dt.date(2026, 6, 1), through=SETTLED_THROUGH, **overrides
):
    """Settled Sales of exactly `total` every open day, so every weekday's
    Baseline is exactly `total` and a test can move one number at a time.

    `overrides` replaces a date's total rather than adding to it --- keyed by
    ISO date with the dashes dropped (`d20260724=1200.0`), since a keyword
    cannot be a date."""
    replaced = {
        dt.date(int(key[1:5]), int(key[5:7]), int(key[7:9])): value
        for key, value in overrides.items()
    }
    rows = []
    date = start
    while date <= through:
        rows += sold(date, replaced.get(date, total))
        date += dt.timedelta(days=1)
    return rows


def build(report=None, forecast_config=None, logged=None, sales=None, today=TODAY):
    return report_payload.build_payload(
        REPORT if report is None else report,
        config() if forecast_config is None else forecast_config,
        full_log() if logged is None else logged,
        sales_frame(flat_history()) if sales is None else sales,
        today,
    )


class TestTheReportWindow:
    def test_a_complete_origin_yields_the_seven_dates_after_today(self):
        payload = build()

        assert isinstance(payload, report_payload.Payload)
        assert payload.window == [
            dt.date(2026, 8, 2),
            dt.date(2026, 8, 3),
            dt.date(2026, 8, 4),
            dt.date(2026, 8, 5),
            dt.date(2026, 8, 6),
            dt.date(2026, 8, 7),
            dt.date(2026, 8, 8),
        ]
        assert payload.origin == TODAY
        assert payload.config_version == VERSION

    def test_a_fourteen_day_horizon_yields_a_fourteen_day_window(self):
        # Nothing anywhere stores a week shape (ADR 0010): the window is
        # origin+1 .. origin+horizon_days, whatever the horizon happens to be.
        payload = build(
            forecast_config=config(horizon_days=14),
            logged=full_log(horizon=14),
        )

        assert len(payload.window) == 14
        assert payload.window[0] == dt.date(2026, 8, 2)
        assert payload.window[-1] == dt.date(2026, 8, 15)
        assert all(len(page.days) == 14 for page in payload.pages)

    def test_a_wednesday_report_covers_thursday_through_wednesday(self):
        # The same row moved to another weekday needs no code change.
        wednesday = dt.date(2026, 8, 5)
        payload = build(
            logged=full_log(origin=wednesday),
            sales=sales_frame(flat_history(through=dt.date(2026, 8, 2))),
            today=wednesday,
        )

        assert payload.window[0] == dt.date(2026, 8, 6)  # Thursday
        assert payload.window[-1] == dt.date(2026, 8, 12)  # the next Wednesday


class TestRefusals:
    def test_a_superseded_config_version_refuses_naming_the_active_one(self):
        refusal = build(forecast_config=config(is_active=False, active_version=9))

        assert isinstance(refusal, report_payload.Refusal)
        assert refusal.reason == "superseded_config"
        assert "9" in refusal.message
        assert str(VERSION) in refusal.message
        assert refusal.report_name == "Bagel forecast"

    def test_a_superseded_config_with_nothing_active_still_refuses(self):
        refusal = build(forecast_config=config(is_active=False, active_version=None))

        assert refusal.reason == "superseded_config"
        assert "no active" in refusal.message.lower()

    def test_an_origin_older_than_today_refuses_with_the_other_message(self):
        # The two failures need different fixes, so they must not share a
        # message (ADR 0010).
        refusal = build(logged=full_log(origin=TODAY - dt.timedelta(days=1)))

        assert isinstance(refusal, report_payload.Refusal)
        assert refusal.reason == "stale_origin"
        assert "2026-07-31" in refusal.message
        assert "2026-08-01" in refusal.message

    def test_nothing_logged_at_all_refuses_as_a_stale_origin(self):
        refusal = build(logged=log_frame([]))

        assert refusal.reason == "stale_origin"
        assert "no forecast" in refusal.message.lower()

    def test_no_eligible_model_refuses(self):
        # Every model has a hole, so no page can be rendered at all.
        holed = without(full_log(), target_date=dt.date(2026, 8, 5))
        refusal = build(logged=holed)

        assert isinstance(refusal, report_payload.Refusal)
        assert refusal.reason == "no_eligible_model"
        assert "ewma" in refusal.message and "holt_winters" in refusal.message

    def test_a_refusal_names_the_report_it_is_about(self):
        # It is posted into a space that may carry several reports.
        assert build(logged=log_frame([])).report_name == "Bagel forecast"


class TestModelEligibility:
    def test_a_model_missing_one_target_date_is_omitted(self):
        holed = without(
            full_log(), model="ewma", target=TARGET, target_date=dt.date(2026, 8, 5)
        )
        payload = build(logged=holed)

        assert [page.model for page in payload.pages] == ["holt_winters"]
        assert "ewma" in payload.omitted_models
        assert "2026-08-05" in payload.omitted_models["ewma"]

    def test_a_model_missing_one_variety_day_is_also_omitted(self):
        # The headline is complete but the split has a hole, which would leave
        # a gap in the variety table rather than in the chart.
        holed = without(
            full_log(), model="ewma", target="plain", target_date=dt.date(2026, 8, 5)
        )
        payload = build(logged=holed)

        assert [page.model for page in payload.pages] == ["holt_winters"]
        assert "plain" in payload.omitted_models["ewma"]

    def test_a_complete_model_keeps_its_page(self):
        payload = build()
        assert sorted(page.model for page in payload.pages) == ["ewma", "holt_winters"]
        assert payload.omitted_models == {}

    def test_the_headline_model_leads_and_the_rest_are_alphabetical(self):
        # ADR 0011: the headline leads, everything after it is alphabetical, so
        # page order asserts no ranking among the others.
        logged = full_log(models=("aardvark", "ewma", "holt_winters", "zulu"))
        forecast_config = config(models=("aardvark", "ewma", "holt_winters", "zulu"))
        payload = build(forecast_config=forecast_config, logged=logged)

        assert [page.model for page in payload.pages] == [
            "ewma",
            "aardvark",
            "holt_winters",
            "zulu",
        ]
        assert payload.headline.model == "ewma"
        assert payload.headline_is_the_named_model is True

    def test_a_headline_model_that_does_not_qualify_leaves_the_others(self):
        # The report still goes out; the headline is whichever page leads.
        holed = without(full_log(), model="ewma", target_date=dt.date(2026, 8, 5))
        payload = build(logged=holed)

        assert payload.headline.model == "holt_winters"
        assert "ewma" in payload.omitted_models
        # ...and the payload says the leading page is a substitute, so the card
        # and the page can both say so rather than quoting it silently.
        assert payload.headline_is_the_named_model is False

    def test_rows_from_another_config_version_are_ignored(self):
        # `logged` is meant to be one version's rows; a stray row from another
        # version must not complete a model's coverage.
        stray = full_log(version=VERSION + 1)
        holed = without(full_log(), model="ewma", target_date=dt.date(2026, 8, 5))
        payload = build(logged=pd.concat([holed, stray], ignore_index=True))

        assert [page.model for page in payload.pages] == ["holt_winters"]


class TestTheWeekdayBaseline:
    def test_it_averages_the_four_most_recent_same_weekdays(self):
        # Friday 2026-08-07's baseline reads the four settled Fridays before
        # today: 07-24, 07-17, 07-10 and 07-03.
        history = flat_history(total=1000.0, d20260724=1200.0, d20260717=800.0)
        payload = build(sales=sales_frame(history))

        friday = next(d for d in payload.headline.days if d.date == dt.date(2026, 8, 7))
        assert friday.baseline == pytest.approx((1200 + 800 + 1000 + 1000) / 4)

    def test_it_ignores_days_inside_the_trailing_window(self):
        # Friday 2026-07-31 is two days old, so ADR 0004's capture window is
        # still open over it and Toast may still correct it. A baseline that
        # shifted under a published report would be worse than one four days
        # older, so that Friday is not settled and is not read.
        history = flat_history(total=1000.0)
        history += sold(dt.date(2026, 7, 31), 5000.0)
        payload = build(sales=sales_frame(history))

        friday = next(d for d in payload.headline.days if d.date == dt.date(2026, 8, 7))
        assert friday.baseline == pytest.approx(1000.0)

    def test_it_stops_at_four_weeks(self):
        # A fifth Friday back (2026-06-26) is outside the window and must not
        # pull the mean.
        payload = build(sales=sales_frame(flat_history(total=1000.0, d20260626=9000.0)))

        friday = next(d for d in payload.headline.days if d.date == dt.date(2026, 8, 7))
        assert friday.baseline == pytest.approx(1000.0)

    def test_a_weekday_with_no_settled_sales_gives_no_baseline(self):
        # Never a 100% jump against nothing.
        history = [
            row for row in flat_history(total=1000.0) if row[0].weekday() != 6
        ]  # every Sunday dropped
        payload = build(sales=sales_frame(history))

        sunday = next(d for d in payload.headline.days if d.date == dt.date(2026, 8, 2))
        assert sunday.baseline is None
        assert sunday.direction == report_payload.NO_BASELINE
        assert sunday.delta_pct is None

    def test_a_baseline_of_zero_is_no_baseline_rather_than_a_spike(self):
        # Four closed Sundays average to nothing, and dividing by it would
        # manufacture the exact jump the "no baseline" rule exists to prevent.
        history = [
            row if row[0].weekday() != 6 else (row[0], row[1], 0.0)
            for row in flat_history(total=1000.0)
        ]
        payload = build(sales=sales_frame(history))

        sunday = next(d for d in payload.headline.days if d.date == dt.date(2026, 8, 2))
        assert sunday.baseline is None
        assert sunday.direction == report_payload.NO_BASELINE

    def test_the_baseline_sums_the_targets_members(self):
        # The headline Target is not a Product: its baseline is its member
        # Products' Settled Sales summed, exactly as the engine fits it.
        history = []
        date = dt.date(2026, 6, 1)
        while date <= SETTLED_THROUGH:
            history += [
                (date, "everything", 100.0),
                (date, "plain", 200.0),
                (date, "sesame", 300.0),
                (date, "turkey club", 999.0),  # not a member: must not count
            ]
            date += dt.timedelta(days=1)
        payload = build(sales=sales_frame(history))

        assert payload.headline.days[0].baseline == pytest.approx(600.0)


class TestDirection:
    def _direction_for(self, forecast):
        payload = build(logged=full_log(headline=lambda date: forecast))
        return payload.headline.days[0]

    def test_a_move_under_three_percent_is_flat(self):
        day = self._direction_for(1029.0)
        assert day.direction == "flat"
        assert day.delta_pct == pytest.approx(2.9)

    def test_a_move_over_three_percent_is_not_flat(self):
        day = self._direction_for(1031.0)
        assert day.direction == "up"
        assert day.delta_pct == pytest.approx(3.1)

    def test_a_fall_over_three_percent_is_down(self):
        day = self._direction_for(969.0)
        assert day.direction == "down"
        assert day.delta_pct == pytest.approx(-3.1)

    def test_a_fall_under_three_percent_is_flat(self):
        assert self._direction_for(971.0).direction == "flat"

    def test_the_page_carries_the_windows_direction_against_typical(self):
        payload = build(logged=full_log(headline=lambda date: 1100.0))
        assert payload.headline.direction == "up"
        assert payload.headline.delta_pct == pytest.approx(10.0)

    def test_the_windows_direction_ignores_days_with_no_baseline(self):
        # Summing seven forecasts against four baselines would read as a
        # collapse that is really just a missing weekday.
        history = [row for row in flat_history(total=1000.0) if row[0].weekday() != 6]
        payload = build(
            logged=full_log(headline=lambda date: 1000.0),
            sales=sales_frame(history),
        )

        assert payload.headline.direction == "flat"
        assert payload.headline.delta_pct == pytest.approx(0.0)

    def test_a_window_with_no_baseline_at_all_has_no_direction(self):
        payload = build(sales=sales_frame([]))
        assert payload.headline.direction == report_payload.NO_BASELINE
        assert payload.headline.delta_pct is None


class TestTheVarietySplit:
    def test_the_varieties_are_the_raw_logged_forecasts(self):
        logged = full_log(variety=lambda name, date: {"everything": 40.0, "plain": 30.0, "sesame": 20.0}[name])
        payload = build(logged=logged)

        assert payload.headline.days[0].varieties == {
            "everything": 40.0,
            "plain": 30.0,
            "sesame": 20.0,
        }

    def test_the_varieties_sum_is_reported_even_when_it_differs(self):
        # Top-down is not bottom-up: the headline is fit to the summed series,
        # so the page states the gap rather than reconciling it away.
        logged = full_log(headline=lambda date: 400.0, variety=lambda name, date: 100.0)
        payload = build(logged=logged)
        day = payload.headline.days[0]

        assert day.forecast == 400.0
        assert day.varieties_total == pytest.approx(300.0)
        assert payload.headline.total == pytest.approx(400.0 * 7)
        assert payload.headline.varieties_total == pytest.approx(300.0 * 7)

    def test_the_split_is_read_per_model_not_from_the_headline_model(self):
        def variety(name, date):
            return 100.0

        def headline(date):
            return 400.0

        logged = full_log(headline=headline, variety=variety)
        # Give holt_winters its own variety numbers.
        logged.loc[
            (logged["model"] == "holt_winters") & (logged["target"] == "plain"),
            "forecast_quantity",
        ] = 250.0
        payload = build(logged=logged)

        ewma = next(p for p in payload.pages if p.model == "ewma")
        ets = next(p for p in payload.pages if p.model == "holt_winters")
        assert ewma.days[0].varieties["plain"] == 100.0
        assert ets.days[0].varieties["plain"] == 250.0


class TestTheHeadlineNumbers:
    def test_the_page_names_its_busiest_and_quietest_days(self):
        # The Chat card quotes these, so they are decided here rather than in
        # the delivery layer.
        levels = {
            dt.date(2026, 8, 2): 300.0,
            dt.date(2026, 8, 5): 900.0,
        }
        payload = build(logged=full_log(headline=lambda date: levels.get(date, 500.0)))

        assert payload.headline.busiest.date == dt.date(2026, 8, 5)
        assert payload.headline.quietest.date == dt.date(2026, 8, 2)

    def test_the_days_are_in_window_order(self):
        payload = build()
        assert [day.date for day in payload.headline.days] == payload.window

    def test_the_payload_carries_what_the_page_header_names(self):
        payload = build()
        assert payload.report_name == "Bagel forecast"
        assert payload.target == TARGET
        assert payload.headline_model == "ewma"
        assert payload.varieties == VARIETIES
