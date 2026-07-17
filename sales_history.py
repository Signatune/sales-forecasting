"""The single seam through which the project reads its Sales history.

Every reader — `forecast.py`, `backtest.py`, `model_comparison.py`,
`inspection_page.py` — obtains the canonical `(product, date, quantity)` Sales
frame by calling `load_sales_history()` rather than opening a file itself.
Concentrating the read here is what let ADR 0003's move to Postgres land by
changing this one function: since ticket 04 it reads the `product_sales` view
(ADR 0005) — the fact rolled up through the Product mapping — rather than a
parquet file.

The old `sales_history.parquet` is gone: nothing reads it to forecast from, and
ticket 07 retired its file-based write path. The only lingering reference is
`migrate.py`'s one-time verification, which owns the path itself now.
"""
import pandas as pd
import psycopg

import db

# Bound the initial connect so an unreachable host fails fast with the clear
# message below, rather than hanging on a TCP timeout before the reader ever
# sees an error. Only the connect is bounded, not the query.
CONNECT_TIMEOUT_SECONDS = 10


def load_sales_history() -> pd.DataFrame:
    """Return the canonical Sales history as a `(product, date, quantity)` frame,
    read from the Postgres `product_sales` view (ADR 0005).

    A missing or unreachable database fails with a message pointing at the setup
    docs — not a confusing pandas error from a downstream reader. `db.connect`
    raises when `DATABASE_URL` is unset; a connection that is set but unreachable
    surfaces here as the same kind of clear, actionable error.
    """
    try:
        with db.connect(connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            return db.read_sales(conn)
    except psycopg.OperationalError as exc:
        raise RuntimeError(
            "Could not reach the Sales database named by DATABASE_URL "
            f"({exc}). Check the database is running and the connection string "
            "is correct — see docs/postgres.md."
        ) from exc
