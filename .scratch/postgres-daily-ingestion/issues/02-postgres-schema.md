# Stand up the Postgres schema

Status: done
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`

## What to build

A managed Postgres database, and the two tables ADR 0003 calls for:

- **Raw Toast responses**, stored as `jsonb`. This is the replay and audit safety
  net that `data/raw/` used to provide — the `modifierGuid` bug in the original
  ingest ticket was caught by rerunning normalization against saved raw responses
  without re-hitting Toast, and that has to stay possible. A row records what was
  captured, for which restaurant and business date, and when it was fetched.
- **Canonical Sales**, one row per `(Product, Date, Quantity)`, with
  `(Product, Date)` unique. The uniqueness is load-bearing: ADR 0004's daily job
  re-pulls the same business date on three consecutive days and must replace that
  day's row rather than accumulate duplicates.

The connection comes from a single environment variable so the same code runs
from a laptop and from a GitHub Actions runner. Applying the schema is a command
someone can run, and running it against an already-set-up database is harmless.

Demoable: apply the schema to an empty database, write the same `(Product, Date)`
twice with different quantities, and read back one row carrying the second
quantity.

## Acceptance criteria

- [ ] A managed Postgres instance exists and its connection string is read from the environment (never committed)
- [ ] Applying the schema creates both tables and is safe to re-run
- [ ] Canonical Sales enforces one row per `(Product, Date)`; a repeat write of the same day replaces it
- [ ] Raw responses are stored as `jsonb` and can be read back and re-normalized without contacting Toast
- [ ] Local setup — how a developer points at a database and applies the schema — is written down

## Blocked by

- None — can start immediately.
