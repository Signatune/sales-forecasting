"""The single Sales-history read seam.

Since ticket 04 it reads canonical Sales from the Postgres `product_sales` view
(ADR 0003, ADR 0005), not the parquet file — so the tests exercise it against a
real database rather than mocking `db` away. They are gated on
`TEST_DATABASE_URL` (a throwaway Postgres; they TRUNCATE its tables) exactly like
the `test_db.py` integration suite, and skip when it is unset.

The two failure-mode tests need no database: a missing `DATABASE_URL` and an
unreachable host must both fail with a clear message, not a confusing pandas
error from a downstream reader.
"""
import os

import pandas as pd
import psycopg
import pytest

import db
import sales_history

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CAMBRIDGE = "28e5b269-1c1c-45df-81a8-1d268c005dfa"
BROOKLINE = "9ae70079-b9cd-4b92-8457-c86bc823188f"


def _fact(date, restaurant_guid, source_name, quantity):
    return pd.DataFrame(
        {
            "date": pd.to_datetime([date]),
            "restaurant_guid": [restaurant_guid],
            "source_type": ["modifier"],
            "source_name": [source_name],
            "quantity": [quantity],
        }
    )


class TestClearErrorWithoutADatabase:
    """A reader that calls the loader must get an actionable message, not a
    pandas/psycopg stack trace, when the database is missing or unreachable."""

    def test_missing_database_url_raises_naming_the_variable(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            sales_history.load_sales_history()

    def test_unreachable_database_raises_pointing_at_the_docs(self, monkeypatch):
        # A refused connection (dead port) must surface as a clear RuntimeError
        # pointing at the setup docs, not psycopg's raw OperationalError.
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://u:p@127.0.0.1:1/postgres"
        )
        with pytest.raises(RuntimeError, match="docs/postgres.md"):
            sales_history.load_sales_history()


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run DB integration tests",
)
class TestLoadsFromPostgres:
    @pytest.fixture()
    def conn(self):
        with psycopg.connect(TEST_DATABASE_URL) as c:
            db.apply_schema(c)
            c.execute("TRUNCATE raw_toast_responses, sales, product_sources, products")
            c.commit()
            yield c

    def test_loads_the_product_sales_view_as_the_canonical_frame(
        self, conn, monkeypatch
    ):
        # Two sources of one Product across both locations roll up through the
        # view into the (product, date, quantity) frame the readers consume.
        db.upsert_product_sources(
            conn, {"plain": [("modifier", "plain bagel"), ("modifier", "plain, bulk")]}
        )
        db.upsert_sales(conn, _fact("2026-07-05", CAMBRIDGE, "plain bagel", 10.0))
        db.upsert_sales(conn, _fact("2026-07-05", BROOKLINE, "plain, bulk", 4.0))

        # The loader points at DATABASE_URL; aim it at the throwaway DB and run
        # the real end-to-end path (no db stub).
        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        loaded = sales_history.load_sales_history()

        assert list(loaded.columns) == ["product", "date", "quantity"]
        assert len(loaded) == 1
        assert loaded["product"].iloc[0] == "plain"
        assert loaded["quantity"].iloc[0] == 14.0

    def test_date_dtype_is_nanosecond_no_regression(self, conn, monkeypatch):
        # The rest of the project pins Demand Forecast dates to datetime64[ns]
        # (forecast.py) and merges against this frame; a coarser resolution from
        # Postgres would be a dtype regression.
        db.upsert_product_sources(conn, {"sesame": [("modifier", "sesame bagel")]})
        db.upsert_sales(conn, _fact("2026-07-06", CAMBRIDGE, "sesame bagel", 5.0))

        monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
        loaded = sales_history.load_sales_history()

        assert str(loaded["date"].dtype) == "datetime64[ns]"
        assert loaded["quantity"].dtype == float
