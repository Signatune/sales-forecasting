-- Postgres schema for the Sales pipeline (ADR 0003, ADR 0005).
--
-- The raw Toast responses that are the replay/audit safety net data/raw/ used
-- to provide, plus canonical Sales as a source-to-product dimensional model
-- (ADR 0005): a fine-grained fact of every configured thing sold, a many-to-one
-- map from those sources up to the Products we forecast, and a view that rolls
-- the fact up through the map to the (product, date, quantity) frame the readers
-- consume. Applying this file is idempotent: every statement is IF NOT EXISTS or
-- CREATE OR REPLACE, so running it against an already-set-up database changes
-- nothing and is safe.

-- Raw Toast responses, stored verbatim as jsonb. This is the replay/audit
-- safety net: normalization can be rerun against a saved response without
-- re-hitting Toast (the modifierGuid bug in the original ingest work was caught
-- exactly this way). A row records what was captured, for which restaurant and
-- business date, and when it was fetched.
CREATE TABLE IF NOT EXISTS raw_toast_responses (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    restaurant_guid text        NOT NULL,
    business_date   date        NOT NULL,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    response        jsonb       NOT NULL
);

-- Look up a business date's saved responses when replaying normalization.
CREATE INDEX IF NOT EXISTS raw_toast_responses_business_date_idx
    ON raw_toast_responses (business_date);

-- These tables live in `public`, which Supabase exposes through its Data API.
-- Enable RLS with no policies: the pipeline connects as the `postgres` role
-- (which bypasses RLS), so its reads and writes are unaffected, while the
-- Data API's `anon`/`authenticated` roles get no access to this business data.
-- ENABLE ROW LEVEL SECURITY is idempotent, so re-applying the schema is safe.
ALTER TABLE raw_toast_responses ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- Canonical Sales as a source-to-product model (ADR 0005)
-- ---------------------------------------------------------------------------

-- products: the canonical Products we aggregate and forecast (plain, sesame,
-- …, and whatever comes later). `name` is the identity the forecast reads;
-- product_sales exposes it as the `product` column.
CREATE TABLE IF NOT EXISTS products (
    id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE
);

ALTER TABLE products ENABLE ROW LEVEL SECURITY;

-- product_sources: the many-to-one map from a sold thing to a Product — this is
-- normalize.py's BAGEL_MODIFIER_NAMES promoted from code into data. Each row is
-- a (source_type, source_name) pointing at one Product; many sources map to one
-- Product (several modifier spellings after in-place renames, an item and a
-- modifier that mean the same thing, two locations' variants). The
-- (source_type, source_name) primary key is what makes the map many-to-one:
-- a source belongs to at most one Product. `source_name` is stored in the same
-- normalized (stripped, lower-cased) form normalize.py matches on, so a Product
-- spans every historical spelling of its modifiers.
CREATE TABLE IF NOT EXISTS product_sources (
    product_id  bigint NOT NULL REFERENCES products (id),
    source_type text   NOT NULL CHECK (source_type IN ('item', 'modifier')),
    source_name text   NOT NULL,
    PRIMARY KEY (source_type, source_name)
);

-- Roll the fact up per Product: the view groups product_sources by Product.
CREATE INDEX IF NOT EXISTS product_sources_product_id_idx
    ON product_sources (product_id);

ALTER TABLE product_sources ENABLE ROW LEVEL SECURITY;

-- sales: the fact. One row per (date, restaurant, source_type, source_name,
-- quantity) — every *configured* thing sold, at both Toast grains (`source_type`
-- is `item` or `modifier`), per location. "Configured" means it carries a Toast
-- GUID; free text a guest or server typed on a check is excluded upstream, as
-- normalize.py excludes it today. The fact keeps every configured source, not
-- just the mapped ones — an unmapped source sits here untracked by any Product
-- until someone maps it (ADR 0005).
--
-- The (date, restaurant_guid, source_type, source_name) primary key is
-- load-bearing: ADR 0004's daily job re-pulls the same business date on three
-- consecutive days and must replace those rows rather than accumulate
-- duplicates. Writers upsert with ON CONFLICT on this key DO UPDATE.
CREATE TABLE IF NOT EXISTS sales (
    date            date             NOT NULL,
    restaurant_guid text             NOT NULL,
    source_type     text             NOT NULL CHECK (source_type IN ('item', 'modifier')),
    source_name     text             NOT NULL,
    quantity        double precision NOT NULL,
    PRIMARY KEY (date, restaurant_guid, source_type, source_name)
);

ALTER TABLE sales ENABLE ROW LEVEL SECURITY;

-- product_sales: the rollup. The fact joined through product_sources to
-- products and summed to (product, date, quantity) — across locations
-- (restaurant_guid drops out) and across a Product's sources. This is the exact
-- frame the readers consume, so switching them onto it (ticket 04) changes
-- numbers nowhere. The join is inner, so unmapped sources in the fact do not
-- appear until they are mapped. CREATE OR REPLACE keeps this idempotent.
CREATE OR REPLACE VIEW product_sales AS
    SELECT p.name  AS product,
           s.date  AS date,
           SUM(s.quantity) AS quantity
    FROM sales s
    JOIN product_sources ps
        ON ps.source_type = s.source_type
       AND ps.source_name = s.source_name
    JOIN products p
        ON p.id = ps.product_id
    GROUP BY p.name, s.date;

-- Run the view with the querying role's own permissions rather than the view
-- owner's, so the base tables' RLS still closes the Data API off from it — a
-- plain (definer) view would otherwise leak the underlying rows to `anon`.
ALTER VIEW product_sales SET (security_invoker = true);
