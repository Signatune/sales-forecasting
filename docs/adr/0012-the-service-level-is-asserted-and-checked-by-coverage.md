# The 95% Service Level is asserted from operating knowledge, not derived from measured costs

ADR 0002 fixed the Service Level at 95% and scores on pinball@95; `CONTEXT.md`
records it as "chosen as a target (currently 95%)". Neither says how 95% was
arrived at. It was not measured, and this records that it will not be.

The textbook route is the critical fractile: the optimal Service Level is
`Cu / (Cu + Co)`, where `Cu` is the cost of one Stockout and `Co` the cost of one
leftover. Choosing 95% asserts `Cu/Co ≈ 19`. **We assert it rather than measure
it**, on two grounds:

- **`Co` is very small, because leftovers are sold day-old.** `Co` is marginal
  cost minus salvage recovery, not fully-loaded cost per bagel. The marginal cost
  of the buffered decision is the *Poolish* ingredients — flour, water, yeast,
  salt, decided at lead 3 (ADR 0001) — and day-old sell-through recovers a large
  part of even that. A small `Co` sits in the denominator, so it drives the
  fractile toward 1 harder than a large `Cu` does.
- **`Cu` is not separable in the data we hold, and is larger than the bagel.** In
  Toast the baked varieties are *modifiers*, and they price at zero: `plain`,
  `everything` and `sesame` all report `averagePrice 0.00`, because the revenue
  belongs to the parent item the bagel is a component of. A bagel's economic
  value is whatever it enables — a sandwich, a schmear, a platter — plus the
  add-ons on that check and the disruption of turning a customer away. There is
  no separable bagel margin to measure.

## Why measuring `Cu` was rejected rather than deferred

It is not merely expensive — the data does not exist. `aggregate_modifier_rows`
(`toast_orders.py`) collapses each day to one row per modifier name before
anything is stored, so `raw_toast_responses` holds no order, check or basket
grain and no modifier-to-parent link. Measuring the margin of checks containing a
bagel would require re-capturing history at basket grain first. That is worth
doing for its own reasons — the table's own header claims to be a replay safety
net, which it is not today — but it is not worth doing *for this*.

And it would buy little. Intuition is reliable about the ordering (`Cu` is much
larger than `Co` — obviously true, no data needed) and unreliable about ratios
above roughly 10, where 90%, 95% and 97.5% all feel alike. Measurement would land
somewhere in that band and change the bake by less than the residual estimate's
own uncertainty.

## The check is realized coverage, not a better cost estimate

`models.coverage` computes the fraction of days a buffered quantity actually met
Demand. Once the forecast log is long enough, that measures whether a nominal 95%
buffer delivers 95% *in the shop*. If it delivers 88%, the buffer is
under-provisioning and no improvement to `Cu` would have revealed it. This is the
intended feedback loop, and it costs nothing but elapsed time.

## Consequences

- **There is a ceiling on the Service Level that has nothing to do with cost,
  and it has been measured: 96-97%.** The buffer reads a quantile of a finite
  pool of lead-3 relative residuals (ADR 0002), and the 99th percentile of a
  100-observation pool *is* its maximum, so above some level raising the Service
  Level buys noise rather than service. That level is 97% for the incumbent EWMA
  and 96% for Holt-Winters, and **95% sits below it either way** — but only
  against a pool spanning the whole stationary era, which is why ADR 0013 widens
  it. See "What the ceiling turned out to be" below.
- **Systematic over-baking is not free even with cheap salvage.** A standing
  day-old supply competes with full-price sales. The mechanism that makes `Co`
  small is the same one that quietly cannibalises fresh Demand if leaned on daily,
  which is a reason not to drift the Service Level upward simply because leftovers
  feel harmless.
- Revisiting this is cheap: it is one number, and the reasoning above is what a
  future reader should argue with. Realized coverage running persistently below
  target is the signal that the *buffer* is wrong; a change in the day-old
  sell-through or the menu is the signal that the *assertion* is.

## What the ceiling turned out to be

Measured by a rolling-origin lead-3 replay of the incumbent
EWMA over the `wheat_bagels` total, one origin per open day, which yields
**1,657 relative residuals** spanning 2022-01-01 to 2026-07-28. Consecutive
residuals correlate (lag-1 ≈ +0.24: a busy week runs busy), so the pool is worth
about **893 independent observations**, not 1,657.

At the 95% Service Level the buffer multiplies the point forecast by **1.32**,
with a moving-block bootstrap interval of 1.30–1.36. On a typical 719-bagel
Saturday that is a bake of 950 give or take 50 — **5% of the bake attributable to
estimation noise**, resting on 45 effective observations. At 97% the same figure
is 9%; at 97.5% it is 16%. The ceiling is that knee.

**Two independent limits land on the same number, which is why 97% is the
answer rather than an artifact of one criterion.**

- *Sample size.* Bootstrap uncertainty stays under a tenth of the bake through
  97% and roughly doubles at 97.5%. This limit moves with the pool: more history
  raises it.
- *Structure, which does not move.* The pool is a mixture. 94% of days are
  ordinary trading (spread 0.17, largest residual +0.73); 6% are calendar
  events — Patriots' Day, Yom Kippur, Labor Day, Christmas Eve — with spread
  0.64 and residuals to +2.69. Of the observations above the 95th percentile,
  52% are calendar events; above the 97.5th, 86%; above the 99th, **100%**. So a
  Service Level past ~97% is not estimated from ordinary trading at all. It buys
  a Patriots'-Day buffer on all 365 days to cover five the calendar already
  names, which is the "systematic over-baking is not free" consequence above,
  arriving by a different road.

No amount of additional history raises the second limit. Only a model that knows
the calendar would — and that, not a higher target, is what a future reader
wanting 99% should build.

**It is the data's ceiling, not one model's.** Replaying Holt-Winters over the
same 1,657 origins gives a residual pool almost indistinguishable from EWMA's
(mean +0.011 against +0.008, spread 0.248 against 0.247) and the identical tail
composition — 52% calendar events above the 95th percentile, 100% above the 99th.
Its knee arrives one notch earlier, at 96%, because its buffer estimate is
slightly noisier. So the honest range is **96-97% depending on the model whose
residuals are pooled, and 95% sits below it either way** — which is the margin
this ADR needed and does not have to spend.

**A parametric estimate does not buy headroom.** The obvious escape from a
sample-size limit is to fit a distribution rather than count order statistics.
It fails here: the pool has skew +3.1 and excess kurtosis +26 (Kolmogorov-Smirnov
against a normal, p ≈ 2e-19). A normal fit *over*-buffers by 68 bagels on a
Saturday at the 95th percentile — its variance is inflated by the same holidays —
and *under*-buffers by 253 at the 99th. It is wrong in both directions at once,
so the empirical quantile is earning its keep.

**A correction to this ADR as first written.** The paragraph above replaced a
claim that the pool should be checked against "the residual pool `backtest.py`
actually produces". `backtest.py` produces no such pool: it is the pilot
backtest, scoring MAPE across leads 2..7 from origins six days apart, and knows
nothing of relative residuals or lead 3. The rolling-origin evaluator that did
produce them, `model_comparison.py`, was retired in commit `1d62ef4`, which left
`models.p95_buffer` taking an argument nothing in the tree could supply. The
rolling-origin replay above is that producer, rebuilt against today's
`models.py` rather than restored, and run ad hoc rather than kept as a
checked-in script.
