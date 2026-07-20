"""The scheduled daily forecast entry point (ticket 05, ADR 0006).

The thin wiring between three pieces that are each already tested on their own:
`sales_history.load_sales_history()`, `db.read_active_config` /
`db.insert_forecasts`, and the pure `forecast_engine.run_forecasts`. So what is
tested here is the *wiring* — that the morning's run forecasts as of today in
the restaurants' timezone, stamps the active config's version, reports what it
wrote, and turns any failure into a visible non-zero exit — not the forecasting
arithmetic, which is `tests/test_forecast_engine.py`'s.

Two layers, mirroring test_daily_capture.py:

- Unit tests that need no database, driven through fake `connect` / `load_sales`
  seams with the db reader and writer monkeypatched.
- Integration tests against a real Postgres, gated behind `TEST_DATABASE_URL`.
  They TRUNCATE the schema's tables, so they run against a throwaway database,
  never `DATABASE_URL`; unset, they skip and the suite still passes
  database-less.

Only EWMA is configured throughout: statsmodels lives in the `forecast` extra
and the default test run must pass without it (the ETS tests `importorskip`).
"""
import datetime as dt
import json
import os

import pandas as pd
import psycopg
import pytest

import daily_forecast
import db

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CAMBRIDGE = "28e5b269-1c1c-45df-81a8-1d268c005dfa"

# A Tuesday, and the `as_of` every test below runs at.
AS_OF = dt.date(2026, 7, 7)

CONFIG = {
    "version": 3,
    "horizon_days": 2,
    "models": {"ewma": {"halflife_weeks": 3}},
    "targets": {"wheat_bagels": ["plain", "sesame"]},
}


def sales(products=("plain", "sesame"), start="2026-06-01", end="2026-07-06"):
    """A gap-free synthetic Sales history in the `(product, date, quantity)`
    shape `load_sales_history()` returns — five weeks, enough same-weekday
    observations for EWMA to reduce."""
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame(
        [
            {"product": product, "date": date, "quantity": 10.0 + n}
            for product in products
            for n, date in enumerate(dates)
        ]
    )


class FakeConn:
    """Stands in for a psycopg connection: a context manager that hands back
    itself and records that it was closed, so a test can assert main released
    it."""

    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


class Recorder:
    """A stand-in `db.insert_forecasts` that keeps the frame it was handed."""

    def __init__(self):
        self.frames = []

    def __call__(self, conn, frame):
        self.frames.append(frame)
        return len(frame)


@pytest.fixture()
def wired(monkeypatch):
    """`db.read_active_config` returning CONFIG and a recording
    `db.insert_forecasts`, so main runs end to end without a database."""
    recorder = Recorder()
    monkeypatch.setattr(db, "read_active_config", lambda conn: dict(CONFIG))
    monkeypatch.setattr(db, "insert_forecasts", recorder)
    return recorder


def run_main(capsys=None, **kwargs):
    """main with the database and Sales seams faked out by default."""
    kwargs.setdefault("connect", lambda **k: FakeConn())
    kwargs.setdefault("load_sales", sales)
    # 09:30 UTC = 05:30 ET, the morning after the capture's 09:00 UTC cron.
    kwargs.setdefault("now", dt.datetime(2026, 7, 7, 9, 30, tzinfo=dt.timezone.utc))
    return daily_forecast.main(**kwargs)


class TestMain:
    def test_logs_todays_forecasts_under_the_active_config_version(self, wired):
        assert run_main() == 0

        (written,) = wired.frames
        # Two target dates (horizon_days=2) x one model x one Target.
        assert len(written) == 2
        assert set(written["as_of"]) == {AS_OF}
        assert set(written["config_version"]) == {3}
        assert set(written["target"]) == {"wheat_bagels"}
        assert sorted(written["target_date"]) == [
            dt.date(2026, 7, 8),
            dt.date(2026, 7, 9),
        ]

    def test_reports_the_config_version_and_the_row_count(self, wired, capsys):
        run_main()
        out = capsys.readouterr().out
        assert "config version 3" in out
        assert "produced 2 rows, logged 2" in out

    def test_releases_the_connection(self, wired):
        conn = FakeConn()
        run_main(connect=lambda **k: conn)
        assert conn.closed

    def test_as_of_is_the_date_in_the_restaurants_timezone(self, wired):
        # The hour that distinguishes the two clocks: 00:30 UTC on the 8th is
        # still 20:30 ET on the 7th. A run then forecasts as of the 7th — the
        # day the restaurants are closing — not the 8th the runner's own date
        # already says. (The scheduled run is nowhere near this hour; the point
        # is that a hand-run evening retry lands on the right day.)
        run_main(now=dt.datetime(2026, 7, 8, 0, 30, tzinfo=dt.timezone.utc))
        (written,) = wired.frames
        assert set(written["as_of"]) == {AS_OF}

    def test_no_active_configuration_exits_nonzero(self, monkeypatch, capsys):
        def boom(conn):
            raise RuntimeError("No active forecast configuration")

        monkeypatch.setattr(db, "read_active_config", boom)
        assert run_main() == 1
        assert "daily forecast failed" in capsys.readouterr().err

    def test_unreachable_database_exits_nonzero(self, capsys):
        def connect(**kwargs):
            raise psycopg.OperationalError("could not connect to server")

        assert run_main(connect=connect) == 1
        assert "daily forecast failed" in capsys.readouterr().err

    def test_a_model_failure_exits_nonzero_and_writes_nothing(
        self, monkeypatch, capsys
    ):
        broken = {**CONFIG, "models": {"prophet": {}}}
        monkeypatch.setattr(db, "read_active_config", lambda conn: broken)
        recorder = Recorder()
        monkeypatch.setattr(db, "insert_forecasts", recorder)

        assert run_main() == 1
        assert "daily forecast failed" in capsys.readouterr().err
        assert recorder.frames == []

    def test_an_unreachable_sales_history_exits_nonzero(self, wired, capsys):
        def load_sales():
            raise RuntimeError("Could not reach the Sales database")

        assert run_main(load_sales=load_sales) == 1
        assert "daily forecast failed" in capsys.readouterr().err


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run DB integration tests",
)
class TestAgainstPostgres:
    """The whole morning against a real database: an active config and a Sales
    history in, logged forecast rows out."""

    @pytest.fixture()
    def conn(self):
        with psycopg.connect(TEST_DATABASE_URL) as c:
            db.apply_schema(c)
            c.execute(
                "TRUNCATE raw_toast_responses, sales, product_sources, products, "
                "forecasts, forecast_configs"
            )
            db.upsert_product_sources(
                c,
                {
                    "plain": [("modifier", "plain bagel")],
                    "sesame": [("modifier", "sesame bagel")],
                },
            )
            db.upsert_sales(c, self._facts())
            # `version` is GENERATED ALWAYS AS IDENTITY, so the database assigns
            # it and `read_active_config` stamps the row's value over whatever
            # the stored document carries. The active version is therefore
            # whatever the identity hands out here, not CONFIG's — taken from
            # RETURNING, as test_db.py's _insert_config does.
            self.version = c.execute(
                "INSERT INTO forecast_configs (is_active, config) VALUES (true, %s) "
                "RETURNING version",
                (json.dumps(CONFIG),),
            ).fetchone()[0]
            c.commit()
            yield c

    def _facts(self):
        """The synthetic history as Sales fact rows, one source per Product."""
        history = sales()
        source = {"plain": "plain bagel", "sesame": "sesame bagel"}
        return pd.DataFrame(
            {
                "date": history["date"],
                "restaurant_guid": CAMBRIDGE,
                "source_type": "modifier",
                "source_name": history["product"].map(source),
                "quantity": history["quantity"],
            }
        )

    def _logged(self, conn):
        return conn.execute(
            "SELECT as_of, config_version, model, target, target_date "
            "FROM forecasts ORDER BY target_date"
        ).fetchall()

    def test_logs_the_active_configs_forecasts(self, conn):
        counts = daily_forecast.run_daily_forecast(conn, sales(), AS_OF)

        assert counts == {"config_version": self.version, "rows": 2, "inserted": 2}
        assert self._logged(conn) == [
            (AS_OF, self.version, "ewma", "wheat_bagels", dt.date(2026, 7, 8)),
            (AS_OF, self.version, "ewma", "wheat_bagels", dt.date(2026, 7, 9)),
        ]

    def test_a_rerun_the_same_morning_writes_nothing_new(self, conn):
        daily_forecast.run_daily_forecast(conn, sales(), AS_OF)
        before = self._logged(conn)

        counts = daily_forecast.run_daily_forecast(conn, sales(), AS_OF)

        # The write-once contract: the retry is reported as having added no rows
        # and the already-logged morning stands exactly as it was recorded.
        assert counts["inserted"] == 0
        assert self._logged(conn) == before
