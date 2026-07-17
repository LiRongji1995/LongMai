# Testing Strategy

The pyramid comprises unit, property, integration, end-to-end, backtest-realism, data-quality, fault-injection, restart-recovery, reconciliation, and clean-source tests. Phase 0 implements only import/default/CLI/repository baseline tests.

Later tests must cover look-ahead, time-range and timezone errors, split errors, duplicate/missing bars, duplicate orders, partial fills, out-of-order events, reconnects, stale quotes, position/balance mismatch, risk bypass, accidental live enablement, configuration mixing, and secret leakage. CI must remain clean-source, deterministic, credential-free, broker-free, and data-download-free.
