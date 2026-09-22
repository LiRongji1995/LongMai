# Strategy Input Dependency Report

## Metadata

| Label | Field | Value |
|---|---|---|
| STATIC_SOURCE_REVIEW | Strategy | `STAGE_AWARE_MOMENTUM_V2` |
| STATIC_SOURCE_REVIEW | Execution path | `/api/daily-primary/run` -> `CandidateService.submit_daily_primary_strategy()` -> `DAILY_PRIMARY_CRITICAL` refresh -> readiness -> `run_active_daily_strategy()` -> `run_stage_aware_momentum()` |
| CODEX_CLAIM | Scope | Restore missing evidence availability only; no strategy formula, threshold, promotion, UNKNOWN, or scoring semantics changed. |

## Dependency Table

| Input | Required By | Source | Capture Stage | Current Availability |
|---|---|---|---|---|
| Observation session and trading calendar | Daily Primary freshness, readiness, V2 session indexing | Exchange calendar / registered calendar fallback | `ExchangeCalendarCapture` | ACTUAL_EXECUTION: latest audited attempt resolved `observation_date=2026-09-21`; controlled validation resolved `2026-09-18`. |
| Mainboard universe identity | `DATED_UNIVERSE_MASTER_USABLE`, `ELIGIBLE_UNIVERSE_CANONICAL`, V2 eligible denominator | `mainboard_sse`, `master_szse`, `master_all`, current identity evidence | `MainBoardMasterCapture`, `MarketBreadthCapture` | ACTUAL_EXECUTION: latest audited attempt had 3040/3040 eligible identity coverage. |
| Eligibility and status | Daily Primary readiness, `_prepare_context()` qualification, V2 eligible ids | Security records with ST, suspension, delisting/listing evidence | `CurrentEligibilityStatusCapture`, `HistoricalSecurityReplayCapture` | ACTUAL_EXECUTION: latest audited attempt had eligibility 3040/3040; historical replay detail was not fully verified. |
| Current raw OHLCV and turnover | `CURRENT_RAW_BAR_COVERAGE`, liquidity bottom, daily path, market benchmark | `market_daily` for observation date | `CurrentDailyPriceCapture` | ACTUAL_EXECUTION: latest audited attempt had current raw bar and turnover coverage 3040/3040. |
| Current comparable price/basis | `CURRENT_COMPARABLE_PRICE_COVERAGE`, returns, RS, baseline diagnostic | Current raw bars plus comparable basis from adjustment-factor or same-basis evidence | `StructureHistoryCapture` plus normalizer | ACTUAL_EXECUTION: before fix latest audited coverage was 1321/3040. ACTUAL_EXECUTION: controlled validation after fix was 12/12. |
| Core structure history window | `CORE_TREND_INPUT_COVERAGE`, MA windows, long slope, setup detection | all-market `market_daily` for V2 required history sessions | `StructureHistoryCapture` | ACTUAL_EXECUTION: before fix latest audited trend/structure coverage was 0/3040. ACTUAL_EXECUTION: controlled validation after fix was 12/12. |
| Required amount history | Liquidity bottom, START amount ratio, TREND_PULLBACK contraction, readiness diagnostics | Historical `market_daily.amount` rows | `StructureHistoryCapture` | ACTUAL_EXECUTION: before fix latest audited amount-history coverage was 0/3040. ACTUAL_EXECUTION: controlled validation after fix was 12/12. |
| Market breadth / market context | market compatibility and market status diagnostics | `master_all`, spot fallback, current price/limit event evidence | `MarketBreadthCapture`, `MarketBreadthRegisteredAlternative`, background limit stages | ACTUAL_EXECUTION: latest audited readiness did not have terminal market-breadth blocker. NOT_VERIFIED: full per-instrument market breadth table for the queued latest job was not independently verified. |
| Theme / sector context | same-theme context, sector level/momentum/breadth | concept/industry list and members | `CurrentThemeCapture` / background research | STATIC_SOURCE_REVIEW: critical capture defers this path. ACTUAL_EXECUTION: latest persisted V2 run had sector context unavailable. |
| Funds / capital context | same-theme capital linkage and enhancement evidence | LHB, money flow, Tushare optional evidence | `OptionalFundsCapture` / background research | STATIC_SOURCE_REVIEW: critical capture defers this path. ACTUAL_EXECUTION: latest persisted V2 run had capital context unavailable. |
| Provenance and source clocks | Evidence resolver, PIT validation, input identity, auditability | RequestQueue receipts, source refs, history artifact manifest, candidate input index | RequestQueue, normalizer, `HistoryArtifactStore`, store documents | ACTUAL_EXECUTION: latest audited attempt persisted source refs and history artifact hash `4e29d20cfae88c5550e6595237d568905bed932469ebe890db1121e8f5b6f276`. |
| Historical security replay | PIT identity/status audit and historical eligibility replay | `master_all` per history session and `stock_basic_all` | `HistoricalSecurityReplayCapture` | NOT_VERIFIED: not a terminal blocker in latest audited readiness; full replay coverage for latest queued job was not verified. |

## Source Review Notes

| Label | Statement |
|---|---|
| STATIC_SOURCE_REVIEW | `daily_primary_readiness.py` maps `has_valid_comparable_basis` to `CURRENT_COMPARABLE_PRICE_COVERAGE` and `trend_computable` to `CORE_TREND_INPUT_COVERAGE`. |
| STATIC_SOURCE_REVIEW | `data_pipeline.py` registers all-market `market_daily` sessions and adjustment-factor batches as `StructureHistoryCapture` before strategy execution. |
| STATIC_SOURCE_REVIEW | `strategy_experiments.py` prepares the strategy context from the persisted `MarketInput`; `stage_aware_momentum.py` consumes that context for trend, setup, behavior, market, sector/funds, counterevidence, and final labels. |
| CODEX_CLAIM | No new alpha signal, score, threshold, promotion rule, or UNKNOWN reinterpretation was introduced. |
