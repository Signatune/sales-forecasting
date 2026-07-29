"""A resolved Scheduled Report as a PDF — one page per qualifying model.

    render_chart(days)   -> str    # an <svg> element
    render_html(payload) -> str    # the whole document, one <section> per model
    render_pdf(payload)  -> bytes

The design is variant C of the retired prototype: **chart-first**, the bar chart
carrying the answer and everything else subordinate to it. The palette is that
prototype's light theme.

WeasyPrint, not a headless browser, and the chart is emitted as SVG *from
python* — so the test suite asserts on the chart directly and **no JavaScript
runs anywhere in this path**. WeasyPrint over a plotting library because the
document is paged media: `break-before: page` on each model's section is what
makes "one page per model" a property of the CSS rather than of arithmetic about
figure heights.

Two things the page must say, and says in plain type rather than in a footnote:

- **These are point Demand Forecasts, not Bake-to Quantities.** No Service Level
  buffer is applied anywhere in this feature. A page that read like a bake sheet
  would have someone baking to the *mean* and stocking out roughly half the
  time — the exact failure the Service Level exists to prevent (ADR 0012).
- **"vs typical for this weekday", never "vs last week".** The Weekday Baseline
  is a four-week same-weekday mean of Settled Sales (CONTEXT.md); calling it last
  week would misrepresent what the direction means.

The variety split is the raw logged per-variety forecasts, and the page states
the gap between their sum and the headline rather than reconciling it: the
headline is fit to the summed series, so top-down is not bottom-up.
"""
import datetime as dt
from html import escape
from typing import Dict, List, Sequence

from report_payload import DOWN, FLAT, NO_BASELINE, UP, Day, Page, Payload

# Variant C's light palette, carried over from the prototype. Literal hex rather
# than CSS custom properties: these values are also written into the SVG's
# presentation attributes, where `var()` has no meaning.
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
SURFACE = "#fcfcfb"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
GHOST = "#c3c2b7"
SERIES = "#2a78d6"
UP_INK = "#006300"
DOWN_INK = "#d03b3b"
BORDER = "rgba(11,11,11,.10)"
WASH = "rgba(11,11,11,.04)"

# The prototype's page tint is dropped for print: a full-bleed background is
# ink the reader pays for on every sheet. The surface tint stays on the cards,
# which is where it was doing the work of separating the chart from the page.
PAGE = "#ffffff"

CHART_WIDTH = 760
CHART_HEIGHT = 196
# `top` leaves room for the legend above the plot area as well as for the
# value label sitting over the tallest bar.
CHART_PADDING = {"top": 38, "right": 12, "bottom": 30, "left": 46}

# The direction words as the page prints them. Words rather than arrow glyphs:
# an arrow depends on the font the runner happens to have, and a missing glyph
# in a PDF is a box where the answer should be.
DIRECTION_WORDS = {UP: UP, DOWN: DOWN, FLAT: FLAT, NO_BASELINE: NO_BASELINE}


# The page and the Chat card are two renderings of one payload, and they have
# to say the same thing about the same numbers. `report_delivery` imports the
# four below rather than spelling them again — the first cut of this feature
# spelled them twice and they had already drifted, the card writing `%a %d %b`
# where the page wrote `%a %-d %b`.


def quantity(value: float) -> str:
    """A Demand Forecast as the report prints it: whole units, thousands
    separated. Nothing downstream buffers or re-scales these, so the rounding
    here is presentational only."""
    return f"{value:,.0f}"


def _percent(value: float) -> str:
    return f"{abs(value):.1f}%"


def move_phrase(direction: str, delta_pct) -> str:
    """A direction against a Weekday Baseline, written out. No baseline prints
    the words and *no percentage at all* — a missing baseline is not a 100% jump,
    and rendering one as a number is the failure this guards."""
    if delta_pct is None:
        return NO_BASELINE
    return f"{DIRECTION_WORDS[direction]} {_percent(delta_pct)}"


def _move(day: Day) -> str:
    """One day's direction against its Weekday Baseline."""
    if day.baseline is None:
        return NO_BASELINE
    return move_phrase(day.direction, day.delta_pct)


def _direction_class(direction: str) -> str:
    return direction if direction in (UP, DOWN) else FLAT


def long_date(date: dt.date) -> str:
    """A target date as both the page and the card write it."""
    return date.strftime("%a %-d %b")


# --- The chart -------------------------------------------------------------


def _axis_top(days: Sequence[Day]) -> float:
    """The value the y-axis tops out at, rounded up so the gridline labels are
    round numbers. A window of nothing but zeroes still gets an axis, so the
    scale never divides by zero."""
    peak = max(
        [day.forecast for day in days] + [day.baseline or 0.0 for day in days] + [0.0]
    )
    if peak <= 0:
        return 100.0
    step = 10 ** max(1, len(str(int(peak))) - 2)
    return float(int(peak / step + 1) * step)


def _legend(width: int) -> str:
    """The key to the chart's two marks.

    Variant C carried one and the first port of it did not, which left the page
    showing two bar widths and no way to tell which was which — the reference
    bar reads as a second forecast without it. Its swatches are `rect.key`, not
    `rect.bar` or `rect.baseline`, so counting the day marks still counts days.
    """
    entries = ((SERIES, 1.0, "Demand Forecast"), (GHOST, 0.45, "Typical for this weekday"))
    x = width - CHART_PADDING["right"] - 240
    parts = []
    for fill, opacity, label in entries:
        parts.append(
            f'<rect class="key" x="{x:.1f}" y="6" width="9" height="9" rx="2" '
            f'fill="{fill}" fill-opacity="{opacity}"/>'
        )
        parts.append(
            f'<text x="{x + 13:.1f}" y="14.5" font-size="10" fill="{INK_2}">'
            f"{escape(label)}</text>"
        )
        x += 13 + len(label) * 5.3 + 14
    return "".join(parts)


def render_chart(days: Sequence[Day]) -> str:
    """The Report Window's daily point Demand Forecasts as one `<svg>` element.

    One `rect.bar` per day — the forecast, the foreground mark — over a
    recessive `rect.baseline` where that day has a Weekday Baseline, which is
    variant C's "forecast against a reference" shape with the four-week weekday
    mean standing in for the prototype's last-week Sales. A day with no
    baseline simply draws no reference, rather than drawing one at zero and
    inventing a comparison."""
    width, height = CHART_WIDTH, CHART_HEIGHT
    padding = CHART_PADDING
    inner_width = width - padding["left"] - padding["right"]
    inner_height = height - padding["top"] - padding["bottom"]
    top = _axis_top(days)
    band = inner_width / max(1, len(days))
    floor = padding["top"] + inner_height

    def y(value: float) -> float:
        return padding["top"] + inner_height - (value / top) * inner_height

    parts: List[str] = [_legend(width)]
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        value = top * fraction
        line_y = y(value)
        stroke = AXIS if fraction == 0 else GRID
        parts.append(
            f'<line x1="{padding["left"]:.1f}" x2="{width - padding["right"]:.1f}" '
            f'y1="{line_y:.1f}" y2="{line_y:.1f}" stroke="{stroke}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{padding["left"] - 10:.1f}" y="{line_y + 4:.1f}" '
            f'text-anchor="end" font-size="11" fill="{MUTED}">{quantity(value)}</text>'
        )

    for index, day in enumerate(days):
        left = padding["left"] + index * band
        centre = left + band / 2
        if day.baseline is not None:
            reference_width = band * 0.62
            parts.append(
                f'<rect class="baseline" x="{left + (band - reference_width) / 2:.1f}" '
                f'y="{y(day.baseline):.1f}" width="{reference_width:.1f}" '
                f'height="{max(0.0, floor - y(day.baseline)):.1f}" rx="4" '
                f'fill="{GHOST}" fill-opacity="0.45"/>'
            )
        bar_width = band * 0.40
        parts.append(
            f'<rect class="bar" x="{left + (band - bar_width) / 2:.1f}" '
            f'y="{y(day.forecast):.1f}" width="{bar_width:.1f}" '
            f'height="{max(0.0, floor - y(day.forecast)):.1f}" rx="4" '
            f'fill="{SERIES}" stroke="{SURFACE}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{centre:.1f}" y="{y(day.forecast) - 8:.1f}" text-anchor="middle" '
            f'font-size="12" font-weight="600" fill="{INK}">'
            f"{quantity(day.forecast)}</text>"
        )
        parts.append(
            f'<text x="{centre:.1f}" y="{height - 18:.1f}" text-anchor="middle" '
            f'font-size="11.5" fill="{INK_2}">{escape(day.date.strftime("%a"))}</text>'
        )
        parts.append(
            f'<text x="{centre:.1f}" y="{height - 5:.1f}" text-anchor="middle" '
            f'font-size="10" fill="{MUTED}">{escape(day.date.strftime("%-d %b"))}</text>'
        )

    # A `viewBox` and no `width`/`height` attributes, sized entirely by the CSS
    # below. Given an explicit `height`, WeasyPrint lays the element out at the
    # viewBox's *intrinsic* width instead of the container's, and a chart wider
    # than its card is silently clipped down the right-hand edge — taking the
    # busiest day of the window with it.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
        f'{"".join(parts)}</svg>'
    )


# --- The document ----------------------------------------------------------


STYLESHEET = f"""
@page {{ size: Letter portrait; margin: 14mm 13mm 12mm; }}
body {{
  background: {PAGE}; color: {INK}; margin: 0;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 10pt; line-height: 1.45;
}}
section.model {{ break-before: page; }}
section.model:first-of-type {{ break-before: auto; }}
h1 {{ font-size: 17pt; margin: 0 0 2pt; font-weight: 600; }}
h2 {{ font-size: 10pt; margin: 9pt 0 3pt; font-weight: 600; color: {INK_2};
     text-transform: uppercase; letter-spacing: .06em; }}
h3 {{ font-size: 10pt; margin: 0 0 2pt; font-weight: 600; }}
p {{ margin: 0 0 4pt; }}
.meta {{ color: {MUTED}; font-size: 8.5pt; margin: 0 0 10pt; }}
.chart {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
          padding: 8pt 6pt 2pt; }}
.chart svg {{ display: block; width: 100%; height: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 9pt;
         font-variant-numeric: tabular-nums; }}
th {{ text-align: right; font-weight: 600; color: {INK_2}; font-size: 8pt;
      text-transform: uppercase; letter-spacing: .04em;
      border-bottom: 1px solid {AXIS}; padding: 2.5pt 4pt; }}
th:first-child, td:first-child {{ text-align: left; }}
td {{ text-align: right; padding: 1.6pt 4pt; border-bottom: 1px solid {BORDER}; }}
tfoot td {{ font-weight: 600; border-top: 1px solid {AXIS}; border-bottom: none; }}
td.baseline {{ color: {MUTED}; }}
td.up {{ color: {UP_INK}; }}
td.down {{ color: {DOWN_INK}; }}
td.flat {{ color: {INK_2}; }}
.not-bake {{ margin: 7pt 0 0; padding: 5pt 8pt; background: {WASH};
             border-left: 3px solid {DOWN_INK}; font-size: 9.5pt; }}
.reserved {{ margin-top: 9pt; padding: 7pt 10pt; border: 1px dashed {AXIS};
             border-radius: 8px; color: {INK_2}; min-height: 38pt; }}
.note {{ color: {MUTED}; font-size: 8.5pt; margin-top: 6pt; }}
.over {{ color: {MUTED}; font-weight: 400; font-size: 8pt; }}
"""


def _days_table(page: Page) -> str:
    """Each day's forecast, its direction against its Weekday Baseline, and the
    baseline itself — so a reader can see what the arrow was drawn from rather
    than having to take it on trust."""
    rows = []
    for day in page.days:
        baseline = "—" if day.baseline is None else quantity(day.baseline)
        rows.append(
            f'<tr class="day" data-date="{day.date.isoformat()}">'
            f"<td>{escape(long_date(day.date))}</td>"
            f"<td>{quantity(day.forecast)}</td>"
            f'<td class="{_direction_class(day.direction)}">{escape(_move(day))}</td>'
            f'<td class="baseline">{baseline}</td>'
            f"</tr>"
        )
    # The window's forecast total spans every day; its move spans only the days
    # that have a baseline. When those differ, the Typical cell says over how
    # many days it was added up — otherwise a reader dividing one total by the
    # other gets a percentage the row does not show, and no way to see why.
    comparable = [day for day in page.days if day.baseline is not None]
    if not comparable:
        baseline_total = "—"
    else:
        baseline_total = quantity(sum(day.baseline for day in comparable))
        if len(comparable) < len(page.days):
            baseline_total += (
                f' <span class="over">over {len(comparable)} of '
                f"{len(page.days)} days</span>"
            )
    window_move = (
        NO_BASELINE
        if page.delta_pct is None
        else f"{DIRECTION_WORDS[page.direction]} {_percent(page.delta_pct)}"
    )
    return (
        "<table>"
        "<thead><tr><th>Day</th><th>Demand Forecast</th>"
        "<th>vs typical for this weekday</th><th>Typical</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody>'
        f'<tfoot><tr><td>Window</td><td>{quantity(page.total)}</td>'
        f'<td class="{_direction_class(page.direction)}">{escape(window_move)}</td>'
        f"<td>{baseline_total}</td></tr></tfoot>"
        "</table>"
    )


def _split_table(page: Page, varieties: Sequence[str]) -> str:
    """The variety split as the **raw logged per-variety forecasts**, with their
    sum next to the headline. Nothing here is scaled to make the two agree."""
    heads = "".join(f"<th>{escape(name)}</th>" for name in varieties)
    rows = []
    for day in page.days:
        cells = "".join(
            f"<td>{quantity(day.varieties.get(name, 0.0))}</td>" for name in varieties
        )
        rows.append(
            f"<tr><td>{escape(long_date(day.date))}</td>{cells}"
            f"<td>{quantity(day.varieties_total)}</td>"
            f"<td>{quantity(day.forecast)}</td></tr>"
        )
    totals = "".join(
        f"<td>{quantity(sum(day.varieties.get(name, 0.0) for day in page.days))}</td>"
        for name in varieties
    )
    return (
        "<table>"
        f"<thead><tr><th>Day</th>{heads}<th>Varieties total</th>"
        "<th>Headline</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody>'
        f"<tfoot><tr><td>Window</td>{totals}"
        f"<td>{quantity(page.varieties_total)}</td>"
        f"<td>{quantity(page.total)}</td></tr></tfoot>"
        "</table>"
    )


def _top_down_note(page: Page) -> str:
    """Shown only when the two totals actually differ. The headline is fit to
    the summed series, so it is not the varieties added up — stating that is
    what keeps the gap from reading as an arithmetic error in the report."""
    if abs(page.varieties_total - page.total) < 0.5:
        return ""
    return (
        f'<p class="note">The headline is fit to the summed series, so it is not '
        f"the varieties added up: {quantity(page.total)} against "
        f"{quantity(page.varieties_total)} across the window.</p>"
    )


# Said in plain type on the page and again on the Chat card, from one string:
# the card is where someone reads a figure and goes to bake off it, and the two
# must not be able to drift into saying different things (ADR 0012).
NOT_BAKE_QUANTITIES = (
    "These are expected Demand, not bake quantities. Baking to these numbers "
    "means running out about half the time."
)

NOT_BAKE_QUANTITIES_HTML = f'<p class="not-bake">{NOT_BAKE_QUANTITIES}</p>'


RESERVED_BLOCK = (
    '<div class="reserved"><h3>Preorders and catering</h3>'
    "<p>Not included in these figures, which cover service Sales only. "
    "Preorders and planned catering are coming to this report later; this space "
    "is held for them.</p></div>"
)


def _incomplete_models_note(omitted_models: Dict[str, str]) -> str:
    """Which models were left out of this report and why, so a reader counting
    pages knows whether one is missing or was never configured."""
    if not omitted_models:
        return ""
    reasons = "; ".join(
        f"{escape(model)} ({escape(why)})" for model, why in sorted(omitted_models.items())
    )
    return (
        f'<p class="note">Models left out for not covering the whole '
        f"window: {reasons}.</p>"
    )


def _section(payload: Payload, page: Page) -> str:
    window = f"{payload.window[0].isoformat()} to {payload.window[-1].isoformat()}"
    return (
        f'<section class="model" data-model="{escape(page.model)}">'
        f"<h1>{escape(payload.report_name)} — {escape(long_date(payload.window[0]))} "
        f"to {escape(long_date(payload.window[-1]))}</h1>"
        f'<p class="meta">Report Window {window} · Forecast Origin '
        f"{payload.origin.isoformat()} · forecast configuration version "
        f"{payload.config_version} · model {escape(page.model)} · Forecast Target "
        f"{escape(payload.target)}</p>"
        f'<div class="chart">{render_chart(page.days)}</div>'
        f"<h2>By day</h2>{_days_table(page)}"
        f"{NOT_BAKE_QUANTITIES_HTML}"
        f"<h2>Variety split</h2>{_split_table(page, payload.varieties)}"
        f"{_top_down_note(page)}"
        f"{RESERVED_BLOCK}"
        f"{_incomplete_models_note(payload.omitted_models)}"
        f"</section>"
    )


def render_html(payload: Payload) -> str:
    """The whole document, one `<section>` per qualifying model in the order the
    payload put them — the headline model first, then alphabetical (ADR 0011).
    This function does not reorder them: which model leads is a decision ticket
    02 already made, and making it twice is how the PDF and the Chat card would
    come to disagree."""
    title = f"{payload.report_name} — {payload.window[0].isoformat()}"
    sections = "".join(_section(payload, page) for page in payload.pages)
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title><style>{STYLESHEET}</style></head>"
        f"<body>{sections}</body></html>"
    )


def render_pdf(payload: Payload) -> bytes:
    """The document as PDF bytes — Letter portrait, one page per qualifying
    model.

    WeasyPrint is imported here rather than at module scope so that importing
    this module, and reading its chart and markup out in the tests, needs only a
    `dev` install — the same lazy-import shape `models.ets_forecast` uses for
    statsmodels. The deployed job installs the `report` extra."""
    from weasyprint import HTML

    return HTML(string=render_html(payload)).write_pdf()
