"""One command: pull the full bagel Sales history from Toast and normalize it.

    .venv/bin/python ingest.py

Two sources, because the Analytics API caps modifier-level history at roughly
14 months per hour of rate-limit budget:

1. toast_orders.py walks the Orders API (5 req/s) backwards month by month
   until it runs out of history, aggregating each month in memory.
2. toast_client.py pulls Analytics week reports newest-first. These are
   authoritative wherever they reach, and normalize.py prefers them.

Leaves behind timestamped raw API responses in data/raw/ and the canonical
Sales history in data/sales_history.parquet. Safe to re-run: already-captured
months and report windows are skipped, so an interrupted pull resumes where it
left off. A pull that fails leaves the previous sales_history.parquet intact.
"""
import sys

import normalize
import toast_client
import toast_orders


def main() -> None:
    toast_orders.pull_orders_history()
    toast_client.pull_history()
    normalize.main()


if __name__ == "__main__":
    sys.exit(main())
