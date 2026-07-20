"""Run the day's Demand Forecasts and append them to the write-once log
(ADR 0006).

One command, run once a morning after the day's Sales are captured (the
`daily-forecast.yml` workflow gates it on `daily-capture.yml`), so the forecast
sees the just-closed day:

    python daily_forecast.py

It is deliberately *thin*. Every piece it wires together is already tested in
its own right, and none of the forecasting lives here:

- **What to forecast** comes from the database — `db.read_active_config` — not
  from constants in this file. Adding a Forecast Target, retuning a
  hyperparameter or widening the horizon is a row in `forecast_configs`, never
  an edit here and never another workflow.
- **The forecasting** is `forecast_engine.run_forecasts`, a pure function of
  `(config, sales, as_of)`. Nothing bake-specific — no lead, no buffer, no
  Poolish — passes through this module either; those are reads over the log.
- **The write** is `db.insert_forecasts`, whose `ON CONFLICT DO NOTHING` makes
  a re-run safe: a morning that failed part way can simply be run again, and
  what was already logged stays frozen at what was predicted then.

So this module owns exactly three decisions: *which day* is being forecast,
that a failure is loud, and what the run reports about itself.

`as_of` is today in the restaurants' timezone, the same day `daily_capture.py`
computes its window in — not the runner's UTC date, which from 20:00 ET has
already rolled over and would forecast from tomorrow. Sales strictly before
`as_of` are what the models see (`forecast.history_before`), so the just-closed
day is the newest observation each forecast is built on.
"""
import datetime as dt
import sys
import traceback
from typing import Dict, List, Optional

import pandas as pd
import psycopg

import db
import forecast_engine
import sales_history
from toast_client import RESTAURANT_TZ


def run_daily_forecast(
    conn: psycopg.Connection, sales: pd.DataFrame, as_of: dt.date
) -> Dict[str, int]:
    """Forecast `as_of`'s morning under the active configuration and append the
    rows to the log, returning `{config_version, rows, inserted}`.

    `rows` is what the engine produced and `inserted` what the log actually
    accepted; they differ when a re-run meets forecasts already logged for that
    `(as_of, config_version)` — the write-once contract at work rather than an
    error, which is why both are reported instead of one count."""
    config = db.read_active_config(conn)
    log = forecast_engine.run_forecasts(config, sales, as_of)
    inserted = db.insert_forecasts(conn, log)
    return {
        "config_version": int(config["version"]),
        "rows": len(log),
        "inserted": inserted,
    }


def main(
    argv: Optional[List[str]] = None,
    *,
    connect=db.connect,
    load_sales=sales_history.load_sales_history,
    now: Optional[dt.datetime] = None,
) -> int:
    """Run the daily forecast. Returns 0 on success; on a database,
    configuration or model failure, prints a clear message to stderr and returns
    non-zero so the failure is visible in the Actions tab rather than a green
    run with an empty log. `connect`, `load_sales` and `now` are seams for
    testing.

    Configuration and model mistakes surface as `ValueError` from the engine —
    an unknown Product in a Target, a hyperparameter a model does not take, a
    model it cannot run. They are caught here for the same reason a database
    failure is: they are the owner's configuration to fix, and their messages
    already say what to fix, so leading with the bare message puts the fix at
    the top of the Actions log. The traceback follows it rather than replacing
    it, because `ValueError` is a wide net: one thrown from inside pandas is a
    bug in the engine, not a configuration to correct, and that one needs the
    stack.

    `now` is whatever clock the caller has — the runner's is UTC — and is
    converted here, so it must be timezone-aware."""
    now = now or dt.datetime.now(dt.timezone.utc)
    as_of = now.astimezone(RESTAURANT_TZ).date()
    try:
        sales = load_sales()
        with connect() as conn:
            counts = run_daily_forecast(conn, sales, as_of)
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        print(f"daily forecast failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(
        f"forecast as of {as_of} under config version {counts['config_version']}: "
        f"produced {counts['rows']} rows, logged {counts['inserted']} "
        f"({counts['rows'] - counts['inserted']} already recorded)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
