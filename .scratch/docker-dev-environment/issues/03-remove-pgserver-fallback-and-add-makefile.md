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

**Status:** done (macOS verification of the Makefile still outstanding — see
Resolution)

- [x] `pgserver` and the `testdb` extra no longer appear anywhere in the project
      metadata
- [x] The root `conftest.py` no longer boots, tears down, or knows about a local
      Postgres server, and its docstring describes current behavior
- [x] A pre-set `TEST_DATABASE_URL` is still used as-is
- [x] `requires-python` is `>=3.12`
- [x] With the container running, the full suite still passes and the
      integration tests still execute rather than skip
- [x] Every Makefile recipe is a single bare invocation with no shell syntax
- [ ] The Makefile targets work unchanged on both the Windows and the macOS
      machine — **Windows verified, macOS not** (see below); left unticked
      because only half the criterion was met

## Resolution (2026-07-20)

`conftest.py` is now a docstring and nothing else.

The ticket asked to "keep the already-set-wins precedence," calling it "the one
part of this file that survives," and that precedence *was* real code here —
`if os.environ.get("TEST_DATABASE_URL"): return`, guarding the boot. What the
removal makes clear is that the guard existed only to protect the boot: with
nothing left to boot, a pre-set value is used as-is trivially, because nothing
anywhere in the repo provisions or overwrites one. The precedence now lives, as
it always really did, in the five suites' module-level
`TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")` gating a `skipif`.
So the criterion holds behaviourally, but not by anything this file does; the
file survives as the discoverable place the contract is written down, which
`compose.yaml` already points at by name.

The Makefile covers the four required commands plus `down`, because
`docker compose run` leaves the Postgres container up between runs and there
was otherwise no short way to stop it.

Unset `K` and unset `CMD` are hard errors rather than defaults. `make db` with
no `CMD` would otherwise fall through to the image's `CMD ["pytest"]` and run
the suite inside the one container holding `DATABASE_URL` — inverting the exact
boundary the `test`/`app` split exists to draw. The guards use `$(or ...)` /
`$(error ...)`, which make expands before `cmd.exe` sees the line, so the
single-bare-invocation rule still holds; both were executed on Windows to
confirm it.

## Verification (2026-07-20)

Before and after are identical, measured in the container:

- Full suite: **238 passed, 8 skipped** (246 collected) both before and after
- `-k AgainstPostgres`, read per-test rather than by exit code: **36 passed, 1
  skipped** both before and after — the one skip is the data-availability skip,
  not a silent no-op
- `pip show pgserver` in the rebuilt image returns nothing
- `pytest --no-ephemeral-postgres` now errors on an unrecognized argument

The database-less path the new docstring promises was checked directly: with
`TEST_DATABASE_URL` blanked, **200 passed, 46 skipped, 0 failed**. The 38 tests
that move from passed to skipped are the 39 DB-gated tests minus the one
already skipping, which reconciles with the totals above. Two of those 39 sit
outside the four `TestAgainstPostgres` classes, in `test_sales_history.py`.

## Notes for later tickets (2026-07-20)

- **macOS is unverified**, which is why the last criterion is unticked. Every
  target was executed on Windows through
  `cmd.exe` — including `test-k`'s quoted `-k "$(K)"` and `db`'s `$(CMD)`
  expansion — which is the platform the single-bare-invocation rule exists to
  protect, so the risk is on the low side. But the criterion says both machines
  and only one was available here.
- `make` was **installed on the Windows machine** during this ticket, via
  `winget install --id ezwinports.make` (GNU Make 4.4.1). Ticket 05 should
  document that id specifically: winget's other hit, `GnuWin32.Make`, is 3.81
  from 2006. Note also that `winget`'s `msstore` source fails a certificate
  check on this machine — `--source winget` is needed.
- `docs/postgres.md` still promises "no Docker and no system install" and still
  documents `--no-ephemeral-postgres`. Both are now false. Deliberately left to
  **ticket 05**, which already owns them; it is the only stale reference in
  live docs or metadata. Older tickets under `.scratch/daily-forecast-log/`
  also mention `pgserver`, but as a record of what was true when they were
  written, so they are correct as they stand.
- Raising `requires-python` to `>=3.12` is metadata only — nothing was
  installing this package on 3.9, and the container was already 3.12. It closes
  a claim nothing backed rather than changing behavior.
