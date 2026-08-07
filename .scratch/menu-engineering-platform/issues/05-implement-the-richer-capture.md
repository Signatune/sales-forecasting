# Implement the richer capture

Type: task
Status: open
Blocked by: 02, 03, 04

## Question

Nothing to decide — 03 and 04 decided it. Do the work, for real. **This is the
clock-stopper**: until it ships, every day that passes is a day of menu
engineering history that cannot be recovered.

- Write the migration(s) for the new grain, using the tooling from 02.
- Change `daily_capture.py` to persist at the new grain, with 03's field
  allowlist enforced where it cannot be bypassed.
- Preserve ADR 0004's semantics at the new grain: the 3-day trailing re-pull
  must still replace rather than duplicate, so voids and back-office corrections
  are picked up.
- Implement whatever 04 decided for `sales` / `product_sales`, and run the
  old-vs-new comparison that ticket named as its proof. **Do not ship if the
  forecast numbers move unexplained.**
- Tests, matching the existing suite's conventions.

Resolved when the scheduled capture is writing the new grain daily and the
forecast pipeline is demonstrably unaffected. Record in the answer: the
migration name(s), the new table shapes as built, row counts and storage growth
per day observed, and the result of the forecast comparison.
