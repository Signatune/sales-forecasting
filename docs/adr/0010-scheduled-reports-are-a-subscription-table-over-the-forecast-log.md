# Scheduled reports are rows in a subscription table, not a workflow per report

The weekly bagel report could have been one GitHub Actions workflow with
`cron: "0 10 * * 6"` in it. Instead, **which reports exist and when they fire is
data**: a `report_configs` table where each row names a forecast configuration
(by foreign key to `forecast_configs.version`), the weekdays it should be
delivered on, and what its pages say. One daily workflow — gated on the daily
forecast the way that job is gated on the capture — asks which rows want
delivering today and delivers them.

This is the same move ADR 0006 made for the forecast surface, applied one layer
up: the forecast surface is configuration so it can change without a deploy, and
the *reporting* surface is configuration for the same reason. A second report on
Tuesdays is an `INSERT`, not another workflow file and another cron to keep in
step with the first.

Load-bearing decisions:

- **The Report Window is emergent, not configured.** A report covers
  `origin+1 .. origin+horizon_days` of the configuration it references. Nothing
  anywhere stores "Sunday through Saturday" — that window is simply what a
  Saturday-scheduled report against a seven-day horizon *is*. Moving the row to
  Wednesdays yields Thursday-through-Wednesday with no code change, and raising
  `horizon_days` widens every report at once. A `week_start` setting would have
  been a second source of truth for something the schedule already determines,
  and the two could disagree.
- **The origin must be today, or the report refuses.** The obvious resolution —
  "the latest forecast logged under this config version" — always returns a row,
  so two distinct failures would both render as a normal-looking bake sheet:
  a forecast run that did not complete (window silently shifted a day earlier,
  including today), and a configuration that has been superseded (window frozen
  at whenever that version was retired, potentially a week already past). Both
  are refused, with a Chat message naming which one it was. This costs nothing
  on a normal day, since the forecast runs daily; it exists because a stale bake
  sheet is indistinguishable from a fresh one at a glance.
- **A model earns a page only by covering the whole window.** The engine emits no
  row for a target date it has no evidence for (`forecast_engine.run_forecasts`),
  so a model can cover six of seven days. Such a model is omitted and named in a
  note on the surviving pages rather than rendered with a hole. If no model
  qualifies, no PDF is produced.
- **Destinations are symbolic names, not URLs in the table.** A row's `delivery`
  key names a destination that the workflow resolves to secrets
  (`CHAT_WEBHOOK_<NAME>`, `DRIVE_FOLDER_<NAME>`). A Google Chat webhook URL is a
  bearer credential; keeping it out of Postgres keeps it out of backups and table
  dumps, and rotating it stays a secrets change.

## Consequences

- One PDF per Report Window accumulates in Drive, named by the window's start
  date, never overwritten — the same write-once instinct as the forecast log, and
  what makes an old Chat link still open the week it announced.
- Google Chat **incoming webhooks cannot upload attachments**: that needs
  `media.upload` under OAuth user credentials, which a webhook's `key`/`token`
  authorization cannot reach. So the PDF goes to Drive under a service account
  and the webhook posts a card with a link. Nothing about this is a limitation of
  the report; it is a fixed property of webhooks, and switching to a full Chat app
  would be the only way around it.
- The service account has no Drive storage of its own, so uploads name a parent
  it is a member of. That parent is a **shared Drive**: files there are owned by
  the Drive rather than by the account that wrote them, so rotating or deleting
  the service account — or the departure of whoever set it up — cannot take the
  back catalogue of reports with it, and the write-once links above keep opening.
  Writing into a shared Drive needs the full `drive` scope rather than
  `drive.file`; the account is bounded by *membership* instead, being a Content
  manager on this one Drive and a member of nothing else.
