# Postgres: local setup

The Sales pipeline stores its data in a managed Postgres database (ADR 0003).
There are two tables — raw Toast responses as `jsonb`, and the canonical
`(product, date, quantity)` Sales history. Their definitions live in
[`schema.sql`](../schema.sql); all access goes through [`db.py`](../db.py).

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

This creates both tables. It is idempotent — every statement is
`IF NOT EXISTS`, so running it against an already-set-up database changes
nothing and is safe to repeat.

## Verify (the ticket's demoable)

```python
import pandas as pd
import db

with db.connect() as conn:
    db.apply_schema(conn)
    day = pd.to_datetime(["2026-07-05"])
    db.upsert_sales(conn, pd.DataFrame({"product": ["plain"], "date": day, "quantity": [10.0]}))
    db.upsert_sales(conn, pd.DataFrame({"product": ["plain"], "date": day, "quantity": [17.0]}))
    print(db.read_sales(conn))   # one row for (plain, 2026-07-05), quantity 17.0
```

The repeat write of the same `(product, date)` replaces the row rather than
adding a duplicate — the uniqueness ADR 0004's daily job depends on.

## Running the database integration tests

The unit tests in `tests/test_db.py` need no database. The integration tests do,
and they `TRUNCATE` the pipeline tables — so they run against a **throwaway**
database, never your real `DATABASE_URL`. Point `TEST_DATABASE_URL` at a scratch
database (a second Supabase project, or a local Postgres) to run them:

```
TEST_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres' pytest tests/test_db.py
```

With `TEST_DATABASE_URL` unset, those tests skip and the rest of the suite runs
unchanged.
