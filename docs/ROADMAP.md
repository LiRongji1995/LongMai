# 24-Week Roadmap

Every phase requires versioned evidence, a clean-source test, risk review, and explicit human gate. Estimated agent rounds are not permission to broaden scope.

| Phase / weeks | Goal, inputs, tasks, deliverables | Tests / risks | Gate, stop condition / agent rounds |
|---|---|---|---|
| 0 / 1 | Empty repo → charter, scaffold, policies, CI; deliverable: baseline. | Import/CLI, lint/type/test, Docker; false-completion risk. | Clean source/no secrets; stop on unsafe workspace/check failure. 4–6. |
| 1 / 2–3 | Phase 0 → LEAN/Alpaca audit, minimal backtest, clean-source reproduction, ADRs. | Reproducibility/dependency tests; unverified-platform risk. | Audit evidence; stop if deterministic minimal run fails. 6–10. |
| 2 / 4–6 | Licensed sources → Raw/Clean/Adjusted/Features, calendar/timezone/corporate actions, manifests. | Quality/checksum/split/dividend/PIT tests; bias/licensing risk. | Rebuild from Raw; stop on lineage/quality failure. 10–16. |
| 3 / 7–9 | Governed data → credible backtest, benchmark, fees/spread/slippage, anti-look-ahead. | Temporal/cost/reproducibility tests; optimism risk. | Independent review; stop on leakage/unreproducibility. 10–16. |
| 4 / 10–13 | Credible backtest → trend/relative strength, mean reversion, regimes, walk-forward, stability/OOS. | OOS/sensitivity/regime tests; overfit risk. | Stable OOS evidence; stop on fragile parameters. 14–22. |
| 5 / 14–15 | Candidate signals → portfolio, targets, stock/sector/turnover limits, kill switch, stress. | Limit/rejection/stress tests; bypass risk. | Risk blocks violations; stop on bypass. 8–12. |
| 6 / 16–18 | Approved paper boundary → Alpaca paper adapter, order state, partial/cancel/reject, reconnect, sync/reconcile. | Stateful/fault/restart tests; lifecycle mismatch. | Reconciliation evidence; stop on unknown state. 12–20. |
| 7 / 19–22 | Paper system → continuous paper, shadow mode, comparisons, fill analysis, recovery drills. | Long-run/fault/recovery tests; drift risk. | Stable measured operation; stop on recurrent drift. 12–20. |
| 8 / 23–24 | Phase 7 evidence → live-admission checklist, 5% capital, daily review, rollback, month-one plan. | Gate/kill/reconciliation drills; premature-capital risk. | Formal approval only; stop on any unmet control. 6–10. |

Months 7–12: evaluate real net profitability and stability across regimes, scale only by approved steps, and decide whether to add 30-minute strategies, SIP data, shorting, or a second broker.
