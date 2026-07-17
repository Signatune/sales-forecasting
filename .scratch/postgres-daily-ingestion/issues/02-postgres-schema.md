# Stand up the Postgres schema

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`
`docs/adr/0005-canonical-sales-is-a-source-to-product-model.md`

> Reopened: the first pass shipped a single pre-aggregated
> `sales(product, date, quantity)` table. ADR 0005 replaces that with a
> source-to-product dimensional model so we can track every item and modifier we
> sell, not just the seven bagels. The `raw_toast_responses` table it already
> shipped is unchanged; this ticket now also stands up the mapping tables, the
> finer-grained fact, and the rollup view.

## What to build

A managed Postgres database and the tables ADR 0003 and ADR 0005 call for:

- **Raw Toast responses**, stored as `jsonb` (unchanged from the first pass).
  This is the replay and audit safety net that `data/raw/` used to provide — the
  `modifierGuid` bug in the original ingest ticket was caught by rerunning
  normalization against saved raw responses without re-hitting Toast, and that
  has to stay possible. A row records what was captured, for which restaurant and
  business date, and when it was fetched.
- **Canonical Sales as a dimensional model** (ADR 0005), replacing the single
  pre-aggregated table:
  - `products` — the canonical Products we aggregate and forecast (plain, sesame,
    …, and whatever comes later).
  - `product_sources` — the many-to-one map from a sold thing to a Product. Each
    row is a `(source_type, source_name)` — `source_type` is `item` or
    `modifier` — pointing at one `products` row, unique on `(source_type,
    source_name)`. This is `BAGEL_MODIFIER_NAMES` promoted from code into data.
  - `sales` — the fact, one row per `(date, restaurant, source_type,
    source_name, quantity)`. The `(date, restaurant, source_type, source_name)`
    primary key is load-bearing: ADR 0004's daily job re-pulls the same business
    date on three consecutive days and must replace those rows rather than
    accumulate duplicates.
  - `product_sales` — a view that rolls the fact up through `product_sources` to
    `(product, date, quantity)`, summed across locations and across a Product's
    sources. This is the exact frame the readers consume, so the switch in
    ticket 04 changes numbers nowhere.

The connection comes from a single environment variable so the same code runs
from a laptop and from a GitHub Actions runner. Applying the schema is a command
someone can run, and running it against an already-set-up database is harmless.

Demoable: apply the schema to an empty database. Seed one Product with two source
mappings. Write the same `(date, restaurant, source)` twice with different
quantities and read back one fact row carrying the second quantity. Write two
different sources that map to the same Product on the same date, and read the
`product_sales` view back as one row carrying their summed quantity.

## Acceptance criteria

- [ ] A managed Postgres instance exists and its connection string is read from the environment (never committed)
- [ ] Applying the schema creates the raw table, `products`, `product_sources`, the `sales` fact and the `product_sales` view, and is safe to re-run
- [ ] The `sales` fact enforces one row per `(date, restaurant, source_type, source_name)`; a repeat write of the same key replaces its quantity
- [ ] `product_sources` maps many sources to one Product; `product_sales` sums the fact through the mapping to `(product, date, quantity)` across locations and sources
- [ ] Raw responses are stored as `jsonb` and can be read back and re-normalized without contacting Toast
- [ ] Local setup — how a developer points at a database and applies the schema — is written down (`docs/postgres.md`)

## Blocked by

- None — can start immediately.
