# Postgres: local setup

The Sales pipeline stores its data in a managed Postgres database (ADR 0003).
There is the raw Toast responses table (`jsonb`), and canonical Sales as a
source-to-product model (ADR 0005):

- **`sales`** — the fact, one row per `(date, restaurant_guid, source_type,
  source_name, quantity)`: every configured thing sold, at both Toast grains
  (`source_type` is `item` or `modifier`), per location.
- **`products`** / **`product_sources`** — the many-to-one map from a sold
  source up to a canonical Product (`BAGEL_MODIFIER_NAMES` promoted from code
  into data).
- **`product_sales`** — a view that rolls the fact up through the map to the
  `(product, date, quantity)` frame the readers consume, summed across locations
  and across a Product's sources.

Their definitions live in [`migrations/`](../migrations) — the schema is an
ordered list of applied-once `.sql` files, not a single state document (ADR
0015), so "what does this table look like now" is answered by reading `0001`
and then everything after it. All access goes through [`db.py`](../db.py).

## Point at a database

Every entry point reads one environment variable, `DATABASE_URL`, so the same
code runs from a laptop and from a GitHub Actions runner. Put it in your local
`.env` — standard dotenv format, `KEY=value` (already git-ignored — never commit
a connection string; copy `.env.example` to start):

```
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/postgres
```

The managed instance is a **Supabase** project. Use the **connection pooler**
(Supavisor) string, in **Session mode** — not the direct connection. Supabase
provisions the direct host (`db.<project-ref>.supabase.co`) as IPv6-only, so it
fails to resolve on IPv4-only networks, including GitHub Actions runners, where
ADR 0003's daily job runs. The pooler is reachable over IPv4, so the same
`DATABASE_URL` works from a laptop and from the runner.

Get it from the Supabase dashboard → **Connect** → **Session pooler**, and paste
in the database password. It looks like:

```
DATABASE_URL=postgresql://postgres.<project-ref>:PASSWORD@aws-0-<region>.pooler.supabase.com:5432/postgres
```

Session mode (port `5432`) — not Transaction mode (`6543`) — is what you want:
applying the schema and the pipeline's multi-statement writes need a full
session.

[`env.py`](../env.py) loads `.env` into the environment on every entry point, so
no shell setup is needed — the variable is picked up wherever you run from. An
environment variable that is already set always wins over the file, which is how
the runner's secrets take precedence when there is no `.env` at all:

```
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres'   # overrides .env
```

## Apply migrations

```
python db.py            # or: python db.py migrate
python db.py status     # what's applied, what's pending; changes nothing
```

`migrate` applies every `migrations/NNNN-<slug>.sql` the database has not run
yet, in numeric order, and records each one in the `schema_migrations` table.
Running it with nothing pending does nothing. On an empty database it builds the
whole schema from `0001`.

A migration on `main` applies itself: `.github/workflows/apply-schema.yml` runs
`python db.py migrate` against `DATABASE_URL` on every push to `main` that
touches `migrations/`, whether it lands via a direct push or a merged PR (ADR
0007, ADR 0015). Run `python db.py` by hand for local development or any other
branch — those aren't automated.

**Because migrations are applied-once and can be destructive, the PR review is
the only gate.** Under the old `schema.sql` a mistake could be corrected by
editing the file and re-applying; here a migration reaches production the moment
it merges, and the only correction is another migration.

## Write a migration

1. Add `migrations/NNNN-<slug>.sql`, where `NNNN` is one above the highest
   number already there and the slug is lower-case words separated by hyphens
   (`0002-add-selections-table.sql`). The runner refuses a filename it cannot
   parse and refuses two files sharing a number, rather than silently skipping
   either.
2. Write plain, ordered DDL. Do **not** copy `0001`'s
   `IF NOT EXISTS` / `CREATE OR REPLACE` style — that is a fossil of the
   desired-state file it used to be. A migration runs exactly once, so it can
   say what it means.
3. Apply it locally (`python db.py migrate` against a scratch database, or just
   run the test suite, which builds its throwaway Postgres from every migration
   in order).
4. Open a PR. Merging applies it to production.

Each file runs inside its own transaction, so a migration that fails partway
leaves nothing behind and the ones before it stay applied. The cost of that
guarantee: statements Postgres refuses to run inside a transaction block —
`CREATE INDEX CONCURRENTLY`, `VACUUM` — cannot go in a migration. If you need
one, run it by hand against the database and record why in the migration that
would otherwise have contained it.

If you branched before someone else's migration merged, your number may collide
or land below theirs. The runner refuses both cases with a message naming the
fix: renumber above the highest applied version and re-open the PR.

## There is no rollback

Forward-only, deliberately (ADR 0015) — `python db.py` has no `down` command:

- **A mistake in a merged migration** is corrected by writing the next one. A
  down file for `ADD COLUMN` is `DROP COLUMN`, an undo button that destroys the
  column's contents.
- **A corrupted production database** is recovered with Supabase's
  point-in-time recovery, which exists whether or not down files were ever
  written.
- **A local or test database** is reset by dropping it and migrating from
  `0001`, not by stepping back one.

## Baseline an existing database

```
python db.py baseline 0001
```

Records a version as applied *without running it* — for a database that already
has that schema. This was used exactly once, on 2026-08-07, to make the live
database migration zero: `0001-baseline.sql` is the `schema.sql` that had
already been applied to it, verified by diffing a `pg_dump --schema-only` of
production against that file applied to an empty Postgres 17 (byte-identical).
No DDL ran and no row was touched. You are unlikely to need this again.

## Migrate the pulled history (one-time, already done)

Note the name collision: `migrate.py` is this one-time *data* load and has
nothing to do with the schema migrations above. `python db.py migrate` applies
schema migrations; `python migrate.py` loaded the history, once, in 2026.

This was a one-time load, run by hand when Postgres became the source of truth
(ADR 0003), and it has already happened — it is documented here as the record of
how the history got in, not a step a fresh clone repeats. The pulled history
lived under `data/raw/`, which is no longer tracked in the repo (ticket 07);
`normalize.py` no longer rebuilds `sales_history.parquet`. So the command below
only runs on a checkout that still holds those pre-migration files locally; a
fresh clone has nothing to migrate.

```
python migrate.py        # load Postgres, then verify the view matches the parquet
```

`migrate.py` shards the saved `menu_week` / `orders_agg` responses into the raw
table (one row per restaurant and business date, capture time from the
filename), seeds `products` / `product_sources` from `normalize.py`'s
`BAGEL_MODIFIER_NAMES`, and loads the canonical `sales` fact — every configured
modifier in the history, via COPY into a staging table plus one `ON CONFLICT`
upsert. The whole load is one transaction and idempotent: re-running it changes
nothing. It wants the **Session pooler** `DATABASE_URL` (the staged COPY relies
on a full session), same as applying the schema.

`python migrate.py verify` re-runs just the comparison (below); `migrate` runs
just the load. With no argument it does both.

## Verify (the ticket's demoable)

```python
import pandas as pd
import db

CAMBRIDGE = "28e5b269-1c1c-45df-81a8-1d268c005dfa"


def fact(source_name, quantity):
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-05"]),
            "restaurant_guid": [CAMBRIDGE],
            "source_type": ["modifier"],
            "source_name": [source_name],
            "quantity": [quantity],
        }
    )


with db.connect() as conn:
    db.apply_migrations(conn)
    # Seed one Product with two source mappings.
    db.upsert_product_sources(
        conn, {"plain": [("modifier", "plain bagel"), ("modifier", "plain, bulk")]}
    )
    # Write the same (date, restaurant, source) twice: the second quantity wins.
    db.upsert_sales(conn, fact("plain bagel", 10.0))
    db.upsert_sales(conn, fact("plain bagel", 17.0))
    # Write a second source of the same Product on the same date.
    db.upsert_sales(conn, fact("plain, bulk", 4.0))
    print(db.read_sales(conn))   # one row: (plain, 2026-07-05), quantity 21.0
```

The repeat write of the same `(date, restaurant, source)` replaces that fact row
rather than adding a duplicate — the uniqueness ADR 0004's daily job depends on
— and the view sums a Product's sources (17.0 + 4.0) into one
`(product, date, quantity)` row.

## Running the database integration tests

The unit tests in `tests/test_db.py` and `tests/test_migrate.py` need no
database. The integration tests do, and they `TRUNCATE` the pipeline tables — so
they run against a **throwaway** database, never your real `DATABASE_URL`.

By default there's nothing to set up: the dev install includes the `pgserver`
wheel, so `pytest` boots a throwaway local Postgres for the run (bundled
binaries — no Docker, no system install), points `TEST_DATABASE_URL` at it, and
tears it down at the end. Just run the suite:

```
pip install -e ".[dev]"
pytest
```

For a fast, database-less run, pass `--no-ephemeral-postgres`; the integration
tests then skip (unless `TEST_DATABASE_URL` is set) and the rest of the suite
runs unchanged.

To run against a scratch database you manage yourself — a second Supabase
project, a local Postgres, or a CI service container — set `TEST_DATABASE_URL`.
When it's already set it wins: no local server is booted and it's used as-is.

```
TEST_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres' pytest tests/test_db.py tests/test_migrate.py
```

`test_migrate.py`'s full-history comparison additionally needs the pre-migration
`data/raw/` history and `sales_history.parquet` checked out locally; on a fresh
clone, where those are no longer tracked (ticket 07), it skips.
