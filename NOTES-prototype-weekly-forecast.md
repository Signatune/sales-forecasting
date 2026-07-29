# Prototype — upcoming-week bagel forecast report

**Throwaway.** Delete this file and `prototype_weekly_forecast.{py,html}` once the
question below is answered and the winner has been folded into real code.

## The question

What should the upcoming-week (Monday–Sunday) bagel forecast report look like?
Specifically: how should it show each day's predicted Sales, and how should it
show whether a day is trending up or down since last week?

Three variants, one route, switchable with `?variant=` and the floating bottom
bar. `?theme=dark|light` flips the palette; the bar's ◐ button does the same.

## Run it

```
python prototype_weekly_forecast.py             # real DB + real models
python prototype_weekly_forecast.py --fabricate # no DB needed
python prototype_weekly_forecast.py --model holt_winters
```

Serves <http://localhost:8765/?variant=A>. Read-only — one connection, reads
Sales and the `forecasts` log, runs the models in memory, writes nothing.

## The variants

| Key | Name | Shape | Primary affordance |
|-----|------|-------|--------------------|
| A | Bake board | 7 equal columns, one card per day | Scan across for the day you're baking for |
| B | Friday briefing | A document — lead sentence, one aligned list, exceptions called out by name | Read top to bottom |
| C | Chart-first | One large bar chart carries the answer; variety small multiples and a table view below | Compare shapes at a glance |

They disagree structurally on purpose. A is a wall board, B is something you'd
read aloud on a Friday, C is something you'd stare at.

## What the prototype had to decide, and what it decided

These came up while building and are worth keeping regardless of which variant wins:

- **The week doesn't fit the horizon.** `horizon_days` is 7, so a Friday `as_of`
  reaches Friday — the upcoming week's Saturday and Sunday fall past it. The
  prototype sources those two days by re-running the same models in memory at a
  longer horizon and labels them `extrapolated` against the `logged` days. A real
  report has to make that distinction visible or raise `horizon_days` to 9+; the
  two claims are not the same and shouldn't render the same.
- **"Last week" is not the current week.** Today is mid-week and today's capture
  is partial (ADR 0004's trailing window is still open), so the baseline is the
  last *complete* Monday–Sunday, matched weekday to weekday. Comparing against
  the in-progress week reads as a phantom collapse.
- **A missing baseline day is not a 100% jump.** Days with no Sales (a closure,
  a gap) render as "no baseline", not as a spike.
- **Under 3% is flat.** On a series that swings 300–680 a day, calling a 1.4%
  move a trend has the board chasing rounding.
- **Top-down ≠ bottom-up.** The `wheat_bagels` headline is fit to the summed
  series, so it does not equal the three varieties added up. Every variant
  footnotes this rather than quietly reconciling it.
- **This is the point forecast, not a Poolish quantity.** No Service Level buffer
  is applied anywhere. If this report is what someone actually bakes off, that's
  the next question to answer, and it's a bigger one than layout.

## Verdict

- C is the winner.
- For the bagel flavors, a simple average percent split for each flavor will suffice. Simply show the estimated split and then show if the trend is moving.
- The forecast should only use data that has been logged in the DB. If the forecast is done on a Sunday, it should be able to get 6 days out as a forecast at least, so until the coming Saturday
- This forecast is only for the bagels we sell via many smaller orders through service, and doesn't represent any bagel preorders or planned caterings that we will know about in advance. The layout should leave space to add that in later 
