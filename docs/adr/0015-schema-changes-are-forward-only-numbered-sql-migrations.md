# Schema changes are forward-only numbered .sql migrations, applied by a runner in db.py

Supersedes the mechanism of [ADR 0007](0007-apply-schema-via-github-actions-on-push-to-main.md),
and keeps its conclusion.

`schema.sql` is a *desired-state* file: every statement is `IF NOT EXISTS` or
`CREATE OR REPLACE`, so `python db.py` can run the whole thing top to bottom
against an already-set-up database and change nothing. That property is what
made ADR 0007's auto-apply safe, and it holds for exactly as long as the schema
only ever grows. It cannot express a rename, a table split, or a
backfill-then-drop, because those are not states — they are ordered steps. The
menu-engineering work needs precisely that: the capture grain change is a
restructure of `sales`, not an addition to it. So the desired-state file gives
way to an ordered list of steps.

## Numbered `.sql` files with a runner in `db.py`, not Alembic or the Supabase CLI

Migrations are `migrations/NNNN-<slug>.sql`, numbered from `0001`, applied in
order by a runner in `db.py` against a `schema_migrations` table that records
which files have run. Each file is applied inside its own transaction, so a
migration that fails partway leaves nothing behind — Postgres has transactional
DDL, and that is the rollback guarantee that matters day to day.

Alembic is the Python default, but its headline feature is autogenerating
migrations by diffing SQLAlchemy models against the database, and this repo has
no ORM — `db.py` is raw `psycopg` (ADR 0003). Adopting it means carrying the
dependency purely as a runner and writing migrations as Python files wrapping
SQL strings. The Supabase CLI is native to where the database actually lives,
but it needs Docker, the `supabase` binary, and a linked project on every
machine including the three GitHub Actions runners — a real cost imposed on
workflows that today need nothing but `pip install -e .`.

Plain `.sql` keeps migrations in the language the schema is already written in,
adds no install for any contributor or runner, and puts the behavior somewhere
it can be read. The runner is small enough that owning it is cheaper than
adopting either alternative.

## Migration `0001` is `schema.sql`, verified against a dump of production

`schema.sql` moves to `migrations/0001-baseline.sql` unchanged — comments
intact — and is deleted from the repo root. Before that move, a
`pg_dump --schema-only` of the live database is diffed against it. The dump is
truth (it carries RLS enablement, any index added through the Supabase UI, any
drift); `schema.sql` is intent. A clean diff proves the two are equivalent, at
which point the commented file is strictly the better artifact to ship and the
dump's job is done. Should the diff *not* be clean, that inverts: the dump
becomes `0001` and the drift it exposed is worth explaining, because every
later migration would otherwise be written against a schema that does not
exist.

The live database is baselined by creating `schema_migrations` and inserting a
row marking `0001` applied. No DDL runs and no row is touched.

`schema.sql` does not survive at the root, as a hand-maintained file or as a
`pg_dump`-regenerated one. Two descriptions of one schema drift, and nothing
detects it; a regenerated version would in any case lose every comment that
made the original worth keeping. "What does the schema look like now" becomes a
question answered by reading the migrations — the cost of that is accepted.
Because a fresh database (`compose.yaml`'s test Postgres, a new laptop) is now
built by running every migration in order, the migration path is exercised
constantly rather than only in production.

## Forward-only: no down migrations

The runner goes forward only. A mistake is corrected by writing migration
`N+1`; catastrophe is handled by Supabase's point-in-time recovery.

Down migrations were considered and rejected as dishonest. Reversing
`ADD COLUMN` is `DROP COLUMN`, which destroys that column's contents with no
recovery — an undo button that deletes data is worse than no undo button.
Reversibility is genuine only for indexes, views and constraints, and for those
the roll-forward migration is barely longer than the down file would have been.
Meanwhile the thing that actually saves a corrupted production database is PITR,
which exists whether or not a single `.down.sql` is ever written. Supabase's own
migration tooling reached the same conclusion.

Resetting a local or test database therefore means rebuilding from `0001`, not
stepping back one.

## Merging to `main` still applies, automatically

`apply-schema.yml` keeps its `push: branches: [main]` trigger and its
`workflow_dispatch`, with the path filter moving from `schema.sql` to
`migrations/**`; the step runs the migration runner instead of `db.py`'s
schema apply. ADR 0007's conclusion — that what is on `main` is what is in the
database, with nothing for anyone to remember — is worth more than the risk it
now carries, and a manual gate reintroduces exactly the drift ADR 0007 was
written to prevent.

What changes is *why* it is safe. ADR 0007 leaned on every statement being
idempotent, so a needless re-run cost nothing. That premise is gone: migrations
are applied-once and can be destructive, and forward-only means the fix for a
bad one is another migration or PITR. **The PR review is now the only gate.** A
migration reaches production the moment it merges, so it must be read as
carefully as the code that depends on it. The `concurrency` group stays, and
matters more than before: two runs applying the same pending migration at once
is no longer a harmless double-apply.

## Consequences

`schema.sql` and `db.py`'s `apply_schema` are gone; the runner and
`migrations/` replace them, and `docs/postgres.md` and `compose.yaml`'s test
database setup follow. Every future schema change — starting with the capture
grain rework — is a dated, ordered, applied-once file rather than an edit to a
state document. The path filter change to `apply-schema.yml` must land in the
same commit as the move, or a merged migration silently fails to apply.
