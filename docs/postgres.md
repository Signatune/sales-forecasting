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
`.env` (already git-ignored — never commit a connection string):

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

`.env` is loaded automatically by any shell that sources it; if yours does not,
export the variable before running:

```
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres'
```

## Apply the schema

```
python db.py
```

This creates the tables and the `product_sales` view. It is idempotent — every
statement is `IF NOT EXISTS` or `CREATE OR REPLACE`, so running it against an
already-set-up database changes nothing and is safe to repeat.

## Migrate the pulled history (one-time)

The history already pulled from Toast lives under `data/raw/`. It is loaded into
Postgres once, by hand, reusing the rate-limit cost already paid rather than
re-pulling (ADR 0003) — the migration never re-contacts Toast. Regenerate the
parquet first so the file-based readers and the fact tell the same story, then
load:

```
python normalize.py     # regenerate sales_history.parquet from the full raw history
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

The easy path needs no Postgres install. Install the `testdb` extra once and
pass `--ephemeral-postgres`; pytest boots a throwaway local Postgres for the run
(bundled by the `pgserver` wheel — no Docker, no system install), points
`TEST_DATABASE_URL` at it, and tears it down at the end:

```
pip install -e ".[testdb]"
pytest --ephemeral-postgres
```

Or point `TEST_DATABASE_URL` at a scratch database you manage yourself (a second
Supabase project, or a local Postgres). This also lets CI reuse a service
container: when `TEST_DATABASE_URL` is already set, `--ephemeral-postgres` uses
it as-is instead of booting one.

```
TEST_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres' pytest tests/test_db.py tests/test_migrate.py
```

With `TEST_DATABASE_URL` unset and no `--ephemeral-postgres`, those tests skip
and the rest of the suite runs unchanged. `test_migrate.py`'s full-history
comparison also expects `sales_history.parquet` to be current
(`python normalize.py`).
