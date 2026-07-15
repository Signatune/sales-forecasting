"""The single seam through which the project reads its Sales history.

Every reader — `forecast.py`, `backtest.py`, `model_comparison.py`,
`inspection_page.py` — obtains the canonical `(product, date, quantity)` Sales
frame by calling `load_sales_history()` rather than opening the parquet file
itself. Concentrating the read here is what lets ADR 0003's move to Postgres
land by changing this one function: today it stays parquet-backed.

`normalize.py` owns the write side and still rebuilds the parquet at
`SALES_HISTORY_PATH`; this module only reads it, and is the one place the path
is named.
"""
from pathlib import Path

import pandas as pd

SALES_HISTORY_PATH = Path(__file__).parent / "data" / "sales_history.parquet"


def load_sales_history() -> pd.DataFrame:
    """Return the canonical Sales history as a `(product, date, quantity)` frame,
    exactly as `normalize.py` wrote it."""
    return pd.read_parquet(SALES_HISTORY_PATH)
