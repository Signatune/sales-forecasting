"""One-time migration of the pulled Toast history into Postgres (ticket 03).

The history already pulled from Toast lives under `data/raw/*.json`. ADR 0003
says it moves into Postgres once, by hand, reusing the rate-limit cost already
paid rather than re-pulling from Toast — nothing here re-contacts the Analytics
or Orders API. It is run once by a person, and re-running it must not duplicate
or corrupt what is already there.

It loads, into the ADR 0005 source-to-product schema:

- the saved canonical raw responses (`menu_week`, `orders_agg`) into the raw
  `jsonb` table, sharded one row per `(restaurant, business_date)` with capture
  time from the filename;
- the `products` / `product_sources` map, seeded from `normalize.py`'s
  `BAGEL_MODIFIER_NAMES`; and
- the canonical Sales fact — one row per `(date, restaurant, source_type,
  source_name, quantity)` for *every configured modifier* in the history, not
  just the seven mapped bagels. `source_type` is `modifier` throughout; there is
  no item history to load (ADR 0005).

The build (reading and parsing files) happens outside the transaction; the
write — raw shards, map, fact — is one transaction that commits once, so a
failure rolls back to nothing and a re-run stays clean. The fact goes in via
`db.bulk_upsert_sales` (COPY into staging, one `ON CONFLICT` upsert), not a
per-row loop.

The bar (ticket 03): for the seven bagel Products the `product_sales` view must
match `sales_history.parquet` exactly — regenerate the parquet with
`python normalize.py` first, since the file-based readers still use it and it
was written before the Orders backfill reached back to 2016.

    python normalize.py     # regenerate the (stale) parquet from full raw
    python migrate.py       # load Postgres, then verify against the parquet

Sub-commands: `migrate` (load only), `verify` (compare only), or no argument
(both). `verify` is the reproducible comparison the ticket asks for.

Connection: COPY and the single long-lived load session want the Session pooler
`DATABASE_URL` (IPv4), not the transaction-mode pooler — see docs/postgres.md.
"""
import collections
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import psycopg

import db
import normalize
import sales_history

# The canonical sources that back the Sales history: the menu_week Analytics
# reports and the orders_agg aggregates load_sales_rows() reads. daily_totals
# (per-restaurant totals) and restaurants_information are not canonical and are
# out of scope (ticket 03).
CANONICAL_PREFIXES = ("menu_week", "orders_agg")

# Raw source rows are all modifier-grained; the fact loads them at that grain.
SOURCE_TYPE = "modifier"


def capture_time(path: Path) -> dt.datetime:
    """The capture time encoded in a raw filename, as a UTC-aware datetime.
    Names look like `{prefix}_{window}__{fetchedAt}.json`, fetchedAt in the
    `20260710T122903Z` form toast_client.py / toast_orders.py write."""
    stamp = path.name.split("__")[1].removesuffix(".json")
    return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def _business_date(yyyymmdd: str) -> dt.date:
    return dt.datetime.strptime(yyyymmdd, "%Y%m%d").date()


def canonical_files(raw_dir: Path = normalize.RAW_DIR) -> List[Path]:
    """The latest capture per window of every canonical source, window order."""
    files: List[Path] = []
    for prefix in CANONICAL_PREFIXES:
        files.extend(normalize.latest_report_files(raw_dir, prefix))
    return files


def raw_shards(raw_dir: Path = normalize.RAW_DIR) -> List[Tuple]:
    """Every canonical raw response sharded one row per `(restaurant,
    business_date)`: `(restaurant_guid, business_date, fetched_at, rows)` tuples
    ready for `db.bulk_insert_raw_responses`. `rows` is the verbatim list of that
    capture's rows for that restaurant and date, so a day can be re-normalized on
    its own. Capture time comes from the filename."""
    shards: List[Tuple] = []
    for path in canonical_files(raw_dir):
        fetched_at = capture_time(path)
        content = json.loads(path.read_text())
        normalize.validate_modifier_rows(content, source=path.name)
        grouped: Dict[Tuple[str, str], List[dict]] = collections.defaultdict(list)
        for row in content:
            grouped[(row["restaurantGuid"], row["businessDate"])].append(row)
        for (restaurant_guid, business_date), rows in grouped.items():
            shards.append(
                (restaurant_guid, _business_date(business_date), fetched_at, rows)
            )
    return shards


def product_source_seed() -> Dict[str, List[Tuple[str, str]]]:
    """`BAGEL_MODIFIER_NAMES` as the `db.seed_products` mapping — each Product's
    modifier names as `(source_type, source_name)` sources. Names are normalized
    (stripped, lower-cased) exactly as the fact stores `source_name`, so the
    view's join lines up (ADR 0005)."""
    return {
        product: [(SOURCE_TYPE, name.strip().lower()) for name in names]
        for product, names in normalize.BAGEL_MODIFIER_NAMES.items()
    }


def fact_rows(raw_dir: Path = normalize.RAW_DIR) -> List[Tuple]:
    """The canonical Sales fact for the whole history: one
    `(date, restaurant_guid, source_type, source_name, quantity)` tuple per
    `(date, restaurant, normalized modifier name)`, quantity summed over the raw
    rows sharing that key. Every *configured* modifier for the in-scope
    restaurants is kept — not just the mapped bagels — so an unmapped source
    sits in the fact until someone maps it (ADR 0005). Uses the same
    coverage-deduped row set normalize.py reads (week reports first, orders
    aggregates only for dates no week report covers)."""
    rows = normalize.load_sales_rows(raw_dir)
    totals: Dict[Tuple[dt.date, str, str], float] = collections.defaultdict(float)
    for row in rows:
        if row["restaurantGuid"] not in normalize.INCLUDED_RESTAURANTS:
            continue
        if not normalize.is_configured_modifier(row):
            continue
        name = row["modifierName"].strip().lower()
        key = (_business_date(row["businessDate"]), row["restaurantGuid"], name)
        totals[key] += float(row["quantitySold"])
    return [
        (date, restaurant_guid, SOURCE_TYPE, name, quantity)
        for (date, restaurant_guid, name), quantity in totals.items()
    ]


def run_migration(
    conn: psycopg.Connection, raw_dir: Path = normalize.RAW_DIR
) -> Dict[str, int]:
    """Load the history into Postgres in one transaction. Reads and parses the
    raw files first (outside the transaction), then writes the raw shards, the
    Product map and the fact together and commits once — so a failure rolls back
    to nothing and a re-run changes nothing. Returns `raw_inserted` (new raw
    shards, 0 on an idempotent re-run), `products` seeded, and `fact_upserted`
    (fact rows written; the whole history is re-upserted every run by design)."""
    shards = raw_shards(raw_dir)
    seed = product_source_seed()
    facts = fact_rows(raw_dir)
    try:
        raw_inserted = db.bulk_insert_raw_responses(conn, shards)
        db.seed_products(conn, seed)
        fact_upserted = db.bulk_upsert_sales(conn, facts)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "raw_inserted": raw_inserted,
        "products": len(seed),
        "fact_upserted": fact_upserted,
    }


def compare_to_parquet(conn: psycopg.Connection) -> Dict[str, object]:
    """Compare the `product_sales` view to `sales_history.parquet` — the ticket's
    bar, as a reproducible check rather than a one-off eyeballing. Both are the
    `(product, date, quantity)` frame; for the seven bagel Products they must
    agree on row count, Product set, date range and every quantity. Returns a
    report dict with `matches` set."""
    view = db.read_sales(conn).sort_values(["date", "product"], ignore_index=True)
    # Read the parquet file directly, not through sales_history.load_sales_history:
    # since ticket 04 the loader returns the view itself, so going through it here
    # would compare the view against itself. This check is the whole point of
    # comparing the file on disk against the database.
    parquet = pd.read_parquet(sales_history.SALES_HISTORY_PATH).sort_values(
        ["date", "product"], ignore_index=True
    )
    merged = parquet.merge(
        view,
        on=["product", "date"],
        how="outer",
        suffixes=("_parquet", "_view"),
        indicator=True,
    )
    only_parquet = merged[merged["_merge"] == "left_only"]
    only_view = merged[merged["_merge"] == "right_only"]
    both = merged[merged["_merge"] == "both"]
    max_abs_diff = (
        float((both["quantity_parquet"] - both["quantity_view"]).abs().max())
        if len(both)
        else 0.0
    )
    matches = (
        len(only_parquet) == 0
        and len(only_view) == 0
        and max_abs_diff < 1e-6
        and set(view["product"]) == set(parquet["product"])
    )
    return {
        "matches": matches,
        "view_rows": len(view),
        "parquet_rows": len(parquet),
        "view_products": sorted(set(view["product"])),
        "parquet_products": sorted(set(parquet["product"])),
        "only_in_parquet": len(only_parquet),
        "only_in_view": len(only_view),
        "max_abs_quantity_diff": max_abs_diff,
        "date_range_view": _date_range(view),
        "date_range_parquet": _date_range(parquet),
    }


def _date_range(frame: pd.DataFrame) -> Optional[Tuple[str, str]]:
    if frame.empty:
        return None
    return (str(frame["date"].min().date()), str(frame["date"].max().date()))


def _print_report(report: Dict[str, object]) -> None:
    print(
        f"view: {report['view_rows']} rows, "
        f"{len(report['view_products'])} Products, {report['date_range_view']}"
    )
    print(
        f"parquet: {report['parquet_rows']} rows, "
        f"{len(report['parquet_products'])} Products, {report['date_range_parquet']}"
    )
    print(
        f"only in parquet: {report['only_in_parquet']}, "
        f"only in view: {report['only_in_view']}, "
        f"max |Δquantity|: {report['max_abs_quantity_diff']}"
    )
    print("MATCH" if report["matches"] else "MISMATCH")


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "all"
    if command not in ("all", "migrate", "verify"):
        print(f"usage: python migrate.py [migrate|verify]\nunknown command: {command!r}")
        return 2

    with db.connect() as conn:
        if command in ("all", "migrate"):
            counts = run_migration(conn)
            print(
                f"upserted {counts['fact_upserted']} fact rows and "
                f"{counts['raw_inserted']} new raw shards across "
                f"{counts['products']} Products "
                "(re-runs are idempotent: 0 new raw shards on a repeat)"
            )
        if command in ("all", "verify"):
            report = compare_to_parquet(conn)
            _print_report(report)
            if not report["matches"]:
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
