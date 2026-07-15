"""The single Sales-history read seam: it returns whatever normalize.py wrote,
from the one path this module names."""
import pandas as pd

import sales_history


def test_loads_the_frame_written_at_the_history_path(tmp_path, monkeypatch):
    history_path = tmp_path / "sales_history.parquet"
    frame = pd.DataFrame(
        {"product": ["plain"], "date": pd.to_datetime(["2026-07-05"]), "quantity": [10.0]}
    )
    frame.to_parquet(history_path, index=False)
    monkeypatch.setattr(sales_history, "SALES_HISTORY_PATH", history_path)

    loaded = sales_history.load_sales_history()

    pd.testing.assert_frame_equal(loaded, frame)
