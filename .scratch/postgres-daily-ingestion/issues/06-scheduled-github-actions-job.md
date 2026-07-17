# Run the daily capture on a GitHub Actions cron

Status: done
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
- [x] A successful scheduled run is observed in production, not just a manual one — secrets are set and a hand-triggered run succeeded end-to-end (auth → orders → Postgres). Accepted as done by the maintainer on the strength of the manual run plus the active `0 9 * * *` cron on `main`; a live cron fire had not yet been observed at close-out (see Comments)

## Blocked by

- `05-daily-orders-capture-trailing-window.md`

## Comments

**Close-out (2026-07-17).** Getting the first hand-triggered run green took two
fixes beyond the workflow itself:

1. **Packaging.** The `pip install -e .` step failed with "Multiple top-level
   packages discovered in a flat-layout: ['data', 'notebooks']". `pyproject.toml`
   declared no build-system and no discovery config, so setuptools' flat-layout
   auto-discovery couldn't choose a package. Fixed by declaring an explicit
   setuptools build-system and opting out with `[tool.setuptools] py-modules = []`
   — honest for a repo of top-level scripts run in place. (commit e7bcfdf)

2. **Credentials.** After the install was fixed, Toast returned `401
   access_denied` (code 10010) at the login endpoint. Reproduced from a laptop
   with the same values, so it was a credential problem, not a secrets-plumbing
   one: the standard API key's `clientSecret` had been **rotated** — same
   `clientId`, new secret. Updated `.env` and the three GitHub Actions secrets to
   the current values; login then returned 200 and the manual run succeeded
   end-to-end.

Remaining follow-up (not blocking close-out): confirm a **Scheduled**-triggered
run appears in the Actions tab after a `09:00 UTC` cycle. If cron reliability
ever bites, move off the contended top-of-hour slot (e.g. `7 9 * * *`).
