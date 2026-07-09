"""Pull the full available daily Sales history for the bagel family from
the Toast Analytics API.

The Analytics API is asynchronous: POST /era/v1/menu[/week] creates a report
and returns a GUID; GET /era/v1/menu/{guid} returns [] until the report is
ready (and also for windows with no sales, e.g. closed days).

Modifier-level detail — the only place bagel Sales exist — is only available
from the day/week report endpoints, so the full history is pulled as a loop
of 7-day windows. Restaurant-level daily totals (custom windows, max 366
days) are pulled first to discover how far back history goes and to
cross-check that no sales day is missing from the weekly reports.

Every raw response is saved timestamped under data/raw/ before any
processing, so normalization can be rebuilt without re-hitting the API.
"""
import datetime as dt
import json
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from normalize import (
    INCLUDED_RESTAURANTS,
    UnexpectedShapeError,
    unlisted_active_restaurants,
    validate_modifier_rows,
)

ENV_PATH = Path(__file__).parent / ".env"
RAW_DIR = Path(__file__).parent / "data" / "raw"
RESTAURANT_TZ = ZoneInfo("America/New_York")

REPORT_POLL_SECONDS = 5
REPORT_TIMEOUT_SECONDS = 180
HTTP_TIMEOUT_SECONDS = 60

# Published Toast rate limits for report creation.
CUSTOM_CREATES_PER_HOUR = 10
WEEK_CREATES_PER_MINUTE = 10
WEEK_CREATES_PER_HOUR = 60


class ToastAuthError(RuntimeError):
    """Authentication against the Toast API failed."""


def load_credentials(env_path: Path = ENV_PATH) -> Dict[str, str]:
    """Read API credentials from the .env file.

    The file is JSON-ish (trailing commas) followed by KEY = value lines;
    parse it leniently but fail loudly on anything missing.
    """
    if not env_path.exists():
        raise ToastAuthError(f"credentials file not found: {env_path}")
    text = env_path.read_text()
    creds = {}
    for key in ("userAccessType", "clientId", "clientSecret"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
        if not match:
            raise ToastAuthError(f"{env_path} is missing {key!r}")
        creds[key] = match.group(1)
    url = re.search(r"^URL\s*=\s*(\S+)", text, re.MULTILINE)
    if not url:
        raise ToastAuthError(f"{env_path} is missing the URL line")
    creds["baseUrl"] = url.group(1).rstrip("/")
    return creds


class RateLimiter:
    """Blocks until a report creation is allowed under the given caps."""

    def __init__(self, caps: List[Tuple[int, int]]):
        self._caps = caps  # (max_calls, per_seconds)
        self._calls: deque = deque()

    def wait(self) -> None:
        while True:
            now = time.monotonic()
            waits = []
            for max_calls, per_seconds in self._caps:
                recent = [t for t in self._calls if t > now - per_seconds]
                if len(recent) >= max_calls:
                    waits.append(recent[0] + per_seconds - now)
            if not waits:
                break
            wait = max(waits) + 1
            print(f"  rate limit: waiting {wait:.0f}s before next report request")
            time.sleep(wait)
        self._calls.append(time.monotonic())
        while len(self._calls) > 100:
            self._calls.popleft()


class ToastAnalyticsClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str,
                 user_access_type: str):
        self._base_url = base_url
        self._login_body = {
            "clientId": client_id,
            "clientSecret": client_secret,
            "userAccessType": user_access_type,
        }
        self._session = requests.Session()
        self._token: Optional[str] = None
        self._custom_limiter = RateLimiter([(CUSTOM_CREATES_PER_HOUR, 3600)])
        self._week_limiter = RateLimiter(
            [(WEEK_CREATES_PER_MINUTE, 60), (WEEK_CREATES_PER_HOUR, 3600)]
        )

    def login(self) -> None:
        response = self._session.post(
            f"{self._base_url}/authentication/v1/authentication/login",
            json=self._login_body,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
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

    def _request(self, method: str, path: str, body=None):
        if self._token is None:
            self.login()
        # 429s can persist for most of an hour when the rate-limit budget
        # is already spent, so wait on a deadline rather than an attempt count.
        deadline = time.monotonic() + 3900
        while time.monotonic() < deadline:
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            if response.status_code == 401:
                self.login()
                continue
            if response.status_code == 429 or response.status_code >= 500:
                # Toast tells us when the rate-limit window resets
                # (doc.toasttab.com/doc/devguide/apiRateLimiting.html)
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(int(retry_after) + 5, 3600)
                else:
                    wait = 120 if response.status_code == 429 else 30
                print(
                    f"  HTTP {response.status_code} from {path}, retrying in {wait}s"
                )
                time.sleep(wait)
                continue
            if response.status_code != 200:
                raise UnexpectedShapeError(
                    f"{method} {path} failed: HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
            return response.json()
        raise UnexpectedShapeError(f"{method} {path}: retries exhausted after 65min")

    def restaurants_information(self) -> list:
        return self._request("GET", "/era/v1/restaurants-information")

    def create_menu_report(self, start: dt.date, end: dt.date,
                           group_by: Optional[List[str]] = None,
                           time_range: Optional[str] = None) -> str:
        body = {
            "startBusinessDate": int(start.strftime("%Y%m%d")),
            "endBusinessDate": int(end.strftime("%Y%m%d")),
            "restaurantIds": [],
            "excludedRestaurantIds": [],
        }
        if group_by:
            body["groupBy"] = group_by
        path = "/era/v1/menu" + (f"/{time_range}" if time_range else "")
        limiter = self._week_limiter if time_range else self._custom_limiter
        limiter.wait()
        guid = self._request("POST", path, body)
        if not isinstance(guid, str):
            raise UnexpectedShapeError(
                f"POST {path} did not return a report GUID string: {guid!r}"
            )
        return guid

    def fetch_report(self, guid: str) -> list:
        """Poll for a report's rows. Returns [] for windows that stay empty
        past the timeout (the API returns [] both while processing and for
        windows with no sales, e.g. days the restaurants were closed)."""
        deadline = time.monotonic() + REPORT_TIMEOUT_SECONDS
        while True:
            rows = self._request("GET", f"/era/v1/menu/{guid}")
            if isinstance(rows, list) and rows:
                return rows
            if not isinstance(rows, list):
                raise UnexpectedShapeError(
                    f"report {guid}: expected a JSON array, got {rows!r}"
                )
            if time.monotonic() > deadline:
                return []
            time.sleep(REPORT_POLL_SECONDS)


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def save_raw(raw_dir: Path, name: str, payload) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{name}__{_timestamp()}.json"
    path.write_text(json.dumps(payload))
    return path


def is_window_captured(raw_dir: Path, prefix: str, start: dt.date,
                       end: dt.date) -> bool:
    """A window is settled once captured after its end date; the window
    containing today must be re-pulled."""
    window = f"{prefix}_{start:%Y%m%d}_{end:%Y%m%d}"
    for path in raw_dir.glob(f"{window}__*.json"):
        fetched_day = path.name.split("__")[1][:8]
        if fetched_day > f"{end:%Y%m%d}":
            return True
    return False


def year_windows(today: dt.date) -> List[Tuple[dt.date, dt.date]]:
    """Calendar-year windows from the current year backwards (stable across
    runs so already-captured years are reused)."""
    windows = [(dt.date(today.year, 1, 1), today)]
    year = today.year - 1
    while year >= 2010:  # long before any Toast deployment
        windows.append((dt.date(year, 1, 1), dt.date(year, 12, 31)))
        year -= 1
    return windows


def week_windows(earliest: dt.date, today: dt.date) -> List[Tuple[dt.date, dt.date]]:
    windows = []
    start = earliest
    while start <= today:
        windows.append((start, min(start + dt.timedelta(days=6), today)))
        start += dt.timedelta(days=7)
    return windows


def validate_daily_totals_rows(rows, source: str) -> None:
    """Daily-totals reports carry no modifier fields; check just the shape
    _sales_dates_in relies on, so drift raises clearly instead of KeyError."""
    if not isinstance(rows, list):
        raise UnexpectedShapeError(
            f"{source}: expected a JSON array of report rows, got {type(rows).__name__}"
        )
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise UnexpectedShapeError(f"{source}: row {i} is not an object")
        for field in ("businessDate", "restaurantGuid"):
            if not isinstance(row.get(field), str):
                raise UnexpectedShapeError(
                    f"{source}: row {i} is missing string field {field!r}: "
                    f"{row.get(field)!r}"
                )
        if not re.fullmatch(r"\d{8}", row["businessDate"]):
            raise UnexpectedShapeError(
                f"{source}: row {i} businessDate {row['businessDate']!r} is not YYYYMMDD"
            )


def _sales_dates_in(rows: list) -> set:
    return {
        row["businessDate"]
        for row in rows
        if row.get("restaurantGuid") in INCLUDED_RESTAURANTS
    }


def discover_sales_dates(client: ToastAnalyticsClient, raw_dir: Path,
                         today: dt.date) -> set:
    """Every business date with any sales, from restaurant-level daily
    totals, walking calendar years backwards.

    Stops only after two consecutive empty years: the API returns [] both
    for a report that is still processing and for a window with no sales,
    so a single empty year is not proof that history has ended."""
    sales_dates: set = set()
    empty_years = 0
    for start, end in year_windows(today):
        cached = sorted(raw_dir.glob(f"daily_totals_{start:%Y%m%d}_{end:%Y%m%d}__*.json"))
        if cached and is_window_captured(raw_dir, "daily_totals", start, end):
            rows = json.loads(cached[-1].read_text())
        else:
            print(f"daily totals {start}..{end}: requesting")
            guid = client.create_menu_report(start, end)
            rows = client.fetch_report(guid)
            save_raw(raw_dir, f"daily_totals_{start:%Y%m%d}_{end:%Y%m%d}", rows)
        validate_daily_totals_rows(rows, source=f"daily totals {start}..{end}")
        dates = _sales_dates_in(rows)
        print(f"daily totals {start}..{end}: {len(dates)} sales days")
        if dates:
            empty_years = 0
            sales_dates |= dates
        else:
            empty_years += 1
            if empty_years >= 2:
                break
    if not sales_dates:
        raise UnexpectedShapeError(
            "no sales days found in any year window — wrong credentials or "
            "restaurant scope?"
        )
    return sales_dates


def pull_history(raw_dir: Path = RAW_DIR) -> None:
    creds = load_credentials()
    client = ToastAnalyticsClient(
        creds["baseUrl"], creds["clientId"], creds["clientSecret"],
        creds["userAccessType"],
    )
    client.login()
    print("authenticated against Toast")

    restaurants = client.restaurants_information()
    save_raw(raw_dir, "restaurants_information", restaurants)
    for r in unlisted_active_restaurants(restaurants):
        print(
            f"note: excluding out-of-scope active restaurant "
            f"{r['restaurantName']!r} ({r['restaurantGuid']}) — Sales history "
            "covers Cambridge and Brookline only"
        )

    today = dt.datetime.now(RESTAURANT_TZ).date()
    sales_dates = discover_sales_dates(client, raw_dir, today)
    earliest = dt.datetime.strptime(min(sales_dates), "%Y%m%d").date()
    print(f"history spans {earliest}..{max(sales_dates)} "
          f"({len(sales_dates)} sales days)")

    # newest first: the recent history the pilot's forecast and backtest
    # need arrives in the first hour; older years back-fill afterwards
    windows = list(reversed(week_windows(earliest, today)))
    missing_dates: set = set()
    for i, (start, end) in enumerate(windows, 1):
        if is_window_captured(raw_dir, "menu_week", start, end):
            continue
        guid = client.create_menu_report(start, end, group_by=["MODIFIER"],
                                         time_range="week")
        rows = client.fetch_report(guid)
        validate_modifier_rows(rows, source=f"week report {start}..{end}")
        save_raw(raw_dir, f"menu_week_{start:%Y%m%d}_{end:%Y%m%d}", rows)

        expected = {
            d for d in sales_dates
            if f"{start:%Y%m%d}" <= d <= f"{end:%Y%m%d}"
        }
        got = _sales_dates_in(rows)
        missing = expected - got
        if missing:
            missing_dates |= missing
            print(f"WARNING: week {start}..{end}: sales days with no modifier "
                  f"rows: {sorted(missing)}")
        print(f"[{i}/{len(windows)}] week {start}..{end}: {len(rows)} rows")

    if missing_dates:
        print(f"WARNING: {len(missing_dates)} sales days had no modifier rows; "
              "raw daily totals are saved for comparison")
    print(f"done: raw reports in {raw_dir}")


if __name__ == "__main__":
    sys.exit(pull_history())
