# Architecture Baseline

```text
Market Data → Data Ingestion → Raw Data → Validation/Normalization → Adjusted Data/Features
→ Research/Backtesting → Strategy Signals → Portfolio Construction → Independent Risk Engine
→ Order Management System → Broker Adapter → Paper or Live Broker → Reconciliation/Monitoring/Audit
```

LLMs are excluded from real-time trading. Strategy code cannot bypass the independent risk engine or disable risk/the global kill switch. Live permission is denied by default; paper and live configuration/credentials are isolated. Data, strategy, portfolio, risk, execution, and reconciliation retain module boundaries; deterministic strategy logic is shared where practical between backtest and runtime. Every backtest has a data manifest, and every decision traces to code commit, configuration, data version, strategy version, and risk-rule version. Failures must be explicit, audited, and fail closed—never silent.
