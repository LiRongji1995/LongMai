# US Equity Quantitative Trading System

**A reproducible, auditable foundation for medium/low-frequency US equity and ETF research and controlled trading.**

## Status and safety

**Phase 0 / Scaffold Only.** This repository has no strategy, LEAN integration, Alpaca integration,
market data, broker connection, order path, Paper Trading approval, or Live Trading approval.

- Not investment advice; profitability is neither promised nor validated.
- Real trading is denied by default: `PAPER_TRADING_ENABLED=false`, `LIVE_TRADING_ENABLED=false`,
  and `LIVE_ORDER_SUBMISSION_ALLOWED=false`.
- LLMs may help development/research/post-trade analysis, never real-time decision or order flow.

## Product scope

The planned first version updates market data every minute, produces primary signals every 60 minutes
or daily, and targets 2–10 trading-day holding periods. It is limited to liquid US stocks and major
ETFs, long-only, unlevered, with no options or second-level trading. Historical backtesting precedes
Paper Trading, which precedes separately approved small-capital live operation.

Not included: a current trading strategy, auto-selection algorithm, parameter optimization, bulk data,
profit claims, completed LEAN/Alpaca support, or any live/paper order submission.

## Architecture

`Market Data → Ingestion → Raw → Validation/Normalization → Adjusted/Features → Research/Backtesting → Signals → Portfolio → Independent Risk → OMS → Broker Adapter → Paper/Live Broker → Reconciliation/Monitoring/Audit`

Subsystems are separate and strategies cannot bypass/disable risk. See [architecture](docs/ARCHITECTURE.md).

## Layout

`src/quant_system` contains module boundaries; `tests` contains baseline tests; `config/examples`
contains non-secret examples; `docs` contains governance; `data` is documentation-only; `docker` and
`.github` hold development/CI scaffolding.

## Requirements and local install

Python **3.12** is required. On PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m quant_system.cli status
```

Run checks with `.\scripts\check.ps1`, or run Ruff, mypy, pytest, build, and the CLI individually.
The CLI is offline and reports project/version/phase plus both trading switches as `false`.

## Docker

```powershell
docker compose build app
docker compose run --rm app
docker compose --profile database up -d postgres
```

The database profile is a future development service only: it does not ingest data or enable trading.

## Configuration and secrets

Copy `.env.example` to untracked `.env` only when needed. It contains placeholders only. Development,
paper, and live examples are separate; the live example still denies order submission. Never commit
keys, logs, data, API responses, database files, caches, or reports.

## Documentation

- [Charter](docs/PROJECT_CHARTER.md), [24-week roadmap](docs/ROADMAP.md), [architecture](docs/ARCHITECTURE.md)
- [Risk policy](docs/RISK_POLICY.md), [data governance](docs/DATA_GOVERNANCE.md), [testing](docs/TESTING_STRATEGY.md)
- [Security](docs/SECURITY.md), [glossary](docs/GLOSSARY.md), [ADR 0001](docs/adr/0001-initial-architecture.md)
- [Agent constitution](AGENTS.md), [contributing](CONTRIBUTING.md), [reports](docs/reports/README.md)

The 24-week plan moves from this baseline through foundation audit, governed data, realistic backtests,
out-of-sample research, independent risk, Paper Trading execution, shadow operation, and a live-admission
review. Months 7–12 validate risk-adjusted net results and stability. Next: **Phase 1—LEAN foundation
audit and minimal clean-source backtest validation**.

## Contribution and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md). Run all checks, retain safety
defaults, report residual risk, and never force-push. Code is [Apache-2.0](LICENSE); this does not grant
rights to third-party data, dependencies, broker services, or redistribution of market data.
