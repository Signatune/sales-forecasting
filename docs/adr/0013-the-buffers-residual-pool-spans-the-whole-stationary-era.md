# The buffer's residual pool spans the whole stationary era, not a trailing 26 weeks

The retired rolling-origin evaluator fed `models.p95_buffer` from a 26-week
warm-up window immediately before the days it scored (`WARMUP_WEEKS = 26`),
matching the ~26-week evaluation window ADR 0002 chose for *ranking models*.
Rebuilding that replay as `residuals.py` showed the two windows should not be
the same length. **Model selection stays recent; the buffer's residual pool
takes everything back to 2022-01-01.**

A 26-week pool holds ~178 residuals, worth ~102 after serial correlation. At the
95% Service Level that leaves **five effective observations** above the quantile,
and the buffer multiplier carries a bootstrap interval of ±16% of the bake —
±157 bagels on a Saturday. Read as an order statistic, a nominal 95% built on
that pool delivers a realised Service Level anywhere in **91.3%–98.0%**, decided
by which handful of days happened to fall in the window; over the full pool the
same band is 93.8%–96.2%. That is the ceiling problem ADR 0012 names, and at 26
weeks **95% sits above the ceiling, not below it**.

Widening the pool fixes it, and the reason it is safe to widen is that the pool
is stationary: yearly p95 sits in [0.29, 0.38] for every year 2022 through 2026,
and the estimated multiplier barely moves with the window — 1.34 at 26 weeks,
1.32 at 104, 1.32 at the full era. **Widening the pool does not change the
answer; it makes the answer trustworthy**, taking the interval from ±157 bagels
to ±50.

## Why relative residuals are what make this legal

ADR 0002 chose *relative* residuals so one pool could serve every weekday. The
same property is what lets one pool serve every year: a drift in the level of
Demand moves the quantity a forecast is made at, not the ratio of error to
forecast, so a 2022 residual is as good evidence about tomorrow's *spread* as a
2026 one — visible in the yearly means, which sit within 0.03 of zero throughout.
Recency matters for *which model wins*, because that is a claim about the world
now; it does not matter for how wide the model's errors are, as long as the error
process is unchanged.

**A note on ADR 0002's premise, which no longer holds.** That ADR justifies its
recent window partly on Demand "trending down ~8%/yr". On the Wheat Dough total
over the pool era the trend is **+0.7%/yr** — flat: yearly mean daily totals run
436, 494, 491, 448, 438 for 2022-2026. The ~8%/yr decline is a feature of the
pre-2021 history, which is one variety and a tenth the volume. This does not
disturb ADR 0002's *conclusion* — a recent window is still the right way to pick
a model — but a future reader should not cite the downtrend as established fact
for the current business without re-measuring it.

## Why the era starts at 2022 and not at the start of history

The Sales history reaches back to 2016, but a "wheat total" is only the same
quantity once all three baked varieties sell. Before 2021 exactly one variety
sold per day; 2021 is a ramp from ~150 to ~410 bagels a day, whose relative
residuals average **+0.38** against +0.01 for every year since — a same-weekday
model chronically under-forecasts a tripling series, and pooling that in would
inflate the buffer with the shape of a recovery that is over.

## Consequences

- **Three years is the floor, not 26 weeks.** The bake swing at a 95% Service
  Level is 16% of the bake at 26 weeks, 12% at two years, and 7% at three. The
  step down at three years is where each recurring calendar event has been seen
  three times rather than two — the tail is set by holidays (ADR 0012), and one
  year of history contains exactly one of each.
- **ADR 0002's ~26 weeks still governs model comparison.** Nothing here changes
  which window models are *ranked* over. The two windows now differ deliberately,
  and a future reader collapsing them back into one constant would reintroduce
  the problem this records.
- **`POOL_ERA_START` is a fact about the shop, not a tuning knob.** It marks the
  day the current three-variety business began. It should move only if the menu
  or the locations change in a way that makes older residuals describe a
  different shop — not to chase a nicer-looking number.
- The forecast log (ADR 0006) is the intended long-run source of these residuals
  and is nine origins deep today. It cannot carry a pool of this length for years,
  so the replay remains the source meanwhile; because `residuals.py` resolves
  Targets top-down exactly as the engine does, a replayed origin reproduces the
  row the log would hold, and the switch will not move the numbers.
