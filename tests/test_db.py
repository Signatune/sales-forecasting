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


class TestPostgresWeekday:
    """The python-date-to-Postgres-DOW mapping `read_due_reports` filters on.
    `report_configs.days_of_week` is stored in Postgres' convention (0 = Sunday)
    and python's `date.weekday()` is Monday-based, so the two are off by one and
    wrap differently --- worth pinning without a database."""

    def test_sunday_is_zero_and_saturday_is_six(self):
        # 2026-07-26 is a Sunday, 2026-08-01 a Saturday.
        assert db.postgres_weekday(datetime.date(2026, 7, 26)) == 0
        assert db.postgres_weekday(datetime.date(2026, 8, 1)) == 6

    def test_every_weekday_maps_once(self):
        week = [datetime.date(2026, 7, 26) + datetime.timedelta(days=n) for n in range(7)]
        assert [db.postgres_weekday(day) for day in week] == [0, 1, 2, 3, 4, 5, 6]


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


class TestMigrationFiles:
    """Reading `migrations/` — the half of the runner that needs no database."""

    def test_the_repo_has_a_readable_baseline(self):
        versions = [version for version, _ in db.migration_files()]
        assert versions[0] == "0001"
        # Ordered and unique is the whole contract; asserting the rest of the
        # list would just restate whatever migrations exist this week.
        assert versions == sorted(set(versions))

    def test_ordering_is_numeric_and_not_alphabetical(self, tmp_path):
        for name in ("0002-b.sql", "0010-c.sql", "0009-a.sql"):
            (tmp_path / name).write_text("SELECT 1;")
        assert [v for v, _ in db.migration_files(tmp_path)] == ["0002", "0009", "0010"]

    def test_a_file_that_is_not_a_migration_raises_rather_than_being_skipped(
        self, tmp_path
    ):
        # The failure this prevents: a migration that looks committed, never
        # runs, and is noticed only when a later one fails.
        (tmp_path / "add_column.sql").write_text("SELECT 1;")
        with pytest.raises(RuntimeError, match="not a migration filename"):
            db.migration_files(tmp_path)

    def test_two_migrations_sharing_a_version_raise(self, tmp_path):
        # Two PRs each adding 0002 merge cleanly in git; one would otherwise be
        # left permanently unapplied.
        (tmp_path / "0002-one.sql").write_text("SELECT 1;")
        (tmp_path / "0002-two.sql").write_text("SELECT 1;")
        with pytest.raises(RuntimeError, match="share version 0002"):
            db.migration_files(tmp_path)


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run DB integration tests",
)
class TestMigrationRunner:
    """Applying migrations against a real Postgres (ADR 0015).

    Its own connection rather than the shared `conn` fixture: these tests write
    to `schema_migrations` and create and drop tables of their own, which the
    truncating fixture neither expects nor cleans up.
    """

    @pytest.fixture()
    def conn(self):
        with psycopg.connect(TEST_DATABASE_URL) as c:
            db.apply_migrations(c)
            yield c
            c.rollback()
            c.execute("DROP TABLE IF EXISTS runner_scratch")
            c.execute("DELETE FROM schema_migrations WHERE version <> '0001'")
            c.commit()

    @pytest.fixture()
    def migrations(self, tmp_path):
        """A migration directory the tests own, holding a copy of the real
        baseline so the runner does not see 0001 as applied-but-missing."""
        baseline = dict(db.migration_files())["0001"]
        (tmp_path / baseline.name).write_bytes(baseline.read_bytes())
        return tmp_path

    def test_a_pending_migration_applies_and_is_recorded(self, conn, migrations):
        (migrations / "0002-runner-scratch.sql").write_text(
            "CREATE TABLE runner_scratch (note text NOT NULL);"
        )

        assert db.apply_migrations(conn, migrations) == ["0002"]

        recorded = conn.execute(
            "SELECT filename FROM schema_migrations WHERE version = '0002'"
        ).fetchone()
        assert recorded == ("0002-runner-scratch.sql",)
        assert conn.execute(
            "SELECT to_regclass('public.runner_scratch') IS NOT NULL"
        ).fetchone() == (True,)

    def test_applying_twice_runs_it_once(self, conn, migrations):
        (migrations / "0002-runner-scratch.sql").write_text(
            "CREATE TABLE runner_scratch (note text NOT NULL);"
        )
        db.apply_migrations(conn, migrations)

        # Not merely "does not raise": a second CREATE TABLE would, so the
        # empty list is the evidence the file was not re-run.
        assert db.apply_migrations(conn, migrations) == []

    def test_a_migration_that_fails_partway_leaves_nothing_behind(
        self, conn, migrations
    ):
        # The first statement is valid and the second is not, so anything less
        # than a per-file transaction would leave runner_scratch created and
        # 0002 unrecorded --- the exact state that makes a retry impossible.
        (migrations / "0002-half-broken.sql").write_text(
            "CREATE TABLE runner_scratch (note text NOT NULL);\n"
            "CREATE TABLE runner_scratch (note text NOT NULL);"
        )

        with pytest.raises(psycopg.errors.DuplicateTable):
            db.apply_migrations(conn, migrations)

        assert conn.execute(
            "SELECT to_regclass('public.runner_scratch') IS NULL"
        ).fetchone() == (True,)
        assert conn.execute(
            "SELECT count(*) FROM schema_migrations WHERE version = '0002'"
        ).fetchone() == (0,)

    def test_an_earlier_migration_arriving_late_is_refused(self, conn, migrations):
        # A branch adding 0002 merged after 0003 already applied: running it now
        # would apply it against a schema its author never saw.
        (migrations / "0002-late.sql").write_text("SELECT 1;")
        (migrations / "0003-early.sql").write_text("SELECT 1;")
        db.apply_migrations(conn, migrations)
        conn.execute("DELETE FROM schema_migrations WHERE version = '0002'")
        conn.commit()

        with pytest.raises(RuntimeError, match="merged out of order"):
            db.pending_migrations(conn, migrations)

    def test_a_database_ahead_of_the_checkout_is_refused(self, conn, migrations):
        conn.execute(
            "INSERT INTO schema_migrations (version, filename) "
            "VALUES ('0099', '0099-from-the-future.sql')"
        )
        conn.commit()

        with pytest.raises(RuntimeError, match="newer than this code"):
            db.pending_migrations(conn, migrations)

    def test_baseline_records_a_version_without_running_it(self, conn, migrations):
        # The live-database case: the schema is already there, so the file must
        # not run --- proven by a migration that would fail if it did.
        (migrations / "0002-would-fail.sql").write_text(
            "CREATE TABLE products (this_would_collide text);"
        )

        db.baseline(conn, "0002", migrations)

        assert db.pending_migrations(conn, migrations) == []
        assert conn.execute(
            "SELECT count(*) FROM schema_migrations WHERE version = '0002'"
        ).fetchone() == (1,)

    def test_baseline_is_repeatable(self, conn, migrations):
        (migrations / "0002-noop.sql").write_text("SELECT 1;")
        db.baseline(conn, "0002", migrations)
        db.baseline(conn, "0002", migrations)

        assert conn.execute(
            "SELECT count(*) FROM schema_migrations WHERE version = '0002'"
        ).fetchone() == (1,)


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to a throwaway Postgres to run DB integration tests",
)
class TestAgainstPostgres:
    @pytest.fixture()
    def conn(self):
        with psycopg.connect(TEST_DATABASE_URL) as c:
            db.apply_migrations(c)
            c.execute(
                # report_configs is listed because it references
                # forecast_configs: Postgres refuses to truncate the referenced
                # table unless the referencing one goes in the same statement.
                "TRUNCATE raw_toast_responses, sales, product_sources, products, "
                "forecasts, report_configs, forecast_configs"
            )
            c.commit()
            yield c

    def test_migrating_an_up_to_date_database_does_nothing(self, conn):
        # The fixture already migrated; a second run has nothing left to apply.
        assert db.apply_migrations(conn) == []

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
        for table in ("forecast_configs", "forecasts", "report_configs"):
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

    # --- Scheduled Reports as a subscription table (ADR 0010, ticket 01) -----

    # 2026-08-01 is a Saturday, so SATURDAY_DOW is what read_due_reports filters
    # on for it. Spelled as dates rather than numbers in the tests below so a
    # weekday is read off the calendar, not off an off-by-one.
    SATURDAY = datetime.date(2026, 8, 1)
    SUNDAY = datetime.date(2026, 8, 2)
    WEDNESDAY = datetime.date(2026, 8, 5)
    THURSDAY = datetime.date(2026, 8, 6)

    REPORT = {
        "name": "Bagel forecast",
        "headline_model": "ewma",
        "target": "wheat_bagels",
        "varieties": ["everything", "plain", "sesame"],
        "delivery": "bagel-team",
    }

    def _insert_report(
        self, conn, forecast_config_version, days_of_week, is_active=True, config=None
    ):
        """Insert one report_configs row and return its generated id."""
        return conn.execute(
            "INSERT INTO report_configs "
            "(forecast_config_version, days_of_week, is_active, config) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (
                forecast_config_version,
                list(days_of_week),
                is_active,
                json.dumps(self.REPORT if config is None else config),
            ),
        ).fetchone()[0]

    def test_read_due_reports_returns_the_reports_firing_today(self, conn):
        version = self._insert_config(conn, {"horizon_days": 7})
        saturday_report = self._insert_report(conn, version, [6])
        self._insert_report(conn, version, [2])  # Tuesdays only
        conn.commit()

        due = db.read_due_reports(conn, self.SATURDAY)
        assert [report["id"] for report in due] == [saturday_report]

    def test_read_due_reports_ignores_inactive_reports(self, conn):
        version = self._insert_config(conn, {"horizon_days": 7})
        # Every weekday, so only `is_active` can be what keeps it out.
        self._insert_report(conn, version, [0, 1, 2, 3, 4, 5, 6], is_active=False)
        conn.commit()

        assert db.read_due_reports(conn, self.SATURDAY) == []
        assert db.read_due_reports(conn, self.THURSDAY) == []

    def test_a_report_listing_several_weekdays_fires_on_each(self, conn):
        version = self._insert_config(conn, {"horizon_days": 7})
        report = self._insert_report(conn, version, [0, 3, 6])  # Sun, Wed, Sat
        conn.commit()

        for day in (self.SUNDAY, self.WEDNESDAY, self.SATURDAY):
            assert [r["id"] for r in db.read_due_reports(conn, day)] == [report]
        # ...and not on the days it does not list.
        assert db.read_due_reports(conn, self.THURSDAY) == []

    def test_read_due_reports_merges_the_config_document(self, conn):
        version = self._insert_config(conn, {"horizon_days": 7})
        report_id = self._insert_report(conn, version, [6])
        conn.commit()

        report, = db.read_due_reports(conn, self.SATURDAY)
        assert report == {
            **self.REPORT,
            "id": report_id,
            "forecast_config_version": version,
        }
        # jsonb round-trips the list as a list, not as a string --- the payload
        # builder iterates the varieties.
        assert report["varieties"] == ["everything", "plain", "sesame"]

    def test_the_row_columns_win_over_the_stored_document(self, conn):
        # forecast_config_version is the foreign key the report is defined by
        # (ADR 0010), so a stale copy of it inside the document must not shadow
        # the column --- the same rule read_active_config applies to `version`.
        version = self._insert_config(conn, {"horizon_days": 7})
        self._insert_report(
            conn, version, [6], config={**self.REPORT, "forecast_config_version": 999}
        )
        conn.commit()

        report, = db.read_due_reports(conn, self.SATURDAY)
        assert report["forecast_config_version"] == version

    def test_a_report_must_reference_a_recorded_config_version(self, conn):
        # The foreign key is the point of the table (ADR 0010): a report can
        # never name a configuration that was never used to forecast.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            self._insert_report(conn, 9999, [6])

    def test_an_empty_days_of_week_is_rejected(self, conn):
        # A report that silently never fires looks exactly like a broken one.
        version = self._insert_config(conn, {"horizon_days": 7})
        with pytest.raises(psycopg.errors.CheckViolation):
            self._insert_report(conn, version, [])

    def test_a_weekday_outside_zero_to_six_is_rejected(self, conn):
        version = self._insert_config(conn, {"horizon_days": 7})
        with pytest.raises(psycopg.errors.CheckViolation):
            self._insert_report(conn, version, [7])

    def test_read_forecast_config_stamps_version_active_and_replacement(self, conn):
        # What a refusal needs to tell "this row points at a superseded
        # configuration" apart from "today's run did not happen" (ADR 0010).
        old = self._insert_config(conn, {"horizon_days": 7}, is_active=False)
        new = self._insert_config(conn, {"horizon_days": 9})
        conn.commit()

        assert db.read_forecast_config(conn, old) == {
            "horizon_days": 7,
            "version": old,
            "is_active": False,
            "active_version": new,
        }
        assert db.read_forecast_config(conn, new)["is_active"] is True

    def test_read_forecast_config_reports_no_active_version(self, conn):
        version = self._insert_config(conn, {"horizon_days": 7}, is_active=False)
        conn.commit()

        assert db.read_forecast_config(conn, version)["active_version"] is None

    def test_read_forecast_config_raises_on_a_version_that_does_not_exist(self, conn):
        with pytest.raises(RuntimeError, match="9999"):
            db.read_forecast_config(conn, 9999)

    def test_read_latest_forecasts_returns_only_the_newest_origin(self, conn):
        version = self._insert_config(conn, {"horizon_days": 1})
        conn.commit()
        for as_of in (datetime.date(2026, 7, 30), datetime.date(2026, 7, 31)):
            db.insert_forecasts(conn, log_row(
                as_of, version, "ewma", "wheat_bagels",
                as_of + datetime.timedelta(days=1), 42.0,
            ))

        latest = db.read_latest_forecasts(conn, version)
        assert list(latest.columns) == list(db.FORECAST_COLUMNS)
        assert list(latest["as_of"]) == [datetime.date(2026, 7, 31)]

    def test_read_latest_forecasts_ignores_other_config_versions(self, conn):
        mine = self._insert_config(conn, {"horizon_days": 1}, is_active=False)
        other = self._insert_config(conn, {"horizon_days": 1})
        conn.commit()
        db.insert_forecasts(conn, log_row(
            datetime.date(2026, 7, 30), mine, "ewma", "wheat_bagels",
            datetime.date(2026, 7, 31), 42.0,
        ))
        # A newer origin under a *different* version must not hide mine.
        db.insert_forecasts(conn, log_row(
            datetime.date(2026, 7, 31), other, "ewma", "wheat_bagels",
            datetime.date(2026, 8, 1), 99.0,
        ))

        latest = db.read_latest_forecasts(conn, mine)
        assert list(latest["as_of"]) == [datetime.date(2026, 7, 30)]
        assert list(latest["forecast_quantity"]) == [42.0]

    def test_read_latest_forecasts_of_an_unused_version_is_empty(self, conn):
        # Not an error: it is how "nothing was ever logged under this version"
        # reaches the payload builder.
        version = self._insert_config(conn, {"horizon_days": 1})
        conn.commit()

        empty = db.read_latest_forecasts(conn, version)
        assert empty.empty
        assert list(empty.columns) == list(db.FORECAST_COLUMNS)

    def test_a_null_weekday_is_rejected(self, conn):
        # `{NULL}` passes both a length check and an array containment check on
        # its own, so it is named explicitly.
        version = self._insert_config(conn, {"horizon_days": 7})
        with pytest.raises(psycopg.errors.CheckViolation):
            self._insert_report(conn, version, [None])
