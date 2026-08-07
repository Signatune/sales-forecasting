# Stand up migrations and baseline the live schema

Type: task
Status: resolved
Blocked by: 01

## Question

Nothing to decide — 01 decided it. Do the work, for real:

- Install and configure the chosen tool.
- Create the baseline migration and mark it applied against the live database
  without altering existing data.
- Rewire CI: replace or amend the `apply-schema.yml` workflow per 01's answer,
  keeping the same `DATABASE_URL` secret.
- Write the ADR that 01 settled, superseding/amending ADR 0007.
- Prove a round trip on a throwaway migration: apply, roll back, re-apply.
- Document the contributor workflow (how to write a migration, how to apply
  locally) — `docs/` alongside `postgres.md`.

Resolved when a new migration can be authored, applied to the shared DB via the
normal path, and rolled back. Record in the answer: the tool, where migrations
live, the command to create/apply/roll back, and anything a later ticket needs
to know.

## Decided by ticket 01

The shape is settled: see [01's answer](01-choose-migration-tooling-and-baseline-strategy.md#answer)
and [ADR 0015](../../../docs/adr/0015-schema-changes-are-forward-only-numbered-sql-migrations.md).
Concretely, this ticket must:

- Write the runner in `db.py` (`schema_migrations` table, ordered
  `migrations/NNNN-<slug>.sql`, each file in its own transaction, forward-only —
  no `down` command), replacing `apply_schema`.
- `pg_dump --schema-only` production and diff against `schema.sql`. Clean diff →
  ship `schema.sql` as `migrations/0001-baseline.sql` with comments intact. Dirty
  diff → the dump becomes `0001` and the drift gets written up.
- Delete `schema.sql` from the repo root.
- On the live DB: create `schema_migrations`, insert the row marking `0001`
  applied. No DDL, no row touched.
- Change `apply-schema.yml`'s path filter to `migrations/**` and its step to the
  runner — **in the same commit as the move**, or a merged migration silently
  fails to apply. Keep the `concurrency` group.
- Follow through in `docs/postgres.md` and `compose.yaml`'s test-database setup
  (a fresh DB is now built by running all migrations in order).

## Answer

Built as ticket 01 and [ADR 0015](../../../docs/adr/0015-schema-changes-are-forward-only-numbered-sql-migrations.md)
specified. The ADR was already written when 01 resolved, so this ticket wrote no
new ADR — it executed the one that existed.

**The runner lives in `db.py`.** `migration_files` reads `migrations/` in
numeric-prefix order, `applied_versions` reads the ledger (creating it on
demand), `pending_migrations` is the difference, `apply_migrations` applies it,
`baseline` records a version without running it. `apply_schema` and
`SCHEMA_PATH` are gone.

**Commands** — the thing later tickets will type:

```
python db.py                  # same as: python db.py migrate
python db.py status           # applied vs pending; changes nothing
python db.py baseline NNNN    # record as applied without running (one-time)
```

There is no `down`. Forward-only.

**Where migrations live and what they are named.** `migrations/NNNN-<slug>.sql`,
enforced by a regex — a `.sql` file the runner cannot parse *raises* rather than
being skipped, because a migration that looks committed and never runs is the
failure mode worth paying for. `schema_migrations` is
`(version text PK, filename text, applied_at timestamptz)`; the four-digit
version is the identity, so renaming a slug does not re-apply anything. The
ledger has RLS enabled with no policies, matching every other table in `public`.

**The dump-and-diff came back clean, and it was verified mechanically rather
than by eye.** `pg_dump --schema-only --schema=public` of production was diffed
against the same dump of `schema.sql` applied to an empty Postgres 17: **byte-
identical** once the per-dump `\restrict` token and version header were
stripped. No drift, no UI-added index, no RLS policy anywhere. So `schema.sql`
shipped as `migrations/0001-baseline.sql` with its comments intact (`git mv`, so
history follows) and is deleted from the root. A header block was added to
`0001` explaining what it is and warning that later migrations must **not** copy
its `IF NOT EXISTS` / `CREATE OR REPLACE` style — that style is a fossil.

A database built by running migrations from scratch was then dumped and diffed
against production too: identical. The migration path reproduces production.

**The live database is baselined.** `python db.py baseline 0001` ran against the
Supabase instance on **2026-08-07**. Verified after the fact:

- Schema dump before vs. after (excluding the new ledger): **identical**.
- Row counts before vs. after, all unchanged — `sales` 574,691,
  `raw_toast_responses` 5,589, `forecasts` 1,064, `products` 7,
  `product_sources` 18, `forecast_configs` 1, `report_configs` 1.
- `python db.py status` on production: `1 applied, 0 pending`.

**Round trip, proven on a throwaway Postgres 17, not asserted.** All of it ran:

- Fresh empty DB → `migrate` builds the whole schema; a second `migrate` is a
  no-op ("nothing to apply").
- A second migration creating a table applies and is recorded.
- **A migration whose first statement succeeds and second fails leaves nothing
  behind** — the created table is gone and the version is unrecorded. This is
  the per-file transaction actually working, and it is the only rollback there
  is.
- All four guards fire with usable messages: duplicate version, unparseable
  filename, applied-version-with-no-file, and out-of-order merge.

This surfaced one real bug, which is why the round trip was worth running:
`apply_migrations` set `autocommit` *after* reading the ledger, and the ledger's
bootstrap DDL had already opened an implicit transaction psycopg refuses to
switch out of. Migrating any database failed outright. Fixed and covered.

**CI is rewired in the same working tree as the move**, as 01 insisted.
`apply-schema.yml` keeps its filename, its `push`/`workflow_dispatch` triggers,
its `DATABASE_URL` secret and its `apply-schema` concurrency group; the path
filter is now `migrations/**` and the step runs `python db.py migrate`. A
`status` step runs with `if: always()`, so a failed apply still says which files
made it in. **These must be one commit** — a merged migration silently fails to
apply otherwise.

**Follow-through.** Five test fixtures now build the throwaway database with
`db.apply_migrations`; `test_apply_schema_is_idempotent` became
`test_migrating_an_up_to_date_database_does_nothing`; 11 new tests cover the
runner (`TestMigrationFiles` needs no database, `TestMigrationRunner` does).
`compose.yaml`, `docs/postgres.md` and `docs/reports.md` follow. **376 passed,
5 skipped** against a Postgres 17 built from migrations.

`docs/postgres.md` gained "Write a migration", "There is no rollback" and
"Baseline an existing database" sections.

### What later tickets need to know

- **Add a migration**: next number up, plain ordered DDL, open a PR. Merging to
  `main` applies it to production immediately — **PR review is the only gate**.
- **`CREATE INDEX CONCURRENTLY` and `VACUUM` cannot go in a migration.** Every
  file runs inside a transaction, and Postgres refuses those there. Ticket 05's
  capture-grain work and ticket 07's backfill are the likely places this bites:
  a concurrent index on a large new table has to be run by hand.
- **Ticket 05 writes `0002`.** Nothing is pending, so it starts from a clean
  ledger on both production and the test database.
- **Name collision to keep straight**: `python db.py migrate` applies schema
  migrations; `python migrate.py` is the unrelated one-time 2026 history load.
  Noted in `docs/postgres.md`; worth renaming eventually, out of scope here.
- **Tooling on the dev machine.** Docker is not installed on this laptop, so
  `make test` could not run; `libpq` and `postgresql@17` were installed via
  Homebrew and the suite ran against a local throwaway cluster instead, which
  `conftest.py` explicitly supports. `pg_dump` (18.4) is needed only for the
  baseline verification, never by the runner — no contributor or CI runner needs
  anything beyond `pip install -e .`, exactly as ADR 0015 promised.
- **Unrelated pre-existing condition**: the repo's `.venv` is Python 3.9 while
  `pyproject.toml` requires >=3.12. The suite passes on both; CI uses 3.12.
