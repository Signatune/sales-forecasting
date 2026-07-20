# Docker as the Development and Test Environment

Status: ready-for-agent

## Problem Statement

The developer works across two machines — a Windows laptop and a macOS one — and
every move between them means reinstalling the project's Python dependencies by
hand. That is already tedious and gets worse with each dependency added.

Underneath that annoyance sits a more serious problem the same fix resolves. The
DB integration tests — the `TestAgainstPostgres` suites covering the Sales
pipeline's readers, writers, and the one-time history migration — **have never
executed anywhere**. They are gated on `TEST_DATABASE_URL`, which is populated by
booting a throwaway local Postgres from the `pgserver` wheel. That wheel has no
Windows build, so on the Windows machine the boot silently no-ops and every one
of those tests skips. No CI workflow runs the suite either: all three existing
workflows are production cron jobs, not test runs. The result is that a
meaningful block of database code has shipped entirely unverified, and the gap is
invisible because a skipped test reports as a pass.

There is a third, smaller symptom of the same cause: the machine's environment
also lacks the notebook dependencies, so the inspection notebook cannot run
either.

## Solution

A container becomes the one place the project's dependencies live, and a
version-pinned Postgres container becomes the test database.

`docker compose` defines the environment. A **`test` service** runs the suite
against a **Postgres 17** service — matching what the managed Supabase instance
runs — and is the only way the DB integration tests are run. A separate **`app`
service** exists for commands that must reach the real database (applying the
schema, a manual capture). A **`Makefile`** wraps the compose invocations so the
daily commands stay short.

The same compose file drives a **new `test.yml` GitHub Actions workflow**, so the
suite runs on every push in a byte-identical environment to the developer's. This
is the first time the project's tests run in CI at all.

Because a real Postgres is now always available, the `pgserver` fallback and its
`testdb` extra are removed. Docker becomes a hard prerequisite for running the
database tests — a deliberate trade, made because the fallback's only purpose was
to paper over not having a real Postgres, and it never worked on the machine that
needed it most.

The three production cron jobs are **deliberately left alone**. They work, they
write live data, and containerizing them would be a deployment change with real
blast radius and no current benefit.

See `docs/adr/0008-docker-is-the-dev-and-test-environment.md`.

## User Stories

1. As a developer, I want the project's dependencies defined once in an image, so
   that moving between my Windows and macOS machines requires no reinstall.
2. As a developer, I want adding a dependency to be a one-line change that both
   machines pick up automatically, so that dependency growth stops compounding
   the setup cost.
3. As a developer, I want a single documented command to get from a fresh clone to
   a working environment, so that onboarding a new machine is not an exercise in
   recall.
4. As a developer, I want the DB integration tests to actually execute on my
   Windows machine, so that database code stops shipping unverified.
5. As a developer, I want a skipped test to be a deliberate choice rather than an
   accident of my operating system, so that a green run means something.
6. As a developer, I want the test database's Postgres version pinned to match the
   managed instance, so that a query cannot pass locally and fail in production.
7. As a developer, I want the container's Python version pinned to match the
   production workflows, so that the environment my tests run in is the
   environment my live jobs run in.
8. As a maintainer, I want the test suite to run automatically on every push, so
   that a regression is caught by CI rather than by the next person to run pytest.
9. As a maintainer, I want CI to drive the same compose file I use locally, so
   that the test environment is defined exactly once and cannot drift.
10. As a maintainer, I want the Postgres version, credentials, and service wiring
    declared in one place, so that changing them is a single edit.
11. As a developer, I want the test process to have no access whatsoever to the
    production connection string, so that a truncating test cannot reach live
    Sales history under any circumstance.
12. As a developer, I want reaching the real database to require naming a
    different service explicitly, so that production access is opt-in rather than
    ambient.
13. As a developer, I want the test database to be discarded after every run, so
    that no state persists to create order-dependent tests.
14. As a developer, I want the test service to wait for Postgres to be genuinely
    ready, so that the suite does not flake on a connection race in CI.
15. As a developer, I want to edit source and re-run tests without rebuilding the
    image, so that the change-test loop stays fast.
16. As a developer, I want short commands for the operations I run constantly, so
    that the container does not add friction to every invocation.
17. As a developer, I want those commands to be identical on Windows and macOS, so
    that muscle memory transfers between machines.
18. As a maintainer, I want one mechanism for provisioning a test database rather
    than two, so that there is no second path that can be subtly misconfigured.
19. As a maintainer, I want the `conftest.py` server-lifecycle code removed once a
    real Postgres is always present, so that the test harness stops carrying
    machinery it no longer needs.
20. As a maintainer, I want the existing "`TEST_DATABASE_URL` already set wins"
    precedence preserved, so that compose feeds the tests through a contract that
    already exists and is already documented.
21. As a maintainer, I want the declared minimum Python version to reflect what is
    actually tested, so that the package metadata stops making a claim nothing
    backs.
22. As a maintainer, I want the production capture, forecast, and schema-apply
    jobs untouched, so that this change cannot break the pipeline that writes real
    data.
23. As a maintainer, I want a runtime-capable image to exist even though prod is
    not containerized yet, so that revisiting that decision later is cheap.
24. As a developer, I want the documented setup prerequisites to be accurate for
    both my machines, so that following the docs on either one actually works.
25. As a developer, I want the docs to stop promising that no Docker is needed, so
    that they do not contradict the tooling.
26. As a maintainer, I want the reasoning behind requiring Docker recorded, so that
    a future reader understands why the no-install fallback was removed rather
    than assuming it was an oversight.
27. As a developer, I want the first-ever run of the integration tests to be part
    of this work, so that "the tests can run" is proven rather than asserted.
28. As a maintainer, I want any failures that first run exposes reported honestly
    rather than worked around, so that the backlog of unverified work becomes
    visible.

## Implementation Decisions

- **Primary seam — the existing `TEST_DATABASE_URL` contract.** No new seam is
  introduced. `conftest.py` already specifies that a pre-set `TEST_DATABASE_URL`
  is used as-is; compose sets it to the Postgres service, and every existing
  `TestAgainstPostgres` suite hangs off it unchanged. This is the highest
  available seam and the only one this effort touches.
- **Scope is dev and test only.** The `daily-capture`, `daily-forecast`, and
  `apply-schema` workflows continue to run `pip install -e .` on `ubuntu-latest`.
  Containerizing them is a deployment decision with live-data blast radius and no
  present benefit; it is explicitly deferred.
- **A single image, one stage.** With the notebook dependencies retired (see the
  companion effort), the remaining dependency set is small enough that staged
  build targets would be structure without payoff. The image installs the runtime
  and dev dependencies together.
- **Python is pinned to 3.12**, matching what the production workflows pin. The
  container follows production, not the developer's local interpreter — otherwise
  this effort would create the very drift it exists to remove. `requires-python`
  is raised from `>=3.9`, which has never been tested and now never will be.
- **Postgres is pinned to 17**, matching what Supabase provisions. The hard floor
  is 15: `schema.sql` sets `security_invoker` on the `product_sales` view, which
  earlier versions reject outright. Pinning above the managed instance would be
  the same drift in the opposite direction, so this version must be re-checked
  against the real instance if it is ever unclear.
- **Two services, split on database access.** The `test` service receives
  `TEST_DATABASE_URL` pointing at the Postgres container and **no `DATABASE_URL`
  at all**. The `app` service receives the `.env` file and is how anything reaches
  the real Supabase instance. Since the integration tests `TRUNCATE` the pipeline
  tables and the migrated Sales history has no backup, the separation is enforced
  structurally: the truncating process has no production connection string in its
  environment to misuse.
- **The test database is ephemeral.** No named volume; the data directory is
  backed by `tmpfs`. Tests truncate regardless, a throwaway database is the stated
  intent, and nothing persisting between runs means nothing can create
  order-dependence.
- **Postgres carries a healthcheck** and the `test` service depends on it passing,
  so the suite never races a database that is not yet accepting connections — a
  routine source of CI flakes.
- **Source is bind-mounted, not copied.** Edits are live with no rebuild. This is
  clean here because the project opts out of flat-layout packaging, so installing
  it installs dependencies only; modules resolve from the working directory via
  the existing pytest `pythonpath` setting.
- **`pgserver` and the `testdb` extra are removed.** `conftest.py` loses its
  temp-directory creation, server boot, and teardown hooks — roughly thirty-five
  lines whose entire purpose was substituting for a real Postgres. The
  already-set-wins precedence stays, because that is the seam compose uses.
- **CI runs the compose file, not a lookalike.** The new `test.yml` invokes the
  suite through the same `compose.yaml` the developer uses. The alternative —
  GitHub's native Postgres service block — would declare the version, credentials,
  and health-check a second time in a second syntax, which is precisely the drift
  this effort removes.
- **The image is built in-workflow with layer caching; nothing is published.** A
  registry only earns its keep when something needs to pull the image, and with
  the production jobs deferred, nothing does.
- **A `Makefile` wraps the compose commands.** Recipes are kept to single bare
  invocations — no pipes, no chaining, no shell built-ins — because Windows `make`
  runs recipes through `cmd.exe` rather than a POSIX shell. Any target needing
  real shell logic puts that logic inside the container instead. `make` is not
  present on the Windows machine and becomes a documented prerequisite alongside
  Docker, installed via `winget`.
- **Documentation is corrected, not appended to.** The Postgres doc currently
  promises that the integration tests need "no Docker, no system install"; that
  promise becomes false and is rewritten. A new Docker doc carries the
  prerequisites and the command reference.
- **The domain glossary is unchanged.** This effort introduces no domain term and
  changes no existing one; `CONTEXT.md` is a glossary and Docker is implementation.

## Testing Decisions

- **Test external behavior, not implementation.** Unchanged from the existing
  suites — synthetic frames in, assertions on returned values worked by hand.
  Nothing in this effort changes how tests are written.
- **No tests are written for the container itself.** There is no application code
  here to unit-test, and asserting on Dockerfile or compose contents would test
  implementation rather than behavior. The container's correctness is demonstrated
  by the suite it makes runnable.
- **The acceptance criterion is the existing suite, green, in both places.** All
  205 tests must pass inside the container and in `test.yml`. Roughly fifty-five
  of those live in the DB-touching files and include integration tests that have
  never executed; their first passing run is the deliverable.
- **Expect real failures on that first run.** Because these tests have never
  executed, failures may be in the tests or in the database module they exercise.
  Fixing what the first run exposes is in scope for this effort. Failures are to
  be reported plainly, not routed around by loosening assertions or re-skipping.
- **Verify the tests genuinely ran rather than skipped.** The failure mode this
  effort exists to fix is a silent skip reporting as a pass, so the run must be
  confirmed to have collected and executed the `TestAgainstPostgres` classes, not
  merely exited zero.
- **Prior art.** The integration patterns already exist in the DB and migration
  test modules — a fixture opening a connection to `TEST_DATABASE_URL`, applying
  the schema, truncating the pipeline tables, and yielding. Those patterns are
  used as-is; this effort changes what provisions the database, not how the tests
  are shaped.

## Out of Scope

- **Containerizing the production cron jobs.** The capture, forecast, and
  schema-apply workflows keep their current install step. Revisiting this is
  cheap once the image has proven itself, and the decision is recorded rather
  than closed.
- **Publishing the image to a registry.** Follows from the above; nothing needs to
  pull it.
- **Consolidating the duplicated test fixtures.** The connection fixture, the
  single-row builder, and the restaurant-GUID constants are triplicated across the
  three DB-touching test modules, and the truncation list must be edited in three
  places whenever a pipeline table is added. This was deferred because it could
  not be verified on a machine with no Postgres — a blocker this effort removes.
  It is a **follow-up ticket**, kept separate so an infrastructure change and a
  test refactor do not land in one diff.
- **A devcontainer.** Commands enter the container from the host; living inside it
  via an editor integration was considered and set aside as editor-specific.
- **Retiring the inspection notebook.** Tracked as its own effort, though the two
  land together and the notebook's removal is what keeps this image small.
- **Any change to the pipeline's behavior.** No forecasting, capture, schema, or
  Sales-model logic changes. If the first integration run reveals a genuine
  pipeline bug, it is reported and triaged separately rather than fixed inline.

## Further Notes

- The three existing workflows are production cron jobs; despite a passing comment
  in the forecast workflow mentioning pytest, **none of them run the test suite.**
  `test.yml` is genuinely the project's first CI test run.
- The `security_invoker` setting on the `product_sales` view is the sharpest
  version constraint in the schema and the reason the Postgres floor is 15 rather
  than something older. It exists so the base tables' row-level security still
  closes the Data API off from the view.
- Row-level security is enabled with no policies across the pipeline tables. The
  container's default superuser bypasses it exactly as the production role does,
  so the security posture does not change what the tests see.
- Bind-mount performance is a non-issue here despite being a common macOS
  complaint: the mounted tree is source only, and the large dependency tree lives
  in the image rather than the mount.
- This effort makes previously-invisible risk visible. A larger-than-expected set
  of failures on the first integration run is a sign it is working, not a sign it
  went wrong.
- Respects ADR 0003 (Postgres as the source of truth), ADR 0005 (the
  source-to-Product model the fixtures build against), and ADR 0007 (schema
  application on push, which is unaffected).
