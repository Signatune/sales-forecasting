# 03 — Remove the pgserver fallback and wrap compose in a Makefile

**What to build:** There is exactly one way to provision a test database — the
container — and the daily commands for driving it are short and identical on
both machines.

**Removing the fallback.** With a real Postgres always available, the `pgserver`
substitute has no remaining purpose. It never worked on the machine that needed
it most: the wheel publishes no Windows build, so the boot silently no-opped and
every DB integration test skipped. Remove:

- The `pgserver` dependency and the `testdb` extra it lives in, and the `dev`
  extra's reference to it
- The root `conftest.py`'s temp-directory creation, server boot, teardown hooks,
  and the `--ephemeral-postgres` / `--no-ephemeral-postgres` option — roughly
  thirty-five lines whose entire purpose was substituting for a real Postgres

**Keep the already-set-wins precedence.** A pre-set `TEST_DATABASE_URL` is used
as-is. That is the seam compose feeds the tests through, it is already
documented, and it is the one part of this file that survives. Update the
module docstring so it describes what the file now does — it currently promises
"no Docker and no system install," which becomes false.

Docker becomes a hard prerequisite for running the database tests. That is a
deliberate trade: the fallback existed only to paper over not having a real
Postgres.

**Raising the Python floor.** `requires-python` is `>=3.9`, which has never been
tested and now never will be. Raise it to `>=3.12` to match the container and
the production workflows, so the package metadata stops making a claim nothing
backs.

**The Makefile.** Recipes are kept to **single bare invocations** — no pipes, no
chaining, no shell built-ins — because Windows `make` runs recipes through
`cmd.exe` rather than a POSIX shell. Any target needing real shell logic puts
that logic inside the container instead. Cover at minimum: run the tests, run a
subset, open a shell in the test service, and run something against the real
database via the `app` service.

`make` is not present on the Windows machine; it becomes a documented
prerequisite alongside Docker (installed via `winget`), covered in ticket 05.

**Blocked by:** 01 — Compose environment that runs the suite against Postgres 17.

**Status:** ready-for-agent

- [ ] `pgserver` and the `testdb` extra no longer appear anywhere in the project
      metadata
- [ ] The root `conftest.py` no longer boots, tears down, or knows about a local
      Postgres server, and its docstring describes current behavior
- [ ] A pre-set `TEST_DATABASE_URL` is still used as-is
- [ ] `requires-python` is `>=3.12`
- [ ] With the container running, the full suite still passes and the
      integration tests still execute rather than skip
- [ ] Every Makefile recipe is a single bare invocation with no shell syntax
- [ ] The Makefile targets work unchanged on both the Windows and the macOS
      machine
