# Apply schema.sql to the shared Postgres via GitHub Actions on push to main, not a local git hook

`schema.sql` applies today by hand: a person runs `python db.py` against
`DATABASE_URL` after editing it. Nothing catches a forgotten apply, so the
shared Supabase instance can silently drift from what's checked in.

The obvious first idea — a local `pre-push` git hook that runs `python db.py`
when `schema.sql` is in the push — doesn't actually close that gap. A git hook
only fires on `git push` from a contributor's own machine. A PR merged through
GitHub's UI (or `gh pr merge`) lands the same commit on `main` without ever
running anyone's local hooks, so a schema change merged that way would never
apply. Local hooks also aren't tracked by git, so every clone needs its own
setup step just to have the hook at all.

Instead, a GitHub Actions workflow (`apply-schema.yml`) triggers on push to
`main`, filtered to `paths: ['schema.sql']`, mirroring `daily-capture.yml` /
`daily-forecast.yml`. This fires no matter how the commit reached `main` —
direct push or PR merge — because it runs server-side, not on a contributor's
laptop. It runs the same `python db.py` a person would run locally, against
the same `DATABASE_URL` secret the other two workflows already use, so no new
credential is introduced. `schema.sql`'s DDL is already idempotent (every
statement is `IF NOT EXISTS` or `CREATE OR REPLACE`), so the path filter is
purely an optimization — skipping a needless DB round-trip on pushes that
don't touch the file — not a correctness requirement. `workflow_dispatch` lets
a failed apply (e.g. a transient DB outage) be re-run by hand without waiting
for another `schema.sql` edit.

## Consequences

`schema.sql` changes on `main` apply to the shared DB automatically; nobody
has to remember to run `python db.py` after a merge. Local development and any
other branch still apply manually via `python db.py`, exactly as before —
only pushes to `main` are automated. A failed apply fails the workflow
visibly in the Actions tab, the same way a Toast or database failure already
fails `daily-capture.yml` / `daily-forecast.yml`.
