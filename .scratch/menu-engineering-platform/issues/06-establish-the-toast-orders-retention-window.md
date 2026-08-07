# Establish how far Toast orders history actually reaches

Type: research
Status: open

## Question

How far back can the Toast **Orders** API actually be re-pulled, at full
selection-and-modifier fidelity, for these restaurants — today?

This is the fact the backfill (ticket 07) is sized against, and it is on a
rolling clock: whatever the window is, its far edge moves forward every day.

`toast_orders.py:3` notes the *Analytics* API caps modifier-level reports at
~14 months of history per request. That is a different API and possibly a
different limit — the daily path uses Orders (ADR 0004). Do not assume the two
match.

Establish empirically, not from documentation alone:

- The oldest business date the Orders API returns data for, per restaurant GUID
  in `INCLUDED_RESTAURANTS`.
- Whether older orders come back *complete* — selections, prices, nested
  modifiers — or degraded at the edges.
- Rate limits and page sizes, so 07 can estimate how long a full backfill runs
  and whether it needs throttling.
- Whether any documented retention policy exists that would move the boundary
  without warning.

AFK — no human needed. Produce a markdown note under `docs/` and link it from
the resolution. Record the concrete oldest-available date **with the date it was
measured on**, since it will have moved by the time 07 runs.
