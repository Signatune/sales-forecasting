# Ingest bagel Sales history from Toast

Status: ready-for-human
Blocked by: None — can start immediately

## Parent

`.scratch/bagel-forecast-pilot/PRD.md`

## What to build

Running one command pulls the full available daily Sales history for the bagel varieties from the Toast Analytics API and leaves behind two artifacts: timestamped raw API responses (so normalization can be rebuilt and debugged without re-hitting the API) and a normalized Sales history of canonical (Product, Date, Quantity) records ready for forecasting.

This ticket absorbs the pilot's unknowns: discovering the actual response shape, whether the API caps how far back a single pull can reach (paginate/loop if so), and which Toast menu items map to our bagel Products. Fail loudly on auth errors or an unexpected response shape — surfacing "Toast changed its shape" is valuable; silently papering over it is not.

Use the domain vocabulary from `CONTEXT.md`: these are `Sales` records for `Products` — not orders, items, or SKUs.

## Acceptance criteria

- [x] A single command authenticates against Toast and pulls the full available daily Sales history for the bagel family, looping/paginating past any lookback cap
- [x] Raw responses saved timestamped under a raw-data directory
- [x] Normalized Sales history written as one record per (Product, Date, Quantity)
- [x] The confirmed list of bagel varieties (as Toast exposes them) is recorded in this ticket's Comments
- [x] A unit test locks the normalization against a saved real sample response, so future shape drift breaks loudly
- [x] Auth failures and unexpected response shapes raise clear errors rather than producing partial/empty output

## Blocked by

- None — can start immediately

## Comments

### Confirmed bagel varieties (2026-07-10)

Seven Products. Bagels exist in Toast **only as modifiers** — there is no menu
item per flavor — so a Product's daily Sales is the sum of its modifiers'
`quantitySold` across the Cambridge and Brookline locations.

Modifier names are edited in place and Toast does not rewrite history, so each
Product spans several spellings. Every historical name is listed; dropping one
puts a phantom step change in that Product's series on the day of the rename.
The authoritative mapping is `BAGEL_MODIFIER_NAMES` in `normalize.py`.

| Product | Toast modifier names (→ = renamed, old name still in old rows) |
|---|---|
| plain | `plain bagel`, `plain, bulk`, `plain bagel [allergens: wheat]` |
| sesame | `sesame bagel`, `sesame, bulk` |
| everything | `everything bagel`, `everything, bulk` |
| cinnamon raisin | `cinnamon raisin bagel (wednesdays only!)` |
| pumpernickel | `pumpernickel bagel - (thursdays only!)` → `pumpernickel bagel (thursdays only!)` (Apr 2025) |
| gluten-free plain | `plain gluten-free`; `gluten free plain bagel (must be toasted)` → `gluten free plain bagel (original sunshine, …)` → `gluten-free plain bagel (original sunshine, …)` (Feb 2025) |
| gluten-free everything | `everything gluten-free`; `gluten free everything bagel (must be toasted)` → `gluten free everything bagel (original sunshine, …)` → `gluten-free everything bagel (original sunshine, …)` (Mar 2025) |

The three main flavors each have a sandwich modifier (`plain bagel`) and a
bulk modifier (`plain, bulk`). Cinnamon raisin and pumpernickel are sold one
weekday a week (Wednesdays / Thursdays), which the data confirms: 138 and 121
sales-days respectively over ~118 weeks.

**Deliberately excluded — `rainbow bagel`.** A genuine configured modifier, but
a June promo: ~440 units across 21 sales-days over three summers, under a fresh
date-scoped name each year (`rainbow bagel (6/1-6/7 only)`, `rainbow bagel
(available 6/14 & 6/15)`, …). Too sparse to forecast; it would add a series
that is zero on ~97% of days. Recorded in `EXCLUDED_MODIFIER_NAMES` so it stays
a decision rather than a warning on every run. Revisit if bagel promos become
a regular thing.

### Two Toast behaviors that shaped the implementation

**Free text is a modifier.** Toast records text a guest or server types on a
check (`Light on the hazelnut please!`, `dana`, `cut in half`) as a modifier
row like any other, but assigns `modifierGuid` **only to configured menu
entities**. Names alone cannot separate the two — guests type `everything
bagel` verbatim. So:

- Requiring `modifierGuid` crashed the pull on the first open-text row it met
  (`week report 2026-04-25..2026-05-01: row 734 is missing 'modifierGuid'`).
  It is now optional, and type-checked when present.
- Presence of a configured GUID is what makes a row Sales. This drops ~19
  units of guests typing Product names, and cut the unmapped-modifier drift
  warning from 542 names to the 11 that are real.

**The `--fast` Orders API path is not optional.** Analytics caps modifier
detail at ~60 weeks/hour, so the full history is a multi-hour pull.
`toast_orders.py` (standard key, 5 req/s) backfills the same rows from
`/orders/v2/ordersBulk`; `normalize.py` merges both, Analytics winning any
date it covers. `ingest.py` now drives both, then normalizes.

### Result

`data/sales_history.parquet` — one record per (Product, Date, Quantity), no
duplicate (Product, Date) pairs. Snapshot at 2026-07-10 while the Orders
backfill was still running: 4,488 records, 7 Products, 2024-03-01..2026-07-09.

The start date moves earlier as the backfill walks back a month at a time.
Daily-totals probes show Cambridge data to at least 2019-07-10 and Brookline
from mid-2021, so expect the history to roughly triple. Re-run `normalize.py`
to pick up newly captured months — it rebuilds from `data/raw/`, no re-pull.

Do not run `ingest.py` while a `toast_orders.py` backfill is in flight: it
starts a second one, and the two would double-hit the Orders API.
