# Daily Primary Critical Capture Boundary v1

Date: 2026-09-22.

This change separates the Daily Primary user-visible capture path from background research capture without creating a second provider or data system.

## Boundary

`MainboardPipeline.capture(..., purpose="DAILY_PRIMARY_CRITICAL")` reuses the existing request queue, provider request identities, receipts, normalizer, history artifact writer, PIT visibility and provenance checks. It stops after the critical MarketInput is normalized from the shared DAG inputs needed by Daily Primary readiness:

- observation session and exchange calendar
- current mainboard universe/master identity
- eligibility/status evidence, including ST, suspension, delisting and listing history
- current raw OHLC/amount/turnover facts
- comparable close/basis and adjustment-factor evidence required for comparable basis correctness
- full Stage Aware V2 core history window
- source/provenance receipts and history artifact identity

The following stages remain available in the full compatibility path but are classified as `BACKGROUND_RESEARCH` and are not drained in the Daily Primary critical path:

- `PublishedCandidateOutcomeCapture`
- `MarketBreadthLimitEvidence`
- `MarketBreadthDirectLimitEvidence`
- `CurrentThemeCapture`
- `TushareOptionalEnrichment`
- `CandidateAndPeerHistoryCapture`
- `OptionalFundsCapture`

## Runtime Path

Normal latest `/api/daily-primary/run` still performs v1.1 freshness/readiness first. When refresh is allowed and required, `CandidateService._daily_primary_incremental_refresh()` now calls `CandidateScheduler._capture(..., purpose="DAILY_PRIMARY_CRITICAL", observation_date=expected_session)`. The refreshed input is registered and then evaluated by the unchanged Daily Primary readiness gate before `STAGE_AWARE_MOMENTUM_V2` can start.

Manual/full `/api/candidate-data-pipeline/capture` remains the compatibility research capture path and still drains the background stages. RequestQueue payloads now carry `purpose`/`purposes` metadata in addition to the existing `stage`/`stages`; this metadata is not part of provider logical identity and does not change cache reuse or source hashes.

## Correctness Guardrails

No strategy semantics changed. The following remain unchanged:

- `STAGE_AWARE_MOMENTUM_V2` setup, sorting, thresholds and `FOCUS/WATCH/REJECTED/UNKNOWN` rules
- Daily Primary v1.1 freshness/readiness semantics
- `UNKNOWN_HISTORICAL_STATUS` blocking readiness
- PIT visibility, source receipts, comparable basis and history artifact semantics
- `MARKET_EDGE=NOT_ESTABLISHED`

Critical capture may still return `NOT_READY` when eligibility, current price, comparable basis or the core history window is missing. Background theme, funds, limit evidence, outcomes and tracking can remain pending without blocking readiness.

## Evidence

Targeted tests are in `tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py`:

- complete critical capture with 10,000 pending `PublishedCandidateOutcomeCapture` rows still reaches Daily Primary `READY`
- unknown historical eligibility remains `NOT_READY` and does not start strategy
- missing current price, comparable basis or history window remains `NOT_READY` with typed blockers

Relevant regression suites:

- `tests/r05_product/test_daily_primary_reliability_v1.py`
- `tests/r05_product/test_r14_daily_fast_path.py`
- `tests/r03_pipeline/test_request_queue.py`
- `tests/r03_pipeline/test_data_dag.py`
- `tests/r04_product/test_direct_limits_and_summary.py`

Open limitations: background scheduling is still shared, provider latency remains external, live provider E2E for this boundary was not run, and no score/enhanced-evidence/market-edge work is included.
