# Daily Primary Fast Decision v1

Date: 2026-09-22.

This change adds an evidence-sufficiency checkpoint to the Daily Primary latest refresh path.  It reduces the first user-visible `NOT_READY` wait when an unchanged readiness gate has already become conclusively impossible to pass.

## Boundary

`MainboardPipeline.capture(..., purpose="DAILY_PRIMARY_CRITICAL")` now evaluates the current partial MarketInput after critical request-queue progress.  The checkpoint reuses the existing Daily Primary readiness output; it does not create a second readiness system and does not change any strategy rule.

The checkpoint can end the refresh early only when all of the following are true:

- the existing readiness evaluator reports a `GLOBAL` `REQUIRED` blocker;
- the blocker is auditable from PIT-valid source rows already visible to the refresh;
- waiting for remaining request-queue work cannot convert that blocker to pass for the first decision;
- the resulting first decision is `NOT_READY`, with `strategy_started=false` and `recommendation_published=false`.

Current v1 intentionally does not early-start strategy.  `READY` still proceeds only after unchanged readiness gates pass and the existing strategy/persistence/export path completes.

## Decision Metadata

RequestQueue stages carry a decision role in capture reports:

- `IMMEDIATE`: observation session, current universe/master, current eligibility/status, current raw price, comparable basis and core trend evidence.
- `DEFERRED`: background research stages plus `HistoricalSecurityReplayCapture` when current universe and eligibility are already sufficient and replay is not the typed blocker.

The refresh report writes `daily_primary_fast_decision` with:

- boundary version
- checkpoint stage
- `READY`, `NOT_READY` or `CONTINUE_REFRESH`
- selected blocker and maximum-factor-coverage proof when applicable
- immediate/deferred queue pending counts

`CandidateService._daily_primary_incremental_refresh()` copies this metadata into the refresh action as `fast_decision` and `fast_decision_early_return`.

## Correctness Guardrails

Unchanged:

- Daily Primary readiness gates and gate fractions
- eligibility and unknown semantics
- comparable-basis requirement
- Stage Aware V2 MA/slope/return windows
- PIT visibility and source receipts
- `STAGE_AWARE_MOMENTUM_V2` strategy semantics
- Focus/Watch/Rejected/Unknown semantics
- score, Enhanced Evidence and `MARKET_EDGE=NOT_ESTABLISHED`

The checkpoint continues refresh when a missing factor batch can still plausibly lift comparable coverage to the gate.  Missing evidence is never converted to `FALSE`, and `UNKNOWN` is never converted to `READY`.

## Implemented v1 Early Boundary

v1 supports early `NOT_READY` for current comparable coverage when the maximum possible improvement from pending adjustment-factor batches still cannot reach the full-market and per-exchange gate.  It also treats settled current raw price, universe/eligibility and core trend blockers as conclusive when their relevant immediate stages are no longer pending.

This is deliberately conservative: if remaining immediate evidence can still change a required gate, the refresh continues.

## Evidence

Targeted tests:

- `tests/r05_product/test_daily_primary_critical_capture_boundary_v1.py`
  - early `NOT_READY` stops before long-history maintenance and does not start strategy
  - pending factor batch that can still reach the comparable gate continues refresh
  - background backlog still does not block READY
  - required missing current price, comparable basis and history remain typed `NOT_READY`
- `tests/r05_product/test_daily_primary_reliability_v1.py`
- `tests/r05_product/test_r14_daily_fast_path.py`

Runtime evidence for this implementation is recorded in `Daily_Primary_Fast_Decision_V1_Report.md` and `daily_primary_fast_decision_v1_bundle/`.
