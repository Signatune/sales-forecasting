"""Deliver the Scheduled Reports that are due today (ADR 0010).

One command, run after the day's forecast has been logged (the
`scheduled-reports.yml` workflow gates it on `daily-forecast.yml`, which is
itself gated on the capture):

    python scheduled_reports.py

It is deliberately *thin*, in the way `daily_forecast.py` is thin. Every piece it
wires together is tested in its own right and none of the report logic lives
here:

- **Which reports exist and when they fire** comes from the database —
  `db.read_due_reports` — not from a cron per report. A second report on
  Tuesdays is an `INSERT`, never another workflow file (ADR 0010).
- **Whether a report can honestly be drawn** is `report_payload.build_payload`,
  a pure function. Every refusal rule, the Report Window, model eligibility and
  the Weekday Baseline live there.
- **The page** is `report_render`, and **the delivery** `report_delivery`.

So this module owns exactly three decisions: *which day* it is, that one
report's failure does not take the others down, and what the run reports about
itself.

`today` is the date in the restaurants' timezone, the same day
`daily_forecast.py` computes its `as_of` in — not the runner's UTC date, which
from 20:00 ET has already rolled over and would ask for the wrong weekday's
reports. On a Saturday-evening run that is the difference between delivering the
bagel report and delivering nothing at all.

**Zero due reports is success.** Most days there are none.
"""
import datetime as dt
import sys
import time
import traceback
from typing import Dict, List, Mapping, Optional

import pandas as pd
import psycopg

import db
import report_delivery
import report_payload
import report_render
import sales_history
from toast_client import RESTAURANT_TZ


def deliver_report(
    conn: psycopg.Connection,
    report: Dict,
    sales: pd.DataFrame,
    today: dt.date,
    environ: Optional[Mapping[str, str]],
    render,
    delivery,
) -> str:
    """Deliver one due report, returning the one line the run logs about it.

    The destination is resolved first, before any work: a report whose webhook
    or folder secret is missing cannot be delivered *or* refused, and finding
    that out after rendering a PDF wastes the render and buries the real error
    under a later one.

    Drive credentials are built per report rather than once for the whole run.
    It is a JSON parse and no network call, and it keeps a missing or malformed
    key a failure of *this* report — reported into its own space by the caller —
    rather than something that takes the whole morning down before any report is
    attempted."""
    destination = delivery.resolve_destination(report.get("delivery", ""), environ)
    version = report["forecast_config_version"]
    forecast_config = db.read_forecast_config(conn, version)
    logged = db.read_latest_forecasts(conn, version)

    result = report_payload.build_payload(
        report, forecast_config, logged, sales, today
    )
    if isinstance(result, report_payload.Refusal):
        delivery.post_refusal(result, destination.webhook_url)
        return (
            f"{result.report_name}: refused ({result.reason}); "
            "refusal posted to " + destination.name
        )

    credentials = delivery.drive_credentials(environ)
    pdf = render.render_pdf(result)
    link = delivery.upload_pdf(
        pdf, delivery.report_filename(result), destination.folder_id, credentials
    )
    delivery.post_card(result, link, destination.webhook_url)
    return (
        f"{result.report_name}: {len(result.pages)} page(s) for "
        f"{result.window[0]}..{result.window[-1]} delivered to "
        f"{destination.name} — {link}"
    )


def _post_failure(report: Dict, exc: Exception, environ, delivery) -> None:
    """Tell the report's space that it broke, if the space can still be reached.

    Best effort on purpose: the most common reason a report fails *is* an
    unresolvable destination, and raising out of the error path would turn one
    broken report into a broken run. Whatever happens here, the traceback is
    already on stderr."""
    try:
        destination = delivery.resolve_destination(report.get("delivery", ""), environ)
        delivery.post_refusal(
            report_payload.Refusal(
                report_name=report.get("name", "report"),
                reason="delivery_failed",
                message=(
                    f"It failed while being delivered: {exc}. The full traceback "
                    "is in the 'Scheduled Forecast Reports' run in the Actions "
                    "tab. Fix the cause and re-run that workflow — nothing has "
                    "been overwritten."
                ),
            ),
            destination.webhook_url,
        )
    except Exception as post_failure:  # noqa: BLE001 - best effort, see above
        print(
            f"could not post the failure notice: {post_failure}",
            file=sys.stderr,
        )


def main(
    argv: Optional[List[str]] = None,
    *,
    connect=db.connect,
    load_sales=sales_history.load_sales_history,
    now: Optional[dt.datetime] = None,
    environ: Optional[Mapping[str, str]] = None,
    render=report_render,
    delivery=report_delivery,
    sleep=time.sleep,
) -> int:
    """Deliver today's Scheduled Reports. Returns 0 when every due report was
    delivered or refused cleanly, and non-zero if any failed — so the Actions
    tab still shows red — but a broken Tuesday report must not silence a working
    Saturday one, so each is delivered inside its own try/except and the loop
    continues.

    `connect`, `load_sales`, `now`, `environ`, `render`, `delivery` and `sleep`
    are the seams the tests drive; `render` and `delivery` stand in for the two
    modules, so a fake can record the whole chain in order.

    Deliveries are paced by `WEBHOOK_MIN_INTERVAL_SECONDS`, not run
    concurrently: Google Chat allows one request per second per space, and a
    rate-limited post would drop a report whose PDF had already been uploaded
    (ADR 0010).

    `now` is whatever clock the caller has — the runner's is UTC — and must be
    timezone-aware."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(RESTAURANT_TZ).date()
    failures = 0
    try:
        with connect() as conn:
            reports = db.read_due_reports(conn, today)
            if not reports:
                print(f"no Scheduled Reports are due on {today}; nothing to deliver")
                return 0
            sales = load_sales()
            for position, report in enumerate(reports):
                if position:
                    sleep(delivery.WEBHOOK_MIN_INTERVAL_SECONDS)
                name = report.get("name", "report")
                try:
                    print(
                        deliver_report(
                            conn, report, sales, today, environ, render, delivery
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one report, not the run
                    failures += 1
                    print(f"{name}: delivery failed: {exc}", file=sys.stderr)
                    traceback.print_exc()
                    _post_failure(report, exc, environ, delivery)
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        # Reaching the database, or reading the Sales history, failed — nothing
        # could be attempted, so there is no per-report space to post into.
        print(f"scheduled reports failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print(
        f"{len(reports) - failures} of {len(reports)} Scheduled Report(s) "
        f"handled for {today}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
