"""The Scheduled Report's PDF (ticket 03 of scheduled-forecast-reports).

The chart is SVG emitted from python, so these tests assert on it directly
rather than through a headless browser; no JavaScript runs anywhere in this
path. The payloads below are built as dataclasses rather than through
`build_payload`, so a change to the report's *rules* fails ticket 02's suite and
a change to its *page* fails this one.
"""
import datetime as dt
import re
import xml.etree.ElementTree as ET

import pytest

import report_payload
import report_render
from report_payload import Day, Page, Payload

SVG = "{http://www.w3.org/2000/svg}"

ORIGIN = dt.date(2026, 8, 1)
WINDOW = [ORIGIN + dt.timedelta(days=lead) for lead in range(1, 8)]
VARIETIES = ["everything", "plain", "sesame"]


def day(date, forecast=400.0, baseline=380.0, varieties=None):
    direction, delta_pct = report_payload.direction_against(forecast, baseline)
    return Day(
        date=date,
        forecast=forecast,
        varieties=varieties or {name: 100.0 for name in VARIETIES},
        baseline=baseline,
        direction=direction,
        delta_pct=delta_pct,
    )


def page(model="ewma", days=None, direction="up", delta_pct=5.3):
    return Page(
        model=model,
        days=days if days is not None else [day(date) for date in WINDOW],
        direction=direction,
        delta_pct=delta_pct,
    )


def payload(pages=None, omitted_models=None):
    return Payload(
        report_name="Bagel forecast",
        target="wheat_bagels",
        varieties=VARIETIES,
        headline_model="ewma",
        origin=ORIGIN,
        config_version=4,
        window=WINDOW,
        pages=pages if pages is not None else [page()],
        omitted_models=omitted_models or {},
    )


def section_for(html, model):
    """The markup of one model's `<section>`, so an assertion about one page
    cannot be satisfied by another page's text."""
    match = re.search(
        rf'<section[^>]*data-model="{model}".*?</section>', html, re.DOTALL
    )
    assert match, f"no section for {model!r}"
    return match.group(0)


def row_for(markup, date):
    """One day's row, so "no percentage anywhere in its markup" means that
    day's markup and not the whole document's."""
    match = re.search(rf'<tr[^>]*data-date="{date}".*?</tr>', markup, re.DOTALL)
    assert match, f"no row for {date}"
    return match.group(0)


class TestTheChart:
    def test_it_parses_as_xml(self):
        # Emitted from python rather than drawn by a browser, so it is a
        # document the suite can assert on directly.
        chart = report_render.render_chart([day(date) for date in WINDOW])
        assert ET.fromstring(chart).tag == f"{SVG}svg"

    def test_it_has_one_bar_per_day_in_the_window(self):
        chart = report_render.render_chart([day(date) for date in WINDOW])
        bars = ET.fromstring(chart).findall(f".//{SVG}rect[@class='bar']")
        assert len(bars) == len(WINDOW)

    def test_a_shorter_window_draws_fewer_bars(self):
        days = [day(date) for date in WINDOW[:3]]
        bars = ET.fromstring(report_render.render_chart(days)).findall(
            f".//{SVG}rect[@class='bar']"
        )
        assert len(bars) == 3

    def test_a_day_with_no_baseline_draws_no_reference_bar(self):
        days = [day(date) for date in WINDOW]
        days[0] = Day(
            date=WINDOW[0],
            forecast=400.0,
            varieties={name: 100.0 for name in VARIETIES},
            baseline=None,
            direction=report_payload.NO_BASELINE,
            delta_pct=None,
        )
        chart = ET.fromstring(report_render.render_chart(days))

        assert len(chart.findall(f".//{SVG}rect[@class='bar']")) == 7
        assert len(chart.findall(f".//{SVG}rect[@class='baseline']")) == 6

    def test_it_carries_a_legend_for_its_two_marks(self):
        # Two bar widths with no key reads as two forecasts. The swatches are
        # rect.key, so they never join the per-day counts above.
        chart = ET.fromstring(report_render.render_chart([day(d) for d in WINDOW]))
        labels = [text.text for text in chart.findall(f".//{SVG}text")]

        assert len(chart.findall(f".//{SVG}rect[@class='key']")) == 2
        assert "Demand Forecast" in labels
        assert "Typical for this weekday" in labels

    def test_a_taller_day_draws_a_taller_bar(self):
        days = [day(WINDOW[0], forecast=200.0), day(WINDOW[1], forecast=800.0)]
        short, tall = ET.fromstring(report_render.render_chart(days)).findall(
            f".//{SVG}rect[@class='bar']"
        )
        assert float(tall.get("height")) > float(short.get("height"))

    def test_an_all_zero_window_still_renders(self):
        # Nothing divides by the maximum without a guard.
        days = [day(date, forecast=0.0, baseline=None) for date in WINDOW]
        chart = ET.fromstring(report_render.render_chart(days))
        assert len(chart.findall(f".//{SVG}rect[@class='bar']")) == 7


class TestThePage:
    def test_the_header_names_the_report_window_origin_version_and_model(self):
        html = report_render.render_html(payload())
        section = section_for(html, "ewma")

        assert "Bagel forecast" in section
        assert "2026-08-02" in section and "2026-08-08" in section  # the window
        assert "2026-08-01" in section  # the Forecast Origin
        assert "4" in section  # the config version
        assert "ewma" in section

    def test_a_day_with_no_baseline_renders_no_percentage(self):
        days = [day(date) for date in WINDOW]
        days[0] = Day(
            date=WINDOW[0],
            forecast=400.0,
            varieties={name: 100.0 for name in VARIETIES},
            baseline=None,
            direction=report_payload.NO_BASELINE,
            delta_pct=None,
        )
        html = report_render.render_html(payload(pages=[page(days=days)]))
        row = row_for(section_for(html, "ewma"), "2026-08-02")

        assert "no baseline" in row
        assert "%" not in row

    def test_a_day_with_a_baseline_renders_its_move_and_the_baseline(self):
        html = report_render.render_html(payload())
        row = row_for(section_for(html, "ewma"), "2026-08-02")

        assert "%" in row
        assert "380" in row  # the Weekday Baseline itself, not only the move

    def test_the_totals_row_says_how_many_days_the_typical_covers(self):
        # The forecast total spans the window; the move spans only the days
        # with a baseline. A reader dividing one by the other must be able to
        # see why the answer is not the percentage printed beside it.
        days = [day(date) for date in WINDOW]
        days[3] = Day(
            date=WINDOW[3],
            forecast=400.0,
            varieties={name: 100.0 for name in VARIETIES},
            baseline=None,
            direction=report_payload.NO_BASELINE,
            delta_pct=None,
        )
        html = report_render.render_html(payload(pages=[page(days=days)]))
        assert "over 6 of 7 days" in html

    def test_the_totals_row_says_nothing_when_every_day_has_a_baseline(self):
        assert "over 7 of 7" not in report_render.render_html(payload())

    def test_the_page_says_vs_typical_for_this_weekday(self):
        # Not "vs last week": the baseline is a four-week weekday mean
        # (CONTEXT.md), and mislabelling it would misrepresent the arrows.
        html = report_render.render_html(payload())
        assert "typical for this weekday" in html
        assert "last week" not in html.lower()

    def test_the_top_down_footnote_appears_when_the_sums_differ(self):
        html = report_render.render_html(payload())
        section = section_for(html, "ewma")

        assert "300" in section  # the varieties' sum, next to the headline 400
        assert "summed series" in section

    def test_the_footnote_is_absent_when_the_sums_agree(self):
        days = [
            day(date, forecast=300.0, varieties={name: 100.0 for name in VARIETIES})
            for date in WINDOW
        ]
        html = report_render.render_html(payload(pages=[page(days=days)]))
        assert "summed series" not in section_for(html, "ewma")

    def test_every_variety_is_named_with_its_raw_logged_forecast(self):
        days = [
            day(
                date,
                varieties={"everything": 40.0, "plain": 30.0, "sesame": 20.0},
            )
            for date in WINDOW
        ]
        section = section_for(
            report_render.render_html(payload(pages=[page(days=days)])), "ewma"
        )
        for name in VARIETIES:
            assert name in section

    def test_the_page_says_these_are_not_bake_quantities(self):
        # A page that read like a bake sheet would have someone baking to the
        # mean and stocking out about half the time (ADR 0012).
        html = report_render.render_html(payload())
        assert "not bake quantities" in html
        assert "half the time" in html

    def test_the_preorder_block_is_reserved_and_labelled(self):
        html = report_render.render_html(payload())
        assert "Preorders and catering" in html
        assert "Not included in these figures" in html

    def test_omitted_models_are_named_with_why(self):
        html = report_render.render_html(
            payload(omitted_models={"holt_winters": "no logged forecast for plain on 2026-08-05"})
        )
        assert "holt_winters" in html
        assert "2026-08-05" in html

    def test_nothing_is_said_about_omissions_when_there_are_none(self):
        html = report_render.render_html(payload())
        assert "omitted" not in html.lower()


class TestPageOrder:
    def test_the_headline_model_leads_and_the_rest_are_alphabetical(self):
        # The payload already ordered them (ADR 0011); the document must not
        # reorder them behind its back.
        pages = [page(model=name) for name in ("ewma", "aardvark", "holt_winters")]
        html = report_render.render_html(payload(pages=pages))

        assert re.findall(r'data-model="([^"]+)"', html) == [
            "ewma",
            "aardvark",
            "holt_winters",
        ]

    def test_each_model_gets_its_own_page_break(self):
        pages = [page(model=name) for name in ("ewma", "holt_winters")]
        html = report_render.render_html(payload(pages=pages))
        # Paged media is why WeasyPrint was chosen over a plotting library.
        assert "break-before: page" in html


def require_weasyprint():
    """WeasyPrint, or skip --- so the suite still runs on a `dev`-only install,
    exactly as the ETS tests do for statsmodels.

    Not `pytest.importorskip`, which only catches `ImportError`. WeasyPrint is a
    cffi binding over Pango, and pip does not carry Pango: an install that has
    the wheel but not the system libraries raises `OSError` from `dlopen` at
    import. That is an environment to fix (see docs/reports.md), not a failure
    of this code, so it skips like the absent case rather than reddening the
    whole suite.
    """
    try:
        import weasyprint
    except (ImportError, OSError) as exc:
        pytest.skip(f"weasyprint is not usable here: {exc}")
    return weasyprint


def page_count(document_html):
    """How many pages `document_html` lays out to.

    Counted through WeasyPrint's own document model rather than off the PDF
    bytes: the page objects live in a compressed object stream, so grepping the
    output for `/Type /Page` finds nothing whatever the page count is --- an
    assertion that would have passed for the wrong reason.
    """
    return len(require_weasyprint().HTML(string=document_html).render().pages)


class TestThePdf:
    def test_it_is_a_pdf(self):
        require_weasyprint()
        assert report_render.render_pdf(payload()).startswith(b"%PDF-")

    def test_it_has_one_page_per_qualifying_model(self):
        # `break-before: page` on each model's section, which is the paged-media
        # support WeasyPrint was chosen for.
        pages = [page(model=name) for name in ("ewma", "holt_winters")]
        assert page_count(report_render.render_html(payload(pages=pages))) == 2

    def test_one_model_is_one_page(self):
        # And no leading blank page from a `break-before` on the first section.
        assert page_count(report_render.render_html(payload())) == 1

    def test_a_four_model_report_is_four_pages(self):
        pages = [page(model=name) for name in ("ewma", "a", "b", "c")]
        assert page_count(report_render.render_html(payload(pages=pages))) == 4
