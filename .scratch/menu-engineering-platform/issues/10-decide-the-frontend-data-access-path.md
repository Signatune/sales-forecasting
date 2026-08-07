# Decide how the Next.js app reaches the data

Type: grilling
Status: open

## Question

A Next.js app needs to read data that a Python pipeline owns. By what path?

The current posture is deliberately closed. `schema.sql` enables Row Level
Security on every table with **no policies at all**, and its comment says why:
the pipeline connects as `postgres` (which bypasses RLS), while Supabase's Data
API roles `anon`/`authenticated` get no access to business data. `product_sales`
is explicitly `security_invoker = true` so a view cannot leak around it. Opening
a read path means deliberately unpicking that, and it should be unpicked
knowingly rather than by whatever the app framework makes easiest.

Weigh at least:

- **Supabase Data API + RLS policies.** Idiomatic for the stack, and the client
  library is well-trodden. Means authoring RLS policies as the real access
  control surface, and reasoning about what `authenticated` can see. The whole
  security model then lives in policies, which no one currently maintains.
- **Direct Postgres from Next.js server components / route handlers.** The app
  holds a privileged connection string and does its own authorization in
  application code. Simpler to reason about, concentrates trust in the app, and
  needs connection pooling thought through (note: `DATABASE_URL` must use the
  Session pooler — the direct host is IPv6-only).
- **A Python API in front.** Reuses the existing `db.py` query layer and keeps
  one language owning the schema, at the cost of another deployed service.

Also decide:

- **Where menu engineering figures are computed** — in Postgres (view /
  materialized view), in the Python pipeline as a precomputed table, or in the
  app at request time. This is really a question about whether a figure is a
  stored fact or a derived read, and it interacts with ticket 09's
  point-in-time-vs-trend answer.
- **Whether the app ever writes.** The mapping review tool
  (`menu_engineering/mapping_review.html`) is a write surface today, and
  `forecast_configs` has a comment anticipating "a future frontend" flipping
  `is_active`. If the app writes, the access path has to carry that too.

Leave an ADR — this is the app's foundational architectural decision.
