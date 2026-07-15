"""Postgres access for the Sales pipeline (ADR 0003, ADR 0005).

The connection string comes from a single environment variable, `DATABASE_URL`,
so the same code runs from a laptop and from a GitHub Actions runner. Applying
the schema is one command:

    python db.py            # or: python db.py apply-schema

`schema.sql` is idempotent, so re-running it against an already-set-up database
is harmless.

The objects (see `schema.sql`):

- `raw_toast_responses` --- raw Toast responses as `jsonb`, the replay/audit
  safety net. `insert_raw_response` saves one; `read_raw_responses` reads them
  back for re-normalization without contacting Toast.
- Canonical Sales as a source-to-product model (ADR 0005). The fact `sales` is
  keyed one row per `(date, restaurant_guid, source_type, source_name)`;
  `upsert_sales` writes fact rows with replace-on-repeat semantics.
  `product_sources` maps each source up to one Product; `upsert_product_sources`
  seeds that map. `read_sales` reads the `product_sales` view, which rolls the
  fact up through the map to the `(product, date, quantity)` frame the readers
  consume — so the write side is at source grain and the read side at Product
  grain, exactly as ADR 0005 lays out.

`source_name` is stored in the normalized (stripped, lower-cased) form
`normalize.py` matches on; callers pass names already normalized (as
`normalize.py` produces them), and both the fact and the map store them that
way so the view's join lines up.
"""
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

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
    """Create the tables and view from `schema.sql`. Idempotent: safe to run
    against an already-set-up database."""
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


def upsert_product_sources(
    conn: psycopg.Connection,
    mapping: Dict[str, Iterable[tuple]],
) -> None:
    """Seed the Product mapping: `mapping` is `{product_name: [(source_type,
    source_name), ...]}`, many sources to one Product. Idempotent — a Product is
    inserted once and its sources are re-pointed rather than duplicated, so this
    is safe to re-run (and is how ticket 03 seeds BAGEL_MODIFIER_NAMES). Source
    names are stored verbatim, so pass them already normalized (see module
    docstring) — that is what lines the map up with the fact under the view's join."""
    with conn.cursor() as cur:
        for product, sources in mapping.items():
            # ON CONFLICT ... DO UPDATE (not DO NOTHING) so RETURNING yields the
            # id whether the Product is new or already present.
            product_id = cur.execute(
                "INSERT INTO products (name) VALUES (%s) "
                "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (product,),
            ).fetchone()[0]
            source_rows = [
                (product_id, str(source_type), str(source_name))
                for source_type, source_name in sources
            ]
            if source_rows:
                cur.executemany(
                    "INSERT INTO product_sources (product_id, source_type, source_name) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (source_type, source_name) "
                    "DO UPDATE SET product_id = EXCLUDED.product_id",
                    source_rows,
                )
    conn.commit()


def sales_rows(frame: pd.DataFrame) -> List[tuple]:
    """The Sales fact frame as `(date, restaurant_guid, source_type,
    source_name, quantity)` tuples ready for Postgres: dates as python `date`,
    quantities as `float`. Pure, so the frame-to-rows mapping is testable
    without a database."""
    return [
        (
            pd.Timestamp(row.date).date(),
            str(row.restaurant_guid),
            str(row.source_type),
            str(row.source_name),
            float(row.quantity),
        )
        for row in frame.itertuples(index=False)
    ]


def upsert_sales(conn: psycopg.Connection, frame: pd.DataFrame) -> None:
    """Write Sales fact rows with replace-on-repeat semantics: a second write of
    the same `(date, restaurant_guid, source_type, source_name)` replaces that
    row's quantity rather than adding a duplicate. This is what lets ADR 0004's
    daily job re-pull a business date across three days without accumulating
    duplicates."""
    rows = sales_rows(frame)
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sales (date, restaurant_guid, source_type, source_name, quantity) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (date, restaurant_guid, source_type, source_name) "
            "DO UPDATE SET quantity = EXCLUDED.quantity",
            rows,
        )
    conn.commit()


def read_sales(conn: psycopg.Connection) -> pd.DataFrame:
    """The canonical Sales history as a `(product, date, quantity)` frame, read
    from the `product_sales` view — the fact rolled up through the Product
    mapping, summed across locations and sources. Same shape
    `sales_history.load_sales_history()` returns today: `date` a datetime64
    column, `quantity` a float, sorted by `(date, product)`."""
    rows = conn.execute(
        "SELECT product, date, quantity FROM product_sales ORDER BY date, product"
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
