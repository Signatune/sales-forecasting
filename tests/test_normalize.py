"""Locks normalize.py against a real captured Toast Analytics API response.

Fixtures are unmodified API captures:
- menu_week_modifier_sample.json: GET /era/v1/menu/{guid} result for a
  week report (20260703-20260709, groupBy=MODIFIER), captured 2026-07-09.
- restaurants_information_sample.json: GET /era/v1/restaurants-information,
  captured 2026-07-09.
"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from normalize import (
    BAGEL_MODIFIER_NAMES,
    INCLUDED_RESTAURANTS,
    UnexpectedShapeError,
    find_unmapped_bagelish,
    latest_report_files,
    normalize_sales,
    unlisted_active_restaurants,
    validate_modifier_rows,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def week_rows():
    return json.loads((FIXTURES / "menu_week_modifier_sample.json").read_text())


@pytest.fixture()
def restaurants():
    return json.loads((FIXTURES / "restaurants_information_sample.json").read_text())


class TestGoldenNormalization:
    """Exact values computed independently from the captured sample."""

    def test_produces_expected_records(self, week_rows):
        df = normalize_sales(week_rows)
        assert list(df.columns) == ["product", "date", "quantity"]
        assert len(df) == 17

        def qty(product, date):
            match = df[(df["product"] == product) & (df["date"] == pd.Timestamp(date))]
            assert len(match) == 1, f"expected one record for {product} {date}"
            return match["quantity"].iloc[0]

        # sandwich modifier + bulk modifier summed, across both locations
        assert qty("plain", "2026-07-07") == 122.0
        assert qty("sesame", "2026-07-08") == 126.0
        assert qty("everything", "2026-07-09") == 145.0
        # rotating flavors appear only on their day of the week
        assert qty("cinnamon raisin", "2026-07-08") == 25.0  # a Wednesday
        assert qty("pumpernickel", "2026-07-09") == 20.0  # a Thursday
        assert df[df["product"] == "cinnamon raisin"].shape[0] == 1
        # gluten-free variants (sandwich + bulk naming styles summed)
        assert qty("gluten-free plain", "2026-07-08") == 14.0
        assert qty("gluten-free everything", "2026-07-07") == 6.0

    def test_only_known_bagel_products_present(self, week_rows):
        df = normalize_sales(week_rows)
        assert set(df["product"]) <= set(BAGEL_MODIFIER_NAMES)

    def test_sorted_and_no_duplicate_product_dates(self, week_rows):
        df = normalize_sales(week_rows)
        assert not df.duplicated(subset=["product", "date"]).any()
        assert df.sort_values(["date", "product"]).reset_index(drop=True).equals(
            df.reset_index(drop=True)
        )


class TestRestaurantFiltering:
    """Only the Cambridge and Brookline locations are in scope."""

    def test_included_restaurants_are_cambridge_and_brookline(self):
        assert sorted(INCLUDED_RESTAURANTS.values()) == ["Brookline", "Cambridge"]

    def test_rows_from_other_restaurants_excluded(self, week_rows):
        polluted = copy.deepcopy(week_rows)
        fake = copy.deepcopy(week_rows[0])
        # the Production Kitchen: real, active, but out of scope
        fake["restaurantGuid"] = "edc11a00-9da6-417f-8a7e-4fd645803aab"
        fake["modifierName"] = "plain bagel"
        fake["quantitySold"] = 10000.0
        polluted.append(fake)

        assert normalize_sales(week_rows).equals(normalize_sales(polluted))

    def test_unlisted_active_restaurants_surfaced(self, restaurants):
        # the capture holds one active, non-test restaurant we deliberately
        # exclude (the Production Kitchen); testMode ones are not flagged
        flagged = unlisted_active_restaurants(restaurants)
        assert [r["restaurantGuid"] for r in flagged] == [
            "edc11a00-9da6-417f-8a7e-4fd645803aab"
        ]


class TestShapeValidation:
    """Future Toast shape drift must break loudly, not silently."""

    def test_real_sample_passes(self, week_rows):
        validate_modifier_rows(week_rows)

    def test_not_a_list_raises(self):
        with pytest.raises(UnexpectedShapeError):
            validate_modifier_rows({"status": "PROCESSING"})

    def test_missing_key_raises(self, week_rows):
        broken = copy.deepcopy(week_rows)
        del broken[3]["modifierName"]
        with pytest.raises(UnexpectedShapeError, match="modifierName"):
            validate_modifier_rows(broken)

    def test_wrong_type_raises(self, week_rows):
        broken = copy.deepcopy(week_rows)
        broken[0]["quantitySold"] = "36"
        with pytest.raises(UnexpectedShapeError, match="quantitySold"):
            validate_modifier_rows(broken)

    def test_bad_business_date_raises(self, week_rows):
        broken = copy.deepcopy(week_rows)
        broken[0]["businessDate"] = "2026-07-06"
        with pytest.raises(UnexpectedShapeError, match="businessDate"):
            validate_modifier_rows(broken)


class TestUnmappedBagelish:
    def test_real_sample_has_no_unmapped_bagel_modifiers(self, week_rows):
        assert find_unmapped_bagelish(week_rows) == {}

    def test_new_bagel_flavor_is_surfaced(self, week_rows):
        rows = copy.deepcopy(week_rows)
        novel = copy.deepcopy(rows[0])
        novel["modifierName"] = "asiago bagel (fridays only!)"
        novel["quantitySold"] = 7.0
        rows.append(novel)
        assert find_unmapped_bagelish(rows) == {"asiago bagel (fridays only!)": 7.0}


class TestLatestReportFiles:
    def test_picks_latest_capture_per_window(self, tmp_path):
        (tmp_path / "menu_week_20260601_20260607__20260701T000000Z.json").write_text("[]")
        (tmp_path / "menu_week_20260601_20260607__20260708T120000Z.json").write_text("[]")
        (tmp_path / "menu_week_20260608_20260614__20260701T000000Z.json").write_text("[]")
        (tmp_path / "restaurants_information__20260701T000000Z.json").write_text("[]")

        files = latest_report_files(tmp_path, "menu_week")
        assert [f.name for f in files] == [
            "menu_week_20260601_20260607__20260708T120000Z.json",
            "menu_week_20260608_20260614__20260701T000000Z.json",
        ]
