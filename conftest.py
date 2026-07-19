"""Ephemeral Postgres for the DB integration tests (ADR 0003).

The `TestAgainstPostgres` suites in test_db.py and test_migrate.py TRUNCATE the
schema's tables, so they run against a throwaway Postgres named by
`TEST_DATABASE_URL`, never `DATABASE_URL`.

By default `pytest` boots a throwaway local Postgres for the session --- bundled
by the `pgserver` wheel, so no Docker and no system install --- and points
`TEST_DATABASE_URL` at it, so the integration tests actually run. The server
lives in a temp dir and is torn down at the end of the session; nothing
persists. Precedence:

- If `TEST_DATABASE_URL` is already set (e.g. a CI service container or a
  throwaway Supabase project), that server is used as-is and none is booted.
- `--no-ephemeral-postgres` skips booting entirely, for a fast database-less
  run; the integration tests then skip unless `TEST_DATABASE_URL` is set.
- If the `pgserver` wheel isn't installed (a `dev`-less install), booting is
  skipped rather than failing, so the suite still runs and the DB tests skip.
"""
import argparse
import os
import shutil
import tempfile

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--ephemeral-postgres",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Boot a throwaway local Postgres (via the pgserver wheel --- no "
        "Docker, no system install) and point TEST_DATABASE_URL at it, so the "
        "DB integration tests run. On by default; pass --no-ephemeral-postgres "
        "for a fast, database-less run that skips them. Ignored when "
        "TEST_DATABASE_URL is already set (e.g. a CI service container).",
    )


def pytest_configure(config):
    if not config.getoption("--ephemeral-postgres"):
        return
    if os.environ.get("TEST_DATABASE_URL"):
        # A server is already provisioned (a CI service container, a throwaway
        # Supabase project); use it as-is rather than booting a second one.
        return
    try:
        import pgserver
    except ImportError:
        # A dev-less install without the pgserver wheel: fall back to skipping
        # the DB integration tests rather than failing, so the suite still runs.
        # `pip install -e ".[dev]"` pulls pgserver in and makes them run.
        return

    pgdata = tempfile.mkdtemp(prefix="sales-forecasting-testdb-")
    server = pgserver.get_server(pgdata)
    # Record the server before anything that can raise, so an error mid-startup
    # still gets torn down by pytest_unconfigure rather than leaking a process.
    config._ephemeral_pg = (server, pgdata)
    os.environ["TEST_DATABASE_URL"] = server.get_uri()


def pytest_unconfigure(config):
    provisioned = getattr(config, "_ephemeral_pg", None)
    if provisioned is None:
        return
    server, pgdata = provisioned
    try:
        server.cleanup()
    finally:
        # Remove the temp data dir and drop the URI we injected, so a later
        # in-process run doesn't reuse a torn-down server. ignore_errors keeps a
        # cleanup hiccup from failing an otherwise green session.
        shutil.rmtree(pgdata, ignore_errors=True)
        os.environ.pop("TEST_DATABASE_URL", None)
