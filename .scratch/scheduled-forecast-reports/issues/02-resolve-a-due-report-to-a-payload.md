# Resolve a due report into a render-ready payload, or a refusal

Status: done
Blocked by: 01

## Resolution note

Built as planned in `report_payload.py`, pure, with `Payload`/`Refusal`
dataclasses and every rule tested against in-memory frames.

Three decisions the ticket left open.

**`logged` is not pre-filtered to today.** The refusal has to name the origin it
actually *found*, and rows filtered to today cannot tell a late run from an
empty log. `db.read_latest_forecasts` supplies the newest origin's rows and the
builder checks that origin against `today` itself.

**A Weekday Baseline of zero is "no baseline".** Four closed same-weekdays
average to nothing, and dividing by it manufactures exactly the spike the "no
baseline" rule exists to prevent. The four candidates are calendar dates — the
newest settled same weekday, then a week earlier, and so on — not the four
newest *observations*, so a closed day shortens the mean rather than reaching a
fifth week back.

**The window's direction compares only the days that have a baseline.** Summing
seven forecasts against four baselines would read as a collapse that is really a
missing weekday. Ticket 03's page says over how many days the total was taken
whenever the two differ.

`Payload.headline_is_the_named_model` was added after review: `headline` is
`pages[0]`, which is a *substitute* when the named headline model failed to
cover the window, and the card was quoting it without saying so.

## Parent

`.scratch/scheduled-forecast-reports/PRD.md`

## What to build

A new module `report_payload.py`, holding the whole of this feature's decision
logic as **pure functions** — no database, no clock, no filesystem — for the same
reason `forecast_engine.run_forecasts` is pure (ADR 0006): every rule below is
then testable without a Postgres or a Saturday.

```
build_payload(report, forecast_config, logged, sales, today)
    -> Payload | Refusal
```

- `report` — one row from `db.read_due_reports`
- `forecast_config` — the referenced `forecast_configs` document, for
  `horizon_days`, `models` and `is_active`
- `logged` — the `forecasts` rows for that config version at the chosen origin
- `sales` — Sales history, as `sales_history.load_sales_history` returns it

### The rules it enforces

**Refuse rather than render** (ADR 0010). Two failures must produce two distinct
messages, because they need different fixes:

- the referenced config version is no longer active → the report points at a
  superseded configuration; name the version that replaced it
- it is active but the newest origin under it is before `today` → today's
  forecast run did not complete; say so and name the re-run

**The Report Window** is `today + 1 .. today + horizon_days`. It is computed, not
configured — nothing stores a week shape (ADR 0010).

**Model eligibility.** A model earns a page only if it has a logged row for every
date in the window, for the headline target *and* all three varieties — a missing
variety day leaves a hole in the split. Ineligible models are named in the
payload so the surviving pages can say which were omitted and why. No eligible
model at all is a refusal.

**The Weekday Baseline.** For each date in the window, the mean Settled Sales of
the headline target for that weekday over the four most recent weeks. "Settled"
means at least three days before `today` — inside ADR 0004's trailing window,
Sales are still revisable, and a baseline that shifts under a published report is
worse than one that is four days older. Days with no baseline at all yield
`"no baseline"`, never a percentage.

**Direction.** Up, down, or flat against that baseline; flat when the move is
under 3%. On a series that swings 300–680 a day, a 1.4% move is rounding.

**Varieties are raw.** Each variety's own logged forecast, unmodified. The payload
carries both the varieties' sum and the headline so the page can state the
top-down gap rather than reconcile it away.

## Tests

A new `tests/test_report_payload.py`. Build `logged` and `sales` as small
in-memory frames — the module never touches a database, so these are cheap:

- a complete origin yields a payload whose window is exactly the seven dates
  after `today`
- a superseded config version refuses, and the message names the active version
- an origin older than `today` refuses, with the other message
- a model missing one target date is omitted; a model missing one *variety* date
  on an otherwise complete day is also omitted
- no eligible model refuses
- the baseline averages the right four weekdays and ignores days inside the
  trailing window
- a weekday with no settled Sales gives "no baseline", not a 100% move
- moves of 2.9% and 3.1% land either side of flat
- the varieties' sum is reported even when it differs from the headline
- a report whose config has `horizon_days: 14` produces a fourteen-day window,
  with no week-shaped assumption anywhere
