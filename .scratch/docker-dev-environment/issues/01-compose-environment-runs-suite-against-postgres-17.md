# 01 — Compose environment that runs the suite against Postgres 17

**What to build:** A developer on either the Windows or the macOS machine can go
from a fresh clone to running the full test suite with a single compose command,
with no hand-installed Python dependencies and no local Postgres. The DB
integration tests — which have never executed anywhere — execute for the first
time.

The environment is defined by a `Dockerfile` and a `compose.yaml` at the repo
root:

- **Image:** single stage, Python pinned to **3.12** to match what the three
  production workflows pin. Installs the runtime and dev dependencies together;
  with the inspection notebook retired (ADR 0009) the dependency set is small
  enough that staged build targets would be structure without payoff.
- **Postgres service:** pinned to **17**, matching what the managed Supabase
  instance provisions. The hard floor is 15 — `schema.sql` sets
  `security_invoker` on the `product_sales` view, which earlier versions reject
  outright. The data directory is backed by `tmpfs`, no named volume: the tests
  truncate regardless, and nothing persisting between runs means nothing can
  create order-dependence. It carries a **healthcheck**.
- **`test` service:** receives `TEST_DATABASE_URL` pointing at the Postgres
  service and **no `DATABASE_URL` at all**. Depends on the Postgres healthcheck
  passing, so the suite never races a database that is not yet accepting
  connections. This feeds the tests through the existing already-set-wins
  precedence in the root `conftest.py` — no new seam is introduced.
- **`app` service:** receives the `.env` file, and is how anything reaches the
  real Supabase instance (applying the schema, a manual capture). Reaching
  production requires naming this service explicitly rather than being ambient.
- **Source is bind-mounted, not copied**, so edits are live with no rebuild.
  This works because the project opts out of flat-layout packaging — installing
  it installs dependencies only, and modules resolve from the working directory
  via the existing pytest `pythonpath` setting.

The separation between the two services is the security boundary: the
integration tests `TRUNCATE` the pipeline tables and the migrated Sales history
has no backup, so the truncating process must have no production connection
string in its environment to misuse.

**Test failures are expected on this ticket and are not a reason to hold it.**
These tests have never run; fixing what they expose is ticket 02. What this
ticket must prove is that they *ran*.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] `docker compose run test` (or equivalent) executes the suite end to end on
      the Windows machine
- [x] All four `TestAgainstPostgres` classes — in the db, migration, daily
      capture, and daily forecast test modules — are confirmed **collected and
      executed**, not skipped. A zero exit code is not sufficient evidence; the
      failure mode this whole effort exists to fix is a silent skip reporting as
      a pass, so verify against collected-test output.
- [x] The `test` service's environment contains no `DATABASE_URL`
- [x] Postgres runs 17; the `test` service waits on its healthcheck rather than
      a sleep or a retry loop
- [x] The Postgres data directory does not survive a run
- [x] Editing a source file changes what the next run executes, with no rebuild
- [x] The pass/fail/error tally of that first run is recorded in ticket 02 for
      triage, along with the true total test count (the PRD's "205" predates the
      current suite and needs re-baselining)

## Resolution (2026-07-20)

`Dockerfile` and `compose.yaml` at the repo root. Verified on the Windows
machine — each criterion was checked by running it, not by reading the config:

- **Suite ran end to end.** `docker compose run --rm test pytest -v -rs` →
  **246 collected, 236 passed, 8 skipped, 2 errors.** Tally and triage notes
  recorded in ticket 02; 246 is the re-baselined total.
- **All four classes executed.** Confirmed against verbose per-test output, not
  exit code — 37 `TestAgainstPostgres` tests ran across the four modules, 34 of
  them green on their first-ever execution. The only skip among them is
  `test_full_history_view_matches_regenerated_parquet` ("no raw history checked
  out"), a data-availability skip, not a missing-database one. The two errors
  are one root cause, left for ticket 02.
- **No `DATABASE_URL` in the `test` service.** `'DATABASE_URL' in os.environ` →
  `False`, with `TEST_DATABASE_URL` set to the Postgres service.
- **Postgres 17, gated on the healthcheck.** `show server_version` → `17.10`.
  Compose reports `Waiting` → `Healthy` before starting the test container; no
  sleep, no retry loop.
- **Data directory does not survive.** Created a marker table, `docker compose
  down`, brought it back up — the marker was gone, and the project has no named
  volumes at all. `df` confirms `/var/lib/postgresql/data` is `tmpfs`.
- **Bind mount is live.** A probe test passed, was edited on the host, and
  failed on the next run against the same image with no rebuild.

### The security boundary leaked, and the first draft of this note overclaimed

Code review caught this and it is worth recording, because the original
resolution note asserted "the service carries no `env_file`, so `.env` never
reaches it" on the strength of a check that only tested the *environment*.

The environment was clean. The **filesystem** was not. The bind mount puts the
developer's real `.env` at `/app/.env`, and `env.load_env` reads
`Path(__file__).parent / ".env"` by path, regardless of what `environment:`
declares. Reproduced against the pre-fix mount:

```
$ docker run --rm -v <repo>:/app -w /app sales-forecasting-test python envprobe.py
DATABASE_URL after load_env: 'postgresql://<redacted>@aws-1-us-east-2.pooler.supabase.com:5432/postgres'
```

A container with no `DATABASE_URL` in its environment resolved the production
connection string on demand — in the process that `TRUNCATE`s the pipeline
tables, against history with no backup. Not live breakage (every current call
site passes an explicit mapping), but the spec asked for structural
enforcement, and convention holding is not that.

Fixed by mounting `docker/no-secrets.env` read-only over `/app/.env` in the
`test` service. Verified in both directions afterwards:

- `test` service → `load_env()` yields `None`; `db.connection_string()` raises
  `RuntimeError: DATABASE_URL is not set`
- `app` service → still resolves the real Supabase pooler string

`DATABASE_URL` stays genuinely *absent* from the test environment rather than
being set empty, which is what the criterion above asks for.

Two notes for later tickets:

- The `dev` extra still pulls `pgserver` into the image via
  `sales-forecasting[testdb]`. Harmless — `conftest.py`'s already-set-wins
  precedence means compose's `TEST_DATABASE_URL` short-circuits the boot, which
  is the seam working as designed — and **ticket 03** removes it. Not pulled
  forward.
- `env_file` on the `app` service is marked `required: false`, because compose
  validates every service in the file rather than only the one named, so a
  fresh clone with no `.env` would otherwise fail to run `test`.
- The image installs `.[dev]`, **not** `.[forecast]`. An early draft included
  it; that is not what the ticket asked for, and it changed what the suite
  measures by activating seven `importorskip("statsmodels")` sites. Whether the
  image should track the daily forecast job's `[forecast]` install is a genuine
  question, deliberately left to a ticket rather than smuggled in here.
- Both files cite the PRD rather than ADR 0008, which **ticket 05 still has to
  write** — `docs/adr/` currently jumps 0007 → 0009. Those comments should be
  repointed at the ADR when it lands.
