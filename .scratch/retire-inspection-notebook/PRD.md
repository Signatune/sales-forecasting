# Retire the Inspection Notebook

Status: ready-for-agent

## Problem Statement

The project carries a Jupyter notebook as its human inspection surface — the
place a person eyeballs forecast-versus-actual charts before trusting a number.
It has outlived the phase it was built for.

The notebook belonged to the pilot and the model-comparison efforts, both of
which have concluded. Their findings are recorded durably elsewhere: the research
in the model-evaluation field notes, the decisions in the ADRs. What the notebook
still holds is rendered chart output, which is a record of those same concluded
decisions rather than a live tool.

Meanwhile it costs something. Jupyter and the plotting library are a large
dependency tree carried for a surface nobody runs — they are not even installed
in the current environment, so the notebook cannot be opened on the machine that
holds it. The notebook file itself is several hundred kilobytes, almost entirely
embedded output, in every clone. And as the project moves to a containerized
environment, that tree would be paid for on every image build and every CI run.

## Solution

Remove Jupyter, the plotting dependency, and the notebook from the project.

Forecast inspection moves to the frontend the project already anticipates — the
one that will read and edit the forecast configuration directly from the
database. Since ADR 0006 made forecasts a write-once log in Postgres rather than
a file, the data a future inspection surface needs is already queryable; what is
retired is one particular way of looking at it, not the ability to look.

This is done with an explicit, recorded acceptance of risk. Between this change
and that frontend, the daily forecast job runs unattended with **no human sanity
check on its output**. That gap is deliberate and is the main reason this
decision gets an ADR of its own rather than being folded into the container work.

See `docs/adr/0009-retire-the-inspection-notebook.md`.

## User Stories

1. As a maintainer, I want the Jupyter dependency tree removed, so that the
   environment stops carrying a large install for a surface nobody runs.
2. As a maintainer, I want the container image to stay small, so that image builds
   and CI runs do not pay for a plotting stack they never use.
3. As a maintainer, I want the notebook's embedded output out of the repository,
   so that every clone stops carrying several hundred kilobytes of rendered charts.
4. As a maintainer, I want the optional notebook dependency group removed, so that
   the declared extras reflect what the project actually supports.
5. As a maintainer, I want the notebook-related ignore rules removed, so that the
   ignore file stops describing files that cannot exist.
6. As a maintainer, I want module documentation to stop referring to a notebook
   that has been removed, so that comments do not send a reader looking for
   something that is gone.
7. As a developer, I want the concluded pilot's findings to remain available, so
   that retiring the notebook loses no reasoning.
8. As a developer, I want the removed notebook recoverable from version history,
   so that deletion is reversible if a chart is ever needed again.
9. As a maintainer, I want the reason for removal recorded, so that a future reader
   understands the pilot concluded rather than assuming the surface was lost by
   accident.
10. As a shop owner, I want the loss of the visual sanity check explicitly
    acknowledged, so that nobody assumes forecasts are being reviewed when they
    are not.
11. As a shop owner, I want the condition that closes this gap named, so that it is
    a tracked commitment rather than an open-ended absence.
12. As a maintainer, I want inspection to land in the planned frontend rather than
    in a replacement script, so that effort goes toward the durable surface instead
    of an interim one.
13. As a maintainer, I want the forecast log to remain directly queryable in the
    meantime, so that an ad-hoc check is always possible even without a built tool.
14. As a maintainer, I want the test suite unaffected by this removal, so that the
    change carries no risk to verified behavior.
15. As a maintainer, I want the pipeline's runtime behavior unchanged, so that
    retiring an inspection surface cannot alter what gets forecast or captured.
16. As a maintainer, I want the concluded efforts' tickets left as the historical
    record they are, so that removing a tool does not mean rewriting the history
    that produced it.

## Implementation Decisions

- **No seam, no new code.** This effort only removes things. There is no interface
  to design and no behavior to introduce.
- **The notebook file is deleted rather than frozen in place.** Version history
  retains it, which makes the deletion reversible, and the conclusions it
  illustrated are already written down in the model-evaluation field notes and the
  ADRs. Keeping several hundred kilobytes of embedded output in every clone to
  preserve a rendered view of a concluded decision is not a trade worth making.
- **The optional notebook dependency group is removed** from the project metadata,
  along with the plotting and Jupyter dependencies it named. Nothing under test
  imports either, so the suite is unaffected.
- **Ignore rules for notebook checkpoints and rendered HTML are removed**, since
  the files they describe can no longer be produced.
- **Docstring references to the notebook are corrected** in the backtest module,
  which mentions the notebook charting particular rows. The behavior those comments
  describe — that rows with a zero actual are carried in the comparison frame but
  excluded from the mean and counted separately — is still true and still worth
  documenting; only the claim that a notebook charts them is removed.
- **No replacement inspection surface is built.** A script printing a
  forecast-versus-actual table, and a variant writing a chart image, were both
  considered and declined. Inspection lands in the planned frontend, which reads
  the forecast log and the configuration from the database directly.
- **The gap is recorded as an accepted risk, not omitted.** The ADR states plainly
  that between this change and the frontend, a silently degrading model — an
  upstream rename that empties a Forecast Target, a fit that begins returning
  implausible values — accumulates rows in the log with no human review, and that
  the first practical signal would be an operational shortfall. This is the ADR's
  primary reason for existing.
- **Concluded efforts' PRDs and tickets are left untouched.** Several describe the
  notebook as the pilot's demo surface and one names it as a trust gate. They are a
  record of what was decided at the time; rewriting them would falsify that record.
  The current-state documents are what get corrected.
- **The domain glossary is unchanged.** No domain term is introduced or altered.

## Testing Decisions

- **Test external behavior, not implementation.** Unchanged; this effort adds no
  tests because it adds no behavior.
- **The acceptance criterion is that the suite still passes**, unchanged in count
  and outcome. Nothing under test imports the removed dependencies, so any
  deviation indicates an unintended coupling worth investigating rather than
  absorbing.
- **Verify the removal is complete** by confirming no remaining reference to the
  notebook, the plotting library, or Jupyter survives in project metadata, ignore
  rules, module documentation, or current-state documentation — excluding the
  concluded efforts' historical tickets, which are intentionally preserved.
- **Prior art.** The project has retired paths before — the file-based ingestion
  path and the parquet forecast outputs were both removed once superseded, each
  recorded in an ADR rather than dropped silently. This follows the same pattern.

## Out of Scope

- **Building any replacement inspection surface.** Explicitly declined; the gap is
  accepted and recorded.
- **The frontend itself.** Editing configuration through a UI, the scoped
  row-level-security policies and authentication that exposing the tables would
  require, and any inspection views it offers, all remain a separate future effort.
- **The analysis layer.** Forecast-versus-actual views, summary statistics, and the
  operational bake number are a distinct tracked follow-on and are not affected by
  this removal.
- **Rewriting concluded efforts' tickets** to remove their notebook references.
- **Any change to forecasting, capture, or schema behavior.**
- **The container work.** Tracked as its own effort; the two land together and this
  removal is what keeps that image small, but the decisions are independent.

## Further Notes

- The removed dependencies were never installed in the current environment, so the
  notebook was already unrunnable on the machine holding it. This change makes an
  existing reality explicit rather than taking a working tool away.
- No Python module imports the plotting library or Jupyter. The only code contact
  is documentation prose in the backtest module, which makes the removal
  mechanically safe.
- The pilot PRD's language — a manual visual sanity check before treating any
  forecast number as trustworthy — is the clearest statement of what is being given
  up, and is worth quoting in the ADR so the accepted risk is recorded in the
  project's own words.
- Because ADR 0006 already moved forecasts out of files and into a queryable
  write-once log, the data needed for inspection is not going anywhere. What is
  retired is a viewer, not a record.
- Respects ADR 0002 (the scoring decisions the notebook illustrated, which remain
  recorded independently) and ADR 0006 (the forecast log a future inspection
  surface will read).
