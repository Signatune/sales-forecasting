# 05 — Correct the docs and record ADR 0008

**What to build:** Someone following the setup docs on either machine ends up
with a working environment, and a future reader can find out why Docker became
mandatory rather than assuming it was an oversight.

**ADR 0008.** Write
`docs/adr/0008-docker-is-the-dev-and-test-environment.md`. Note that the ADR
sequence currently jumps 0007 → 0009; this fills that gap, and the PRD already
cites this filename. The decision to record is not merely "we adopted Docker" —
it is **why the no-install fallback was removed**: `pgserver` existed to
substitute for a real Postgres, it published no Windows wheel, and so the DB
integration tests silently skipped on the machine that needed them most, letting
database code ship unverified behind a green run. Record also what was
deliberately *not* done: the production cron jobs stay uncontainerized, and no
image is published.

**A new Docker doc** carrying the prerequisites — Docker, and `make` via
`winget` on Windows — and the command reference for the Makefile targets and the
two services. Explain the `test` / `app` split as the safety property it is: the
truncating process has no production connection string in its environment.

**The Postgres doc is corrected, not appended to.** Its "Running the database
integration tests" section currently promises the tests need "no Docker, no
system install" and documents `--no-ephemeral-postgres`. Both become false.
Rewrite the section rather than adding a Docker note beneath a now-wrong
paragraph. The already-set-`TEST_DATABASE_URL`-wins behavior survives and stays
documented.

**The domain glossary is unchanged.** This effort introduces no domain term and
changes no existing one; `CONTEXT.md` is a glossary and Docker is
implementation.

**Blocked by:** 01 (compose environment), 03 (the removed fallback and the
Makefile targets being documented), 04 (CI, referenced by the ADR).

**Status:** ready-for-agent

- [ ] `docs/adr/0008-docker-is-the-dev-and-test-environment.md` exists and
      records why the fallback was removed, not just that Docker was adopted
- [ ] It records the deferred decisions — uncontainerized production jobs, no
      published image — as deferred rather than closed
- [ ] A Docker doc lists Docker and `make` as prerequisites, with the `winget`
      install for Windows, and documents every Makefile target
- [ ] The Docker doc explains the `test` / `app` split as a production-access
      safety boundary
- [ ] The Postgres doc's integration-test section is rewritten; no surviving
      text promises "no Docker, no system install" or references
      `--no-ephemeral-postgres`
- [ ] No stale reference to `pgserver` or the `testdb` extra remains in any doc
- [ ] `CONTEXT.md` is unchanged
