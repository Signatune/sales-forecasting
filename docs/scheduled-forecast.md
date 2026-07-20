# Scheduled daily forecast

Each morning's Demand Forecasts are produced by `daily_forecast.py` running on a
GitHub Actions runner and appended to the write-once `forecasts` log (ADR 0006,
ticket 05). The workflow is
[`.github/workflows/daily-forecast.yml`](../.github/workflows/daily-forecast.yml);
it reads the active configuration from the database, runs the engine, and
writes — no laptop has to be awake for it.

This is the *only* forecast job, by design. Adding a Forecast Target, retuning a
hyperparameter or widening the horizon is a row in `forecast_configs`, never
another workflow and never a code edit.

## When it runs

Not on a clock of its own: it triggers on the **completion of the "Daily Sales
capture" workflow** and runs only when that capture succeeded. A bare cron could
race the capture — on a slow Toast pull the forecast would read the Sales history
before the just-closed day landed and log a whole morning fit to stale data,
silently, since a forecast built on a day-old history still looks like a
forecast. Gating on the capture means the day is written before this starts.

A capture that *failed* wrote no Sales for the just-closed day, so the forecast
is skipped rather than run on an incomplete history; re-running the capture
triggers a fresh forecast behind it.

`daily_forecast.py` computes `as_of` as today in the restaurants' timezone
(`America/New_York`), the same way `daily_capture.py` computes its window — not
the runner's UTC date, which after 20:00 ET has already rolled over. Models see
Sales strictly before `as_of`, so the just-closed day is the newest observation
each forecast is built on.

## Secrets

| Secret | What it is |
| --- | --- |
| `DATABASE_URL` | Supabase **session-pooler** connection string ([docs/postgres.md](postgres.md) — the pooler is IPv4, which the runner needs) |

No Toast credentials: this job never contacts Toast, it reads the Sales the
capture already wrote.

## `statsmodels`

The workflow installs `pip install -e ".[forecast]"`. That extra adds
`statsmodels`, which `models.ets_forecast` imports lazily — so Holt-Winters runs
in production while the base and dev installs, and the default `pytest` run,
stay light.

## Run it by hand

The workflow is also `workflow_dispatch`-triggerable: **Actions** →
**Daily Demand Forecast** → **Run workflow**. That is an operator's way to re-run
a missed or failed morning without re-pulling Toast — not a "forecast on demand"
feature, which ADR 0006 and the PRD deliberately leave out: it still forecasts
as of today from what the database already holds. Safe to press twice: the log's
write-once
insert (`ON CONFLICT DO NOTHING`) fills the gaps a failed run left and never
overwrites what was already recorded, so a logged forecast stays frozen at what
was predicted that morning.

A concurrency group (`daily-forecast`, queueing rather than cancelling) keeps a
hand-triggered run and the capture-triggered one from writing at once.

A database, configuration or model failure exits non-zero and fails the run
visibly; a successful run's log names the config version it ran, how many rows
the engine produced, and how many the log accepted (a re-run of an
already-logged morning accepts none).
