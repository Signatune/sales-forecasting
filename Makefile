# The daily commands for driving compose.yaml, identical on the Windows and the
# macOS machine. What each service *is*, and why `test` and `app` are split, is
# documented in compose.yaml and not repeated here.
#
# Every recipe below is a single bare invocation --- no pipes, no `&&`, no
# redirection, no shell built-ins, no line continuations. Windows `make` runs
# recipes through `cmd.exe` rather than a POSIX shell, so anything resembling
# shell syntax would work on macOS and fail on Windows. Logic that genuinely
# needs a shell belongs inside the container, where it is Linux either way.
#
# `$(or ...)`/`$(error ...)` below are make syntax, expanded by make before
# `cmd.exe` ever sees the line, so the rule above still holds.
#
# Prerequisites are Docker and `make` itself; both are covered in the docs by
# ticket 05 of .scratch/docker-dev-environment/.

.PHONY: test test-k shell db down

test:
	docker compose run --rm test pytest

# A subset: `make test-k K=TestAgainstPostgres`. Verbose on purpose --- the
# point of a subset run is usually seeing *which* tests ran, and a silent skip
# is indistinguishable from a pass on the summary line alone. Unset `K` is an
# error rather than `-k ""`, which would quietly run everything.
test-k:
	docker compose run --rm test pytest -v -k "$(or $(K),$(error set K, e.g. make test-k K=TestAgainstPostgres))"

shell:
	docker compose run --rm test bash

# Anything that must reach the real Supabase instance:
# `make db CMD="python migrate.py"`. Unset `CMD` is an error and not a
# convenience default: bare `docker compose run --rm app` would fall through to
# the image's `CMD ["pytest"]` and run the suite in the one container holding
# DATABASE_URL, inverting the boundary compose.yaml exists to draw.
db:
	docker compose run --rm app $(or $(CMD),$(error set CMD, e.g. make db CMD="python migrate.py"))

# `docker compose run` leaves the Postgres container up between runs; this
# stops it.
down:
	docker compose down
