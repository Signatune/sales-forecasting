# Map: Menu engineering platform

Label: `wayfinder:map`

## Destination

A **spec** for expanding this repo from a forecasting pipeline into a
menu-engineering-capable, web-fronted system — every significant decision made
and written down, ready for an implementation effort to pick up — **plus three
things built for real**, because each is on a clock or blocks everything else:
dated migration tooling, the richer PII-safe capture grain, and the historical
backfill at that grain.

Reaching the end means: someone could build the Next.js app and the menu
engineering analysis from this map's decisions without needing to re-open any of
them, and the data those features need is already accumulating.

## Notes

**Domain.** A deli & bakery. `CONTEXT.md` is the ubiquitous-language source of
truth and already carries a Menu Engineering section (Modifier, Base Recipe,
Cost Sign, Realized Cost). Keep it current as tickets resolve — new terms go
there, not into ticket bodies.

**Skills every session should consult.** `/grilling` and `/domain-modeling` are
the default. `/prototype` for the "what does it look like" tickets.
`/ubiquitous-language` when a ticket coins a term.

**Decisions become ADRs.** This repo records decisions in `docs/adr/`. A
resolved ticket that settles something architectural should leave an ADR behind
and link it from the resolution.

**Execution override.** This map is planning by default, with one exception:
tickets typed `task` are **executed for real** (migrations, capture grain,
backfill). Everything else produces decisions, not deliverables.

**Working with Mike.** From-scratch stats background — build concepts up from
first principles before asking for a choice, especially on ticket 09
(classification rules). Where a branch has many defensible variants and no
evidence between them, offer deferral as a first-class option.

**Prior art in the tree.** `menu_engineering/` holds a working Toast↔MarginEdge
mapping tool, `product_map.csv` (confident matches with yields, scale factors,
costs), and a research note on how modifiers fold into Kasavana & Smith. ADR
0014 already rules that modifier eligibility is discovered from orders, never
curated. Don't re-litigate these; build on them.

**The finding that shaped this map.** `daily_capture.py` aggregates full Toast
orders to per-day modifier-name rows *in memory* — deliberately, so no guest PII
is persisted — and saves only the aggregate. So the `sales` fact is flat
`(date, restaurant_guid, source_type, source_name, quantity)`: items and
modifiers are sibling rows with **no link to the parent selection**, and **no
price anywhere**. `raw_toast_responses` stores the same aggregate, so it is not a
fallback. Both axes of menu engineering — revenue, and Realized Cost — need data
that is not stored and cannot be recovered by re-normalizing. Hence tickets 03,
05, 06, 07, and the urgency on all of them.

## Decisions so far

<!-- one line per closed ticket: gist + link -->

- [Choose the migration tooling and the baseline strategy](issues/01-choose-migration-tooling-and-baseline-strategy.md)
  — Plain numbered `migrations/NNNN-<slug>.sql` run by a small runner in `db.py`
  against a `schema_migrations` table, each in its own transaction; **forward-only**,
  no down files (roll-forward + Supabase PITR). `schema.sql` becomes
  `0001-baseline.sql` after a `pg_dump` diff confirms no drift, and is deleted from
  the root. Auto-apply on merge to `main` survives with the path filter moved to
  `migrations/**` — so **PR review is now the only gate**. ADR
  [0015](../../docs/adr/0015-schema-changes-are-forward-only-numbered-sql-migrations.md)
  supersedes 0007's mechanism, keeps its conclusion.

- [Stand up migrations and baseline the live schema](issues/02-stand-up-migrations-and-baseline-the-live-schema.md)
  — **Built and live.** Runner in `db.py` (`python db.py migrate | status |
  baseline NNNN`); `schema_migrations` ledger; `schema.sql` is now
  `migrations/0001-baseline.sql` and the root file is gone. The dump-and-diff
  came back **byte-identical**, so no drift to explain. Production baselined
  2026-08-07 — schema unchanged, all 574,691 `sales` rows and every other count
  untouched. `apply-schema.yml` filters on `migrations/**` and runs the runner.
  Round trip proven on a real Postgres 17, including that a half-failed
  migration leaves nothing behind. **Ticket 05 writes `0002` against a clean
  ledger.** Caveat it inherits: `CREATE INDEX CONCURRENTLY` cannot live in a
  migration.

- [Decide the capture grain and its PII rule](issues/03-decide-the-capture-grain-and-its-pii-rule.md)
  — New `selections` table, one row per Toast selection with Modifiers nested
  as `jsonb`; `sales`/`product_sales` expected to become derived from it
  (ticket 04). Keeps `price` + `preDiscountPrice` (comp vs. cheap-item falls
  out of the two, no flag needed), drops `tax`, excludes voided selections.
  PII kept out by an explicit field allowlist enforced at one extraction
  function — no order/customer/delivery/employee data, ever; selections or
  modifiers without a real Toast GUID are dropped. `raw_toast_responses`'s
  retention gets bounded to a short rolling window since `selections` now
  serves its replay role; `selections` itself stays unbounded for now. ADR
  [0016](../../docs/adr/0016-selection-grain-capture-with-an-allowlisted-pii-boundary.md).

## Not yet specified

- **Where the Toast↔MarginEdge product map lives in the schema.** Today it is
  `menu_engineering/product_map.csv` plus an HTML review tool. It has to become
  tables — but its shape (how Cost Sign, scale numerator/denominator, and recipe
  yield are modeled, and whether the review tool writes to the DB) depends on
  what ticket 08 settles as the unit of analysis. The migrations half of this is
  no longer a blocker — 02 has landed, so the tables are a migration away.
  Revisit once 08 closes.

- **How recipe costs stay current.** `product_map.csv` carries a static `cost`
  column snapshotted from MarginEdge. Real costs drift with invoices. Open:
  snapshot-per-period vs. live pull vs. cost history table — and what a menu
  engineering figure even means when its cost input moves under it. Probably
  interacts with MarginEdge's `Get_recipe_cost_histories`.

- **What the app actually shows.** A prototype question, not an argument one.
  Blocked until 08 and 09 fix what the numbers *are*, and 10 fixes how the app
  reaches them.

- **How the app supersedes the scheduled PDF/Chat report path.** In scope (the
  destination includes deciding it), but unspecifiable until there is an app
  shape to supersede *with*. Touches ADR 0010 and 0011, `report_configs`,
  `scheduled_reports.py`, and what replaces the Google Chat card as the "it's
  ready" signal. The map decides this; the implementation effort performs the
  retirement.

- **Whether the forecast path should read the new grain.** Ticket 04 decides how
  the new grain relates to `sales`/`product_sales`. If that answer leaves the
  forecast reading a derived view, there may be follow-on questions about
  whether richer data improves forecasting. Not chartable until 04 closes.

## Out of scope

<!-- ruled beyond the destination; closed, never graduates -->

- **Changes to the forecasting models themselves** — model selection, pinball
  scoring, the Residual Pool, Service Level. ADRs 0001, 0002, 0012, 0013 stand
  as they are. This effort adds a capability alongside forecasting; it does not
  revisit it.

- **The bake / Poolish decision path.** Two-stage Poolish forecasting stays
  exactly as it is.

- **Actually performing the retirement of the scheduled PDF reports.** The map
  decides how the app supersedes them; tearing down a working delivery path is
  the implementation effort's job, not a planning session's.
