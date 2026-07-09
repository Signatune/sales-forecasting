"""One command: pull the full bagel Sales history from Toast and normalize it.

    .venv/bin/python ingest.py

Leaves behind timestamped raw API responses in data/raw/ and the canonical
Sales history in data/sales_history.parquet. Safe to re-run: already-captured
report windows are skipped, so an interrupted pull resumes where it left off.
"""
import sys

import normalize
import toast_client


def main() -> None:
    toast_client.pull_history()
    normalize.main()


if __name__ == "__main__":
    sys.exit(main())
