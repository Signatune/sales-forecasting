"""Locks normalize.py against a real captured Toast Analytics API response.

Fixtures are unmodified API captures:
- menu_week_modifier_sample.json: GET /era/v1/menu/{guid} result for a
  week report (20260703-20260709, groupBy=MODIFIER), captured 2026-07-09.
- restaurants_information_sample.json: GET /era/v1/restaurants-information,
  captured 2026-07-09.
- menu_week_freetext_modifier_row.json: the one row of the 20260425-20260501
  week report (captured 2026-07-10) that carries no modifierGuid — an
  open-text modifier typed by a server rather than a configured menu entity.
"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from normalize import (
    BAGEL_MODIFIER_NAMES,
    EXCLUDED_MODIFIER_NAMES,
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


@pytest.fixture()
def freetext_rows():
    return json.loads((FIXTURES / "menu_week_freetext_modifier_row.json").read_text())


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


class TestFreeTextModifiers:
    """Open-text modifiers a server types on a check ("Light on the hazelnut
    please!") carry no modifierGuid — Toast only assigns GUIDs to configured
    menu entities. They must not stop a pull: normalization matches on
    modifierName and never reads the GUID."""

    def test_row_without_modifier_guid_is_valid(self, freetext_rows):
        assert "modifierGuid" not in freetext_rows[0]
        validate_modifier_rows(freetext_rows)

    def test_modifier_guid_still_type_checked_when_present(self, week_rows):
        broken = copy.deepcopy(week_rows)
        broken[0]["modifierGuid"] = 12345
        with pytest.raises(UnexpectedShapeError, match="modifierGuid"):
            validate_modifier_rows(broken)

    def test_free_text_modifier_is_not_a_bagel_product(self, week_rows, freetext_rows):
        rows = copy.deepcopy(week_rows) + freetext_rows
        assert normalize_sales(rows).equals(normalize_sales(week_rows))

    def test_free_text_modifier_is_not_flagged_as_unmapped_bagelish(
        self, freetext_rows
    ):
        assert find_unmapped_bagelish(freetext_rows) == {}

    def test_typing_a_product_name_as_free_text_is_not_a_sale(self, week_rows):
        """A guest note that happens to read "everything bagel" is not Toast
        selling an everything bagel. Only configured menu modifiers are Sales."""
        rows = copy.deepcopy(week_rows)
        for guid in (None, "unknown"):  # Analytics omits it; toast_orders.py sentinel
            note = copy.deepcopy(rows[0])
            note["modifierName"] = "everything bagel"
            note["quantitySold"] = 500.0
            if guid is None:
                del note["modifierGuid"]
            else:
                note["modifierGuid"] = guid
            rows.append(note)

        assert normalize_sales(rows).equals(normalize_sales(week_rows))


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

    def test_free_text_is_not_drift(self, week_rows):
        """Guests and servers type bagel-shaped sentences into open-text
        modifiers ("please slice all bagels."). Toast gives those no
        modifierGuid; toast_orders.py records them as 'unknown'. Neither is a
        configured menu entity, so neither is Toast changing its shape."""
        rows = copy.deepcopy(week_rows)
        analytics_freetext = copy.deepcopy(rows[0])
        analytics_freetext["modifierName"] = "please slice all bagels."
        del analytics_freetext["modifierGuid"]
        orders_freetext = copy.deepcopy(rows[0])
        orders_freetext["modifierName"] = "fresh bagels please"
        orders_freetext["modifierGuid"] = "unknown"
        rows += [analytics_freetext, orders_freetext]

        assert find_unmapped_bagelish(rows) == {}

    def test_deliberately_excluded_modifiers_are_not_drift(self, week_rows):
        """Rainbow bagel is a configured Product we chose not to forecast
        (ticket 01). It must not nag on every run as if it were new."""
        rows = copy.deepcopy(week_rows)
        rainbow = copy.deepcopy(rows[0])
        rainbow["modifierName"] = "Rainbow Bagel (6/1-6/7 only)"
        rainbow["quantitySold"] = 145.0
        rows.append(rainbow)
        assert find_unmapped_bagelish(rows) == {}


class TestHistoricalModifierNames:
    """Toast modifier names were edited in place over the pulled history.
    Each rename must fold into the Product it always was, or the Sales series
    shows a phantom step change on the day of the rename."""

    def _row(self, week_rows, name, date, qty):
        row = copy.deepcopy(week_rows[0])
        row["restaurantGuid"] = next(iter(INCLUDED_RESTAURANTS))
        row["modifierName"] = name
        row["businessDate"] = date
        row["quantitySold"] = qty
        return row

    @pytest.mark.parametrize(
        "old_name,current_name,product",
        [
            (
                "gluten free everything bagel (original sunshine, contains wheat, must be toasted)",
                "gluten-free everything bagel (original sunshine, contains wheat, must be toasted)",
                "gluten-free everything",
            ),
            (
                "gluten free plain bagel (original sunshine, contains wheat, must be toasted)",
                "gluten-free plain bagel (original sunshine, contains wheat, must be toasted)",
                "gluten-free plain",
            ),
            (
                "gluten free everything bagel (must be toasted)",
                "gluten-free everything bagel (original sunshine, contains wheat, must be toasted)",
                "gluten-free everything",
            ),
            (
                "gluten free plain bagel (must be toasted)",
                "gluten-free plain bagel (original sunshine, contains wheat, must be toasted)",
                "gluten-free plain",
            ),
            (
                "pumpernickel bagel - (thursdays only!)",
                "pumpernickel bagel (thursdays only!)",
                "pumpernickel",
            ),
            ("plain bagel [allergens: wheat]", "plain bagel", "plain"),
        ],
    )
    def test_old_and_current_names_are_the_same_product(
        self, week_rows, old_name, current_name, product
    ):
        rows = [
            self._row(week_rows, old_name, "20240601", 10.0),
            self._row(week_rows, current_name, "20260601", 4.0),
        ]
        df = normalize_sales(rows)
        assert set(df["product"]) == {product}
        assert df["quantity"].tolist() == [10.0, 4.0]

    def test_rename_on_the_same_day_sums_into_one_record(self, week_rows):
        """Both spellings were live during the 2025-02/03 cutover."""
        rows = [
            self._row(week_rows, "gluten free plain bagel (original sunshine, contains wheat, must be toasted)", "20250227", 3.0),
            self._row(week_rows, "gluten-free plain bagel (original sunshine, contains wheat, must be toasted)", "20250227", 5.0),
        ]
        df = normalize_sales(rows)
        assert len(df) == 1
        assert df["quantity"].iloc[0] == 8.0

    def test_rainbow_bagel_is_not_a_product(self, week_rows):
        rows = [self._row(week_rows, "rainbow bagel (6/1-6/7 only)", "20260601", 145.0)]
        assert normalize_sales(rows).empty


class TestMergedSources:
    """Analytics week reports win for dates they cover; orders-derived
    aggregates fill the remaining dates only."""

    CAMBRIDGE = "28e5b269-1c1c-45df-81a8-1d268c005dfa"

    def row(self, date, qty):
        return {
            "restaurantGuid": self.CAMBRIDGE,
            "businessDate": date,
            "modifierGuid": "g",
            "modifierName": "plain bagel",
            "quantitySold": qty,
        }

    def test_orders_rows_only_fill_uncovered_dates(self, tmp_path):
        from normalize import load_sales_rows

        (tmp_path / "menu_week_20260706_20260709__20260710T000000Z.json").write_text(
            json.dumps([self.row("20260707", 100.0)])
        )
        (tmp_path / "orders_agg_202606__20260710T000000Z.json").write_text(
            json.dumps([
                self.row("20260707", 999.0),  # covered by the week window: ignored
                self.row("20260601", 55.0),   # uncovered: used
            ])
        )
        df = normalize_sales(load_sales_rows(tmp_path))
        by_date = {str(d.date()): q for d, q in zip(df["date"], df["quantity"])}
        assert by_date == {"2026-07-07": 100.0, "2026-06-01": 55.0}

    def test_week_windows_cover_their_empty_days(self, tmp_path):
        """A closed day inside a pulled week must not be back-filled from
        orders data (the week report is authoritative for its whole window)."""
        from normalize import load_sales_rows

        (tmp_path / "menu_week_20260706_20260709__20260710T000000Z.json").write_text(
            json.dumps([self.row("20260707", 100.0)])
        )
        (tmp_path / "orders_agg_202607__20260801T000000Z.json").write_text(
            json.dumps([self.row("20260708", 42.0)])  # inside the week window
        )
        df = normalize_sales(load_sales_rows(tmp_path))
        assert len(df) == 1
        assert df["quantity"].iloc[0] == 100.0


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
