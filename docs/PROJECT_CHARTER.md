# Project Charter

## Mandate

This is a 24-week engineering closed loop followed by a 12-month profitability-validation plan for a reproducible, auditable US equity/ETF system. The aim is trustworthy data, point-in-time backtesting, interpretable research, independent risk, correct order lifecycle, reconciliation, and recovery—not a flattering backtest curve. Target users are the governed research, operations, and engineering team.

Version one updates observations every minute, makes principal signals hourly/daily, holds 2–10 trading days, and trades liquid US stocks and major ETFs only. It is long-only, unlevered, and excludes options and second-level trading. Commercial logic is risk-adjusted net return, not gross backtest return.

## Success, non-goals, and risks

Required measures: annualized net return, benchmark excess return, maximum drawdown, volatility, Sharpe, Sortino, turnover, slippage, fill rate, capacity, performance by market regime, and backtest/paper/live divergence. Success requires trustworthy data, reproducible/no-look-ahead backtests, acceptable out-of-sample results, stable paper operations, correct reconciliation, and risk controls that block.

Non-goals: guaranteed profitability, unrestricted universe, leverage, shorting, options, HFT, or an LLM in the order path. Primary risks are data bias/corporate actions, model overfit, execution divergence, API outage, time/config error, credential compromise, and governance failure.

## Governance and gates

Each phase has documented inputs, tests, stop conditions, evidence, and human approval. A failed gate pauses progression; a risk/control failure stops opening risk until investigated. Live operation requires separate approval after sustained paper/shadow evidence, reconciliation, kill-switch tests, least-privilege credentials, rollback rehearsal, and explicit capital authorization. The 24-week objective is an evidence-backed controlled-system baseline; months 7–12 evaluate real risk-adjusted net performance across regimes. Terminate or pause for unbounded control failures, unreconcilable accounts/data, sustained out-of-sample deterioration, material security incident, or inability to meet gate evidence.
