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

Their definitions live in [`schema.sql`](../schema.sql); all access goes through
[`db.py`](../db.py).

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

## Apply the schema

```
python db.py
```

This creates the tables and the `product_sales` view. It is idempotent — every
statement is `IF NOT EXISTS` or `CREATE OR REPLACE`, so running it against an
already-set-up database changes nothing and is safe to repeat.

A change to `schema.sql` on `main` applies itself: `.github/workflows/apply-schema.yml`
runs this same command against `DATABASE_URL` on every push to `main` that
touches the file, whether it lands via a direct push or a merged PR (ADR 0007).
Run `python db.py` by hand for local development or any other branch — those
aren't automated.

## Migrate the pulled history (one-time, already done)

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
    db.apply_schema(conn)
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
