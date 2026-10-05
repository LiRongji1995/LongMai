> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../CURRENT_STATUS.md)；原路径可在[归档索引](README.md)查找。

# Daily Primary Fast Decision v1 Report

Date: 2026-09-22 Asia/Shanghai.

## A. Verdict

`DAILY_PRIMARY_FAST_DECISION_V1 = PASS`

Scope delivered: Fast Decision boundary, conservative early `NOT_READY`, and preserved Daily Primary correctness.  This is not provider optimization, strategy tuning, score work, market-edge work, Go work, or memory work.

## B. Architecture

Before: normal latest Daily Primary did `BEFORE_REFRESH readiness -> synchronous DAILY_PRIMARY_CRITICAL refresh -> AFTER_REFRESH readiness -> READY/NOT_READY`.

After: `MainboardPipeline.capture(..., purpose="DAILY_PRIMARY_CRITICAL")` runs an evidence-sufficiency checkpoint during critical refresh.  The checkpoint reuses the existing `evaluate_daily_primary_readiness(...)` result and only decides whether waiting can still change a required gate.  It writes `daily_primary_fast_decision` into the capture report, and `CandidateService._daily_primary_incremental_refresh()` carries that metadata into `refresh_actions`.

Implemented v1 early boundary: if `CURRENT_COMPARABLE_PRICE_COVERAGE` is below gate and pending adjustment-factor batches cannot mathematically raise full-market/per-exchange coverage to the gate, the pipeline returns early with `NOT_READY`.  If pending factor evidence could still repair the gate, refresh continues.

## C. Correctness

Unchanged: READY gate, NOT_READY semantics, eligibility/status rules, PIT visibility, source receipts, comparable basis, Stage Aware V2 windows, strategy semantics, persistence/export requirements, Focus/Watch/Rejected/Unknown, score, and `MARKET_EDGE=NOT_ESTABLISHED`.

Strategy still starts only after readiness passes.  Early `NOT_READY` records `strategy_started=false` and `recommendation_published=false`.

## D. Real Runtime Evidence

Before actual reference from prior Daily Primary Acceptance v2: request to `NOT_READY` was about `1807.968s`; critical refresh was about `1399.242s`.

After closest-runtime evidence from isolated TEST_FIXTURE checkpoint scenario: `test_fast_decision_early_not_ready_stops_before_long_history_maintenance` completed in `2.85s`, returned `NOT_READY`, left long-history `StructureHistoryCapture` requests pending, and did not start strategy.

Live provider E2E was not rerun in this task, to avoid another long external capture.  The evidence is therefore correctness and closest-runtime synthetic coverage, not a new production SLA claim.

## E. Remaining Issues

- Provider latency and baostock factor transport remain outside this task.
- Core trend early-impossibility can be refined further; v1 is conservative.
- Historical replay remains available for audit/maintenance and is not deleted.
- No strategy, score, Enhanced Evidence, market-edge, Go, or memory changes were made.

## Verification

- `python -m py_compile stock-fish/stockfish_core/yaogu/daily_primary_fast_decision.py stock-fish/stockfish_core/yaogu/data_pipeline.py stock-fish/stockfish_core/yaogu/candidate_service.py stock-fish/stockfish_core/yaogu/scheduler.py`
- `pytest stock-fish/tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py -q` -> `5 passed`
- `pytest stock-fish/tests/r05_product/test_daily_primary_reliability_v1.py -q` -> `11 passed`
- `pytest stock-fish/tests/r05_product/test_r14_daily_fast_path.py -q` -> `8 passed`
- Combined targeted run -> `24 passed in 73.11s`
