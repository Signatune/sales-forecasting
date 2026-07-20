# The one place the project's Python dependencies live, so moving between the
# Windows and macOS machines needs no reinstall. The reasoning is recorded in
# .scratch/docker-dev-environment/PRD.md; ticket 05 of that effort promotes it
# to ADR 0008, which is not written yet.
#
# Single stage: with the inspection notebook retired (ADR 0009) the dependency
# set is small enough that staged build targets would be structure without
# payoff, so the runtime and dev dependencies install together.
#
# Python is pinned to 3.12 to match what the three production workflows pin.
# The container follows production, not whatever interpreter a laptop happens
# to have --- otherwise this image would create the very drift it exists to
# remove.
FROM python:3.12-slim

WORKDIR /app

# Only the packaging metadata is copied, so a source edit doesn't invalidate
# the dependency layer. The source itself is bind-mounted at run time (see
# compose.yaml) rather than copied, so edits are live with no rebuild.
COPY pyproject.toml ./

# `py-modules = []` in pyproject.toml opts the project out of flat-layout
# packaging, so installing it installs dependencies only --- no source is
# needed at build time and none is shipped in the image. At run time the
# modules resolve from the bind-mounted working directory via pytest's
# `pythonpath = ["."]`.
#
# `dev` only --- deliberately not `[dev,forecast]`. statsmodels is by a wide
# margin the largest thing that could land here, and pulling it in would both
# undercut the single-stage justification above and change what the suite
# measures: seven `importorskip("statsmodels")` sites would start executing, so
# the test total would no longer be the default-install total. Whether the
# image should track the daily forecast job's `[forecast]` install is a real
# question, but it belongs to a ticket, not to a silent flag here.
RUN pip install --no-cache-dir -e ".[dev]"

CMD ["pytest"]
