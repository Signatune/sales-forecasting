"""The scheduled reports entry point (ticket 05 of scheduled-forecast-reports).

The thin wiring between four pieces that are each already tested on their own:
`db.read_due_reports`, the pure `report_payload.build_payload`, `report_render`
and `report_delivery`. So what is tested here is the *wiring* --- that it asks
for the right day's reports, runs the chain in order, keeps one report's failure
off the others, and turns any failure into a visible non-zero exit --- not the
report rules, which are `tests/test_report_payload.py`'s.

Driven entirely through `main`'s seams: fake `connect` / `load_sales`, the db
readers monkeypatched (the pattern `tests/test_daily_forecast.py` uses), and
fake `render` / `delivery` modules that record what they were asked to do.
"""
import datetime as dt

import pandas as pd
import pytest

import db
import scheduled_reports

# 2026-08-01 is a Saturday. 09:30 UTC is 05:30 ET, the morning after the
# forecast run.
SATURDAY = dt.date(2026, 8, 1)
SATURDAY_MORNING_UTC = dt.datetime(2026, 8, 1, 9, 30, tzinfo=dt.timezone.utc)

VARIETIES = ["everything", "plain", "sesame"]
WINDOW = [SATURDAY + dt.timedelta(days=lead) for lead in range(1, 8)]

REPORT = {
    "id": 1,
    "forecast_config_version": 4,
    "name": "Bagel forecast",
    "headline_model": "ewma",
    "target": "wheat_bagels",
    "varieties": VARIETIES,
    "delivery": "bagel-team",
}

FORECAST_CONFIG = {
    "version": 4,
    "is_active": True,
    "active_version": 4,
    "horizon_days": 7,
    "models": {"ewma": {}},
    "targets": {"wheat_bagels": VARIETIES, **{v: [v] for v in VARIETIES}},
}


def logged(origin=SATURDAY, version=4):
    rows = []
    for lead in range(1, 8):
        date = origin + dt.timedelta(days=lead)
        rows.append((origin, version, "ewma", "wheat_bagels", date, 400.0))
        for name in VARIETIES:
            rows.append((origin, version, "ewma", name, date, 100.0))
    return pd.DataFrame(rows, columns=list(db.FORECAST_COLUMNS))


def sales():
    rows = []
    date = dt.date(2026, 6, 1)
    while date <= dt.date(2026, 7, 29):
        for name in VARIETIES:
            rows.append({"product": name, "date": pd.Timestamp(date), "quantity": 120.0})
        date += dt.timedelta(days=1)
    return pd.DataFrame(rows)


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRender:
    def __init__(self, pdf=b"%PDF-fake"):
        self.pdf = pdf
        self.rendered = []

    def render_pdf(self, payload):
        self.rendered.append(payload)
        return self.pdf


class FakeDelivery:
    """Records the whole outward-facing chain, in the order it happened."""

    WEBHOOK_MIN_INTERVAL_SECONDS = 1.0

    def __init__(self, unresolvable=(), raise_on_upload=()):
        self.calls = []
        self.unresolvable = set(unresolvable)
        self.raise_on_upload = set(raise_on_upload)

    def resolve_destination(self, name, environ=None):
        """Bound by `make_delivery`, which needs the Destination class below."""
        raise NotImplementedError

    def drive_credentials(self, environ=None):
        self.calls.append(("credentials",))
        return "creds"

    def report_filename(self, payload):
        return f"{payload.report_name}-{payload.window[0]}.pdf"

    def upload_pdf(self, pdf, filename, folder_id, credentials):
        self.calls.append(("upload", filename, folder_id, pdf))
        if filename in self.raise_on_upload:
            raise RuntimeError("Drive said no")
        return "https://drive/link"

    def post_card(self, payload, link, webhook_url):
        self.calls.append(("card", payload.report_name, link, webhook_url))

    def post_refusal(self, refusal, webhook_url):
        self.calls.append(("refusal", refusal.report_name, refusal.reason, webhook_url))

    @property
    def kinds(self):
        return [call[0] for call in self.calls]


class Destination:
    def __init__(self, name):
        self.name = name
        self.webhook_url = f"https://chat/{name}"
        self.folder_id = f"folder-{name}"


def make_delivery(**kwargs):
    fake = FakeDelivery(**kwargs)
    unresolvable = fake.unresolvable

    def resolve(name, environ=None):
        fake.calls.append(("resolve", name))
        if not name or name in unresolvable:
            raise RuntimeError(f"CHAT_WEBHOOK_{name.upper()} is not set.")
        return Destination(name)

    fake.resolve_destination = resolve
    return fake


@pytest.fixture()
def wired(monkeypatch):
    """The db readers answering for one due Saturday report with a complete
    origin, so main runs end to end without a database."""
    state = {"due": [dict(REPORT)], "logged": logged(), "asked_for": []}

    def read_due_reports(conn, today):
        state["asked_for"].append(today)
        return [dict(report) for report in state["due"]]

    monkeypatch.setattr(db, "read_due_reports", read_due_reports)
    monkeypatch.setattr(db, "read_forecast_config", lambda conn, v: dict(FORECAST_CONFIG))
    monkeypatch.setattr(db, "read_latest_forecasts", lambda conn, v: state["logged"])
    return state


def run_main(**kwargs):
    kwargs.setdefault("connect", lambda **k: FakeConn())
    kwargs.setdefault("load_sales", sales)
    kwargs.setdefault("now", SATURDAY_MORNING_UTC)
    kwargs.setdefault("environ", {})
    kwargs.setdefault("render", FakeRender())
    kwargs.setdefault("delivery", make_delivery())
    kwargs.setdefault("sleep", lambda seconds: None)
    return scheduled_reports.main(**kwargs)


class TestWhichDay:
    def test_it_asks_for_todays_reports_in_the_restaurants_timezone(self, wired):
        run_main()
        assert wired["asked_for"] == [SATURDAY]

    def test_a_utc_time_past_eight_pm_et_still_asks_for_today(self, wired):
        # 01:30 UTC on Sunday is 21:30 ET on Saturday. The runner has rolled
        # over and the shop has not, and asking for Sunday's reports would
        # deliver nothing while looking like a clean run.
        run_main(now=dt.datetime(2026, 8, 2, 1, 30, tzinfo=dt.timezone.utc))
        assert wired["asked_for"] == [SATURDAY]


class TestNothingDue:
    def test_no_due_reports_exits_zero_and_delivers_nothing(self, wired, capsys):
        wired["due"] = []
        delivery = make_delivery()

        assert run_main(delivery=delivery) == 0
        assert delivery.calls == []
        assert "nothing to deliver" in capsys.readouterr().out

    def test_no_due_reports_reads_no_sales(self, wired):
        # Most days there are none; loading the whole Sales history for them
        # would be the job's main cost.
        wired["due"] = []
        loaded = []
        run_main(load_sales=lambda: loaded.append(1) or sales())

        assert loaded == []


class TestTheHappyPath:
    def test_it_runs_the_whole_chain_in_order(self, wired):
        delivery = make_delivery()
        render = FakeRender()

        assert run_main(render=render, delivery=delivery) == 0
        assert delivery.kinds == ["resolve", "credentials", "upload", "card"]

    def test_it_renders_the_payload_the_rules_produced(self, wired):
        render = FakeRender()
        run_main(render=render)

        (payload,) = render.rendered
        assert payload.report_name == "Bagel forecast"
        assert payload.origin == SATURDAY
        assert payload.window == WINDOW

    def test_it_uploads_to_the_destinations_folder_and_posts_its_link(self, wired):
        delivery = make_delivery()
        run_main(delivery=delivery)

        upload = next(c for c in delivery.calls if c[0] == "upload")
        card = next(c for c in delivery.calls if c[0] == "card")
        assert upload[2] == "folder-bagel-team"
        assert upload[3] == b"%PDF-fake"
        assert card[2] == "https://drive/link"
        assert card[3] == "https://chat/bagel-team"

    def test_it_reports_what_it_delivered(self, wired, capsys):
        run_main()
        out = capsys.readouterr().out
        assert "Bagel forecast" in out
        assert "1 of 1" in out


class TestRefusals:
    def test_a_refusal_posts_a_refusal_and_does_not_upload(self, wired):
        # Yesterday's origin: today's forecast run did not complete (ADR 0010).
        wired["logged"] = logged(origin=SATURDAY - dt.timedelta(days=1))
        delivery = make_delivery()

        assert run_main(delivery=delivery) == 0
        assert delivery.kinds == ["resolve", "refusal"]
        assert delivery.calls[1][2] == "stale_origin"

    def test_a_refusal_is_not_a_failed_run(self, wired):
        # It is a correct outcome loudly reported, not an error: the exit code
        # is what the Actions tab colours, and a refused report is the system
        # working.
        wired["logged"] = logged(origin=SATURDAY - dt.timedelta(days=1))
        assert run_main() == 0

    def test_a_superseded_config_refuses_without_rendering(self, wired, monkeypatch):
        monkeypatch.setattr(
            db,
            "read_forecast_config",
            lambda conn, v: {**FORECAST_CONFIG, "is_active": False, "active_version": 9},
        )
        render = FakeRender()
        delivery = make_delivery()

        assert run_main(render=render, delivery=delivery) == 0
        assert render.rendered == []
        assert delivery.calls[1][2] == "superseded_config"


class TestOneReportFailing:
    def _two_reports(self, wired, second_delivery="tuesday-team"):
        wired["due"] = [
            dict(REPORT),
            {**REPORT, "id": 2, "name": "Second report", "delivery": second_delivery},
        ]

    def test_one_report_raising_still_delivers_the_others(self, wired):
        self._two_reports(wired)
        # The first report's destination cannot be resolved; the second's can.
        delivery = make_delivery(unresolvable=["bagel-team"])

        exit_code = run_main(delivery=delivery)

        assert exit_code != 0
        cards = [call for call in delivery.calls if call[0] == "card"]
        assert [call[1] for call in cards] == ["Second report"]

    def test_a_failure_is_reported_where_it_can_be(self, wired):
        self._two_reports(wired)
        # Uploading the first report's PDF fails after its destination resolved,
        # so the failure notice can still reach its space.
        delivery = make_delivery(raise_on_upload=["Bagel forecast-2026-08-02.pdf"])

        assert run_main(delivery=delivery) != 0
        refusals = [call for call in delivery.calls if call[0] == "refusal"]
        assert [(call[1], call[2]) for call in refusals] == [
            ("Bagel forecast", "delivery_failed")
        ]

    def test_an_unpostable_failure_does_not_take_the_run_down(self, wired, capsys):
        # The commonest failure *is* an unresolvable destination, so the error
        # path must tolerate having nowhere to post.
        delivery = make_delivery(unresolvable=["bagel-team"])

        assert run_main(delivery=delivery) != 0
        assert "could not post the failure notice" in capsys.readouterr().err

    def test_the_exit_code_is_non_zero_but_the_others_still_ran(self, wired, capsys):
        self._two_reports(wired)
        delivery = make_delivery(unresolvable=["bagel-team"])

        assert run_main(delivery=delivery) == 1
        assert "1 of 2" in capsys.readouterr().out


class TestPacing:
    def test_it_sleeps_between_reports_not_before_the_first(self, wired):
        wired["due"] = [dict(REPORT), {**REPORT, "id": 2, "name": "Second"}]
        slept = []

        run_main(sleep=slept.append)

        # One gap for two reports, at least the webhook's per-space limit.
        assert len(slept) == 1
        assert slept[0] >= 1.0


class TestHardFailures:
    def test_an_unreachable_database_exits_non_zero(self, capsys):
        def connect(**kwargs):
            raise RuntimeError("could not connect")

        assert run_main(connect=connect) == 1
        assert "scheduled reports failed" in capsys.readouterr().err

    def test_a_failing_sales_read_exits_non_zero(self, wired):
        def load_sales():
            raise RuntimeError("no database")

        assert run_main(load_sales=load_sales) == 1
