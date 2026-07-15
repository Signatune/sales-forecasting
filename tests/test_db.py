"""The Postgres access seam (ADR 0003, ticket 02).

Two layers:

- Unit tests that need no database --- the connection-string contract and the
  frame-to-rows mapping.
- Integration tests that exercise a real Postgres, gated behind
  `TEST_DATABASE_URL`. They TRUNCATE the schema's tables, so they run against a
  throwaway test database, never `DATABASE_URL`; when the variable is unset they
  skip, and the suite still passes on a dev-only, database-less install.
"""
import datetime
import os

import pandas as pd
import psycopg
import pytest

import db
from normalize import validate_modifier_rows

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


class TestConnectionString:
    def test_reads_the_url_from_the_environment(self):
        assert (
            db.connection_string({"DATABASE_URL": "postgresql://x/y"})
            == "postgresql://x/y"
        )

    def test_missing_url_raises_naming_the_variable(self):
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            db.connection_string({})

    def test_empty_url_raises(self):
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            db.connection_string({"DATABASE_URL": ""})


class TestSalesRows:
    """The frame-to-rows mapping upsert_sales feeds Postgres."""

    def test_maps_to_python_typed_tuples(self):
        frame = pd.DataFrame(
            {
                "product": ["plain"],
                "date": pd.to_datetime(["2026-07-05"]),
                "quantity": [10.0],
            }
        )
        (product, date, quantity), = db.sales_rows(frame)
        assert product == "plain"
        assert date == datetime.date(2026, 7, 5)
        assert isinstance(quantity, float) and quantity == 10.0

    def test_empty_frame_maps_to_no_rows(self):
        frame = pd.DataFrame(columns=["product", "date", "quantity"])
        assert db.sales_rows(frame) == []


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run DB integration tests",
)
class TestAgainstPostgres:
    @pytest.fixture()
    def conn(self):
        with psycopg.connect(TEST_DATABASE_URL) as c:
            db.apply_schema(c)
            c.execute("TRUNCATE raw_toast_responses, sales")
            c.commit()
            yield c

    def test_apply_schema_is_idempotent(self, conn):
        # The fixture already applied it once; a second apply must not raise.
        db.apply_schema(conn)
        db.apply_schema(conn)

    def test_repeat_write_of_a_day_replaces_the_row(self, conn):
        # The ticket's demoable: write the same (product, date) twice with
        # different quantities, read back one row carrying the second quantity.
        first = pd.DataFrame(
            {"product": ["plain"], "date": pd.to_datetime(["2026-07-05"]), "quantity": [10.0]}
        )
        second = pd.DataFrame(
            {"product": ["plain"], "date": pd.to_datetime(["2026-07-05"]), "quantity": [17.0]}
        )
        db.upsert_sales(conn, first)
        db.upsert_sales(conn, second)

        result = db.read_sales(conn)
        assert len(result) == 1
        assert result["quantity"].iloc[0] == 17.0

    def test_read_sales_matches_the_loader_shape(self, conn):
        frame = pd.DataFrame(
            {
                "product": ["sesame", "plain"],
                "date": pd.to_datetime(["2026-07-06", "2026-07-05"]),
                "quantity": [5.0, 10.0],
            }
        )
        db.upsert_sales(conn, frame)

        result = db.read_sales(conn)
        assert list(result.columns) == ["product", "date", "quantity"]
        assert str(result["date"].dtype) == "datetime64[ns]"
        assert result["quantity"].dtype == float
        # sorted by (date, product)
        assert list(result["product"]) == ["plain", "sesame"]

    def test_raw_response_round_trips_as_jsonb(self, conn):
        payload = [
            {"businessDate": "20260705", "modifierName": "plain bagel", "quantitySold": 3}
        ]
        db.insert_raw_response(conn, "28e5b269-1c1c-45df-81a8-1d268c005dfa", "2026-07-05", payload)

        saved = db.read_raw_responses(conn)
        assert saved == [payload]
        # and re-normalization can run off it without contacting Toast
        validate_modifier_rows(saved[0])

    def test_read_raw_responses_filters_by_business_date(self, conn):
        db.insert_raw_response(conn, "r1", "2026-07-05", [{"a": 1}])
        db.insert_raw_response(conn, "r1", "2026-07-06", [{"b": 2}])

        assert db.read_raw_responses(conn, business_date="2026-07-05") == [[{"a": 1}]]
