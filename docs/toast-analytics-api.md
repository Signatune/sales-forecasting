# Toast Analytics API — field notes

Everything below was verified live against production on 2026-07-09 while
implementing ticket 01 (bagel Sales ingestion). Official docs:
[analytics overview](https://doc.toasttab.com/doc/devguide/apiAnalyticsAccessOverview.html),
[rate limiting](https://doc.toasttab.com/doc/devguide/apiRateLimiting.html).

## Credentials and auth

- `POST https://ws-api.toasttab.com/authentication/v1/authentication/login`
  with `{clientId, clientSecret, userAccessType: "TOAST_MACHINE_CLIENT"}` →
  Bearer token, expires in 86400s (24h).
- Our credential's scope is **`enterprise-metrics:read` only** — it can call
  the Analytics API (`/era/v1/...`) but *not* the standard Orders/Menus/Labor
  APIs. Faster bulk pulls via `/orders/v2/ordersBulk` would need a
  differently-scoped credential.
- The `restaurantGUID = IQID_3` line in `.env` is not a Toast identifier;
  restaurant scope is set per-request (or by filtering rows), not by header.

## The async report pattern

Every Analytics query is a two-step async report:

1. `POST /era/v1/menu` (custom range) or `/era/v1/menu/{day|week|month|year}`
   → returns a bare JSON string: the report GUID.
2. `GET /era/v1/menu/{guid}` → `[]` until ready, then the full row array.

**There is no status field.** `[]` means *both* "still processing" and
"genuinely no data" (e.g. days the restaurants were closed). Real reports
were ready in ~20–40s; treat a stubbornly-empty result with suspicion and
cross-check (we require two consecutive empty year windows before believing
history has ended).

## Request body gotchas

- `startBusinessDate` / `endBusinessDate` are **integers** (`20260709`), but
  `businessDate` in *response* rows is a **string** (`"20260709"`).
- `restaurantIds` and `excludedRestaurantIds` must both be present (empty
  array = no filter). Populating both at once is a 400.
- `groupBy` (enum `MENU`, `MENU_GROUP`, `MENU_ITEM`, `MODIFIER`) is **only
  accepted by the day/week endpoints**. The custom endpoint rejects it
  ("API request body does not support groupBy option") and always returns
  restaurant-level daily totals — one row per restaurant per business date.
- Custom ranges are capped at **366 days** ("difference between End Date and
  Start Date should be less than 366").

## Rate limits (the real constraint)

| Endpoint | Limit |
|---|---|
| `POST /era/v1/menu` (custom, also month/year) | 10 / hour |
| `POST /era/v1/menu/day`, `/week` | 10 / min **and** 60 / hour |
| `GET /era/v1/menu/{guid}` | 5 / sec, 30 / min |

Modifier detail therefore flows at **≤ 60 weeks (~14 months) of history per
hour** — a full multi-year backfill is a multi-hour job by construction.
Observed 429s did **not** carry a `Retry-After` header despite the rate-limit
doc describing one; code should honor it when present but needs a fallback.

## Response row shape

Modifier-grouped rows (the ones bagel Sales live in):
`restaurantGuid`, `restaurantName`, `restaurantLocationName`,
`restaurantLocationCode`, `businessDate` (string), `modifierGuid`,
`modifierName`, `quantitySold` (float), `netSalesAmount`,
`grossSalesAmount`, `discountAmount`, `refundAmount`, `voidAmount`,
`averagePrice`, `wasteCount`, `wasteAmount`.

- `quantitySold` is the meaningful field for modifiers — `netSalesAmount` is
  usually `0.0` because most modifiers are free-of-charge line items.
- **The same `modifierName` can exist under multiple GUIDs** (we see two
  distinct `"plain, bulk"` modifiers). Aggregate by name, not GUID.
- **`modifierGuid` is absent on open-text modifiers.** Toast treats free text
  a guest or server types on a check (`"Light on the hazelnut please! "`,
  `"dana"`, `"cut in half"`) as a modifier row like any other, but assigns a
  GUID only to configured menu entities. Presence of `modifierGuid` is the
  only reliable way to tell a real menu modifier from typed text — the names
  are not distinguishable (guests type `"everything bagel"` verbatim). Over
  our history, ~11k distinct open-text names appear. `toast_orders.py`
  reproduces this from the Orders API, where such modifiers have no `item`,
  as the sentinel `modifierGuid: "unknown"`.
- **`modifierName` is edited in place, and the history is not rewritten.** A
  renamed modifier keeps its GUID but every past row shows the old name, so
  one Product's Sales span several spellings. Seen so far: `"gluten free …"`
  → `"gluten-free …"` (Feb–Mar 2025) and `"pumpernickel bagel - (thursdays
  only!)"` → `"pumpernickel bagel (thursdays only!)"` (Apr 2025). Both
  spellings are live for days-to-weeks around a cutover. Match on the union
  of a Product's historical names, never on the current one alone.

`GET /era/v1/restaurants-information` lists the management group:
`restaurantGuid`, `restaurantName`, `active`, `testMode`, `archived`.

## Mamaleh's specifics

- Management group has 6 restaurants: Cambridge
  (`28e5b269-1c1c-45df-81a8-1d268c005dfa`) and Brookline
  (`9ae70079-b9cd-4b92-8457-c86bc823188f`) are the in-scope locations; the
  Production Kitchen (`edc11a00-9da6-417f-8a7e-4fd645803aab`) is active but
  out of scope and has no menu-report rows; three others are `testMode`.
- Bagel Sales exist **only as modifiers** — no menu item per flavor. Each
  main flavor (plain, sesame, everything) has a sandwich modifier
  (`"plain bagel"`) and a bulk modifier (`"plain, bulk"`) that must be
  summed. Rotating flavors: cinnamon raisin (Wednesdays), pumpernickel
  (Thursdays). Gluten-free flavors use two naming styles
  (`"gluten-free plain bagel (...)"` and `"plain gluten-free"`).
- Daily-totals probes show data back to at least **2019-07-10** (Cambridge);
  Brookline first appears mid-2021. Closed days (e.g. 2026-07-03..05, the
  July 4th weekend) legitimately return empty reports.
