# Deliver: upload to Drive, post a Google Chat card

Status: ready-for-agent
Blocked by: 03

## Parent

`.scratch/scheduled-forecast-reports/PRD.md`

## What to build

A new module `report_delivery.py`.

```
upload_pdf(pdf, filename, folder_id, credentials) -> str   # a shareable link
post_card(payload, link, webhook_url)             -> None
post_refusal(refusal, webhook_url)                -> None
```

### Destinations are symbolic

A report's `config["delivery"]` names a destination; the caller resolves it to
`CHAT_WEBHOOK_<NAME>` and `DRIVE_FOLDER_<NAME>` (upper-cased, hyphens to
underscores) through `env.py`. A Chat webhook URL is a bearer credential and does
not belong in Postgres (ADR 0010). A name that resolves to nothing is a loud
error, not a skipped delivery — a report that silently goes nowhere looks exactly
like one that was never due.

### Drive

The service account has **no storage of its own**, so uploads must name a parent
folder shared to it from a real account; the file lives in that account's space
and inherits its sharing. Never create files without a parent.

One file per Report Window, named `<report-slug>-<window-start>.pdf`, never
overwritten — an old Chat link must still open the week it announced (ADR 0010).

### The Chat card

A `cardsV2` message quoting the **headline model only** (ADR 0011): the Report
Window, the origin and config version, the window total, the busiest and quietest
days, the overall direction against typical, and a button linking to the PDF.

**No attachment, and no chart image.** Incoming webhooks cannot upload media —
that needs `media.upload` under OAuth user credentials, unreachable from a
webhook's `key`/`token` authorization. Card images must be HTTPS-hosted PNG/JPG,
so the chart cannot be inlined either. Do not spend time trying; this is a fixed
property of webhooks, recorded in ADR 0010.

Webhooks are limited to **1 request per second per space** — deliver reports
sequentially with a small gap, not concurrently.

### Refusals

`post_refusal` posts plain text, not a card: it must be legible when something is
already broken. It states which report, which failure, and the fix — re-run the
forecast workflow, or repoint the row at the active config version.

## Tests

A new `tests/test_report_delivery.py`, with the HTTP and Drive clients passed in
as seams (the pattern `daily_forecast.main` uses for `connect` and `load_sales`):

- the card's payload contains the headline model's totals and the link, and no
  other model's numbers
- an unresolvable delivery name raises rather than returning quietly
- the upload always names a parent folder
- filenames derive from the window start, and two different windows never collide
- a refusal posts text, and its message names the failing report and the fix
