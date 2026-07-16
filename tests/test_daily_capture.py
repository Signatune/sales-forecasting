"""The daily Orders capture (ticket 05, ADR 0004, ADR 0005).

Two layers, mirroring test_db.py / test_migrate.py:

- Unit tests that need no database and no Toast --- the trailing window, the
  pull-and-aggregate step (PII stripped), and main's failure path --- driven
  through a fake Orders client.
- Integration tests against a real Postgres, gated behind `TEST_DATABASE_URL`.
  They TRUNCATE the schema's tables, so they run against a throwaway database,
  never `DATABASE_URL`; unset, they skip and the suite still passes
  database-less.

The fake client is synthetic ordersBulk data --- structurally faithful to real
Orders responses but with no guest data --- so the aggregation strips it to
Analytics-shaped modifier rows exactly as production does.
"""
import datetime as dt
import os

import psycopg
import pytest

import daily_capture
import db
import normalize
from normalize import validate_modifier_rows
from toast_client import ToastAuthError

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CAMBRIDGE = "28e5b269-1c1c-45df-81a8-1d268c005dfa"
BROOKLINE = "9ae70079-b9cd-4b92-8457-c86bc823188f"


def _mod(name, qty, guid="item-guid"):
    """A configured modifier selection (carries a Toast item GUID)."""
    return {"displayName": name, "quantity": qty, "item": {"guid": guid}}


def _order(modifiers):
    """A minimal ordersBulk order: one check, one selection, its modifiers."""
    return {"checks": [{"selections": [{"modifiers": modifiers}]}]}


class FakeOrdersClient:
    """Stands in for ToastOrdersClient: hands back canned orders per
    `(restaurant_guid, business_date)` and records every call, so a test can
    assert only the Orders API was touched (never Analytics)."""

    def __init__(self, orders_by_key=None):
        self.orders_by_key = orders_by_key or {}
        self.calls = []

    def login(self):
        self.calls.append(("login",))

    def orders_for_business_date(self, restaurant_guid, business_date):
        self.calls.append(("orders", restaurant_guid, business_date))
        return self.orders_by_key.get((restaurant_guid, business_date), [])


class TestTrailingBusinessDates:
    def test_last_three_dates_ending_at_today_oldest_first(self):
        got = daily_capture.trailing_business_dates(dt.date(2026, 7, 7))
        assert got == [dt.date(2026, 7, 5), dt.date(2026, 7, 6), dt.date(2026, 7, 7)]

    def test_window_width_is_configurable(self):
        got = daily_capture.trailing_business_dates(dt.date(2026, 7, 7), days=1)
        assert got == [dt.date(2026, 7, 7)]


class TestPullAndAggregate:
    def test_shards_one_per_restaurant_and_date_with_python_dates(self):
        client = FakeOrdersClient(
            {(CAMBRIDGE, "20260707"): [_order([_mod("plain bagel", 10)])]}
        )
        shards, _rows = daily_capture.pull_and_aggregate(
            client, [dt.date(2026, 7, 7)]
        )
        keys = {(guid, date) for guid, date, _agg in shards}
        # both in-scope restaurants, even the one with no orders (a closed day)
        assert keys == {
            (CAMBRIDGE, dt.date(2026, 7, 7)),
            (BROOKLINE, dt.date(2026, 7, 7)),
        }

    def test_aggregates_strip_to_analytics_shaped_rows_no_pii(self):
        client = FakeOrdersClient(
            {(CAMBRIDGE, "20260707"): [_order([_mod("plain bagel", 10)])]}
        )
        _shards, rows = daily_capture.pull_and_aggregate(client, [dt.date(2026, 7, 7)])
        # the PII-bearing order structure is gone; only modifier rows remain
        validate_modifier_rows(rows)
        assert rows == [
            {
                "restaurantGuid": CAMBRIDGE,
                "businessDate": "20260707",
                "modifierGuid": "item-guid",
                "modifierName": "plain bagel",
                "quantitySold": 10.0,
            }
        ]

    def test_a_toast_failure_propagates_before_any_write(self):
        class Boom(FakeOrdersClient):
            def orders_for_business_date(self, restaurant_guid, business_date):
                raise normalize.UnexpectedShapeError("ordersBulk exploded")

        with pytest.raises(normalize.UnexpectedShapeError):
            daily_capture.pull_and_aggregate(Boom(), [dt.date(2026, 7, 7)])


class TestMainFailurePath:
    """main turns a Toast or database failure into a non-zero exit with a clear
    message, without touching the database on a Toast failure."""

    def test_toast_login_failure_exits_nonzero_and_never_connects(self, capsys):
        def make_client():
            raise ToastAuthError("Toast login failed: HTTP 401")

        def connect(*a, **k):  # pragma: no cover - must never be reached
            raise AssertionError("must not connect after a Toast failure")

        rc = daily_capture.main(
            connect=connect, make_client=make_client, now=dt.datetime(2026, 7, 7)
        )
        assert rc == 1
        assert "daily capture failed" in capsys.readouterr().err

    def test_unreachable_database_exits_nonzero(self, capsys):
        def connect(*a, **k):
            raise psycopg.OperationalError("could not connect to server")

        rc = daily_capture.main(
            connect=connect,
            make_client=lambda: FakeOrdersClient(),
            now=dt.datetime(2026, 7, 7),
        )
        assert rc == 1
        assert "daily capture failed" in capsys.readouterr().err


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run DB integration tests",
)
class TestAgainstPostgres:
    @pytest.fixture()
    def conn(self):
        with psycopg.connect(TEST_DATABASE_URL) as c:
            db.apply_schema(c)
            c.execute("TRUNCATE raw_toast_responses, sales, product_sources, products")
            db.seed_products(
                c,
                {
                    "plain": [("modifier", "plain bagel")],
                    "sesame": [("modifier", "sesame bagel")],
                },
            )
            c.commit()
            yield c

    def _client(self):
        """Cambridge sold plain+sesame on 7/7 and plain on 7/6; 7/5 is closed and
        Brookline sold nothing. The window is 7/5..7/7."""
        return FakeOrdersClient(
            {
                (CAMBRIDGE, "20260707"): [
                    _order([_mod("plain bagel", 10), _mod("sesame bagel", 4)])
                ],
                (CAMBRIDGE, "20260706"): [_order([_mod("plain bagel", 7)])],
            }
        )

    def _view(self, conn):
        view = db.read_sales(conn)
        return {
            (row.product, row.date.date()): row.quantity
            for row in view.itertuples(index=False)
        }

    def test_captures_window_into_fact_and_raw(self, conn):
        counts = daily_capture.run_capture(
            conn, self._client(), daily_capture.trailing_business_dates(dt.date(2026, 7, 7))
        )
        view = self._view(conn)
        assert view[("plain", dt.date(2026, 7, 7))] == 10.0
        assert view[("sesame", dt.date(2026, 7, 7))] == 4.0
        assert view[("plain", dt.date(2026, 7, 6))] == 7.0
        assert counts["fact_upserted"] == 3
        # a raw shard per (restaurant, date): 2 restaurants x 3 dates
        raw = conn.execute("SELECT count(*) FROM raw_toast_responses").fetchone()[0]
        assert raw == 6

    def test_only_the_orders_api_is_called(self, conn):
        client = self._client()
        daily_capture.run_capture(
            conn, client, daily_capture.trailing_business_dates(dt.date(2026, 7, 7))
        )
        # every Toast interaction is an Orders pull --- Analytics is never touched
        assert client.calls
        assert all(call[0] == "orders" for call in client.calls)

    def test_rerun_with_unchanged_numbers_is_a_noop(self, conn):
        window = daily_capture.trailing_business_dates(dt.date(2026, 7, 7))
        daily_capture.run_capture(conn, self._client(), window)
        before = self._view(conn)
        fact_before = conn.execute("SELECT count(*) FROM sales").fetchone()[0]

        daily_capture.run_capture(conn, self._client(), window)
        assert self._view(conn) == before
        assert conn.execute("SELECT count(*) FROM sales").fetchone()[0] == fact_before

    def test_rerun_corrects_a_hand_changed_day(self, conn):
        # The ticket's demoable: hand-corrupt a stored quantity, run again, and
        # the day is corrected back from Toast (upsert replaces on the key).
        window = daily_capture.trailing_business_dates(dt.date(2026, 7, 7))
        daily_capture.run_capture(conn, self._client(), window)
        conn.execute(
            "UPDATE sales SET quantity = 999 "
            "WHERE date = %s AND source_name = 'plain bagel'",
            (dt.date(2026, 7, 7),),
        )
        conn.commit()

        daily_capture.run_capture(conn, self._client(), window)
        assert self._view(conn)[("plain", dt.date(2026, 7, 7))] == 10.0

    def test_unmapped_bagelish_is_surfaced_loudly(self, conn, capsys):
        client = FakeOrdersClient(
            {(CAMBRIDGE, "20260707"): [_order([_mod("blueberry bagel", 5)])]}
        )
        daily_capture.run_capture(
            conn, client, [dt.date(2026, 7, 7)]
        )
        out = capsys.readouterr().out
        assert "not mapped to any Product" in out
        assert "blueberry bagel" in out
        # it still sits in the fact, just untracked by any Product (ADR 0005)
        in_fact = conn.execute(
            "SELECT count(*) FROM sales WHERE source_name = 'blueberry bagel'"
        ).fetchone()[0]
        assert in_fact == 1

    def test_a_database_failure_leaves_the_history_untouched(self, conn, monkeypatch):
        # A day already captured and committed.
        daily_capture.run_capture(
            conn, self._client(), [dt.date(2026, 7, 6)]
        )
        before = self._view(conn)
        raw_before = conn.execute(
            "SELECT count(*) FROM raw_toast_responses"
        ).fetchone()[0]

        # The next run's fact write blows up; the raw shards written earlier in
        # the same transaction must roll back with it.
        def boom(*a, **k):
            raise psycopg.errors.DatabaseError("upsert failed")

        monkeypatch.setattr(db, "bulk_upsert_sales", boom)
        with pytest.raises(psycopg.Error):
            daily_capture.run_capture(
                conn, self._client(), daily_capture.trailing_business_dates(dt.date(2026, 7, 7))
            )

        # nothing changed: the committed day stands, no new raw shards landed
        assert self._view(conn) == before
        assert (
            conn.execute("SELECT count(*) FROM raw_toast_responses").fetchone()[0]
            == raw_before
        )
