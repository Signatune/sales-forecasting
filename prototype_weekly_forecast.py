"""PROTOTYPE --- THROWAWAY. Delete me once a variant has won.

Question this answers: *what should the upcoming-week bagel forecast report look
like?* Three radically different variants of one report, on one route, switchable
via `?variant=` and the floating bottom bar. See NOTES-prototype-weekly-forecast.md.

Run it:

    python prototype_weekly_forecast.py            # real DB + real models
    python prototype_weekly_forecast.py --fabricate # no DB needed

It serves http://localhost:8765/?variant=A. Read-only: it opens one connection,
reads Sales and the Demand Forecast log, runs the real models in memory, and
writes nothing back.

Where the numbers come from
---------------------------
The report covers the upcoming Monday--Sunday. The daily job's `horizon_days` is
7, so a Friday `as_of` reaches only to the following Friday --- the week's last
two days are past the logged horizon. So each day is sourced twice over:

- `logged`   --- read straight out of the `forecasts` table (ADR 0006), frozen at
                 what was predicted that morning. This is the real report's
                 source of truth.
- `extended` --- beyond the logged horizon, `forecast_engine.run_forecasts` is
                 re-run in memory at a longer horizon off the same Sales history.
                 Same models, same config, just further out. Nothing is logged.

The variants show that distinction, because a real report has to: "we predicted
this on Friday" and "we extrapolated this just now" are not the same claim.

Trend is against the last *complete* Monday--Sunday week of actual Sales, matched
weekday to weekday. The current week is deliberately not the baseline --- today
is mid-week and its capture is partial (ADR 0004's trailing window is still
open), so comparing against it would read as a phantom collapse.
"""
import argparse
import datetime as dt
import json
import random
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TEMPLATE = Path(__file__).parent / "prototype_weekly_forecast.html"

# The headline Forecast Target and the varieties it splits into. Hardcoded
# because this is a prototype of one report, not a general viewer.
HEADLINE = "wheat_bagels"
VARIETIES = ["plain", "sesame", "everything"]


def upcoming_week(today):
    """The next Monday--Sunday strictly after `today`."""
    monday = today + dt.timedelta(days=7 - today.weekday())
    return [monday + dt.timedelta(days=i) for i in range(7)]


def last_complete_week(today):
    """The most recent Monday--Sunday that has entirely happened."""
    sunday = today - dt.timedelta(days=today.weekday() + 1)
    monday = sunday - dt.timedelta(days=6)
    return [monday + dt.timedelta(days=i) for i in range(7)]


def _trend(forecast, actual):
    """Direction and size of the move, or `unknown` when the baseline day has no
    Sales at all (a closure, or a gap in the history). A missing baseline is not
    a 100% jump and must not render as one."""
    if actual is None or actual <= 0:
        return {"direction": "unknown", "delta": None, "delta_pct": None}
    delta = forecast - actual
    pct = delta / actual * 100
    # Under 3% is noise on a series that swings 300--680 a day; calling it a
    # trend would have the board chasing rounding.
    direction = "flat" if abs(pct) < 3 else ("up" if pct > 0 else "down")
    return {"direction": direction, "delta": delta, "delta_pct": pct}


def from_database(model, today):
    """Real Sales, the real Demand Forecast log, and the real models for the days
    the log does not reach."""
    import pandas as pd

    import db
    import forecast_engine

    week = upcoming_week(today)
    baseline = last_complete_week(today)

    with db.connect(connect_timeout=10) as conn:
        sales = db.read_sales(conn)
        config = db.read_active_config(conn)
        as_of = conn.execute("SELECT max(as_of) FROM forecasts").fetchone()[0]
        logged = conn.execute(
            "SELECT model, target, target_date, forecast_quantity FROM forecasts "
            "WHERE as_of = %s",
            (as_of,),
        ).fetchall()

    # (model, target, date) -> quantity, for both the logged rows and, below, the
    # extended run. Provenance is tracked separately so the UI can label it.
    values = {(m, t, d): q for m, t, d, q in logged}
    provenance = {d: "logged" for (_, _, d) in values}

    # Reach the rest of the week by re-running the same models further out.
    uncovered = [d for d in week if d not in provenance]
    if uncovered:
        horizon = (max(uncovered) - as_of).days
        extended = forecast_engine.run_forecasts(
            {**config, "horizon_days": horizon}, sales, as_of
        )
        for row in extended.itertuples():
            key = (row.model, row.target, row.target_date)
            if key not in values:
                values[key] = row.forecast_quantity
                provenance.setdefault(row.target_date, "extended")

    # Actual Sales for the baseline week, per variety and summed.
    actuals = {}
    for row in sales.itertuples():
        date = pd.Timestamp(row.date).date()
        if date in baseline:
            actuals[(row.product, date)] = float(row.quantity)

    days = []
    for target_date, baseline_date in zip(week, baseline):
        forecast = values.get((model, HEADLINE, target_date))
        actual = sum(
            actuals.get((v, baseline_date), 0.0) for v in VARIETIES
        ) or None
        days.append(
            {
                "date": target_date.isoformat(),
                "weekday": target_date.strftime("%A"),
                "short": target_date.strftime("%a"),
                "label": target_date.strftime("%b %-d"),
                "provenance": provenance.get(target_date, "extended"),
                "forecast": forecast,
                "by_model": {
                    m: values.get((m, HEADLINE, target_date))
                    for m in config["models"]
                },
                "varieties": {
                    v: values.get((m_, v, target_date))
                    for v in VARIETIES
                    for m_ in [model]
                },
                "baseline_date": baseline_date.isoformat(),
                "baseline_label": baseline_date.strftime("%b %-d"),
                "actual": actual,
                "varieties_actual": {
                    v: actuals.get((v, baseline_date)) for v in VARIETIES
                },
                **_trend(forecast, actual),
            }
        )

    return {
        "source": "database",
        "as_of": as_of.isoformat(),
        "config_version": config["version"],
        "model": model,
        "models": sorted(config["models"]),
        "days": days,
    }


def fabricated(model, today):
    """Plausible numbers in the shape the real payload has, for when there is no
    database to hand. Deliberately built off the same weekday rhythm the real
    series shows --- weekends roughly double a weekday."""
    rng = random.Random(20260724)
    week = upcoming_week(today)
    baseline = last_complete_week(today)
    # Monday..Sunday centres, eyeballed off the real 2026 July history.
    centres = [300, 360, 355, 400, 425, 660, 650]
    splits = {"plain": 0.31, "sesame": 0.26, "everything": 0.43}

    days = []
    for i, (target_date, baseline_date) in enumerate(zip(week, baseline)):
        forecast = centres[i] * rng.uniform(0.95, 1.06)
        actual = centres[i] * rng.uniform(0.88, 1.12)
        days.append(
            {
                "date": target_date.isoformat(),
                "weekday": target_date.strftime("%A"),
                "short": target_date.strftime("%a"),
                "label": target_date.strftime("%b %-d"),
                "provenance": "logged" if i < 5 else "extended",
                "forecast": forecast,
                "by_model": {
                    "ewma": forecast,
                    "holt_winters": forecast * rng.uniform(0.96, 1.04),
                },
                "varieties": {v: forecast * s for v, s in splits.items()},
                "baseline_date": baseline_date.isoformat(),
                "baseline_label": baseline_date.strftime("%b %-d"),
                "actual": actual,
                "varieties_actual": {v: actual * s for v, s in splits.items()},
                **_trend(forecast, actual),
            }
        )

    return {
        "source": "fabricated",
        "as_of": today.isoformat(),
        "config_version": 0,
        "model": model,
        "models": ["ewma", "holt_winters"],
        "days": days,
    }


def build_payload(model, today, fabricate):
    if fabricate:
        payload = fabricated(model, today)
    else:
        try:
            payload = from_database(model, today)
        except Exception as error:  # prototype: any DB trouble falls back
            print(f"  ! database unavailable ({error}); using fabricated data")
            payload = fabricated(model, today)

    days = payload["days"]
    forecast_total = sum(d["forecast"] or 0 for d in days)
    actual_total = sum(d["actual"] or 0 for d in days)
    payload["week"] = {
        "start": days[0]["date"],
        "end": days[-1]["date"],
        "label": f"{days[0]['label']} – {days[-1]['label']}",
        "baseline_label": f"{days[0]['baseline_label']} – {days[-1]['baseline_label']}",
        "forecast": forecast_total,
        "actual": actual_total,
        **_trend(forecast_total, actual_total),
    }
    payload["generated_at"] = dt.datetime.now().strftime("%A %-d %B, %-I:%M %p")
    return payload


def render(payload):
    return TEMPLATE.read_text().replace(
        "__PAYLOAD__", json.dumps(payload, default=str)
    )


def serve(payload, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = render(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    url = f"http://localhost:{port}/?variant=A"
    print(f"\n  Weekly forecast report prototype --- {url}")
    print("  Variants: ?variant=A, B, C. Arrow keys or the bottom bar to switch.")
    print("  Ctrl-C to stop.\n")
    webbrowser.open(url)
    HTTPServer(("localhost", port), Handler).serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fabricate", action="store_true", help="skip the DB")
    parser.add_argument("--model", default="ewma", help="which model to headline")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--today", help="override today, YYYY-MM-DD")
    parser.add_argument("--dump", action="store_true", help="print payload, don't serve")
    args = parser.parse_args(argv)

    today = (
        dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    )
    payload = build_payload(args.model, today, args.fabricate)

    if args.dump:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    try:
        serve(payload, args.port)
    except KeyboardInterrupt:
        print("  stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
