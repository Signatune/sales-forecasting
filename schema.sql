-- Postgres schema for the Sales pipeline (ADR 0003).
--
-- Two tables: the raw Toast responses that are the replay/audit safety net
-- data/raw/ used to provide, and the canonical Sales history. Applying this
-- file is idempotent: every statement is IF NOT EXISTS, so running it against
-- an already-set-up database changes nothing and is safe.

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

-- Canonical Sales: one row per (product, date). The uniqueness is load-bearing
-- --- ADR 0004's daily job re-pulls the same business date on three
-- consecutive days and must replace that day's row rather than accumulate
-- duplicates. Writers upsert with ON CONFLICT (product, date) DO UPDATE.
CREATE TABLE IF NOT EXISTS sales (
    product  text             NOT NULL,
    date     date             NOT NULL,
    quantity double precision NOT NULL,
    PRIMARY KEY (product, date)
);

-- RLS with no policies, as for raw_toast_responses above: closes the Data API
-- off from the Sales history while the pipeline's `postgres` role bypasses it.
ALTER TABLE sales ENABLE ROW LEVEL SECURITY;
