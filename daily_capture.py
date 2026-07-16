"""Capture a day's Sales end to end from the Toast Orders API (ADR 0004, 0005).

One command, run once a day (ticket 06 puts it on a GitHub Actions cron): pull
the Orders API for a trailing window of business dates across both in-scope
restaurants, aggregate each day to per-modifier quantities, store the aggregates
as raw `jsonb`, normalize them to the canonical Sales fact and upsert. Capture
and normalization happen in the same run — a GitHub Actions runner is ephemeral,
so there is no half-finished state to hand to a later step.

    python daily_capture.py

Three properties the pipeline leans on:

- **Orders only.** The Orders-derived aggregation was reconciled live against
  Analytics `quantitySold` on 2026-07-07 and matched exactly, and Analytics'
  lookback-rate cap makes it a poor fit for a job that runs every day forever
  (ADR 0004). `toast_client.py` (Analytics) is never called from this path.
- **No guest PII is persisted.** Raw orders carry guest data, so they are never
  stored as-pulled; each day is aggregated in memory to Analytics-shaped modifier
  rows first (exactly as `toast_orders.py` does), and only those are saved.
- **Re-pulling a day corrects it.** ADR 0004's trailing window re-pulls the last
  few business dates every run and upserts the fact by
  `(date, restaurant, source_type, source_name)`, so a back-office correction or
  a void Toast allows after a business date closes is picked up automatically —
  a second run with unchanged numbers is a no-op, and a changed quantity in Toast
  overwrites the stored one.

The raw shards and the fact are written in one transaction that commits once, so
a Toast or database failure exits non-zero and leaves the stored history
untouched.
"""
import datetime as dt
import sys
from typing import Dict, List, Optional, Tuple

import psycopg

import db
import normalize
import toast_orders
from toast_client import RESTAURANT_TZ

# ADR 0004's trailing window: re-pull the last few business dates every run, not
# just the newest, so post-close corrections and voids are picked up. Three is
# the "last three business dates" the ticket demos; a one-line change here widens
# it.
TRAILING_WINDOW_DAYS = 3


def trailing_business_dates(
    today: dt.date, days: int = TRAILING_WINDOW_DAYS
) -> List[dt.date]:
    """The `days` most recent business dates ending at `today`, oldest first."""
    return [today - dt.timedelta(days=n) for n in reversed(range(days))]


def pull_and_aggregate(
    client, business_dates: List[dt.date]
) -> Tuple[List[Tuple], List[dict]]:
    """Pull Orders for each business date and both in-scope restaurants and
    aggregate them to per-day modifier quantity rows — the step that strips guest
    PII, exactly as `toast_orders.py` does. Returns `(shards, rows)`:

    - `shards` is one `(restaurant_guid, business_date, agg_rows)` per
      `(restaurant, business date)` for the raw `jsonb` table: `business_date` a
      python `date`, `agg_rows` the Analytics-shaped, PII-free aggregate (an
      empty list for a closed day, so the capture is still recorded).
    - `rows` is every aggregated modifier row, flat, for the fact.

    Toast is contacted only here, before any database write, so a failed pull
    raises before the stored history is touched."""
    shards: List[Tuple] = []
    rows: List[dict] = []
    for date in business_dates:
        business_date = f"{date:%Y%m%d}"
        for restaurant_guid in normalize.INCLUDED_RESTAURANTS:
            orders = client.orders_for_business_date(restaurant_guid, business_date)
            agg = toast_orders.aggregate_modifier_rows(
                orders, restaurant_guid, business_date
            )
            shards.append((restaurant_guid, date, agg))
            rows.extend(agg)
    return shards, rows


def _warn_unmapped(unmapped: Dict[str, float]) -> None:
    """Surface bagel-looking modifiers that map to no Product, loudly, the way
    `normalize.py` does — a new rotating flavor or a rename would otherwise sit
    in the fact untracked and silent (ADR 0005)."""
    if not unmapped:
        return
    print("WARNING: bagel-looking modifiers not mapped to any Product:")
    for name, qty in sorted(unmapped.items(), key=lambda kv: -kv[1]):
        print(f"  {qty:>10.1f}  {name!r}")


def run_capture(
    conn: psycopg.Connection, client, business_dates: List[dt.date]
) -> Dict[str, object]:
    """Capture a window of business dates end to end into Postgres and return a
    counts dict. Pulls and aggregates first (outside the transaction), then
    writes the raw shards and upserts the fact together and commits once — so a
    Toast failure raises before any write, and a database failure rolls back to
    nothing, leaving the stored history untouched either way.

    The fact is built through `normalize.modifier_fact_rows`, the same function
    the history migration uses, so the daily job's normalization rules cannot
    drift from the migrated history: only configured modifiers count, out-of-scope
    restaurants are excluded, names are matched normalized. The mapping is not
    touched — `product_sales` rolls the fact up to Products for the readers."""
    # One capture time for the whole run, so a business date's shards share it.
    fetched_at = dt.datetime.now(dt.timezone.utc)
    shards, rows = pull_and_aggregate(client, business_dates)
    _warn_unmapped(normalize.find_unmapped_bagelish(rows))
    facts = normalize.modifier_fact_rows(rows)
    raw_shards = [
        (restaurant_guid, date, fetched_at, agg)
        for restaurant_guid, date, agg in shards
    ]
    try:
        raw_inserted = db.bulk_insert_raw_responses(conn, raw_shards)
        fact_upserted = db.bulk_upsert_sales(conn, facts)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "business_dates": [str(date) for date in business_dates],
        "raw_inserted": raw_inserted,
        "fact_upserted": fact_upserted,
    }


def _authenticated_client() -> toast_orders.ToastOrdersClient:
    """A logged-in Orders client from the standard-key credentials in `.env`."""
    creds = toast_orders.load_standard_credentials()
    client = toast_orders.ToastOrdersClient(
        creds["baseUrl"], creds["clientId"], creds["clientSecret"]
    )
    client.login()
    return client


def main(
    argv: Optional[List[str]] = None,
    *,
    connect=db.connect,
    make_client=_authenticated_client,
    now: Optional[dt.datetime] = None,
) -> int:
    """Run the daily capture. Returns 0 on success; on a Toast or database
    failure, prints a clear message to stderr and returns non-zero, having left
    the stored history untouched. `connect`, `make_client` and `now` are seams
    for testing. The business date window is computed in the restaurants'
    timezone, not UTC, so a run just after midnight there captures the day that
    just closed (ticket 06 times the cron for after close)."""
    now = now or dt.datetime.now(RESTAURANT_TZ)
    business_dates = trailing_business_dates(now.date())
    try:
        client = make_client()
        with connect() as conn:
            counts = run_capture(conn, client, business_dates)
    except (RuntimeError, psycopg.Error) as exc:
        print(f"daily capture failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"captured business dates {', '.join(counts['business_dates'])}: "
        f"upserted {counts['fact_upserted']} Sales fact rows, "
        f"stored {counts['raw_inserted']} raw shards"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
