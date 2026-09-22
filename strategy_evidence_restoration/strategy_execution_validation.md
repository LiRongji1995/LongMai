# Strategy Execution Validation

## Validation Scope

| Label | Statement |
|---|---|
| ACTUAL_EXECUTION | A controlled Daily Primary run was executed with isolated SQLite/state under `D:\stockfish-qlib-quant-system\work\strategy_evidence_restoration_validation`. |
| ACTUAL_EXECUTION | The controlled input used `TEST_FIXTURE` data and did not touch the production database. |
| CODEX_CLAIM | This validation proves execution correctness for complete inputs, not strategy performance. |

## Execution Chain

| Required checkpoint | Label | Result |
|---|---|---|
| READY | ACTUAL_EXECUTION | `final_status=READY` |
| strategy_started=true | ACTUAL_EXECUTION | `strategy_started=true` |
| stage classification executed | ACTUAL_EXECUTION | 12 instruments evaluated; setup counts persisted for all four setup families. |
| decision artifact generated | ACTUAL_EXECUTION | `strategy_experiment_runs` document persisted and export validation status `COMPLETE`. |

## Run Identity

| Label | Field | Value |
|---|---|---|
| ACTUAL_EXECUTION | run_id | `strategy_experiment_b8f4414ab4cc6b2f910cc624d39efb937fa2f339ed4c241438c5690c4ce6434b` |
| ACTUAL_EXECUTION | trace_id | `trace_e3a536047a7b45c3be461008b8859b22` |
| ACTUAL_EXECUTION | job_id | `job_4efdeebb952048f98fdbde3b78716077` |
| ACTUAL_EXECUTION | input_id | `market_ea63aa2841c9aadc4d849214d2e4c87624dd864879a81bd8f02862314ab89c91` |
| ACTUAL_EXECUTION | attempt_id | `daily_primary_attempt_099597f5040b5f6defa26c93703f571ef95f58e15e44dd9365907dc55940748e` |

## Counts

| Label | Metric | Value |
|---|---|---|
| ACTUAL_EXECUTION | eligible | 12 |
| ACTUAL_EXECUTION | evaluated | 12 |
| ACTUAL_EXECUTION | FOCUS count | 0 |
| ACTUAL_EXECUTION | WATCH count | 0 |
| ACTUAL_EXECUTION | UNKNOWN count | 0 |
| ACTUAL_EXECUTION | REJECTED count | 12 |

## Stage Counts

| Stage | PASS | FAIL | UNKNOWN | Label |
|---|---:|---:|---:|---|
| START | 0 | 12 | 0 | ACTUAL_EXECUTION |
| ACCELERATION | 4 | 8 | 0 | ACTUAL_EXECUTION |
| HIGH_LEVEL_REPAIR | 2 | 10 | 0 | ACTUAL_EXECUTION |
| TREND_PULLBACK | 0 | 12 | 0 | ACTUAL_EXECUTION |

## Commands

| Label | Command | Exit code |
|---|---|---:|
| ACTUAL_EXECUTION | `.\.venv\Scripts\python.exe -m py_compile stockfish_core\yaogu\daily_primary_fast_decision.py stockfish_core\yaogu\data_pipeline.py stockfish_core\yaogu\candidate_service.py` | 0 |
| ACTUAL_EXECUTION | `.\.venv\Scripts\python.exe -m pytest tests\r05_product\test_daily_primary_critical_capture_boundary_v1.py -q` | 0 |
| ACTUAL_EXECUTION | Isolated Daily Primary validation script executed via `python -X utf8 -c ...` | 0 |

## Restrictions Observed

| Label | Statement |
|---|---|
| STATIC_SOURCE_REVIEW | `STAGE_AWARE_MOMENTUM_V2` strategy code was not modified. |
| STATIC_SOURCE_REVIEW | FOCUS/WATCH promotion rules were not modified. |
| STATIC_SOURCE_REVIEW | UNKNOWN semantics were not modified. |
| STATIC_SOURCE_REVIEW | No new scoring system or alpha signal was added. |
| CODEX_CLAIM | No market edge, profitability, or better stock selection is claimed. |
