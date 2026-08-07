# Scheduled Forecast Reports: the one-time setup

Each morning, after the daily forecast has logged its origin, the
[`scheduled-reports.yml`](../.github/workflows/scheduled-reports.yml) workflow
runs [`scheduled_reports.py`](../scheduled_reports.py). It asks `report_configs`
which Scheduled Reports fire on today's weekday, renders each to a PDF from the
forecasts already in the log, uploads it to Google Drive, and posts a Google
Chat card linking to it (ADR 0010).

**Which reports exist and when they fire is data, not code.** A second report on
Tuesdays is one `INSERT`; there is no second workflow and no second cron.

Everything below is the part that is *not* code — the Google accounts, the
sharing, and the secrets. Do it once per destination.

## The moving parts

| Piece | Where it lives |
|---|---|
| Which reports exist, and their weekdays | `report_configs` ([`migrations/0001-baseline.sql`](../migrations/0001-baseline.sql)) |
| Whether a report can honestly be drawn | [`report_payload.py`](../report_payload.py) |
| The page | [`report_render.py`](../report_render.py) |
| Drive upload and the Chat card | [`report_delivery.py`](../report_delivery.py) |
| Webhook URLs and folder ids | GitHub Actions secrets — never Postgres |

A Chat webhook URL is a **bearer credential**: anyone holding it can post to the
space. It stays out of the database so it stays out of backups and table dumps,
and so rotating it is a secrets change rather than a migration.

## 1. A Drive service account

The job uploads as a service account, not as a person, so nobody's password is
in a runner.

1. In the [Google Cloud console](https://console.cloud.google.com/), create (or
   pick) a project and enable the **Google Drive API**.
2. **IAM & Admin → Service Accounts → Create service account.** No project roles
   are needed — everything it may touch comes from Drive sharing, below.
3. On the account, **Keys → Add key → Create new key → JSON**. Download it.
4. Note the account's email, `something@project-id.iam.gserviceaccount.com`.

Store the key file's **entire contents** as the repository secret
`GOOGLE_SERVICE_ACCOUNT_JSON` — the JSON itself, braces included, not a path. A
runner has no file to point at.

## 2. A shared Drive, with the account as a member

**A service account has no Drive storage of its own.** It cannot create a file
that has no parent, so every upload names a parent it was made a member of.

Reports go to a **shared Drive**, not to a folder in someone's My Drive. The
difference is ownership: a file in a shared Drive is owned by the Drive, so
deleting the service account — or the person who set this up leaving — cannot
take the back catalogue with it, and old Chat links keep opening.

1. In Drive, **Shared drives → + New**, name it (e.g. *Bagel forecast reports*).
   You need to be a **Manager** of it to do the next step.
2. **Manage members** → paste the service account's email → role **Content
   manager** → **Send**. Google may warn that it is outside your organisation
   and cannot receive mail; that is expected, add it anyway.
3. Add the people who should be able to open the reports. **Viewer** is enough —
   the Chat card links to the file, and the link only opens for people the Drive
   already reaches.
4. Take the id out of the URL. For the Drive's root,
   `https://drive.google.com/drive/folders/`**`<this-part>`**; for a subfolder
   inside it, open that folder and take the same trailing segment.

Store that id as `DRIVE_FOLDER_<NAME>` (see step 4 for `<NAME>`).

> **If your Workspace admin restricts sharing outside the organisation**, step 2
> fails — a service account's `…iam.gserviceaccount.com` address is a different
> domain. The setting is *Admin console → Apps → Google Workspace → Drive and
> Docs → Sharing settings → allow members outside the organisation*, scoped to
> the OU the Drive belongs to. This is the one step here that may need an admin
> who is not you.
>
> Uploading into a shared Drive needs the full
> `https://www.googleapis.com/auth/drive` scope, which is what `DRIVE_SCOPES` in
> [`report_delivery.py`](../report_delivery.py) is set to. The narrower
> `drive.file` grants access only to files the app itself created and cannot
> create into a folder it did not create. The account stays harmless through
> *membership* instead: it is a Content manager on this one Drive and a member
> of nothing else, so "everything it can see" is exactly this Drive.

## 3. A Google Chat webhook

1. In the Chat **space** that should receive the report: **space name → Apps &
   integrations → Webhooks → Add webhook**. Name it, then copy the URL.
2. Store it as `CHAT_WEBHOOK_<NAME>`.

Two things about webhooks are fixed, and are why the report is shaped as it is:

- **They cannot upload attachments.** That needs `media.upload` under OAuth
  *user* credentials, which a webhook's `key`/`token` authorization cannot
  reach. Hence Drive plus a link. Do not spend an afternoon on this — a full
  Chat app is the only way around it.
- **Card images must be HTTPS-hosted PNG or JPG**, so the chart cannot be
  inlined either. The card carries the numbers and a button.

They are also rate-limited to **one request per second per space**, which is why
`scheduled_reports.py` delivers sequentially with a gap.

## 4. Naming the destination

A report row names its destination *symbolically*, in
`report_configs.config.delivery`. The name is upper-cased with hyphens turned
into underscores to make the two secret names:

| `delivery` | Secrets |
|---|---|
| `bagel-team` | `CHAT_WEBHOOK_BAGEL_TEAM`, `DRIVE_FOLDER_BAGEL_TEAM` |

A name that resolves to nothing is a **loud error**, never a skipped delivery: a
report that silently goes nowhere looks exactly like one that was never due.

GitHub gives a workflow no way to enumerate secrets, so adding a destination
means adding its two secrets **and** adding the two lines that pass them through
in [`scheduled-reports.yml`](../.github/workflows/scheduled-reports.yml).

## 5. The report row

With the forecast configuration already active (ADR 0006), subscribe to it:

```sql
INSERT INTO report_configs
  (forecast_config_version, days_of_week, is_active, config)
VALUES (1, '{6}', true, '{
  "name": "Bagel forecast",
  "headline_model": "ewma",
  "target": "wheat_bagels",
  "varieties": ["everything", "plain", "sesame"],
  "delivery": "bagel-team"
}'::jsonb);
```

- `days_of_week` is Postgres' `EXTRACT(DOW)` convention — **0 is Sunday**, so
  `{6}` is Saturdays. It may list several.
- `forecast_config_version` is a foreign key: a report can only reference a
  configuration that was actually used to forecast.
- **The Report Window is not stored.** It is `origin+1 .. origin+horizon_days`
  of the referenced configuration, so a Saturday report against a seven-day
  horizon covers Sunday through Saturday, and moving the row to Wednesdays
  covers Thursday through Wednesday with no code change.
- `headline_model` leads the PDF and is the model quoted in the card (ADR 0011).
  Every other qualifying model still gets a full page.
- Unlike `forecast_configs`, this table is **edited in place**. Rescheduling a
  report is an `UPDATE`.

## What a report refuses to do

The report requires a Forecast Origin of **today** under the referenced
configuration and refuses otherwise, posting plain text to the space rather than
a card. Two failures, two messages, because they need different fixes:

| Message says | What happened | Fix |
|---|---|---|
| the version is no longer active | the row points at a superseded configuration | repoint `forecast_config_version` at the active version it names |
| the newest origin is *older than today* | the morning's forecast did not complete | re-run **Daily Demand Forecast**, then re-run this workflow |
| no model covers the window | a model logged no row for some target date | re-run the forecast; if a date is still missing, that Forecast Target has no Sales history for that weekday |

A model missing even one day — of the headline Target *or* of a variety — is
left out of the report and named on the surviving pages, rather than drawn with
a hole in it. If the report's `headline_model` is the one left out, the report
still goes out under whichever model leads, and both the page and the Chat card
say that the headline was substituted.

**The report runs even when the forecast failed**, unlike the forecast itself,
which is skipped when the capture fails. That is the whole point of the
stale-origin refusal: the people who read the report read Chat, not the Actions
tab, and silence on a Saturday morning is indistinguishable from "nothing was
due today".

## What the report is not

**It is not a bake sheet.** The pages carry point Demand Forecasts with no
Service Level buffer applied anywhere, and say so in plain type: baking to these
numbers means running out about half the time (ADR 0012). Turning them into a
Bake-to Quantity is a separate read over the log.

Running accuracy statistics, Service Level buffering, and preorder/catering
figures are all deliberately out of the first version; the page reserves a
labelled block for the last of these so adding it later does not reflow.

## Running it by hand

**Actions → Scheduled Forecast Reports → Run workflow.** This re-delivers
without re-forecasting. Safe to press: the origin's logged rows are frozen, so a
re-run of the same Report Window produces the same numbers under the same
filename.

Locally, with `DATABASE_URL` and the destination's secrets in `.env`:

```
pip install -e ".[forecast,report]"
python scheduled_reports.py
```

WeasyPrint needs Pango, which pip does not carry. On Debian/Ubuntu that is
`libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0`; on macOS,
`brew install pango` (and, on Apple silicon, `export
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` so the dynamic loader finds it).
