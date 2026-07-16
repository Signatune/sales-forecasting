# Run the daily capture on a GitHub Actions cron

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`

## What to build

Getting each day's Sales automatically needs a trigger that does not depend on a
laptop being awake at the right time. A GitHub Actions workflow runs the daily
capture from ticket 05 on a cron schedule, once a day, and writes to the managed
Postgres database.

Toast credentials move out of the local `.env` and into GitHub Actions secrets,
along with the database connection string. The schedule should sit far enough
after the restaurants' close that the business date is settled, in the
restaurants' timezone rather than UTC.

The workflow is also manually triggerable — that is how it gets demonstrated, and
how a missed day gets re-run without waiting for tomorrow.

Demoable: trigger the workflow by hand, watch it succeed in the Actions tab, and
see the last three business dates present and correct in Postgres.

## Acceptance criteria

- [x] A workflow runs the daily capture on a daily cron, timed after close in the restaurants' timezone — `.github/workflows/daily-capture.yml`, `0 9 * * *` (04:00 EST / 05:00 EDT in `America/New_York`)
- [x] Toast credentials and the database connection string come from GitHub Actions secrets; no credential is in the repo — `load_standard_credentials` and `db.connection_string` read them from the environment, exported from secrets by the workflow
- [x] The workflow can also be triggered manually — `workflow_dispatch`
- [x] A failing run fails the workflow visibly rather than passing silently — a Toast/DB failure exits non-zero, failing the step and the run
- [x] The run's log says what it captured — which business dates, how many Sales rows — `daily_capture.main` prints the captured business dates and upserted/stored counts
- [ ] A successful scheduled run is observed in production, not just a manual one — requires setting the secrets and waiting for the cron; can only be checked once deployed

## Blocked by

- `05-daily-orders-capture-trailing-window.md`
