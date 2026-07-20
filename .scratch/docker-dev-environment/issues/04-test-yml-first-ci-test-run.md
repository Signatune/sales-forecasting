# 04 — `test.yml`, the project's first CI test run

**What to build:** Every push runs the full test suite — integration tests
included — in an environment byte-identical to the developer's. A regression is
caught by CI rather than by the next person to run pytest.

This is genuinely the project's **first CI test run**. The three existing
workflows are production cron jobs; despite a passing comment in the forecast
workflow mentioning pytest, none of them run the suite.

**CI drives the same compose file, not a lookalike.** The workflow invokes the
suite through the very `compose.yaml` the developer uses. The obvious
alternative — GitHub's native Postgres service block — would declare the
version, the credentials, and the healthcheck a second time in a second syntax,
which is precisely the drift this effort exists to remove. The Postgres version,
credentials, and service wiring stay declared in exactly one place.

**The image is built in-workflow with layer caching; nothing is published.** A
registry only earns its keep when something needs to pull the image, and with
the production jobs deferred, nothing does.

**The three production cron jobs are left alone.** `daily-capture`,
`daily-forecast`, and `apply-schema` keep their current `pip install -e .` step
on `ubuntu-latest`. Containerizing them is a deployment change with live-data
blast radius and no present benefit; it is explicitly deferred, not forgotten.

**Blocked by:** 01 (compose environment), 02 (green suite), 03 (fallback removed,
so CI cannot install a dead extra).

**Status:** ready-for-agent

- [ ] `test.yml` runs on every push and executes the full suite
- [ ] It invokes the suite through the existing `compose.yaml` — no second
      declaration of the Postgres version, credentials, or healthcheck anywhere
      in the workflow
- [ ] The `TestAgainstPostgres` classes are confirmed executed in CI, not
      skipped — verified against CI's collected-test output, not just a zero
      exit code
- [ ] The image is built in-workflow with layer caching and pushed to no registry
- [ ] The CI test environment matches the local one; there is nothing a
      developer can run locally that CI provisions differently
- [ ] `daily-capture.yml`, `daily-forecast.yml`, and `apply-schema.yml` are
      untouched
