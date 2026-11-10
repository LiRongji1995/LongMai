# StockFish — Personal A-Share Short-Term Screening

Local, single-user tool for SSE/SZSE main-board short-term trading support: daily data, a pre-market
list (mainline leaders, 连板梯队, 观察, 高波动) with a market-temperature read
and hit-rate review. The 09:25 call auction is read by the person, not the tool. It never connects to a broker or places orders: a person decides every trade.

```sh
bin/sf premarket     # after 18:00: list and temperature for the next session
bin/sf crowding      # short-term money temperature, last N sessions
bin/sf evaluate      # entry-free hit rates of the screening and the temperature calls
bin/sf --help
```

- [User guide](USER_GUIDE.md) — commands, outputs, data store.
- [Agent rules](AGENTS.md) — point-in-time, explicit missing data, reproducible results.
- [Current screening spec (V7)](docs/STRATEGY_LONGMAI_V7.md) and [architecture review](docs/ARCHITECTURE_REVIEW_2026-11-10.md).
- [Changelog](CHANGELOG.md)

Setup: Python 3.12, `python3.12 -m venv ../stockfish-env && ../stockfish-env/bin/pip install -r stock-fish/requirements.txt`.
Tests: `cd stock-fish && ../../stockfish-env/bin/python -m pytest tests/sf`.

The legacy R2/qlib pipeline and the V1–V6 strategy code are preserved at git tag `legacy-r2`.
Licenses: [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md).
