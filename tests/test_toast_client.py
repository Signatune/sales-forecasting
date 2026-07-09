import datetime as dt

import pytest

from normalize import UnexpectedShapeError
from toast_client import (
    ToastAuthError,
    is_window_captured,
    load_credentials,
    validate_daily_totals_rows,
    week_windows,
    year_windows,
)

ENV_TEXT = """{
  "userAccessType": "TOAST_MACHINE_CLIENT",
  "clientId": "abc123",
  "clientSecret": "s3cret",
}

restaurantGUID = IQID_3
URL = https://ws-api.toasttab.com
"""


class TestLoadCredentials:
    def test_parses_json_ish_env_file(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(ENV_TEXT)
        creds = load_credentials(env)
        assert creds == {
            "userAccessType": "TOAST_MACHINE_CLIENT",
            "clientId": "abc123",
            "clientSecret": "s3cret",
            "baseUrl": "https://ws-api.toasttab.com",
        }

    def test_missing_secret_fails_loudly(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(ENV_TEXT.replace("clientSecret", "clientTypo"))
        with pytest.raises(ToastAuthError, match="clientSecret"):
            load_credentials(env)

    def test_missing_file_fails_loudly(self, tmp_path):
        with pytest.raises(ToastAuthError, match="not found"):
            load_credentials(tmp_path / ".env")


class TestWindows:
    def test_week_windows_cover_range_without_overlap(self):
        windows = week_windows(dt.date(2026, 6, 1), dt.date(2026, 7, 9))
        assert windows[0] == (dt.date(2026, 6, 1), dt.date(2026, 6, 7))
        assert windows[-1] == (dt.date(2026, 7, 6), dt.date(2026, 7, 9))
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
            assert next_start == prev_end + dt.timedelta(days=1)

    def test_year_windows_walk_backwards_from_today(self):
        windows = year_windows(dt.date(2026, 7, 9))
        assert windows[0] == (dt.date(2026, 1, 1), dt.date(2026, 7, 9))
        assert windows[1] == (dt.date(2025, 1, 1), dt.date(2025, 12, 31))


class TestRetryAfter:
    def test_429_honors_retry_after_header(self, monkeypatch):
        from toast_client import ToastAnalyticsClient

        client = ToastAnalyticsClient("https://example.test", "id", "secret", "TYPE")
        client._token = "tok"

        class FakeResponse:
            def __init__(self, status_code, headers=None, payload=None):
                self.status_code = status_code
                self.headers = headers or {}
                self._payload = payload

            def json(self):
                return self._payload

        responses = iter([
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, payload=[]),
        ])
        sleeps = []
        monkeypatch.setattr(
            client._session, "request", lambda *a, **kw: next(responses)
        )
        monkeypatch.setattr("toast_client.time.sleep", sleeps.append)

        assert client._request("GET", "/era/v1/menu/some-guid") == []
        assert sleeps == [12]  # Retry-After 7s + 5s buffer

    def test_connection_error_retries_instead_of_crashing(self, monkeypatch):
        import requests

        from toast_client import ToastAnalyticsClient

        client = ToastAnalyticsClient("https://example.test", "id", "secret", "TYPE")
        client._token = "tok"

        class OkResponse:
            status_code = 200
            headers = {}

            def json(self):
                return []

        calls = iter([requests.ConnectionError("died during sleep"), OkResponse()])

        def fake_request(*a, **kw):
            result = next(calls)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(client._session, "request", fake_request)
        monkeypatch.setattr("toast_client.time.sleep", lambda s: None)

        assert client._request("GET", "/era/v1/menu/some-guid") == []


class TestValidateDailyTotalsRows:
    GOOD_ROW = {"businessDate": "20260701", "restaurantGuid": "abc-123"}

    def test_valid_rows_pass(self):
        validate_daily_totals_rows([self.GOOD_ROW], source="test")
        validate_daily_totals_rows([], source="test")

    def test_not_a_list_raises(self):
        with pytest.raises(UnexpectedShapeError, match="JSON array"):
            validate_daily_totals_rows({"status": "PROCESSING"}, source="test")

    def test_missing_restaurant_guid_raises(self):
        with pytest.raises(UnexpectedShapeError, match="restaurantGuid"):
            validate_daily_totals_rows([{"businessDate": "20260701"}], source="test")

    def test_bad_business_date_raises(self):
        broken = dict(self.GOOD_ROW, businessDate="2026-07-01")
        with pytest.raises(UnexpectedShapeError, match="businessDate"):
            validate_daily_totals_rows([broken], source="test")


class TestIsWindowCaptured:
    def test_settled_window_with_capture_after_end(self, tmp_path):
        (tmp_path / "menu_week_20260601_20260607__20260701T000000Z.json").write_text("[]")
        assert is_window_captured(
            tmp_path, "menu_week", dt.date(2026, 6, 1), dt.date(2026, 6, 7)
        )

    def test_window_captured_on_its_last_day_is_repulled(self, tmp_path):
        (tmp_path / "menu_week_20260706_20260709__20260709T120000Z.json").write_text("[]")
        assert not is_window_captured(
            tmp_path, "menu_week", dt.date(2026, 7, 6), dt.date(2026, 7, 9)
        )

    def test_uncaptured_window(self, tmp_path):
        assert not is_window_captured(
            tmp_path, "menu_week", dt.date(2026, 6, 1), dt.date(2026, 6, 7)
        )
