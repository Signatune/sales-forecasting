"""Locks the Orders-API aggregation against the counting semantics that were
reconciled live against Analytics quantitySold on 2026-07-07 (exact match:
raw modifier quantities, voided excluded at every level, nested modifiers
counted). The fixture is synthetic — structurally faithful to real
ordersBulk responses but with no guest data.
"""
import json
from pathlib import Path

import pytest

from toast_client import ToastAuthError
from toast_orders import aggregate_modifier_rows, load_standard_credentials

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def orders():
    return json.loads((FIXTURES / "orders_bulk_synthetic.json").read_text())


class TestAggregateModifierRows:
    def qty_by_name(self, rows):
        return {r["modifierName"]: r["quantitySold"] for r in rows}

    def test_counts_raw_modifier_quantities(self, orders):
        rows = aggregate_modifier_rows(orders, "rest-guid", "20260707")
        by_name = self.qty_by_name(rows)
        # NOT scaled by the parent selection's quantity (sel-1 has qty 2.0):
        # Analytics quantitySold counts the modifier's own quantity.
        assert by_name["everything bagel"] == 2.0  # mod-1 + mod-7
        assert by_name["plain, bulk"] == 6.0

    def test_voided_excluded_at_every_level(self, orders):
        by_name = self.qty_by_name(aggregate_modifier_rows(orders, "r", "20260707"))
        assert "plain bagel" not in by_name   # parent selection voided
        assert "sesame bagel" not in by_name  # voided order and voided modifier

    def test_nested_modifiers_counted(self, orders):
        by_name = self.qty_by_name(aggregate_modifier_rows(orders, "r", "20260707"))
        assert by_name["toasted"] == 1.0

    def test_rows_are_analytics_shaped(self, orders):
        from normalize import validate_modifier_rows

        rows = aggregate_modifier_rows(orders, "rest-guid", "20260707")
        validate_modifier_rows(rows, source="aggregated orders")
        assert all(r["restaurantGuid"] == "rest-guid" for r in rows)
        assert all(r["businessDate"] == "20260707" for r in rows)


ENVIRON = {
    "TOAST_BASE_URL": "https://ws-api.toasttab.com",
    "TOAST_STANDARD_CLIENT_ID": "std-id",
    "TOAST_STANDARD_CLIENT_SECRET": "std-secret",
}


class TestLoadStandardCredentials:
    def test_reads_the_standard_key_from_the_environment(self):
        # Whether these came from a local .env or from secrets on a runner is
        # env.load_env's business; by here it is all just the environment.
        assert load_standard_credentials(ENVIRON) == {
            "clientId": "std-id",
            "clientSecret": "std-secret",
            "baseUrl": "https://ws-api.toasttab.com",
        }

    def test_strips_a_trailing_slash_from_the_base_url(self):
        environ = {**ENVIRON, "TOAST_BASE_URL": "https://ws-api.toasttab.com/"}
        assert (
            load_standard_credentials(environ)["baseUrl"]
            == "https://ws-api.toasttab.com"
        )

    def test_ignores_the_analytics_key(self):
        # The two keys are separate credentials; the analytics one must never
        # stand in for a missing standard one.
        environ = {
            "TOAST_BASE_URL": "https://ws-api.toasttab.com",
            "TOAST_ANALYTICS_CLIENT_ID": "analytics-id",
            "TOAST_ANALYTICS_CLIENT_SECRET": "analytics-secret",
        }
        with pytest.raises(ToastAuthError, match="TOAST_STANDARD_CLIENT_ID"):
            load_standard_credentials(environ)

    def test_missing_standard_key_fails_loudly(self):
        with pytest.raises(ToastAuthError, match="TOAST_STANDARD_CLIENT_SECRET"):
            environ = {k: v for k, v in ENVIRON.items()
                       if k != "TOAST_STANDARD_CLIENT_SECRET"}
            load_standard_credentials(environ)

    def test_missing_base_url_fails_loudly(self):
        environ = {k: v for k, v in ENVIRON.items() if k != "TOAST_BASE_URL"}
        with pytest.raises(ToastAuthError, match="TOAST_BASE_URL"):
            load_standard_credentials(environ)
