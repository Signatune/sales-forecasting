# Backfill orders history at the new grain

Type: task
Status: open
Blocked by: 05, 06

## Question

Nothing to decide — do the work. Re-pull Toast orders as far back as ticket 06
established the window reaches, and load them at the grain ticket 03 defined.

Also on a rolling clock: the window's far edge moves forward daily, so history
not pulled is history lost.

- A one-shot backfill script, in the spirit of the retired `migrate.py`, not a
  change to the daily path.
- Apply ticket 03's PII allowlist to the backfill exactly as the daily capture
  does. A backfill that persists fields the daily path strips would be the worst
  possible outcome — do not let the two diverge; share the code.
- Idempotent and resumable: it will be long-running and will fail partway.
- Reconcile against what is already known. Existing `sales` rows cover this
  period at the aggregate grain; the backfilled data rolled up to that grain
  should match. **Investigate any discrepancy before declaring done** — a
  mismatch means either the backfill or the existing history is wrong, and both
  matter.

Resolved when history is loaded and reconciled. Record in the answer: the date
range actually loaded, row counts, storage consumed, the reconciliation result
against existing `sales`, and any period that could not be recovered.
