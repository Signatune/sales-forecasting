"""Fast bagel Sales backfill via the Toast Orders API.

The Analytics API caps modifier-level reports at ~14 months of history per
hour; the Orders API (standard key, orders:read scope) allows 5 requests per
second per location, so the same history pulls in well under an hour.

Raw orders contain guest PII, so they are NOT saved. Instead each month is
aggregated in memory to per-day modifier quantity rows in the same shape as
Analytics modifier reports and saved to data/raw/orders_agg_{yyyymm}__{ts}.json.
The counting semantics (raw modifier quantities, voided excluded at every
level, nested modifiers counted) were reconciled live against Analytics
quantitySold on 2026-07-07 and matched exactly.

normalize.py merges both sources: Analytics week reports win for any date
they cover; orders aggregates fill the remaining dates.
"""
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from normalize import INCLUDED_RESTAURANTS, validate_modifier_rows
from toast_client import (
    ENV_PATH,
    HTTP_TIMEOUT_SECONDS,
    RAW_DIR,
    RESTAURANT_TZ,
    ToastAuthError,
    UnexpectedShapeError,
    save_raw,
)

PAGE_SIZE = 100
REQUEST_INTERVAL_SECONDS = 0.25  # stay under 5 req/s/location with headroom
EMPTY_MONTHS_TO_STOP = 2


def _build_credentials(
    client_id: str, client_secret: str, base_url: str
) -> Dict[str, str]:
    """The standard-key credential dict `ToastOrdersClient` expects, base URL
    normalized. Both the environment path and the `.env` path build it here, so
    the shape and the `rstrip` rule can't drift between them."""
    return {
        "clientId": client_id,
        "clientSecret": client_secret,
        "baseUrl": base_url.rstrip("/"),
    }


def _standard_credentials_from_env(environ: Dict[str, str]) -> Optional[Dict[str, str]]:
    """Read standard-key credentials from the environment, or `None` if any of
    the three is absent. This is how the GitHub Actions runner supplies them —
    from secrets, the same way `db.py` reads `DATABASE_URL` — so the daily
    capture needs no `.env` file on disk. All-or-nothing: a stray `URL` in the
    ambient environment must not shadow a local `.env`, so a partial set falls
    back to the file."""
    client_id = environ.get("STANDARD_CLIENT_ID")
    client_secret = environ.get("STANDARD_CLIENT_SECRET")
    url = environ.get("URL")
    if client_id and client_secret and url:
        return _build_credentials(client_id, client_secret, url)
    return None


def load_standard_credentials(
    env_path: Path = ENV_PATH, environ: Dict[str, str] = os.environ
) -> Dict[str, str]:
    """Standard-key (Orders API) credentials. Read from `environ` when all three
    of `STANDARD_CLIENT_ID`, `STANDARD_CLIENT_SECRET` and `URL` are set there —
    how the GitHub Actions runner supplies them from secrets — otherwise from the
    `.env` file at `env_path`. Mirrors `db.connection_string`, so the same code
    runs from a laptop and from the runner."""
    from_env = _standard_credentials_from_env(environ)
    if from_env is not None:
        return from_env
    if not env_path.exists():
        raise ToastAuthError(f"credentials file not found: {env_path}")
    text = env_path.read_text()
    creds = {}
    for key, name in (("clientId", "STANDARD_CLIENT_ID"),
                      ("clientSecret", "STANDARD_CLIENT_SECRET")):
        match = re.search(rf"^{name}\s*=\s*(\S+)", text, re.MULTILINE)
        if not match:
            raise ToastAuthError(f"{env_path} is missing the {name} line")
        creds[key] = match.group(1)
    url = re.search(r"^URL\s*=\s*(\S+)", text, re.MULTILINE)
    if not url:
        raise ToastAuthError(f"{env_path} is missing the URL line")
    return _build_credentials(creds["clientId"], creds["clientSecret"], url.group(1))


class ToastOrdersClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str):
        self._base_url = base_url
        self._login_body = {
            "clientId": client_id,
            "clientSecret": client_secret,
            "userAccessType": "TOAST_MACHINE_CLIENT",
        }
        self._session = requests.Session()
        self._token = None

    def login(self) -> None:
        for attempt in range(5):
            try:
                response = self._session.post(
                    f"{self._base_url}/authentication/v1/authentication/login",
                    json=self._login_body,
                    timeout=HTTP_TIMEOUT_SECONDS,
                )
                break
            except requests.RequestException as exc:
                if attempt == 4:
                    raise ToastAuthError(f"Toast login unreachable: {exc}") from exc
                print(f"  {type(exc).__name__} during login, retrying in 30s")
                time.sleep(30)
        if response.status_code != 200:
            raise ToastAuthError(
                f"Toast login failed: HTTP {response.status_code}: {response.text[:500]}"
            )
        token = response.json().get("token", {}).get("accessToken")
        if not token:
            raise ToastAuthError(
                f"Toast login response has no token: {response.text[:500]}"
            )
        self._token = token

    def _get(self, path: str, restaurant_guid: str):
        if self._token is None:
            self.login()
        deadline = time.monotonic() + 3900
        while time.monotonic() < deadline:
            try:
                response = self._session.get(
                    f"{self._base_url}{path}",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Toast-Restaurant-External-ID": restaurant_guid,
                    },
                    timeout=HTTP_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                print(f"  {type(exc).__name__} from {path}, retrying in 30s")
                time.sleep(30)
                continue
            if response.status_code == 401:
                self.login()
                continue
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After", "")
                wait = min(int(retry_after) + 5, 3600) if retry_after.isdigit() else 30
                print(f"  HTTP {response.status_code} from {path}, retrying in {wait}s")
                time.sleep(wait)
                continue
            if response.status_code != 200:
                raise UnexpectedShapeError(
                    f"GET {path} failed: HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
            return response.json()
        raise UnexpectedShapeError(f"GET {path}: retries exhausted after 65min")

    def orders_for_business_date(self, restaurant_guid: str,
                                 business_date: str) -> List[dict]:
        orders: List[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/orders/v2/ordersBulk?businessDate={business_date}"
                f"&pageSize={PAGE_SIZE}&page={page}",
                restaurant_guid,
            )
            if not isinstance(batch, list):
                raise UnexpectedShapeError(
                    f"ordersBulk {business_date} page {page}: expected a JSON "
                    f"array, got {type(batch).__name__}"
                )
            orders.extend(batch)
            if len(batch) < PAGE_SIZE:
                return orders
            page += 1
            time.sleep(REQUEST_INTERVAL_SECONDS)


def _count_modifiers(modifiers, acc: Dict[str, list]) -> None:
    for modifier in modifiers or []:
        if modifier.get("voided"):
            continue
        name = modifier.get("displayName")
        quantity = modifier.get("quantity")
        if isinstance(name, str) and isinstance(quantity, (int, float)):
            item_guid = str((modifier.get("item") or {}).get("guid") or "unknown")
            entry = acc.setdefault(name, [0.0, item_guid])
            entry[0] += float(quantity)
        _count_modifiers(modifier.get("modifiers"), acc)


def aggregate_modifier_rows(orders: List[dict], restaurant_guid: str,
                            business_date: str) -> List[dict]:
    """One Analytics-shaped modifier row per modifier name sold that day.

    Counting matches Analytics quantitySold: the modifier's own quantity
    (not scaled by its parent selection's quantity), skipping voided or
    deleted orders/checks and voided selections/modifiers, and including
    nested modifiers."""
    acc: Dict[str, list] = {}
    for order in orders:
        if order.get("voided") or order.get("deleted"):
            continue
        for check in order.get("checks") or []:
            if check.get("voided") or check.get("deleted"):
                continue
            for selection in check.get("selections") or []:
                if selection.get("voided"):
                    continue
                _count_modifiers(selection.get("modifiers"), acc)
    return [
        {
            "restaurantGuid": restaurant_guid,
            "businessDate": business_date,
            "modifierGuid": item_guid,
            "modifierName": name,
            "quantitySold": quantity,
        }
        for name, (quantity, item_guid) in sorted(acc.items())
    ]


def _month_is_captured(raw_dir: Path, yyyymm: str, today: dt.date) -> bool:
    """A month is settled once captured after the month ended."""
    month_end = f"{yyyymm}31"
    for path in raw_dir.glob(f"orders_agg_{yyyymm}__*.json"):
        fetched_day = path.name.split("__")[1][:8]
        if fetched_day > month_end:
            return True
    return False


def _days_in_month(year: int, month: int, today: dt.date) -> List[dt.date]:
    day = dt.date(year, month, 1)
    days = []
    while day.month == month and day <= today:
        days.append(day)
        day += dt.timedelta(days=1)
    return days


def pull_orders_history(raw_dir: Path = RAW_DIR) -> None:
    creds = load_standard_credentials()
    client = ToastOrdersClient(
        creds["baseUrl"], creds["clientId"], creds["clientSecret"]
    )
    client.login()
    print("authenticated against Toast (standard key)")

    today = dt.datetime.now(RESTAURANT_TZ).date()
    year, month = today.year, today.month
    empty_months = 0
    while empty_months < EMPTY_MONTHS_TO_STOP:
        yyyymm = f"{year:04d}{month:02d}"
        if _month_is_captured(raw_dir, yyyymm, today):
            cached = sorted(raw_dir.glob(f"orders_agg_{yyyymm}__*.json"))[-1]
            rows = json.loads(cached.read_text())
            print(f"orders {yyyymm}: already captured ({len(rows)} rows), skipping")
            empty_months = empty_months + 1 if not rows else 0
        else:
            rows: List[dict] = []
            n_orders = 0
            for day in _days_in_month(year, month, today):
                business_date = f"{day:%Y%m%d}"
                for guid, label in INCLUDED_RESTAURANTS.items():
                    orders = client.orders_for_business_date(guid, business_date)
                    n_orders += len(orders)
                    rows.extend(aggregate_modifier_rows(orders, guid, business_date))
                    time.sleep(REQUEST_INTERVAL_SECONDS)
            validate_modifier_rows(rows, source=f"orders {yyyymm}")
            save_raw(raw_dir, f"orders_agg_{yyyymm}", rows)
            print(f"orders {yyyymm}: {n_orders} orders -> {len(rows)} modifier rows")
            empty_months = empty_months + 1 if n_orders == 0 else 0
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    print(f"done: reached {EMPTY_MONTHS_TO_STOP} consecutive empty months "
          f"(history starts after {year:04d}-{month:02d})")


if __name__ == "__main__":
    sys.exit(pull_orders_history())
