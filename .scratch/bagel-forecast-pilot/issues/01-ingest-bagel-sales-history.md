# Ingest bagel Sales history from Toast

Status: ready-for-agent
Blocked by: None — can start immediately

## Parent

`.scratch/bagel-forecast-pilot/PRD.md`

## What to build

Running one command pulls the full available daily Sales history for the bagel varieties from the Toast Analytics API and leaves behind two artifacts: timestamped raw API responses (so normalization can be rebuilt and debugged without re-hitting the API) and a normalized Sales history of canonical (Product, Date, Quantity) records ready for forecasting.

This ticket absorbs the pilot's unknowns: discovering the actual response shape, whether the API caps how far back a single pull can reach (paginate/loop if so), and which Toast menu items map to our bagel Products. Fail loudly on auth errors or an unexpected response shape — surfacing "Toast changed its shape" is valuable; silently papering over it is not.

Use the domain vocabulary from `CONTEXT.md`: these are `Sales` records for `Products` — not orders, items, or SKUs.

## Acceptance criteria

- [ ] A single command authenticates against Toast and pulls the full available daily Sales history for the bagel family, looping/paginating past any lookback cap
- [ ] Raw responses saved timestamped under a raw-data directory
- [ ] Normalized Sales history written as one record per (Product, Date, Quantity)
- [ ] The confirmed list of bagel varieties (as Toast exposes them) is recorded in this ticket's Comments
- [ ] A unit test locks the normalization against a saved real sample response, so future shape drift breaks loudly
- [ ] Auth failures and unexpected response shapes raise clear errors rather than producing partial/empty output

## Blocked by

- None — can start immediately
