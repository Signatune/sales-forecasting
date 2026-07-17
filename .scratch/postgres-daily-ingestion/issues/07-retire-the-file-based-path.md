# Retire the file-based ingestion path

Status: done
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`
`docs/adr/0004-daily-capture-uses-orders-api-only-with-a-trailing-window.md`

## What to build

Once Postgres is the source of truth (ticket 04) and the scheduled job is
actually running (ticket 06), the old path is dead weight that can still mislead
someone. Remove it.

- `data/raw/` is no longer tracked in the repo, and neither is
  `sales_history.parquet`. ADR 0003 makes this explicit: the raw JSON's job — being
  able to replay normalization without re-hitting Toast — now belongs to the raw
  `jsonb` table. **Do not delete the local files until ticket 03's migration has
  been confirmed against a live database**; untracking them is a one-way door for
  anyone who clones fresh.
- `ingest.py`'s manual two-source flow goes away — nobody types it any more, and
  `normalize.py` no longer rebuilds the whole history from every raw file on
  every run.
- `toast_client.py` (Analytics) **stays in the repo**. ADR 0004 keeps it
  deliberately: it is no longer on the daily path, but it is what a future
  backfill or a manual reconciliation would use if Orders-API counting ever drifts
  from Analytics. Removing it would throw away the only cross-check.

Finally, `CONTEXT.md` should describe the system that now exists rather than the
one that used to.

Demoable: a fresh clone of the repo, with only the database connection string and
Toast credentials configured, can run the forecast — with no data files in the
repo at all.

## Acceptance criteria

- [x] `data/raw/` and `sales_history.parquet` are untracked and ignored, after the migration is confirmed against the live database
- [x] The manual `ingest.py` flow and the rebuild-from-all-raw-files behaviour are gone
- [x] `toast_client.py` remains, documented as off the daily path and kept for backfill or reconciliation
- [x] A fresh clone with credentials configured can run the forecast against Postgres with no data files present
- [x] The test suite passes without any tracked data files
- [x] `CONTEXT.md` reflects Postgres as the source of truth and the scheduled Orders-only capture

## Blocked by

- `04-readers-read-from-postgres.md`
- `06-scheduled-github-actions-job.md`
