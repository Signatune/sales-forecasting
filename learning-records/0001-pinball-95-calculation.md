# 0001 — Pinball@95 calculation, concretely

**Date:** 2026-07-17
**Lesson:** `lessons/0001-pinball-95-concretely.html`
**Mission link:** understand the headline model-selection score.

## What was taught
- Per-day pinball loss in the repo's convention: `level*(a−f)` when short,
  `(1−level)*(f−a)` when over. Headline = mean over days.
- The 19× asymmetry at 0.95 (`0.95/0.05`), grounded in bagels/Stockout vs leftover.
- Worked 5-day example; showed the two short days dominating the mean.
- Coverage introduced as the companion metric (mentioned, not drilled).

## Key insight to carry forward
The score is dominated by a few short (under-forecast) days — the seed of the
"is a lower score evidence or noise?" question. Next lessons build on this.

## Established convention (do not re-teach as new)
- Repo drops the factor of 2 that FPP3 carries. Mike has been told once.
- "forecast" scored = P95 buffered quantity, not the raw point forecast.

## Zone of proximal development — candidate next steps
1. **How the P95 quantity itself is built** (`p95_buffer`, relative residuals) —
   the input to this score. Natural prerequisite Mike may now want.
2. **Coverage vs pinball** — the two-number read of the ranked table.
3. **Evidence vs noise** — pairing daily losses across two models and testing the
   difference (`inspection_page.recommendation`). Higher difficulty; save for later.

## Open / to confirm
- MISSION.md is provisional — confirm the framing with Mike.
- Not yet assessed: does Mike want the *statistical* "why 0.95 = the optimal
  quantile under asymmetric cost" derivation, or is the intuition enough?
