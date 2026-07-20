"""The Postgres access seam (ADR 0003, ADR 0005, ticket 02); ticket 01 of the
daily-forecast-log effort adds the config-driven, write-once Demand Forecast log
schema (ADR 0006), and ticket 04 the reader and writer that own it.

Two layers:

- Unit tests that need no database --- the connection-string contract and the
  fact and forecast-log frame-to-rows mappings.
- Integration tests that exercise a real Postgres, gated behind
  `TEST_DATABASE_URL`. They TRUNCATE the schema's tables, so they run against a
  throwaway test database, never `DATABASE_URL`; when the variable is unset they
  skip, and the suite still passes on a dev-only, database-less install.
"""
import datetime
import json
import os

import pandas as pd
import psycopg
import pytest

import db
from normalize import validate_modifier_rows

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CAMBRIDGE = "28e5b269-1c1c-45df-81a8-1d268c005dfa"
BROOKLINE = "9ae70079-b9cd-4b92-8457-c86bc823188f"


def log_row(as_of, config_version, model, target, target_date, quantity):
    """One Demand Forecast log row as the single-row frame insert_forecasts
    takes --- the shape forecast_engine.run_forecasts returns."""
    return pd.DataFrame(
        {
            "as_of": [as_of],
            "config_version": [config_version],
            "model": [model],
            "target": [target],
            "target_date": [target_date],
            "forecast_quantity": [quantity],
        }
    )


def fact(date, restaurant_guid, source_name, quantity, source_type="modifier"):
    """One Sales fact row as the single-row frame upsert_sales takes."""
    return pd.DataFrame(
        {
            "date": pd.to_datetime([date]),
            "restaurant_guid": [restaurant_guid],
            "source_type": [source_type],
            "source_name": [source_name],
            "quantity": [quantity],
        }
    )


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
    """The fact frame-to-rows mapping upsert_sales feeds Postgres."""

    def test_maps_to_python_typed_tuples(self):
        frame = fact("2026-07-05", CAMBRIDGE, "plain bagel", 10.0)
        (date, restaurant_guid, source_type, source_name, quantity), = db.sales_rows(frame)
        assert date == datetime.date(2026, 7, 5)
        assert restaurant_guid == CAMBRIDGE
        assert source_type == "modifier"
        assert source_name == "plain bagel"
        assert isinstance(quantity, float) and quantity == 10.0

    def test_empty_frame_maps_to_no_rows(self):
        frame = pd.DataFrame(
            columns=["date", "restaurant_guid", "source_type", "source_name", "quantity"]
        )
        assert db.sales_rows(frame) == []


class TestForecastRows:
    """The log frame-to-rows mapping insert_forecasts feeds Postgres."""

    def test_the_columns_match_what_the_engine_returns(self):
        # db.py spells the log's columns itself rather than importing the
        # engine; this is what keeps the two spellings from drifting apart and
        # breaking the daily job silently. forecast_engine imports statsmodels
        # lazily, so this needs no ETS install.
        import forecast_engine

        assert list(db.FORECAST_COLUMNS) == forecast_engine.LOG_COLUMNS

    def test_maps_to_python_typed_tuples(self):
        # run_forecasts returns python dates and a numpy float; Postgres wants
        # neither pandas Timestamps nor numpy scalars.
        frame = log_row(
            datetime.date(2026, 7, 5), 3, "ewma", "wheat_bagels",
            datetime.date(2026, 7, 8), 42.5,
        )
        (as_of, version, model, target, target_date, quantity), = db.forecast_rows(frame)
        assert as_of == datetime.date(2026, 7, 5)
        assert isinstance(version, int) and version == 3
        assert model == "ewma"
        assert target == "wheat_bagels"
        assert target_date == datetime.date(2026, 7, 8)
        assert isinstance(quantity, float) and quantity == 42.5

    def test_empty_frame_maps_to_no_rows(self):
        assert db.forecast_rows(pd.DataFrame(columns=db.FORECAST_COLUMNS)) == []


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run DB integration tests",
)
class TestAgainstPostgres:
    @pytest.fixture()
    def conn(self):
        with psycopg.connect(TEST_DATABASE_URL) as c:
            db.apply_schema(c)
            c.execute(
                "TRUNCATE raw_toast_responses, sales, product_sources, products, "
                "forecasts, forecast_configs"
            )
            c.commit()
            yield c

    def test_apply_schema_is_idempotent(self, conn):
        # The fixture already applied it once; a second apply must not raise.
        db.apply_schema(conn)
        db.apply_schema(conn)

    def test_repeat_write_of_a_fact_row_replaces_it(self, conn):
        # The ticket's demoable: write the same (date, restaurant, source) twice
        # with different quantities, read back one fact row carrying the second.
        db.upsert_product_sources(conn, {"plain": [("modifier", "plain bagel")]})
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "plain bagel", 10.0))
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "plain bagel", 17.0))

        result = db.read_sales(conn)
        assert len(result) == 1
        assert result["quantity"].iloc[0] == 17.0

    def test_view_sums_across_a_products_sources(self, conn):
        # The ticket's demoable: two different sources that map to the same
        # Product on the same date read back through the view as one summed row.
        db.upsert_product_sources(
            conn,
            {"plain": [("modifier", "plain bagel"), ("modifier", "plain, bulk")]},
        )
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "plain bagel", 10.0))
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "plain, bulk", 4.0))

        result = db.read_sales(conn)
        assert len(result) == 1
        assert result["product"].iloc[0] == "plain"
        assert result["quantity"].iloc[0] == 14.0

    def test_view_sums_across_locations(self, conn):
        # A Product's daily Sales is summed across both locations (CONTEXT.md).
        db.upsert_product_sources(conn, {"sesame": [("modifier", "sesame bagel")]})
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "sesame bagel", 6.0))
        db.upsert_sales(conn, fact("2026-07-05", BROOKLINE, "sesame bagel", 9.0))

        result = db.read_sales(conn)
        assert len(result) == 1
        assert result["quantity"].iloc[0] == 15.0

    def test_unmapped_source_sits_in_the_fact_but_not_the_view(self, conn):
        # ADR 0005: the fact keeps every configured source; the view (inner join)
        # shows only mapped ones.
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "rainbow bagel", 3.0))

        assert db.read_sales(conn).empty
        in_fact = conn.execute("SELECT count(*) FROM sales").fetchone()[0]
        assert in_fact == 1

    def test_read_sales_matches_the_loader_shape(self, conn):
        db.upsert_product_sources(
            conn,
            {
                "plain": [("modifier", "plain bagel")],
                "sesame": [("modifier", "sesame bagel")],
            },
        )
        db.upsert_sales(conn, fact("2026-07-06", CAMBRIDGE, "sesame bagel", 5.0))
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "plain bagel", 10.0))

        result = db.read_sales(conn)
        assert list(result.columns) == ["product", "date", "quantity"]
        assert str(result["date"].dtype) == "datetime64[ns]"
        assert result["quantity"].dtype == float
        # sorted by (date, product)
        assert list(result["product"]) == ["plain", "sesame"]

    def test_upsert_product_sources_is_idempotent_and_repoints(self, conn):
        # Re-running the seed adds no duplicate sources, and a source can be
        # moved from one Product to another by re-seeding (ADR 0005).
        db.upsert_product_sources(conn, {"plain": [("modifier", "plain bagel")]})
        db.upsert_product_sources(conn, {"plain": [("modifier", "plain bagel")]})
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM product_sources").fetchone()[0] == 1

        db.upsert_product_sources(conn, {"everything": [("modifier", "plain bagel")]})
        db.upsert_sales(conn, fact("2026-07-05", CAMBRIDGE, "plain bagel", 8.0))
        result = db.read_sales(conn)
        assert list(result["product"]) == ["everything"]

    def test_raw_response_round_trips_as_jsonb(self, conn):
        payload = [
            {
                "businessDate": "20260705",
                "modifierName": "plain bagel",
                "quantitySold": 3,
                "restaurantGuid": CAMBRIDGE,
                "modifierGuid": "g1",
            }
        ]
        db.insert_raw_response(conn, CAMBRIDGE, "2026-07-05", payload)

        saved = db.read_raw_responses(conn)
        assert saved == [payload]
        # and re-normalization can run off it without contacting Toast
        validate_modifier_rows(saved[0])

    def test_read_raw_responses_filters_by_business_date(self, conn):
        db.insert_raw_response(conn, "r1", "2026-07-05", [{"a": 1}])
        db.insert_raw_response(conn, "r1", "2026-07-06", [{"b": 2}])

        assert db.read_raw_responses(conn, business_date="2026-07-05") == [[{"a": 1}]]

    def test_bulk_upsert_sales_replaces_on_repeat_key(self, conn):
        # The bulk path (COPY into staging + one ON CONFLICT upsert) keeps the
        # same replace-on-repeat semantics as the row-at-a-time upsert_sales.
        db.upsert_product_sources(conn, {"plain": [("modifier", "plain bagel")]})
        rows = [(datetime.date(2026, 7, 5), CAMBRIDGE, "modifier", "plain bagel", 10.0)]
        db.bulk_upsert_sales(conn, rows)
        conn.commit()
        db.bulk_upsert_sales(
            conn, [(datetime.date(2026, 7, 5), CAMBRIDGE, "modifier", "plain bagel", 17.0)]
        )
        conn.commit()

        result = db.read_sales(conn)
        assert len(result) == 1
        assert result["quantity"].iloc[0] == 17.0

    def test_bulk_insert_raw_responses_batches_and_dedupes(self, conn):
        fetched = datetime.datetime(2026, 7, 10, tzinfo=datetime.timezone.utc)
        shards = [
            (CAMBRIDGE, datetime.date(2026, 7, 5), fetched, [{"a": 1}]),
            (BROOKLINE, datetime.date(2026, 7, 5), fetched, [{"b": 2}]),
        ]
        assert db.bulk_insert_raw_responses(conn, shards, batch_size=1) == 2
        conn.commit()
        # Re-inserting the same captures is a no-op (ON CONFLICT DO NOTHING).
        db.bulk_insert_raw_responses(conn, shards)
        conn.commit()
        assert conn.execute("SELECT count(*) FROM raw_toast_responses").fetchone()[0] == 2

    # --- The config-driven, write-once Demand Forecast log (ADR 0006) -------
    # These exercise the DDL contract itself with raw SQL; the db.py reader and
    # writer that own these tables are covered further down.

    def _insert_config(self, conn, config, is_active=True):
        """Insert one forecast_configs row and return its generated version."""
        return conn.execute(
            "INSERT INTO forecast_configs (is_active, config) VALUES (%s, %s) "
            "RETURNING version",
            (is_active, json.dumps(config)),
        ).fetchone()[0]

    def _insert_forecast(self, conn, key, quantity, on_conflict_do_nothing=False):
        """Insert one forecasts row from a full-key tuple `(as_of, config_version,
        model, target, target_date)`, optionally with the write-once ON CONFLICT
        DO NOTHING clause the daily writer uses."""
        conflict = (
            " ON CONFLICT (as_of, config_version, model, target, target_date) "
            "DO NOTHING"
            if on_conflict_do_nothing
            else ""
        )
        conn.execute(
            "INSERT INTO forecasts "
            "(as_of, config_version, model, target, target_date, forecast_quantity) "
            "VALUES (%s, %s, %s, %s, %s, %s)" + conflict,
            (*key, quantity),
        )

    def test_forecast_config_round_trips_as_jsonb(self, conn):
        config = {
            "horizon_days": 14,
            "models": {"ewma": {"halflife_weeks": 3}, "holt_winters": {}},
            "targets": {"wheat_bagels": ["everything", "plain", "sesame"]},
        }
        version = self._insert_config(conn, config)
        stored = conn.execute(
            "SELECT config FROM forecast_configs WHERE version = %s", (version,)
        ).fetchone()[0]
        assert stored == config

    def test_forecasts_write_once_conflicts_on_the_key(self, conn):
        # The write-once contract: a same-key re-insert (a same-morning retry)
        # is dropped by ON CONFLICT DO NOTHING, keeping the first-logged value.
        version = self._insert_config(conn, {"horizon_days": 1})
        key = (datetime.date(2026, 7, 5), version, "ewma", "wheat_bagels",
               datetime.date(2026, 7, 6))
        self._insert_forecast(conn, key, 42.0, on_conflict_do_nothing=True)
        self._insert_forecast(conn, key, 99.0, on_conflict_do_nothing=True)
        conn.commit()

        rows = conn.execute(
            "SELECT forecast_quantity FROM forecasts"
        ).fetchall()
        assert rows == [(42.0,)]

    def test_forecast_rows_reference_a_config_version(self, conn):
        # config_version is a foreign key: a forecast can't point at a
        # configuration that was never recorded.
        missing_version = (datetime.date(2026, 7, 5), 9999, "ewma", "wheat_bagels",
                           datetime.date(2026, 7, 6))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            self._insert_forecast(conn, missing_version, 42.0)

    def test_new_tables_have_rls_enabled_and_no_policies(self, conn):
        # Private, as the rest of the schema is: RLS on, no policies, so the
        # Data API's anon/authenticated roles get no access to forecast data.
        for table in ("forecast_configs", "forecasts"):
            rls_on = conn.execute(
                "SELECT relrowsecurity FROM pg_class WHERE relname = %s", (table,)
            ).fetchone()[0]
            assert rls_on, f"{table} should have RLS enabled"
            policies = conn.execute(
                "SELECT count(*) FROM pg_policies WHERE tablename = %s", (table,)
            ).fetchone()[0]
            assert policies == 0, f"{table} should have no policies"

    # --- The db.py reader and writer that own those tables (ticket 04) ------

    def test_read_active_config_round_trips_the_document(self, conn):
        config = {
            "horizon_days": 14,
            "models": {"ewma": {"halflife_weeks": 3}, "holt_winters": {}},
            "targets": {"wheat_bagels": ["everything", "plain", "sesame"]},
        }
        version = self._insert_config(conn, config)
        conn.commit()

        active = db.read_active_config(conn)
        # The version is stamped into the document, so what comes back is
        # exactly what run_forecasts takes — it reads config["version"].
        assert active == {**config, "version": version}

    def test_read_active_config_ignores_inactive_versions(self, conn):
        self._insert_config(conn, {"horizon_days": 1}, is_active=False)
        active_version = self._insert_config(conn, {"horizon_days": 7})
        conn.commit()

        assert db.read_active_config(conn)["version"] == active_version

    def test_read_active_config_takes_the_newest_of_several_active(self, conn):
        # Nothing in the schema stops two rows being active at once; the reader
        # settles it rather than leaving the morning's run non-deterministic.
        self._insert_config(conn, {"horizon_days": 1})
        newest = self._insert_config(conn, {"horizon_days": 7})
        conn.commit()

        assert db.read_active_config(conn)["version"] == newest

    def test_read_active_config_raises_when_none_is_active(self, conn):
        self._insert_config(conn, {"horizon_days": 1}, is_active=False)
        conn.commit()

        with pytest.raises(RuntimeError, match="forecast_configs"):
            db.read_active_config(conn)

    def test_insert_forecasts_writes_the_rows_and_counts_them(self, conn):
        version = self._insert_config(conn, {"horizon_days": 1})
        conn.commit()
        frame = pd.concat([
            log_row(datetime.date(2026, 7, 5), version, "ewma", "wheat_bagels",
                    datetime.date(2026, 7, 6), 42.0),
            log_row(datetime.date(2026, 7, 5), version, "holt_winters",
                    "wheat_bagels", datetime.date(2026, 7, 6), 44.0),
        ], ignore_index=True)

        assert db.insert_forecasts(conn, frame) == 2
        rows = conn.execute(
            "SELECT model, forecast_quantity FROM forecasts ORDER BY model"
        ).fetchall()
        assert rows == [("ewma", 42.0), ("holt_winters", 44.0)]

    def test_insert_forecasts_does_not_overwrite_a_logged_forecast(self, conn):
        # The write-once contract, through the writer: a same-morning retry
        # keeps the first-logged quantity and reports it wrote nothing new.
        version = self._insert_config(conn, {"horizon_days": 1})
        conn.commit()
        key = (datetime.date(2026, 7, 5), version, "ewma", "wheat_bagels",
               datetime.date(2026, 7, 6))

        db.insert_forecasts(conn, log_row(*key, 42.0))
        assert db.insert_forecasts(conn, log_row(*key, 99.0)) == 0

        assert conn.execute(
            "SELECT forecast_quantity FROM forecasts"
        ).fetchall() == [(42.0,)]

    def test_insert_forecasts_fills_the_gaps_a_failed_run_left(self, conn):
        # Half the morning's rows landed before the run died; the retry writes
        # only the missing one and leaves the already-logged one alone.
        version = self._insert_config(conn, {"horizon_days": 2})
        conn.commit()
        first = log_row(datetime.date(2026, 7, 5), version, "ewma",
                        "wheat_bagels", datetime.date(2026, 7, 6), 42.0)
        second = log_row(datetime.date(2026, 7, 5), version, "ewma",
                         "wheat_bagels", datetime.date(2026, 7, 7), 50.0)
        db.insert_forecasts(conn, first)

        retry = pd.concat([first, second], ignore_index=True)
        assert db.insert_forecasts(conn, retry) == 1
        assert conn.execute(
            "SELECT forecast_quantity FROM forecasts ORDER BY target_date"
        ).fetchall() == [(42.0,), (50.0,)]

    def test_insert_forecasts_adds_a_row_under_a_new_config_version(self, conn):
        # A config change adds rows rather than clobbering: the old version's
        # logged forecast stays exactly as it was recorded.
        old = self._insert_config(conn, {"horizon_days": 1}, is_active=False)
        new = self._insert_config(conn, {"horizon_days": 2})
        conn.commit()
        as_of, target_date = datetime.date(2026, 7, 5), datetime.date(2026, 7, 6)

        db.insert_forecasts(
            conn, log_row(as_of, old, "ewma", "wheat_bagels", target_date, 42.0)
        )
        assert db.insert_forecasts(
            conn, log_row(as_of, new, "ewma", "wheat_bagels", target_date, 99.0)
        ) == 1

        assert conn.execute(
            "SELECT config_version, forecast_quantity FROM forecasts "
            "ORDER BY config_version"
        ).fetchall() == [(old, 42.0), (new, 99.0)]

    def test_insert_forecasts_of_an_empty_frame_writes_nothing(self, conn):
        assert db.insert_forecasts(conn, pd.DataFrame(columns=db.FORECAST_COLUMNS)) == 0
        assert conn.execute("SELECT count(*) FROM forecasts").fetchone()[0] == 0

    def test_insert_forecasts_commits(self, conn):
        # The write survives the connection, like the other writers': the
        # scheduled job's rows must not depend on a later commit.
        version = self._insert_config(conn, {"horizon_days": 1})
        conn.commit()
        db.insert_forecasts(conn, log_row(
            datetime.date(2026, 7, 5), version, "ewma", "wheat_bagels",
            datetime.date(2026, 7, 6), 42.0,
        ))

        with psycopg.connect(TEST_DATABASE_URL) as other:
            assert other.execute(
                "SELECT count(*) FROM forecasts"
            ).fetchone()[0] == 1
