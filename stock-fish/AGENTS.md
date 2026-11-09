# Python Code Rules

Follow the [root agent rules](../AGENTS.md).

- `sf/` is the active implementation (data store, strategy, pre-market list, auction, review, backtest).
- `scripts/` and `stockfish_core/` are the frozen legacy pipeline. Do not add features there; read-only use
  for historical results only.
- New tests go in `tests/sf/`, offline, synthetic data only.
