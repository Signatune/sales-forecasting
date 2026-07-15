# Route every Sales reader through a single loader

Status: ready-for-agent
Branch: `postgres-daily-ingestion`

## Parent

`docs/adr/0003-toast-ingestion-moves-to-scheduled-github-actions-and-postgres.md`

## What to build

Today each of `forecast.py`, `backtest.py`, `model_comparison.py` and
`inspection_page.py` opens `data/sales_history.parquet` itself. ADR 0003 moves
the Sales history into Postgres, and with four independent readers that swap
would have to land in four places at once.

Put one function in front of the Sales history — a single "give me the Sales
history" seam that returns the canonical `(product, date, quantity)` frame — and
have all four scripts call it instead of reading the file. It stays
parquet-backed for now. This is a prefactor: no behaviour changes, no numbers
change, and it is what lets ticket 04 switch the whole project to Postgres by
changing one function.

Demoable: the forecast, the backtest, the model comparison and the inspection
page all still produce exactly the output they produce today, and no script
outside the loader mentions parquet or the history's path.

## Acceptance criteria

- [ ] One shared loader returns the canonical Sales history; the path to the parquet file is named in exactly one place
- [ ] `forecast.py`, `backtest.py`, `model_comparison.py` and `inspection_page.py` all obtain Sales through it
- [ ] Outputs are unchanged: the Demand Forecast, Sales Forecast, backtest metrics and rendered pages match a pre-change run
- [ ] Existing tests pass, with the tests that stub a Sales history now stubbing the loader
- [ ] `normalize.py` still writes the parquet file as it does today — this ticket only changes the read side

## Blocked by

- None — can start immediately.
