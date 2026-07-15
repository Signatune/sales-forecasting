"""Ephemeral Postgres for the DB integration tests (ADR 0003).

The `TestAgainstPostgres` suites in test_db.py and test_migrate.py TRUNCATE the
schema's tables, so they run against a throwaway Postgres named by
`TEST_DATABASE_URL`, never `DATABASE_URL`. Unset, they skip, and the suite still
passes on a database-less dev install.

`pytest --ephemeral-postgres` boots a throwaway local Postgres for the session
--- bundled by the `pgserver` wheel, so no Docker and no system install --- and
points `TEST_DATABASE_URL` at it, so the integration tests actually run. The
server lives in a temp dir and is torn down at the end of the session; nothing
persists. If `TEST_DATABASE_URL` is already set (e.g. a CI service container),
that server is used as-is and no ephemeral one is booted.
"""
import os
import shutil
import tempfile

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--ephemeral-postgres",
        action="store_true",
        default=False,
        help="Boot a throwaway local Postgres (via the pgserver wheel --- no "
        "Docker, no system install) and point TEST_DATABASE_URL at it, so the "
        "DB integration tests run instead of skipping.",
    )


def pytest_configure(config):
    if not config.getoption("--ephemeral-postgres"):
        return
    if os.environ.get("TEST_DATABASE_URL"):
        # A server is already provisioned (e.g. a CI service container); use it
        # rather than booting a second one.
        return
    try:
        import pgserver
    except ImportError as exc:  # pragma: no cover - environment guard
        raise pytest.UsageError(
            "--ephemeral-postgres needs the pgserver wheel: "
            'pip install -e ".[testdb]"'
        ) from exc

    pgdata = tempfile.mkdtemp(prefix="sales-forecasting-testdb-")
    server = pgserver.get_server(pgdata)
    os.environ["TEST_DATABASE_URL"] = server.get_uri()
    config._ephemeral_pg = (server, pgdata)


def pytest_unconfigure(config):
    provisioned = getattr(config, "_ephemeral_pg", None)
    if provisioned is None:
        return
    server, pgdata = provisioned
    server.cleanup()
    shutil.rmtree(pgdata, ignore_errors=True)
    # Drop the URI we injected, so a later in-process run doesn't reuse a torn
    # down server.
    os.environ.pop("TEST_DATABASE_URL", None)
