# StockFish — Personal A-Share Short-Term Research

Local, single-user tool for SSE/SZSE main-board short-term research: daily data, a pre-market list,
a 09:25 auction re-rank, forward review and honest out-of-sample backtests. It never connects to a
broker or places orders.

```sh
bin/sf premarket     # after 18:00: list for the next session (name, turnover, advice)
bin/sf auction       # 09:25-09:30: re-rank with call-auction data
bin/sf review        # how did saved lists actually do (T+1-open entry, costs)
bin/sf research      # test a rule out of sample, per day
bin/sf --help
```

- [User guide](USER_GUIDE.md) — commands, outputs, data store, strategy status.
- [Agent rules](AGENTS.md) — three invariants: point-in-time, explicit missing data, reproducible results.
- [Project review 2026-11-09](docs/REVIEW_2026-11-09_ARCHITECT_AND_TRADER.md) — why the lean CLI replaced the legacy pipeline.
- [Changelog](CHANGELOG.md)

Setup: Python 3.12, `python3.12 -m venv ../stockfish-env && ../stockfish-env/bin/pip install -r stock-fish/requirements.txt`.
Tests: `cd stock-fish && ../../stockfish-env/bin/python -m pytest tests/sf`.

The legacy pipeline (`stock-fish/scripts/codex_daily_v2.py`) is frozen; its documents
(`CURRENT_STATUS.md`, `CURRENT_STRATEGY.md`, `DEPLOYMENT.md`, `ENGINEERING_BASELINE.md`) are kept for
reading historical results. Licenses: [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md).
