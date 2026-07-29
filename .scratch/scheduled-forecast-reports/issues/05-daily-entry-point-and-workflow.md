# The daily entry point and its scheduled workflow

Status: ready-for-agent
Blocked by: 04

## Parent

`.scratch/scheduled-forecast-reports/PRD.md`

## What to build

`scheduled_reports.py` and `.github/workflows/scheduled-reports.yml`.

### `scheduled_reports.py`

Thin, in the way `daily_forecast.py` is thin — every piece it wires together is
tested in its own right and none of the report logic lives here:

```
today = now.astimezone(RESTAURANT_TZ).date()
for report in db.read_due_reports(conn, today):
    result = report_payload.build_payload(...)
    if refusal:  report_delivery.post_refusal(...);  continue
    pdf  = report_render.render_pdf(payload)
    link = report_delivery.upload_pdf(...)
    report_delivery.post_card(payload, link, ...)
```

`today` is the date in the restaurants' timezone, not the runner's UTC date —
from 20:00 ET the runner has already rolled over and would ask for the wrong
weekday's reports. This is the same reason `daily_forecast.py` converts.

**One report's failure must not take the others down.** Each is delivered in its
own try/except; a failure posts a refusal where it can, is logged, and the loop
continues. The exit code is non-zero if any report failed, so the Actions tab
still shows red — but a broken Tuesday report must not silence a working Saturday
one.

Zero due reports is success, and says so. Most days there are none.

### `.github/workflows/scheduled-reports.yml`

Modelled on `daily-forecast.yml`:

- triggered on `workflow_run` completion of **"Daily Demand Forecast"**, not a
  bare cron. A cron would race the forecast, and the strict same-day origin rule
  (ADR 0010) would then refuse a report whose forecast was merely a few minutes
  late — turning a timing race into a weekly false alarm
- `workflow_dispatch` too, to re-run a missed report without re-forecasting
- `concurrency: scheduled-reports`, `cancel-in-progress: false` — a run in
  progress is uploading and posting; let it finish
- installs `.[forecast,report]` and the WeasyPrint system libraries
  (`libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`, `libfribidi0`)

Secrets: `DATABASE_URL`, the Drive service-account JSON, and the per-destination
`CHAT_WEBHOOK_<NAME>` / `DRIVE_FOLDER_<NAME>` pairs. Document them in the
workflow header the way the other three workflows document theirs.

## Tests

A new `tests/test_scheduled_reports.py`, driving `main` through its seams:

- no due reports exits 0 and delivers nothing
- a due report runs the whole chain in order
- a refusal posts a refusal and does not upload
- one report raising still delivers the others, and the exit code is non-zero
- a UTC time after 20:00 ET asks for the *previous* day's weekday

## Docs

Add a `docs/reports.md` covering the one-time setup that is not code: creating
the service account, sharing the Drive folder to it, creating the Chat webhook,
and the secret names. `docs/postgres.md` is the model.
