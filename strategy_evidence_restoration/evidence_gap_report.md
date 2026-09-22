# Evidence Gap Report

## Latest Daily Primary Evidence

| Label | Field | Value |
|---|---|---|
| ACTUAL_EXECUTION | Latest audited Daily Primary job | `job_6275fa7eddf14659a82e51b15adb89be` |
| ACTUAL_EXECUTION | Attempt status | `NOT_READY` |
| ACTUAL_EXECUTION | Strategy started | `false` |
| ACTUAL_EXECUTION | Observation date | `2026-09-21` |
| ACTUAL_EXECUTION | Input id | `market_6f214509b1d48ed55fa75e2da15dd2b12ec5476245dab4393bcbec5bf55a665d` |
| ACTUAL_EXECUTION | Blocking requirements | `CURRENT_COMPARABLE_PRICE_COVERAGE`, `CORE_TREND_INPUT_COVERAGE` |

## Gap Classification

| Missing input | Label | Observed gap | Classification | Basis |
|---|---|---|---|---|
| Core trend input coverage | ACTUAL_EXECUTION | 0/3040, below 98% gate | capture not executed | `StructureHistoryCapture` was partial in the audited capture report: 663 requested, 274 complete, 389 pending. |
| Structure history coverage | ACTUAL_EXECUTION | MA and long-slope computability 0/3040 | capture not executed | The all-market history sessions required by V2 remained pending before strategy execution. |
| Comparable price coverage | ACTUAL_EXECUTION | 1321/3040, 43.453947%, below 98% gate | capture not executed | Comparable basis and adjustment-factor evidence coverage matched at 1321/3040; remaining structure/factor work was not fully drained. |
| Required amount history | ACTUAL_EXECUTION | 0/3040 | capture not executed | Amount history is sourced from the same historical `market_daily` rows as the core structure window. |
| Current raw price | ACTUAL_EXECUTION | 3040/3040 | NOT_VERIFIED as a gap | This dependency was available in the latest audited attempt. |
| Eligibility | ACTUAL_EXECUTION | 3040/3040 | NOT_VERIFIED as a gap | This dependency was available in the latest audited attempt. |
| Historical replay | NOT_VERIFIED | Per-instrument replay coverage not found in readiness payload | NOT_VERIFIED | Latest readiness did not expose a complete replay availability table, and this was not the terminal readiness blocker. |
| Market breadth | NOT_VERIFIED | No terminal market-breadth blocker in latest readiness | NOT_VERIFIED | Complete breadth evidence table for the queued latest job was not independently verified. |
| Theme / sector context | ACTUAL_EXECUTION | Latest persisted V2 run had sector context 0/3040 | capture not executed | Critical capture intentionally defers theme context as background research. This is not the immediate Daily Primary READY gate. |
| Funds / capital context | ACTUAL_EXECUTION | Latest persisted V2 run had capital context 0/3040 | capture not executed | Critical capture intentionally defers funds context as background research/enhancement. This is not the immediate Daily Primary READY gate. |
| Strategy lookup | STATIC_SOURCE_REVIEW | No evidence that V2 read the wrong fields | NOT_VERIFIED | Readiness and strategy context both use the same `MarketInput` / history resolver path. |
| Persistence | STATIC_SOURCE_REVIEW | No evidence that completed history rows vanished after persistence | NOT_VERIFIED | The observed failure point is before full capture completion, not after persisted full evidence was loaded. |

## Root Cause

| Label | Statement |
|---|---|
| STATIC_SOURCE_REVIEW | `daily_primary_fast_decision.py` allowed `CURRENT_COMPARABLE_PRICE_COVERAGE` to become a conclusive `NOT_READY` blocker while `pending_structure_history=true`. |
| ACTUAL_EXECUTION | Existing synthetic evidence in the prior fast-decision bundle showed `pending_structure_history=true`, `decision=NOT_READY`, and remaining `StructureHistoryCapture` requests. |
| INFERENCE | The missing strategy inputs disappear at the fast-decision scheduling boundary: Daily Primary can stop the critical refresh before the existing StructureHistoryCapture pipeline finishes producing history/comparable evidence. |
| STATIC_SOURCE_REVIEW | The minimal fix is to keep refreshing while structure history is pending; no V2 formula or decision semantics need to change. |

## After-Fix Classification

| Label | Statement |
|---|---|
| ACTUAL_EXECUTION | Focused regression `tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py` passed after the fix. |
| ACTUAL_EXECUTION | Controlled validation captured 260 `market_daily` sessions, left `remaining_structure_history=0`, reached `READY`, started strategy, and persisted a decision artifact. |
| CODEX_CLAIM | This restores evidence availability for the existing strategy path; it does not claim market edge, stock-selection improvement, or profitability. |
