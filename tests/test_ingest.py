"""The single command of ticket 01 must drive both Sales sources.

normalize.py merges Analytics week reports (authoritative) with orders-derived
aggregates (fast backfill past the Analytics lookback cap). Running only one of
them leaves the merge with nothing to merge.
"""
import pytest

import ingest


def test_pulls_both_sources_then_normalizes(monkeypatch):
    calls = []
    monkeypatch.setattr(ingest.toast_orders, "pull_orders_history",
                        lambda: calls.append("orders"))
    monkeypatch.setattr(ingest.toast_client, "pull_history",
                        lambda: calls.append("analytics"))
    monkeypatch.setattr(ingest.normalize, "main", lambda: calls.append("normalize"))

    ingest.main()

    assert calls == ["orders", "analytics", "normalize"]


def test_normalize_is_skipped_when_a_pull_fails(monkeypatch):
    """A half-pulled history must not silently overwrite sales_history.parquet."""
    def boom():
        raise RuntimeError("Toast changed its shape")

    monkeypatch.setattr(ingest.toast_orders, "pull_orders_history", lambda: None)
    monkeypatch.setattr(ingest.toast_client, "pull_history", boom)
    monkeypatch.setattr(ingest.normalize, "main",
                        lambda: pytest.fail("normalize ran after a failed pull"))

    with pytest.raises(RuntimeError, match="Toast changed its shape"):
        ingest.main()
