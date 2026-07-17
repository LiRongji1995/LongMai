# Risk Policy

Default state: `LIVE_TRADING_ENABLED=false`, `PAPER_TRADING_ENABLED=false`, and `LIVE_ORDER_SUBMISSION_ALLOWED=false`. Live operation requires independent, explicit multiple gates—not one Boolean.

Version-one policy is long-only, unlevered, no options, no illiquid or halted securities, no order on stale market data or unreconciled account. Initial hypotheses (not investment advice): single-name maximum 5%, sector maximum 25%, daily turnover/loss limits, maximum drawdown threshold, per-order notional limit, daily traded-notional limit, maximum position count, blocked list, global kill switch, and audited risk rejection.

Capital path: Paper 0%; Live Stage A 5% planned capital; Stage B 10–15%; Stage C 25–30%; stable stage only after formal review. Live checklist requires approved strategy/OOS evidence, sustained paper/shadow operations, reconciliation, monitored kill switch, incident/restart drills, scoped credentials, rollback plan, and human sign-off. Reduce or pause opening risk on limit breach, data anomaly, stale quote, reconciliation mismatch, broker/API/time anomaly, or untested restart. Globally stop on unknown account/order state; recovery requires configuration validation, state reload, broker/account reconciliation, audit record, and explicit approval.
