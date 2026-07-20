"""Where the DB integration tests get their database (ADR 0003).

The `TestAgainstPostgres` suites in test_db.py, test_migrate.py,
test_sales_history.py, test_daily_capture.py and test_daily_forecast.py
TRUNCATE the schema's tables, so they run against a throwaway Postgres named by
`TEST_DATABASE_URL`, never `DATABASE_URL`.

Nothing here provisions that database. `TEST_DATABASE_URL` is read from the
environment by each suite at import time, and whatever set it wins: normally
the `test` service in compose.yaml, which points it at the throwaway Postgres
17 container and is the supported way to run these tests
(`docker compose run --rm test`, or `make test`). A CI service container or a
throwaway Supabase project works the same way.

With the variable unset the integration tests skip and the rest of the suite
still runs --- so `pytest` on the host is a database-less run, not a broken
one. Docker is a hard prerequisite for running the integration tests; this file
booting a bundled Postgres itself was removed because the wheel it relied on
published no Windows build, so it no-opped and every integration test skipped
on the machine that needed it most. The reasoning is recorded in
.scratch/docker-dev-environment/PRD.md; ticket 05 of that effort promotes it to
ADR 0008, which is not written yet.
"""
