# A report names a headline model; the forecast log stays neutral

ADR 0006 refused to promote a winning model — the backtest could not separate
EWMA and Holt-Winters (0.8 standard errors apart), so both run every morning and
both are logged, "two horses in a log". But a PDF has a page 1 and a Chat message
has to quote a number, so a report cannot be neutral the way a table can.

So a Scheduled Report **names a `headline_model`**: it leads the PDF and is the
model quoted in the Chat card. Every other qualifying model still gets a full
page of its own, in the same layout, so the report shows the disagreement rather
than hiding it — flipping pages is the model toggle. This is a *presentation*
choice about which number is easiest to act on, and it is deliberately scoped
there: the engine still runs every configured model, the log still records them
all equally, and nothing about accuracy or promotion is being asserted.

The alternative — quoting both models everywhere — was rejected because it leaves
"how many do I bake on Saturday?" without an answer, which is the one question
the report exists to settle.

**`headline_model` deliberately does not live in `forecast_configs`.** That
document keys the forecast log: changing it means a new `version`, and every row
logged afterwards carries that stamp (ADR 0006 — "provenance is the
`config_version` stamp"). Putting a presentation setting there would fragment the
forecast record whenever someone reordered pages, for a change that altered no
forecast at all. It lives in `report_configs` instead, which references a
forecast configuration without being one.

## Consequences

- The pages after the headline are ordered alphabetically by model, which is
  stable week to week and asserts no ranking among them.
- Changing the headline is an `UPDATE` on one row rather than a deploy — so it
  leaves no reason in git. If the choice ever becomes evidence-driven rather than
  a default, the evidence belongs in the running-accuracy work that was
  deliberately deferred out of the first version of this report.
- `ewma` is the initial headline: the two models were statistically tied, and
  ADR 0006 already noted that ETS "does not earn its `statsmodels` dependency",
  so the simpler model leads absent evidence either way.
