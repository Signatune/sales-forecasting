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


class TestLoadStandardCredentials:
    def test_parses_standard_key_lines(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            '{\n  "clientId": "analytics-id",\n  "clientSecret": "analytics-secret",\n'
            '  "userAccessType": "TOAST_MACHINE_CLIENT",\n}\n'
            "URL = https://ws-api.toasttab.com\n"
            "STANDARD_CLIENT_ID = std-id\n"
            "STANDARD_CLIENT_SECRET = std-secret\n"
        )
        creds = load_standard_credentials(env)
        assert creds == {
            "clientId": "std-id",
            "clientSecret": "std-secret",
            "baseUrl": "https://ws-api.toasttab.com",
        }

    def test_missing_standard_key_fails_loudly(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("URL = https://ws-api.toasttab.com\n")
        with pytest.raises(ToastAuthError, match="STANDARD_CLIENT_ID"):
            load_standard_credentials(env)
