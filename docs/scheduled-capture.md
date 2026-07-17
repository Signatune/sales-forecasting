# Scheduled daily capture

Each day's Sales are captured by `daily_capture.py` running on a GitHub Actions
cron — no laptop has to be awake for it (ADR 0003, ticket 06). The workflow is
[`.github/workflows/daily-capture.yml`](../.github/workflows/daily-capture.yml);
it just runs `python daily_capture.py` on a runner, with credentials from
secrets instead of a local `.env`.

## Schedule

`0 9 * * *` — 09:00 UTC, which is 04:00 EST / 05:00 EDT: early morning in the
restaurants' timezone (`America/New_York`), well after close and after the
business date has rolled over, so the day that just closed is settled before we
pull it. GitHub cron is always UTC and does not follow DST; this hour stays in
the early-morning ET window on either side of the switch.

`daily_capture.py` re-pulls a trailing window (the last three business dates), so
a missed cron heals on the next run — the newest day is captured, and the two
before it are re-checked and corrected if Toast changed them.

## Secrets

Set these under **Settings → Secrets and variables → Actions**. Nothing secret
is in the repo:

| Secret | What it is |
| --- | --- |
| `TOAST_URL` | Toast API base URL (`https://ws-api.toasttab.com`) |
| `TOAST_STANDARD_CLIENT_ID` | standard-key client id (`orders:read` scope) |
| `TOAST_STANDARD_CLIENT_SECRET` | standard-key client secret |
| `DATABASE_URL` | Supabase **session-pooler** connection string ([docs/postgres.md](postgres.md) — the pooler is IPv4, which the runner needs) |

The workflow exports the three Toast secrets as `URL`, `STANDARD_CLIENT_ID` and
`STANDARD_CLIENT_SECRET` — the same names `daily_capture.py` reads from a local
`.env` — so the same code runs on a laptop and on the runner.

## Run it by hand

The workflow is also `workflow_dispatch`-triggerable: open the **Actions** tab →
**Daily Sales capture** → **Run workflow**. That is how it is demoed, and how a
missed day is re-run without waiting for tomorrow. A Toast or database failure
exits non-zero and fails the run visibly; a successful run's log names the
business dates it captured and how many Sales rows it upserted.
