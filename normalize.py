"""Normalize raw Toast modifier reports into canonical Sales records.

The shared normalization library: the rules that turn raw Toast modifier
report rows into canonical Sales — in-scope restaurants only, configured
modifiers only, names normalized, and (for the seven mapped bagels) rolled up
through BAGEL_MODIFIER_NAMES. `migrate.py` (the one-time history load),
`daily_capture.py` (the scheduled Orders-only capture), and `toast_client.py`
all normalize through these functions, so no two paths can drift.

This module no longer writes a parquet. Since Postgres became the source of
truth (ADR 0003) the canonical history lives in the `sales` fact and the
`product_sales` view; the file-based rebuild — reading every raw file and
overwriting `data/sales_history.parquet` on every run — was retired with the
rest of the file-based path (ticket 07). `load_sales_rows` and
`latest_report_files` remain because `migrate.py` still reads the saved raw
history to build the fact.

Bagel Sales in Toast are recorded as modifiers only — there is no menu item
per flavor. The three main flavors each have two modifiers (one used on
sandwiches, one for bulk orders); a Product's daily Sales is the sum of its
modifiers' quantities across both locations.

Two facts about Toast modifiers shape everything below. Modifier names are
edited in place, so one Product spans several spellings over the history
(see BAGEL_MODIFIER_NAMES). And "modifier" covers both configured menu
entities and free text a guest or server types on a check — only the former
carry a modifierGuid, and only the former are Sales.
"""
import collections
import datetime as dt
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pandas as pd

RAW_DIR = Path(__file__).parent / "data" / "raw"

# Scope is the Cambridge and Brookline locations only (see ticket 01).
INCLUDED_RESTAURANTS = {
    "28e5b269-1c1c-45df-81a8-1d268c005dfa": "Cambridge",
    "9ae70079-b9cd-4b92-8457-c86bc823188f": "Brookline",
}

# Product -> the Toast modifier names whose quantities make up its Sales.
# Confirmed against the full pulled history on 2026-07-10; matching is by
# name (not GUID) because the same modifier name exists under multiple
# GUIDs (e.g. two distinct "plain, bulk" modifiers).
#
# Toast modifier names are edited in place, so a Product's history spans
# several spellings. Every historical name a Product ever had must stay
# listed here: drop one and that Product's Sales series takes a phantom step
# change on the day of the rename. Renames seen so far are marked below with
# the window each name was live.
BAGEL_MODIFIER_NAMES: Dict[str, tuple] = {
    "plain": (
        "plain bagel",
        "plain, bulk",
        "plain bagel [allergens: wheat]",  # 2025-02-06 only
    ),
    "sesame": ("sesame bagel", "sesame, bulk"),
    "everything": ("everything bagel", "everything, bulk"),
    "cinnamon raisin": ("cinnamon raisin bagel (wednesdays only!)",),
    "pumpernickel": (
        "pumpernickel bagel (thursdays only!)",
        "pumpernickel bagel - (thursdays only!)",  # ..2025-04-10
    ),
    "gluten-free plain": (
        "gluten-free plain bagel (original sunshine, contains wheat, must be toasted)",
        "plain gluten-free",
        "gluten free plain bagel (original sunshine, contains wheat, must be toasted)",  # ..2025-02-26
        "gluten free plain bagel (must be toasted)",  # ..2024-05-30
    ),
    "gluten-free everything": (
        "gluten-free everything bagel (original sunshine, contains wheat, must be toasted)",
        "everything gluten-free",
        "gluten free everything bagel (original sunshine, contains wheat, must be toasted)",  # ..2025-03-08
        "gluten free everything bagel (must be toasted)",  # ..2024-05-16
    ),
}

_MODIFIER_TO_PRODUCT = {
    name: product
    for product, names in BAGEL_MODIFIER_NAMES.items()
    for name in names
}

# Configured bagel modifiers deliberately left out of the Sales history.
# Listed so they are a recorded decision rather than a warning on every run.
EXCLUDED_MODIFIER_NAMES = frozenset({
    # A June promo: ~440 units across 21 sales-days over three summers, under
    # a fresh date-scoped name each run. Too sparse to forecast (ticket 01).
    "rainbow bagel",
    "rainbow bagel (june 8th & 9th)",
    "rainbow bagel (only june 28th)",
    "rainbow bagel (available 6/14-6/15)",
    "rainbow bagel (available 6/14 & 6/15)",
    "rainbow bagel (6/1-6/7 only)",
})

# Modifier names that look like a bagel flavor we don't map yet (a new
# rotating flavor, a renamed modifier). Surfaced so drift is loud.
_BAGELISH = re.compile(r"bagel|(, |^)bulk$|gluten-free$")

_REQUIRED_ROW_FIELDS = {
    "businessDate": str,
    "modifierName": str,
    "quantitySold": (int, float),
    "restaurantGuid": str,
}

# Toast assigns a modifierGuid only to configured menu entities. Open text a
# guest or server types on a check ("Light on the hazelnut please!") arrives
# as a modifier row with no GUID at all from the Analytics API, and with this
# sentinel from toast_orders.py. Absent is therefore fine; present-but-wrong-
# type is still drift.
_OPTIONAL_ROW_FIELDS = {"modifierGuid": str}
_UNKNOWN_GUID = "unknown"

# The Toast grain every raw modifier report row carries. The history and the
# daily capture are modifier-grained throughout (ADR 0005); items exist in the
# fact's schema but there is no item report to normalize here.
MODIFIER_SOURCE_TYPE = "modifier"


def is_configured_modifier(row: dict) -> bool:
    """Whether the row is a menu entity rather than text someone typed. Only
    configured modifiers can be Product Sales, or evidence of shape drift. Public
    so the history migration (migrate.py) selects the fact's configured sources
    by the same rule that builds the parquet, and the two can't drift."""
    return row.get("modifierGuid", _UNKNOWN_GUID) != _UNKNOWN_GUID


class UnexpectedShapeError(RuntimeError):
    """The Toast response does not look like a modifier-grouped menu report."""


def validate_modifier_rows(rows, source: str = "response") -> None:
    """Raise UnexpectedShapeError unless rows is a list of well-formed
    modifier report rows. An empty list is valid (closed days)."""
    if not isinstance(rows, list):
        raise UnexpectedShapeError(
            f"{source}: expected a JSON array of report rows, got {type(rows).__name__}"
        )
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise UnexpectedShapeError(f"{source}: row {i} is not an object")
        for field, types in _REQUIRED_ROW_FIELDS.items():
            if field not in row:
                raise UnexpectedShapeError(f"{source}: row {i} is missing {field!r}")
            if not isinstance(row[field], types):
                raise UnexpectedShapeError(
                    f"{source}: row {i} field {field!r} has unexpected type "
                    f"{type(row[field]).__name__} (value {row[field]!r})"
                )
        for field, types in _OPTIONAL_ROW_FIELDS.items():
            if field in row and not isinstance(row[field], types):
                raise UnexpectedShapeError(
                    f"{source}: row {i} field {field!r} has unexpected type "
                    f"{type(row[field]).__name__} (value {row[field]!r})"
                )
        if not re.fullmatch(r"\d{8}", row["businessDate"]):
            raise UnexpectedShapeError(
                f"{source}: row {i} businessDate {row['businessDate']!r} "
                "is not YYYYMMDD"
            )


def normalize_sales(rows: List[dict]) -> pd.DataFrame:
    """Canonical Sales records from modifier report rows: one row per
    (product, date, quantity), summed over each Product's modifiers and
    over the in-scope restaurants."""
    validate_modifier_rows(rows)
    records = {}
    for row in rows:
        if row["restaurantGuid"] not in INCLUDED_RESTAURANTS:
            continue
        if not is_configured_modifier(row):
            continue
        product = _MODIFIER_TO_PRODUCT.get(row["modifierName"].strip().lower())
        if product is None:
            continue
        key = (product, row["businessDate"])
        records[key] = records.get(key, 0.0) + float(row["quantitySold"])
    df = pd.DataFrame(
        [
            {"product": product, "date": date, "quantity": quantity}
            for (product, date), quantity in records.items()
        ],
        columns=["product", "date", "quantity"],
    )
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values(["date", "product"], ignore_index=True)


def modifier_fact_rows(rows: List[dict]) -> List[Tuple]:
    """The canonical Sales fact at source grain from raw modifier report rows:
    one `(date, restaurant_guid, source_type, source_name, quantity)` tuple per
    `(date, restaurant, normalized modifier name)`, quantity summed over the rows
    sharing that key. `date` is a python `date`, `quantity` a float,
    `source_type` always `modifier`.

    This is `normalize_sales` at the fact's grain rather than the Product's: it
    applies the same three rules — in-scope restaurants only, configured
    modifiers only (a `modifierGuid`), names normalized (stripped, lower-cased) —
    but keeps *every* configured modifier, not just the mapped bagels, and does
    not roll up through `BAGEL_MODIFIER_NAMES` (that mapping now lives in the
    database, ADR 0005). Both the history migration (`migrate.py`) and the daily
    capture build the fact through this one function, so their normalization can
    never drift from each other or from the parquet."""
    validate_modifier_rows(rows)
    totals: Dict[Tuple[dt.date, str, str], float] = collections.defaultdict(float)
    for row in rows:
        if row["restaurantGuid"] not in INCLUDED_RESTAURANTS:
            continue
        if not is_configured_modifier(row):
            continue
        name = row["modifierName"].strip().lower()
        key = (business_date(row["businessDate"]), row["restaurantGuid"], name)
        totals[key] += float(row["quantitySold"])
    return [
        (date, restaurant_guid, MODIFIER_SOURCE_TYPE, name, quantity)
        for (date, restaurant_guid, name), quantity in totals.items()
    ]


def business_date(yyyymmdd: str) -> dt.date:
    """A Toast `YYYYMMDD` business-date string as a python `date`."""
    return dt.datetime.strptime(yyyymmdd, "%Y%m%d").date()


def find_unmapped_bagelish(rows: List[dict]) -> Dict[str, float]:
    """Bagel-looking modifier names not in BAGEL_MODIFIER_NAMES, with total
    quantities — new flavors or renames that would otherwise vanish silently."""
    validate_modifier_rows(rows)
    unmapped: Dict[str, float] = {}
    for row in rows:
        if row["restaurantGuid"] not in INCLUDED_RESTAURANTS:
            continue
        if not is_configured_modifier(row):
            continue
        name = row["modifierName"].strip().lower()
        if name in _MODIFIER_TO_PRODUCT or name in EXCLUDED_MODIFIER_NAMES:
            continue
        if not _BAGELISH.search(name):
            continue
        unmapped[name] = unmapped.get(name, 0.0) + float(row["quantitySold"])
    return unmapped


def unlisted_active_restaurants(restaurants_info: List[dict]) -> List[dict]:
    """Active, non-test restaurants that are not in scope — surfaced so a
    new location can't silently be missing from the Sales history."""
    if not isinstance(restaurants_info, list):
        raise UnexpectedShapeError(
            "restaurants-information: expected a JSON array, got "
            f"{type(restaurants_info).__name__}"
        )
    return [
        r
        for r in restaurants_info
        if r.get("active")
        and not r.get("testMode")
        and r.get("restaurantGuid") not in INCLUDED_RESTAURANTS
    ]


def latest_report_files(raw_dir: Path, prefix: str) -> List[Path]:
    """The most recently captured raw file per report window, sorted by
    window. Filenames look like {prefix}_{start}_{end}__{fetchedAt}.json."""
    by_window: Dict[str, Path] = {}
    for path in sorted(raw_dir.glob(f"{prefix}_*__*.json")):
        window = path.name.split("__")[0]
        by_window[window] = path  # sorted order: later capture wins
    return [by_window[w] for w in sorted(by_window)]


def _week_window_dates(path: Path) -> Set[str]:
    """Every business date inside a menu_week file's window (from its
    filename): the report is authoritative for all of them, including days
    with no rows (closed days)."""
    match = re.fullmatch(r"menu_week_(\d{8})_(\d{8})__.*\.json", path.name)
    if not match:
        raise UnexpectedShapeError(f"unrecognized raw filename: {path.name}")
    start = dt.datetime.strptime(match.group(1), "%Y%m%d").date()
    end = dt.datetime.strptime(match.group(2), "%Y%m%d").date()
    return {
        f"{start + dt.timedelta(days=i):%Y%m%d}"
        for i in range((end - start).days + 1)
    }


def load_sales_rows(raw_dir: Path = RAW_DIR) -> List[dict]:
    """All modifier rows: Analytics week reports first, then orders-derived
    aggregates (toast_orders.py) for dates no week report covers."""
    week_files = latest_report_files(raw_dir, "menu_week")
    orders_files = latest_report_files(raw_dir, "orders_agg")
    if not week_files and not orders_files:
        raise FileNotFoundError(
            f"no menu_week or orders_agg raw reports under {raw_dir} — this reads "
            "the pre-migration raw history (migrate.py); a fresh clone no longer "
            "carries it (ticket 07)"
        )
    rows: List[dict] = []
    covered: Set[str] = set()
    for path in week_files:
        content = json.loads(path.read_text())
        validate_modifier_rows(content, source=path.name)
        rows.extend(content)
        covered |= _week_window_dates(path)
    for path in orders_files:
        content = json.loads(path.read_text())
        validate_modifier_rows(content, source=path.name)
        rows.extend(r for r in content if r["businessDate"] not in covered)
    return rows
