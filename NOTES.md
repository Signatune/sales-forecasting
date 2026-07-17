# Teaching notes

## Preferences (from memory + observed)
- Build forecasting/stats concepts **from scratch** before asking Mike to choose
  between options. Don't assume prior stats vocabulary.
- Ground every lesson in the real repo code and the real bakery units.

## Conventions decided
- **Pinball loss convention:** this repo uses the *un-doubled* form
  `level*(a-f)` / `(1-level)*(f-a)`. The FPP3 textbook uses `2p`/`2(1-p)`,
  i.e. exactly 2× ours. Same asymmetry ratio (19:1 at 0.95), rankings identical.
  Always teach ours, flag the textbook's factor of 2 so Mike isn't confused when
  he reads FPP3.
- "forecast" scored by pinball@95 = the **P95 Poolish quantity** (buffered point
  forecast), not the raw point forecast. "actual" = actual Wheat Dough Demand.

## Style
- Tufte-ish, print-friendly lessons. Shared stylesheet in `assets/lesson.css`.
- Equal-length quiz answers (no formatting tells).
