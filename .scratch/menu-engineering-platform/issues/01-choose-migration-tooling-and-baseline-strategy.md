# Choose the migration tooling and the baseline strategy

Type: grilling
Status: resolved

## Question

What tool applies dated, version-controlled, roll-forward/roll-back schema
migrations to this repo's Postgres — and how does an already-live schema become
migration zero?

Three sub-questions, all of which have to be answered together:

1. **Which tool.** The repo is Python with raw `psycopg` and no ORM
   (`db.py`), so Alembic's main draw — autogenerate from models — does not
   apply; it would be used purely as a migration runner. Alternatives: the
   Supabase CLI's own migration system (the DB is Supabase-hosted), or plain
   numbered `.sql` files with a small runner and a `schema_migrations` table.
   Weigh: rollback support, what a migration is written *in*, and what a
   contributor has to install.

2. **Baselining.** `schema.sql` is already applied to a live shared database
   with real data. Migration tooling needs a defined starting point. Does
   `schema.sql` become migration `0001` marked as already-applied, or does the
   tool introspect, or is the DB reset? Whatever is chosen must not touch the
   existing rows.

3. **What happens to ADR 0007.** That ADR has a GitHub Action apply `schema.sql`
   on push to `main`, and leans on every statement being idempotent
   (`IF NOT EXISTS` / `CREATE OR REPLACE`). Migrations are not idempotent in
   that way — they are applied-once and tracked. Decide whether ADR 0007 is
   superseded outright, and what the CI apply step becomes. Also decide whether
   `schema.sql` survives at all as a generated artifact or is deleted.

Rollback deserves explicit attention: the user asked for it by name, but a
down-migration that drops a column destroys data. Decide what rollback actually
means here and where its limits are.

Leave an ADR behind. It should supersede or amend ADR 0007 explicitly.

## Answer

**ADR:** [0015 — Schema changes are forward-only numbered .sql migrations, applied by a runner in db.py](../../../docs/adr/0015-schema-changes-are-forward-only-numbered-sql-migrations.md)

All three sub-questions, answered together:

**1. Tool — plain numbered `.sql` files with a runner in `db.py`.** Migrations
are `migrations/NNNN-<slug>.sql` applied in order against a `schema_migrations`
table, each inside its own transaction. Alembic was rejected because its
headline feature (autogenerate from SQLAlchemy models) is dead weight in a repo
with no ORM, leaving a heavy dependency used purely as a runner and migrations
written as Python wrapping SQL strings. The Supabase CLI was rejected because it
needs Docker + the `supabase` binary + a linked project on every machine and all
three GitHub Actions runners, which today need nothing beyond `pip install -e .`.
Plain SQL keeps migrations in the language the schema already speaks and adds no
install for anyone.

**2. Baseline — `schema.sql` becomes `migrations/0001-baseline.sql`, verified by
a dump.** `pg_dump --schema-only` of production is diffed against `schema.sql`
first: the dump is truth (RLS, UI-added indexes, drift), the file is intent. Mike
expects them to match; on a clean diff the commented file ships as `0001` and the
dump's job is done. On a dirty diff that inverts — the dump becomes `0001` and
the drift gets explained, because otherwise every later migration is written
against a schema that doesn't exist. The live DB is baselined by creating
`schema_migrations` and inserting a row marking `0001` applied: no DDL, no row
touched.

`schema.sql` is **deleted** from the repo root — not kept hand-maintained (the
drift trap this ticket exists to escape) and not kept as a `pg_dump`-regenerated
artifact (which would lose every comment that made it valuable). A fresh database
is now built by running all migrations in order, so the migration path is
exercised constantly instead of only in production.

**3. ADR 0007 — its mechanism is superseded, its conclusion kept.** Auto-apply on
merge to `main` stays; the path filter moves from `schema.sql` to `migrations/**`
and the step runs the runner. What changes is why it's safe: ADR 0007 leaned on
every statement being idempotent, and that premise is gone. **The PR review is
now the only gate** — a migration reaches production the moment it merges. The
`concurrency` group matters more than before, since a double-apply is no longer
harmless. A manual `workflow_dispatch`-only gate was considered and rejected as
reintroducing exactly the drift ADR 0007 was written to prevent.

**Rollback — forward-only, no down files.** Three things get called rollback and
only one is the runner's job. Per-migration transactions give the failure-safety
that matters day to day (Postgres has transactional DDL). Down migrations were
rejected as dishonest: reversing `ADD COLUMN` is `DROP COLUMN`, an undo button
that destroys data. Production recovery is roll-forward plus Supabase PITR, which
exists whether or not a `.down.sql` is ever written. Resetting a local/test DB
means rebuilding from `0001`, not stepping back one.

**Handed to ticket 02**, which executes this for real: write the runner, run the
dump-and-diff, move `schema.sql`, create `schema_migrations` and stamp `0001` on
the live DB, and change `apply-schema.yml`'s path filter **in the same commit as
the move** — otherwise a merged migration silently fails to apply. `docs/postgres.md`
and `compose.yaml`'s test-database setup follow.
