"""The one-time history migration (ticket 03).

Two layers, mirroring test_db.py:

- Unit tests that need no database --- the pure builders that turn the saved raw
  files into raw shards, the Product-map seed and the canonical fact.
- Integration tests against a real Postgres, gated behind `TEST_DATABASE_URL`.
  They TRUNCATE the schema's tables, so they run against a throwaway database,
  never `DATABASE_URL`; unset, they skip and the suite still passes database-less.
"""
import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd
import psycopg
import pytest

import db
import migrate
import normalize

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CAMBRIDGE = "28e5b269-1c1c-45df-81a8-1d268c005dfa"
BROOKLINE = "9ae70079-b9cd-4b92-8457-c86bc823188f"
OUT_OF_SCOPE = "00000000-0000-0000-0000-000000000000"


def _row(business_date, name, qty, restaurant=CAMBRIDGE, guid="g"):
    """A raw modifier report row. Omit `guid` (None) for the free-text /
    unconfigured case normalize.py treats as not a Sale."""
    row = {
        "businessDate": business_date,
        "modifierName": name,
        "quantitySold": qty,
        "restaurantGuid": restaurant,
    }
    if guid is not None:
        row["modifierGuid"] = guid
    return row


@pytest.fixture()
def raw_dir(tmp_path):
    """A tiny raw history: one menu_week file (authoritative for its week) and
    one orders_agg file whose in-week date is superseded by the week report and
    whose out-of-week date is not."""
    week = [
        _row("20240103", "Plain Bagel", 5, guid="g1"),
        _row("20240103", "plain bagel", 3, guid="g2"),  # same name, other GUID
        _row("20240103", "Light schmear", 2, guid=None),  # unconfigured
        _row("20240103", "plain bagel", 99, restaurant=OUT_OF_SCOPE, guid="g3"),
        _row("20240103", "everything bagel", 4, restaurant=BROOKLINE, guid="g4"),
        _row("20240103", "hot sauce", 7, guid="g5"),  # configured non-bagel
    ]
    orders = [
        _row("20240103", "plain bagel", 100, guid="g6"),  # covered by the week
        _row("20240110", "sesame bagel", 6, guid="g7"),  # not covered
    ]
    (tmp_path / "menu_week_20240101_20240107__20240108T000000Z.json").write_text(
        json.dumps(week)
    )
    (tmp_path / "orders_agg_202401__20240201T000000Z.json").write_text(
        json.dumps(orders)
    )
    return tmp_path


class TestCaptureTime:
    def test_parses_menu_week_stamp_as_utc(self):
        got = migrate.capture_time(
            Path("data/raw/menu_week_20260704_20260709__20260710T083815Z.json")
        )
        assert got == dt.datetime(2026, 7, 10, 8, 38, 15, tzinfo=dt.timezone.utc)

    def test_parses_orders_agg_stamp(self):
        got = migrate.capture_time(
            Path("data/raw/orders_agg_201607__20260710T210534Z.json")
        )
        assert got == dt.datetime(2026, 7, 10, 21, 5, 34, tzinfo=dt.timezone.utc)


class TestProductSourceSeed:
    def test_reproduces_bagel_modifier_names_normalized(self):
        seed = migrate.product_source_seed()
        assert set(seed) == set(normalize.BAGEL_MODIFIER_NAMES)
        for product, names in normalize.BAGEL_MODIFIER_NAMES.items():
            assert seed[product] == [
                ("modifier", name.strip().lower()) for name in names
            ]

    def test_every_source_is_a_modifier(self):
        seed = migrate.product_source_seed()
        assert all(
            source_type == "modifier"
            for sources in seed.values()
            for source_type, _name in sources
        )


class TestRawShards:
    def test_shards_one_row_per_restaurant_and_business_date(self, raw_dir):
        shards = migrate.raw_shards(raw_dir)
        keys = {(restaurant, business_date) for restaurant, business_date, _f, _r in shards}
        # week: Cambridge/03, out-of-scope/03, Brookline/03; orders: Cambridge/03, Cambridge/10
        assert keys == {
            (CAMBRIDGE, dt.date(2024, 1, 3)),
            (OUT_OF_SCOPE, dt.date(2024, 1, 3)),
            (BROOKLINE, dt.date(2024, 1, 3)),
            (CAMBRIDGE, dt.date(2024, 1, 10)),
        }
        assert len(shards) == 5  # the Cambridge/03 key appears in both files

    def test_capture_time_comes_from_the_filename(self, raw_dir):
        by_source = {}
        for restaurant, business_date, fetched_at, _rows in migrate.raw_shards(raw_dir):
            by_source[(restaurant, business_date)] = fetched_at
        assert by_source[(CAMBRIDGE, dt.date(2024, 1, 10))] == dt.datetime(
            2024, 2, 1, tzinfo=dt.timezone.utc
        )
        assert by_source[(BROOKLINE, dt.date(2024, 1, 3))] == dt.datetime(
            2024, 1, 8, tzinfo=dt.timezone.utc
        )

    def test_rows_are_stored_verbatim(self, raw_dir):
        cambridge_week = next(
            rows
            for restaurant, business_date, fetched_at, rows in migrate.raw_shards(raw_dir)
            if restaurant == CAMBRIDGE
            and business_date == dt.date(2024, 1, 3)
            and fetched_at == dt.datetime(2024, 1, 8, tzinfo=dt.timezone.utc)
        )
        # every Cambridge row from the week file, including the unconfigured one,
        # verbatim so a day can be re-normalized on its own
        names = sorted(r["modifierName"] for r in cambridge_week)
        assert names == ["Light schmear", "Plain Bagel", "hot sauce", "plain bagel"]


class TestFactRows:
    def _fact(self, raw_dir):
        return {
            (date, restaurant, name): quantity
            for date, restaurant, _source_type, name, quantity in migrate.fact_rows(
                raw_dir
            )
        }

    def test_sums_across_guids_for_one_normalized_name(self, raw_dir):
        # "Plain Bagel" (5) + "plain bagel" (3) under two GUIDs -> 8 at one key
        fact = self._fact(raw_dir)
        assert fact[(dt.date(2024, 1, 3), CAMBRIDGE, "plain bagel")] == 8.0

    def test_source_type_is_modifier_and_quantity_is_float(self, raw_dir):
        for date, restaurant, source_type, name, quantity in migrate.fact_rows(raw_dir):
            assert source_type == "modifier"
            assert isinstance(quantity, float)

    def test_excludes_unconfigured_modifiers(self, raw_dir):
        fact = self._fact(raw_dir)
        assert (dt.date(2024, 1, 3), CAMBRIDGE, "light schmear") not in fact

    def test_excludes_out_of_scope_restaurants(self, raw_dir):
        fact = self._fact(raw_dir)
        assert (dt.date(2024, 1, 3), OUT_OF_SCOPE, "plain bagel") not in fact

    def test_keeps_configured_non_bagel_modifiers(self, raw_dir):
        # ADR 0005: the fact holds every configured source, not just the bagels
        fact = self._fact(raw_dir)
        assert fact[(dt.date(2024, 1, 3), CAMBRIDGE, "hot sauce")] == 7.0

    def test_orders_date_covered_by_a_week_report_is_dropped(self, raw_dir):
        # the orders_agg "plain bagel" 100 on the in-week date must not be added
        fact = self._fact(raw_dir)
        assert fact[(dt.date(2024, 1, 3), CAMBRIDGE, "plain bagel")] == 8.0

    def test_orders_date_not_covered_is_kept(self, raw_dir):
        fact = self._fact(raw_dir)
        assert fact[(dt.date(2024, 1, 10), CAMBRIDGE, "sesame bagel")] == 6.0


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
            c.commit()
            yield c

    def test_loads_raw_shards_products_and_the_full_fact(self, conn, raw_dir):
        counts = migrate.run_migration(conn, raw_dir)
        assert counts["raw_inserted"] == 5
        assert counts["products"] == len(normalize.BAGEL_MODIFIER_NAMES)

        raw = conn.execute("SELECT count(*) FROM raw_toast_responses").fetchone()[0]
        assert raw == 5
        products = conn.execute("SELECT count(*) FROM products").fetchone()[0]
        assert products == len(normalize.BAGEL_MODIFIER_NAMES)
        # the fact holds every configured modifier, including the non-bagel one
        in_fact = conn.execute(
            "SELECT count(*) FROM sales WHERE source_name = 'hot sauce'"
        ).fetchone()[0]
        assert in_fact == 1

    def test_view_rolls_the_fact_up_through_the_map(self, conn, raw_dir):
        migrate.run_migration(conn, raw_dir)
        view = db.read_sales(conn)
        plain = view[
            (view["product"] == "plain") & (view["date"] == pd.Timestamp("2024-01-03"))
        ]
        assert len(plain) == 1
        assert plain["quantity"].iloc[0] == 8.0
        # the configured non-bagel modifier is unmapped, so absent from the view
        assert "hot sauce" not in set(view["product"])

    def test_rerun_changes_nothing(self, conn, raw_dir):
        migrate.run_migration(conn, raw_dir)
        before = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("raw_toast_responses", "sales", "product_sources", "products")
        }
        migrate.run_migration(conn, raw_dir)
        after = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("raw_toast_responses", "sales", "product_sources", "products")
        }
        assert before == after

    def test_full_history_view_matches_regenerated_parquet(self, conn):
        # The ticket's bar, end to end against the real raw history. Requires
        # sales_history.parquet to be current (run `python normalize.py` first).
        if not migrate.canonical_files():
            pytest.skip("no raw history checked out")
        migrate.run_migration(conn)
        report = migrate.compare_to_parquet(conn)
        assert report["matches"], report
