# 06 — Consolidate the duplicated DB test fixtures

**What to build:** Adding a pipeline table means editing the truncation list in
one place instead of three, and the DB-touching test modules share one
connection fixture and one row builder.

The connection fixture, the single-row `fact()` builder, and the
restaurant-GUID constants are currently triplicated across the DB-touching test
modules. The truncation list is the sharpest edge: it must be edited in every
copy whenever a pipeline table is added, and missing one produces a test that
passes against stale rows.

This was deferred once already because it could not be verified on a machine
with no Postgres — a blocker ticket 01 removes. The PRD keeps it out of the
Docker effort's scope deliberately, so that an infrastructure change and a test
refactor do not land in one diff.

**This is a refactor: behavior does not change.** The tests assert the same
things before and after, and the suite is green on both sides of it. The
integration patterns themselves are not being redesigned — a fixture opening a
connection to `TEST_DATABASE_URL`, applying the schema, truncating the pipeline
tables, and yielding — only deduplicated.

**Blocked by:** 02 — Fix what the first integration run exposes. The suite must
be genuinely green and genuinely executing before it can be trusted as the
safety net for a refactor of its own fixtures.

**Status:** ready-for-agent

- [ ] The connection fixture is defined once and shared
- [ ] The single-row builder and the restaurant-GUID constants are defined once
      and shared
- [ ] The truncation list exists in exactly one place, and adding a pipeline
      table requires editing only that one
- [ ] No test's assertions change
- [ ] The full suite passes inside the container, integration tests executed
      rather than skipped
