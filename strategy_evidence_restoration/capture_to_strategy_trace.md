# Capture To Strategy Trace

## Trace Overview

| Step | Label | Evidence / behavior |
|---|---|---|
| Daily Primary request | STATIC_SOURCE_REVIEW | `/api/daily-primary/run` submits a `daily_primary_strategy` job through `CandidateService.submit_daily_primary_strategy()`. |
| Initial input load | STATIC_SOURCE_REVIEW | The worker loads a saved `MarketInput` or resolves latest expected observation session. |
| Readiness before refresh | STATIC_SOURCE_REVIEW | `evaluate_daily_primary_readiness()` checks session, universe, current price, comparable basis, and trend input gates. |
| Refresh trigger | STATIC_SOURCE_REVIEW | If GLOBAL REQUIRED readiness fails and refresh is allowed, `_daily_primary_incremental_refresh()` invokes scheduler `_capture(..., purpose=DAILY_PRIMARY_CRITICAL)`. |
| Critical capture planning | STATIC_SOURCE_REVIEW | `MainboardPipeline.capture()` enqueues current price, identity/status, market breadth, all-market V2 history sessions, adjustment-factor batches, and historical replay requests. |
| Storage / normalization | STATIC_SOURCE_REVIEW | RequestQueue results are normalized into `MarketInput`; history can be written through `HistoryArtifactStore`; candidate input is registered. |
| Readiness after refresh | STATIC_SOURCE_REVIEW | Daily Primary re-loads the registered input and re-evaluates readiness. |
| Strategy start | STATIC_SOURCE_REVIEW | Only when GLOBAL REQUIRED readiness passes does the worker call `run_active_daily_strategy()`. |
| Stage detection | STATIC_SOURCE_REVIEW | `run_stage_aware_momentum()` evaluates trend state, setup identification, behavior, market compatibility, same-theme context, counterevidence, and final label. |
| Persistence/export | STATIC_SOURCE_REVIEW | Ready runs persist `strategy_experiment_runs`, compact details/summary, trace link, and Daily Primary attempt. |

## Where Evidence Disappeared Before Fix

| Label | Statement |
|---|---|
| ACTUAL_EXECUTION | Latest audited Daily Primary attempt stopped at `NOT_READY` with `strategy_started=false`. |
| ACTUAL_EXECUTION | The audited critical capture had `StructureHistoryCapture` partial: 663 requested, 274 complete, 389 pending. |
| ACTUAL_EXECUTION | Readiness after that partial capture reported `CORE_TREND_INPUT_COVERAGE=0/3040` and `CURRENT_COMPARABLE_PRICE_COVERAGE=1321/3040`. |
| STATIC_SOURCE_REVIEW | The fast-decision evaluator could declare a comparable-price blocker conclusive even while `pending_structure_history=true`. |
| INFERENCE | Evidence disappeared between `DAILY_PRIMARY_CRITICAL` request planning and storage/strategy input creation because the capture returned before structure history settled. |

## Minimal Implementation

| Label | Change |
|---|---|
| STATIC_SOURCE_REVIEW | Updated `stock-fish/stockfish_core/yaogu/daily_primary_fast_decision.py` so `CURRENT_COMPARABLE_PRICE_COVERAGE` is not conclusive while `summary.pending_structure_history` is true. |
| STATIC_SOURCE_REVIEW | Updated focused tests to assert comparable `NOT_READY` is only conclusive after structure history is no longer pending. |
| CODEX_CLAIM | No change was made to `STAGE_AWARE_MOMENTUM_V2`, FOCUS/WATCH rules, thresholds, UNKNOWN semantics, or alpha signals. |

## After-Fix Trace

| Step | Label | Result |
|---|---|---|
| Critical capture | ACTUAL_EXECUTION | Controlled validation ran `DAILY_PRIMARY_CRITICAL` and captured 260 `market_daily` sessions. |
| Structure history | ACTUAL_EXECUTION | Controlled validation had `remaining_structure_history=0`. |
| Readiness | ACTUAL_EXECUTION | Controlled validation `final_status=READY`; blocking requirements were empty. |
| Strategy start | ACTUAL_EXECUTION | Controlled validation had `strategy_started=true` and `strategy_completed=true`. |
| Stage classification | ACTUAL_EXECUTION | Controlled validation evaluated 12 instruments and produced setup counts for START, ACCELERATION, HIGH_LEVEL_REPAIR, and TREND_PULLBACK. |
| Decision artifact | ACTUAL_EXECUTION | Controlled validation persisted run `strategy_experiment_b8f4414ab4cc6b2f910cc624d39efb937fa2f339ed4c241438c5690c4ce6434b` and trace link `trace_e3a536047a7b45c3be461008b8859b22`. |
