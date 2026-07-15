"""Postgres access for the Sales pipeline (ADR 0003).

The connection string comes from a single environment variable, `DATABASE_URL`,
so the same code runs from a laptop and from a GitHub Actions runner. Applying
the schema is one command:

    python db.py            # or: python db.py apply-schema

`schema.sql` is idempotent, so re-running it against an already-set-up database
is harmless.

Two tables (see `schema.sql`):

- `raw_toast_responses` --- raw Toast responses as `jsonb`, the replay/audit
  safety net. `insert_raw_response` saves one; `read_raw_responses` reads them
  back for re-normalization without contacting Toast.
- `sales` --- canonical `(product, date, quantity)` Sales, one row per
  `(product, date)`. `upsert_sales` writes them with replace-on-repeat
  semantics; `read_sales` returns the same frame `sales_history` reads today.
"""
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

CONNECTION_ENV_VAR = "DATABASE_URL"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connection_string(env=os.environ) -> str:
    """The Postgres connection string from the environment. Raising here, rather
    than letting psycopg fail on an empty DSN, keeps the missing-config error
    pointing at the one variable a developer has to set."""
    url = env.get(CONNECTION_ENV_VAR)
    if not url:
        raise RuntimeError(
            f"{CONNECTION_ENV_VAR} is not set. Point it at your Postgres "
            "database, e.g. "
            f"{CONNECTION_ENV_VAR}='postgresql://user:pass@host:5432/dbname'. "
            "See docs/postgres.md."
        )
    return url


def connect(env=os.environ) -> psycopg.Connection:
    """Open a connection to the database named by `DATABASE_URL`."""
    return psycopg.connect(connection_string(env))


def apply_schema(conn: psycopg.Connection) -> None:
    """Create both tables from `schema.sql`. Idempotent: safe to run against an
    already-set-up database."""
    conn.execute(SCHEMA_PATH.read_text())
    conn.commit()


def insert_raw_response(
    conn: psycopg.Connection,
    restaurant_guid: str,
    business_date,
    response,
    fetched_at=None,
) -> None:
    """Save one raw Toast response as jsonb. `business_date` accepts anything
    Postgres reads as a date (a `date`, or a 'YYYY-MM-DD' string). `fetched_at`
    defaults to the database's `now()` when omitted."""
    conn.execute(
        "INSERT INTO raw_toast_responses "
        "(restaurant_guid, business_date, fetched_at, response) "
        "VALUES (%s, %s, COALESCE(%s, now()), %s)",
        (restaurant_guid, business_date, fetched_at, Jsonb(response)),
    )
    conn.commit()


def read_raw_responses(
    conn: psycopg.Connection, business_date=None
) -> list:
    """The saved raw response payloads, oldest fetch first, for re-normalization
    without contacting Toast — each is a stored Toast response verbatim (a menu
    report is a JSON array of rows). Pass `business_date` to read one day's
    captures."""
    if business_date is None:
        rows = conn.execute(
            "SELECT response FROM raw_toast_responses ORDER BY fetched_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT response FROM raw_toast_responses "
            "WHERE business_date = %s ORDER BY fetched_at",
            (business_date,),
        ).fetchall()
    return [row[0] for row in rows]


def sales_rows(frame: pd.DataFrame) -> List[tuple]:
    """The canonical Sales frame as `(product, date, quantity)` tuples ready for
    Postgres: dates as python `date`, quantities as `float`. Pure, so the
    frame-to-rows mapping is testable without a database."""
    return [
        (str(row.product), pd.Timestamp(row.date).date(), float(row.quantity))
        for row in frame.itertuples(index=False)
    ]


def upsert_sales(conn: psycopg.Connection, frame: pd.DataFrame) -> None:
    """Write canonical Sales with replace-on-repeat semantics: a second write of
    the same `(product, date)` replaces that row's quantity rather than adding a
    duplicate. This is what lets ADR 0004's daily job re-pull a business date
    across three days without accumulating duplicates."""
    rows = sales_rows(frame)
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sales (product, date, quantity) VALUES (%s, %s, %s) "
            "ON CONFLICT (product, date) DO UPDATE SET quantity = EXCLUDED.quantity",
            rows,
        )
    conn.commit()


def read_sales(conn: psycopg.Connection) -> pd.DataFrame:
    """The canonical Sales history as a `(product, date, quantity)` frame, in the
    same shape `sales_history.load_sales_history()` returns today: `date` a
    datetime64 column, `quantity` a float, sorted by `(date, product)`."""
    rows = conn.execute(
        "SELECT product, date, quantity FROM sales ORDER BY date, product"
    ).fetchall()
    frame = pd.DataFrame(rows, columns=["product", "date", "quantity"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["quantity"] = frame["quantity"].astype(float)
    return frame


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "apply-schema"
    if command != "apply-schema":
        print(f"usage: python db.py [apply-schema]\nunknown command: {command!r}")
        return 2
    with connect() as conn:
        apply_schema(conn)
    print(f"schema applied from {SCHEMA_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
