"""One due Scheduled Report, resolved to everything its pages need — or to a
refusal saying why no page can honestly be drawn (ADR 0010).

    build_payload(report, forecast_config, logged, sales, today)
      -> Payload | Refusal

This is the whole of the feature's decision logic, and it is a *pure function of
its five arguments* — no database, no clock, no filesystem — for the same reason
`forecast_engine.run_forecasts` is (ADR 0006). Every rule below is then testable
without a Postgres and without waiting for a Saturday, and the module above it
(`scheduled_reports.py`) owns only the wiring.

The rules, in the order they are applied:

- **The referenced configuration must still be active.** A report pointing at a
  superseded version would render a window frozen at whenever that version was
  retired, which looks exactly like a fresh one.
- **The Forecast Origin must be today.** "The latest origin under this version"
  always returns a row, so a forecast run that failed would silently shift the
  window a day earlier — a stale bake sheet that reads as a current one. Both
  failures refuse, and with *different* messages, because they need different
  fixes: repoint the row, or re-run the forecast.
- **A model earns a page only by covering the whole window** — the headline
  Forecast Target *and* every variety, on every date. The engine emits no row
  for a target date it has no evidence for, so a model can cover six of seven
  days; such a model is omitted and named rather than drawn with a hole. No
  model covering it at all is a third refusal.
- **The Weekday Baseline** each day is called up, down or flat against is the
  mean Settled Sales of the headline Target for that weekday over the four most
  recent weeks (CONTEXT.md). Under 3% is flat; no baseline is `"no baseline"`,
  never a percentage.
- **The variety split is raw.** Each variety's own logged forecast, unmodified.
  The payload carries the varieties' sum *and* the headline so the page can state
  the top-down gap rather than reconcile it away — the headline is fit to the
  summed series, so it is not the varieties added up.

Nothing here applies a Service Level buffer. What the payload carries are point
Demand Forecasts, and turning one into a Bake-to Quantity is a separate read over
the log (ADR 0012).
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

import pandas as pd

# How far back the capture's trailing window still reaches (ADR 0004). Sales for
# a date newer than this many days are still revisable, so the Baseline does not
# read them: a baseline that shifted under an already-published report would be
# worse than one four days older.
SETTLED_AFTER_DAYS = 3

# How many same-weekday observations the Baseline averages (CONTEXT.md). A
# weekday mean rather than the single preceding week, so one unusual day cannot
# flip a direction on its own.
BASELINE_WEEKS = 4

# Moves smaller than this are called flat. On a series that swings 300-680 a
# day, a 1.4% move is rounding, and calling it a trend has the board chasing it.
FLAT_BAND_PERCENT = 3.0

# The direction of a day (or a window) with nothing to compare against. A
# distinct value rather than a 0% move, so nothing downstream can render it as
# a percentage — a missing baseline is not a flat week.
NO_BASELINE = "no baseline"

UP, DOWN, FLAT = "up", "down", "flat"


@dataclass(frozen=True)
class Day:
    """One target date on one model's page."""

    date: dt.date
    forecast: float
    varieties: Dict[str, float]
    baseline: Optional[float]
    direction: str
    delta_pct: Optional[float]

    @property
    def varieties_total(self) -> float:
        """The raw logged per-variety forecasts added up. Deliberately not
        reconciled against `forecast`: the two differ because the headline is
        fit to the summed series (top-down), and the page states that gap."""
        return sum(self.varieties.values())


@dataclass(frozen=True)
class Page:
    """One model's page — the whole Report Window as that model forecast it."""

    model: str
    days: List[Day]
    direction: str
    delta_pct: Optional[float]

    @property
    def total(self) -> float:
        return sum(day.forecast for day in self.days)

    @property
    def varieties_total(self) -> float:
        return sum(day.varieties_total for day in self.days)

    @property
    def busiest(self) -> Day:
        return max(self.days, key=lambda day: day.forecast)

    @property
    def quietest(self) -> Day:
        return min(self.days, key=lambda day: day.forecast)


@dataclass(frozen=True)
class Payload:
    """Everything the renderer and the Chat card need, and nothing they have to
    go back to a database for."""

    report_name: str
    target: str
    varieties: List[str]
    headline_model: str
    origin: dt.date
    config_version: int
    window: List[dt.date]
    pages: List[Page]
    omitted_models: Dict[str, str] = field(default_factory=dict)

    @property
    def headline(self) -> Page:
        """The page that leads the PDF and is quoted in the Chat card (ADR
        0011). It is the report's `headline_model` when that model qualified,
        and otherwise simply whichever page leads — a report whose headline
        model could not cover the window still goes out."""
        return self.pages[0]

    @property
    def headline_is_the_named_model(self) -> bool:
        """Whether the page leading the report is the model the report actually
        names. When it is not, the headline model failed to cover the window and
        a substitute is leading — which the card and the page have to say, or
        the reader is quoted numbers from a model they never chose."""
        return self.headline.model == self.headline_model


@dataclass(frozen=True)
class Refusal:
    """No page can honestly be drawn. `reason` is the machine-readable class of
    failure; `message` is what gets posted, and it names the report, the failure
    and the fix — it is read when something is already broken."""

    report_name: str
    reason: str
    message: str


Result = Union[Payload, Refusal]


# --- Small conversions -----------------------------------------------------


def _as_date(value) -> dt.date:
    """A `date`, whatever the driver or pandas handed over. psycopg returns
    python dates and `load_sales_history` a datetime64 column, and both reach
    this module."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return pd.Timestamp(value).date()


def _report_window(today: dt.date, horizon_days: int) -> List[dt.date]:
    """`today + 1 .. today + horizon_days`. Computed, never configured: nothing
    stores a week shape, so moving a report to another weekday reshapes its
    window with no code change, and raising `horizon_days` widens every report
    at once (ADR 0010)."""
    return [today + dt.timedelta(days=lead) for lead in range(1, int(horizon_days) + 1)]


# --- The Weekday Baseline --------------------------------------------------


def _settled_target_sales(
    sales: pd.DataFrame, members: Sequence[str], today: dt.date
) -> Dict[dt.date, float]:
    """The headline Forecast Target's Settled Sales, one total per date.

    The Target is not a Product, so this sums its member Products exactly as
    `forecast_engine._target_series` does before fitting — comparing a forecast
    of the summed series against anything else would be comparing two different
    quantities.

    "Settled" is `SETTLED_AFTER_DAYS` days or more before `today`: inside ADR
    0004's trailing window Toast may still correct a day, so those dates are
    dropped rather than averaged."""
    if not len(sales) or not len(members):
        return {}
    settled_through = today - dt.timedelta(days=SETTLED_AFTER_DAYS)
    rows = sales[sales["product"].isin(list(members))]
    totals: Dict[dt.date, float] = {}
    for date, quantity in zip(rows["date"], rows["quantity"]):
        date = _as_date(date)
        if date <= settled_through:
            totals[date] = totals.get(date, 0.0) + float(quantity)
    return totals


def weekday_baseline(
    settled: Dict[dt.date, float], target_date: dt.date, today: dt.date
) -> Optional[float]:
    """The Weekday Baseline for `target_date`: the mean Settled Sales of the
    same weekday across the four most recent weeks (CONTEXT.md).

    The four candidate dates are calendar dates — the newest settled same
    weekday, then a week earlier, and so on — not the four newest *observations*.
    A closed day therefore shortens the mean rather than reaching a fifth week
    back for a replacement, which is what keeps "four weeks" meaning four weeks.

    `None` when nothing is there to compare against, including when everything
    there sums to zero. Dividing by a zero baseline would manufacture exactly the
    spike this returns `None` to avoid, so a run of closed days reads as "no
    baseline" rather than as an infinite jump."""
    settled_through = today - dt.timedelta(days=SETTLED_AFTER_DAYS)
    # Step back from the target date to the newest settled date sharing its
    # weekday, then take that week and the three before it.
    newest = target_date
    while newest > settled_through:
        newest -= dt.timedelta(days=7)
    candidates = [newest - dt.timedelta(days=7 * week) for week in range(BASELINE_WEEKS)]
    observed = [settled[date] for date in candidates if date in settled]
    if not observed:
        return None
    mean = sum(observed) / len(observed)
    return mean if mean > 0 else None


def direction_against(
    forecast: float, baseline: Optional[float]
) -> Tuple[str, Optional[float]]:
    """Up, down or flat against `baseline`, with the percentage move.

    Under `FLAT_BAND_PERCENT` is flat, and a missing baseline is `NO_BASELINE`
    with no percentage at all — never a 100% jump against nothing."""
    if baseline is None:
        return NO_BASELINE, None
    delta_pct = (forecast - baseline) / baseline * 100.0
    if abs(delta_pct) < FLAT_BAND_PERCENT:
        return FLAT, delta_pct
    return (UP if delta_pct > 0 else DOWN), delta_pct


# --- Model eligibility -----------------------------------------------------


def _logged_by_model(logged: pd.DataFrame) -> Dict[str, Dict[Tuple[str, dt.date], float]]:
    """The log indexed the way eligibility asks about it:
    `{model: {(target, target_date): quantity}}`."""
    by_model: Dict[str, Dict[Tuple[str, dt.date], float]] = {}
    for model, target, target_date, quantity in zip(
        logged["model"],
        logged["target"],
        logged["target_date"],
        logged["forecast_quantity"],
    ):
        by_model.setdefault(str(model), {})[
            (str(target), _as_date(target_date))
        ] = float(quantity)
    return by_model


def _gaps(
    logged: Dict[Tuple[str, dt.date], float],
    targets: Sequence[str],
    window: Sequence[dt.date],
) -> List[Tuple[str, dt.date]]:
    """The `(target, date)` pairs this model has no logged row for. A single
    one disqualifies it: a missing variety day leaves a hole in the split just
    as a missing headline day leaves one in the chart."""
    return [
        (target, date)
        for target in targets
        for date in window
        if (target, date) not in logged
    ]


def _describe_gaps(gaps: Sequence[Tuple[str, dt.date]]) -> str:
    """Why a model was omitted, in one line the surviving pages can carry:
    the missing dates grouped under the target they are missing for."""
    by_target: Dict[str, List[dt.date]] = {}
    for target, date in gaps:
        by_target.setdefault(target, []).append(date)
    return "; ".join(
        f"no logged forecast for {target} on "
        + ", ".join(date.isoformat() for date in sorted(dates))
        for target, dates in sorted(by_target.items())
    )


def _page(
    model: str,
    logged: Dict[Tuple[str, dt.date], float],
    target: str,
    varieties: Sequence[str],
    window: Sequence[dt.date],
    settled: Dict[dt.date, float],
    today: dt.date,
) -> Page:
    """One model's page: every date in the window, with its forecast, its
    Weekday Baseline and the direction between them, plus the window's own
    direction.

    The window's direction compares like with like — only the days that *have*
    a baseline are on either side of it — because summing seven forecasts
    against four baselines would read as a collapse that is really a missing
    weekday."""
    days = []
    for date in window:
        forecast = logged[(target, date)]
        baseline = weekday_baseline(settled, date, today)
        direction, delta_pct = direction_against(forecast, baseline)
        days.append(
            Day(
                date=date,
                forecast=forecast,
                varieties={name: logged[(name, date)] for name in varieties},
                baseline=baseline,
                direction=direction,
                delta_pct=delta_pct,
            )
        )
    comparable = [day for day in days if day.baseline is not None]
    total = sum(day.forecast for day in comparable)
    baseline_total = sum(day.baseline for day in comparable)
    direction, delta_pct = direction_against(
        total, baseline_total if comparable else None
    )
    return Page(model=model, days=days, direction=direction, delta_pct=delta_pct)


def _order(models: Sequence[str], headline_model: str) -> List[str]:
    """The headline model first, then the rest alphabetically (ADR 0011).
    Alphabetical is stable week to week and asserts no ranking among them."""
    rest = sorted(model for model in models if model != headline_model)
    return ([headline_model] if headline_model in models else []) + rest


# --- The entry point -------------------------------------------------------


def build_payload(
    report: Dict,
    forecast_config: Dict,
    logged: pd.DataFrame,
    sales: pd.DataFrame,
    today: dt.date,
) -> Result:
    """One due report resolved to a `Payload`, or to a `Refusal` naming what to
    fix.

    - `report` — one row from `db.read_due_reports`.
    - `forecast_config` — the *referenced* `forecast_configs` document, with its
      `version`, its `is_active` flag and the version that is active now
      (`active_version`) stamped in. The last of those is what lets a refusal
      name the version a superseded row should be repointed at, which is a
      database question and so is answered before this function is called.
    - `logged` — the `forecasts` rows under that version, at least the newest
      Forecast Origin's (what `db.read_latest_forecasts` returns). Not filtered
      to today: a refusal has to name the origin it actually found, and rows
      pre-filtered to today could not distinguish a late run from an empty log.
      Older origins may be present and are ignored.
    - `sales` — Sales history as `sales_history.load_sales_history` returns it.
    - `today` — the date in the restaurants' timezone, owned by the caller.
    """
    name = report.get("name", "report")
    version = forecast_config["version"]

    def refuse(reason: str, message: str) -> Refusal:
        return Refusal(report_name=name, reason=reason, message=message)

    if not forecast_config.get("is_active"):
        replacement = forecast_config.get("active_version")
        instead = (
            f"Version {replacement} is active now — repoint this report's "
            "forecast_config_version at it."
            if replacement is not None
            else "There is no active forecast configuration at all — activate "
            "one, then repoint this report's forecast_config_version at it."
        )
        return refuse(
            "superseded_config",
            f"{name} references forecast configuration version {version}, which "
            f"is no longer active. Nothing has been forecast under it since it "
            f"was superseded, so any window drawn from it would be frozen at "
            f"whenever that happened. {instead}",
        )

    if len(logged):
        logged = logged[logged["config_version"] == version]
    origins = sorted({_as_date(as_of) for as_of in logged["as_of"]}) if len(logged) else []
    newest_origin = origins[-1] if origins else None
    if newest_origin != today:
        found = (
            f"the newest is {newest_origin.isoformat()}"
            if newest_origin is not None
            else "no forecast has been logged under it at all"
        )
        return refuse(
            "stale_origin",
            f"{name} needs a Forecast Origin of {today.isoformat()} under "
            f"configuration version {version}, and {found}. Today's forecast run "
            "did not complete — re-run the 'Daily Demand Forecast' workflow, then "
            "re-run 'Scheduled Forecast Reports'.",
        )

    horizon_days = forecast_config["horizon_days"]
    window = _report_window(today, horizon_days)
    target = report["target"]
    varieties = list(report.get("varieties", []))
    required = [target] + varieties

    today_only = logged[[_as_date(as_of) == today for as_of in logged["as_of"]]]
    by_model = _logged_by_model(today_only)
    # Models the configuration ran, not merely models that happen to appear in
    # the log: a model configured but wholly absent is an omission worth naming,
    # not a model that silently never existed.
    configured: Set[str] = set(forecast_config.get("models", {})) | set(by_model)

    eligible: List[str] = []
    omitted: Dict[str, str] = {}
    for model in sorted(configured):
        gaps = _gaps(by_model.get(model, {}), required, window)
        if gaps:
            omitted[model] = _describe_gaps(gaps)
        else:
            eligible.append(model)

    if not eligible:
        return refuse(
            "no_eligible_model",
            f"{name} has no model covering {window[0].isoformat()} to "
            f"{window[-1].isoformat()} under configuration version {version}, so "
            f"no page can be drawn without a hole in it. "
            + "; ".join(f"{model}: {why}" for model, why in sorted(omitted.items()))
            + ". Re-run the 'Daily Demand Forecast' workflow; if a target date is "
            "still missing, that Forecast Target has no Sales history for that "
            "weekday to forecast from.",
        )

    members = forecast_config.get("targets", {}).get(target, [])
    settled = _settled_target_sales(sales, members, today)
    pages = [
        _page(model, by_model[model], target, varieties, window, settled, today)
        for model in _order(eligible, report["headline_model"])
    ]

    return Payload(
        report_name=name,
        target=target,
        varieties=varieties,
        headline_model=report["headline_model"],
        origin=today,
        config_version=version,
        window=window,
        pages=pages,
        omitted_models=omitted,
    )
