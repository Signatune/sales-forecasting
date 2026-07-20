# Retire the inspection notebook, accepting an unreviewed daily forecast until the frontend lands

`notebooks/exploration.ipynb` was the project's human inspection surface — the
place a person eyeballed forecast-vs-actual charts per Product before trusting
a number. It belonged to two efforts that have both concluded: the bagel
forecast pilot and the bake forecast model comparison. Their findings are
recorded durably elsewhere — the research in the model-evaluation field notes,
the decisions in ADR 0001 and ADR 0002. What the notebook still held was
rendered chart output: a picture of decisions already made, not a live tool.

It was not free. `matplotlib` and `jupyter` are a large dependency tree carried
for a surface nobody runs — and one not even installed in the current
environment, so the notebook could not be opened on the machine holding it. The
file itself was ~372 KB, almost entirely embedded output, in every clone. As
the project moves to a containerized environment, that tree would be paid for
on every image build and every CI run.

So the notebook, the `notebook` optional-dependency group, and the ignore rules
for notebook checkpoints and rendered HTML are all removed. The file is deleted
rather than frozen in place: version history keeps it recoverable if a chart is
ever wanted again, which is a better trade than several hundred kilobytes in
every clone. No Python module imported either dependency; the only code contact
was docstring prose in `backtest.py` claiming the notebook charted particular
rows. The behavior those docstrings describe — rows with a zero actual carried
in the comparison frame but excluded from the mean and counted in
`unscored_days` — is still true and still documented; only the notebook claim is
gone.

No replacement inspection surface is built. A script printing a
forecast-vs-actual table, and a variant writing a chart image, were both
considered and declined. Inspection lands in the planned frontend — the one
that reads and edits the forecast configuration from the database directly.
Because ADR 0006 already made forecasts a write-once log in Postgres rather
than a file, the data such a surface needs is queryable today; what is retired
is one way of looking, not the ability to look. An ad-hoc check remains a SQL
query away in the meantime.

The concluded efforts' PRDs and tickets keep their notebook references. They
record what was decided at the time, and rewriting them would falsify that
record. Only current-state documents are corrected.

## Consequences

Between this change and that frontend, the daily forecast job runs unattended
with **no human sanity check on its output**. The pilot PRD named what is being
given up in its own words: "Manual visual sanity check via the notebook before
treating any forecast number as trustworthy."

Concretely: a silently degrading model — an upstream rename that empties a
Forecast Target, a fit that begins returning implausible values — accumulates
rows in the forecast log with nobody looking at them. The first practical signal
would be an operational shortfall, a bake that came out wrong. That gap is
accepted deliberately, and it is the reason this decision gets an ADR of its own
rather than being folded into the container work.

The condition that closes it is the frontend, not a rebuilt notebook. Until
then, the forecast log stays directly queryable, and anyone wanting a check has
to go get it.

The test suite is unaffected — nothing under test imported `matplotlib` or
`jupyter` — and no forecasting, capture, or schema behavior changes.
