"""Fast first-decision boundary for Daily Primary refreshes.

The readiness module owns the READY/NOT_READY semantics.  This module only
answers whether the current evidence makes waiting for the rest of a refresh
irrelevant for the first user-visible decision.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
import json
import re

from .coverage_diagnostics import DEFAULT_FULL_MARKET_GATE, EXCHANGES, exchange_of


FAST_DECISION_VERSION = "DAILY_PRIMARY_FAST_DECISION_BOUNDARY_V1"

SETTLED_STATES = {"COMPLETE", "BLOCKED"}
IMMEDIATE = "IMMEDIATE"
DEFERRED = "DEFERRED"

STAGE_DECISION_ROLES = {
    "MainBoardMasterCapture": IMMEDIATE,
    "ExchangeCalendarCapture": IMMEDIATE,
    "CurrentEligibilityStatusCapture": IMMEDIATE,
    "CurrentDailyPriceCapture": IMMEDIATE,
    "MarketBreadthCapture": IMMEDIATE,
    "MarketBreadthRegisteredAlternative": IMMEDIATE,
    "StructureHistoryCapture": IMMEDIATE,
    "HistoricalSecurityReplayCapture": DEFERRED,
    "PublishedCandidateOutcomeCapture": DEFERRED,
    "MarketBreadthLimitEvidence": DEFERRED,
    "MarketBreadthDirectLimitEvidence": DEFERRED,
    "CurrentThemeCapture": DEFERRED,
    "CurrentThemeDirectory": DEFERRED,
    "CurrentThemeRegisteredAlternative": DEFERRED,
    "TushareOptionalEnrichment": DEFERRED,
    "CandidateAndPeerHistoryCapture": DEFERRED,
    "OptionalFundsCapture": DEFERRED,
}

BLOCKER_STAGE_DEPENDENCIES = {
    "OBSERVATION_SESSION_RESOLVED": {"ExchangeCalendarCapture"},
    "DATED_UNIVERSE_MASTER_USABLE": {
        "MainBoardMasterCapture",
        "CurrentEligibilityStatusCapture",
        "CurrentDailyPriceCapture",
        "MarketBreadthCapture",
        "MarketBreadthRegisteredAlternative",
    },
    "ELIGIBLE_UNIVERSE_CANONICAL": {
        "MainBoardMasterCapture",
        "CurrentEligibilityStatusCapture",
        "CurrentDailyPriceCapture",
        "MarketBreadthCapture",
        "MarketBreadthRegisteredAlternative",
    },
    "CURRENT_RAW_BAR_COVERAGE": {"CurrentDailyPriceCapture"},
    "CURRENT_COMPARABLE_PRICE_COVERAGE": {"CurrentDailyPriceCapture", "StructureHistoryCapture"},
    "CORE_TREND_INPUT_COVERAGE": {"StructureHistoryCapture"},
}


def _payload(row) -> dict:
    raw = row["payload"] if not isinstance(row, dict) else row.get("payload")
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw or {})


def _state(row) -> str:
    return str(row["state"] if not isinstance(row, dict) else row.get("state"))


def _stage(payload: dict) -> str | None:
    stages = payload.get("stages") or []
    return payload.get("stage") or (stages[0] if stages else None)


def _role(payload: dict) -> str:
    purposes = set(payload.get("purposes") or [])
    if payload.get("purpose"):
        purposes.add(payload["purpose"])
    if "BACKGROUND_RESEARCH" in purposes:
        return DEFERRED
    return STAGE_DECISION_ROLES.get(_stage(payload), DEFERRED)


def _normalize_code(value) -> str | None:
    text = str(value or "").upper()
    match = re.fullmatch(r"(SH|SZ|BJ)\.(\d{6})", text)
    if match:
        return f"{match.group(2)}.{match.group(1)}"
    match = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", text)
    if match:
        return f"{match.group(2)}.{match.group(1)}"
    match = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ)", text)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return None


def _queue_summary(rows) -> dict:
    stages = defaultdict(Counter)
    endpoints = defaultdict(Counter)
    pending_factor_codes = set()
    pending_current_price = False
    pending_structure_history = False
    immediate_pending = []
    deferred_pending = []
    parsed = []
    for row in rows:
        payload = _payload(row)
        state = _state(row)
        stage = _stage(payload) or "UNKNOWN"
        endpoint = payload.get("endpoint") or "UNKNOWN"
        role = _role(payload)
        settled = state in SETTLED_STATES
        stages[stage][state] += 1
        endpoints[endpoint][state] += 1
        parsed.append({"stage": stage, "endpoint": endpoint, "state": state, "role": role})
        if not settled:
            target = immediate_pending if role == IMMEDIATE else deferred_pending
            target.append({"stage": stage, "endpoint": endpoint, "state": state})
            if endpoint == "adjustment_factors":
                for code in (payload.get("parameters") or {}).get("codes") or ():
                    normalized = _normalize_code(code)
                    if normalized:
                        pending_factor_codes.add(normalized)
            if endpoint == "market_daily" and stage == "CurrentDailyPriceCapture":
                pending_current_price = True
            if stage == "StructureHistoryCapture":
                pending_structure_history = True
    pending_by_stage = Counter(item["stage"] for item in immediate_pending)
    return {
        "stages": {key: dict(value) for key, value in sorted(stages.items())},
        "endpoints": {key: dict(value) for key, value in sorted(endpoints.items())},
        "immediate_pending_count": len(immediate_pending),
        "deferred_pending_count": len(deferred_pending),
        "immediate_pending_by_stage": dict(sorted(pending_by_stage.items())),
        "pending_adjustment_factor_codes": sorted(pending_factor_codes),
        "pending_current_price": pending_current_price,
        "pending_structure_history": pending_structure_history,
        "rows": parsed,
    }


def _stages_settled(summary: dict, stages: set[str]) -> bool:
    for stage in stages:
        states = summary["stages"].get(stage)
        if not states:
            continue
        if any(state not in SETTLED_STATES for state, count in states.items() if count):
            return False
    return True


def _coverage(row: dict) -> dict:
    details = row.get("details") or {}
    return details.get("coverage") or {}


def _gate(readiness: dict, row: dict, gate: Fraction | None) -> Fraction:
    if gate is not None:
        return gate
    value = (row.get("details") or {}).get("gate_fraction", readiness.get("gate_fraction"))
    try:
        return Fraction(str(value))
    except Exception:
        return DEFAULT_FULL_MARKET_GATE


def _pending_factor_counts(summary: dict) -> dict:
    counts = Counter()
    for code in summary.get("pending_adjustment_factor_codes") or ():
        exchange = exchange_of(code)
        counts["ALL"] += 1
        if exchange in {"SSE", "SZSE"}:
            counts[exchange] += 1
    return {exchange: counts.get(exchange, 0) for exchange in EXCHANGES}


def _max_possible_factor_result(readiness: dict, row: dict, summary: dict,
                                gate: Fraction | None) -> dict:
    coverage = _coverage(row)
    pending = _pending_factor_counts(summary)
    threshold = _gate(readiness, row, gate)
    exchanges = {}
    can_still_pass = True
    for exchange in EXCHANGES:
        stats = coverage.get(exchange) or {}
        denominator = int(stats.get("denominator") or 0)
        numerator = int(stats.get("numerator") or 0)
        possible = min(denominator, numerator + int(pending.get(exchange) or 0))
        passes = denominator > 0 and Fraction(possible, denominator) >= threshold
        exchanges[exchange] = {
            "numerator": numerator,
            "denominator": denominator,
            "pending_factor_codes": int(pending.get(exchange) or 0),
            "max_possible_numerator": possible,
            "max_possible_coverage": float(Fraction(possible, denominator)) if denominator else None,
            "can_reach_gate": passes if denominator else False,
        }
        if denominator > 0 and not passes:
            can_still_pass = False
    return {"can_still_pass": can_still_pass, "exchanges": exchanges,
            "gate_fraction": float(threshold)}


def _conclusive_blocker(readiness: dict, blocker: dict, summary: dict,
                        gate: Fraction | None) -> dict | None:
    code = blocker.get("code")
    if code == "CURRENT_COMPARABLE_PRICE_COVERAGE":
        max_result = _max_possible_factor_result(readiness, blocker, summary, gate)
        if not max_result["can_still_pass"]:
            return {
                "code": code,
                "basis": "MAX_POSSIBLE_FACTOR_COVERAGE_BELOW_GATE",
                "details": max_result,
            }
        if not summary.get("pending_current_price") and not summary.get("pending_adjustment_factor_codes"):
            return {
                "code": code,
                "basis": "CURRENT_PRICE_AND_FACTOR_EVIDENCE_SETTLED",
                "details": max_result,
            }
        return None
    if code == "CORE_TREND_INPUT_COVERAGE":
        if _stages_settled(summary, BLOCKER_STAGE_DEPENDENCIES[code]):
            return {"code": code, "basis": "CORE_TREND_EVIDENCE_SETTLED",
                    "details": {"coverage": _coverage(blocker)}}
        return None
    dependencies = BLOCKER_STAGE_DEPENDENCIES.get(code)
    if dependencies and _stages_settled(summary, dependencies):
        return {"code": code, "basis": "RELEVANT_EVIDENCE_SETTLED",
                "details": {"dependencies": sorted(dependencies)}}
    return None


def evaluate_fast_decision_sufficiency(readiness: dict, queue_rows, *,
                                       gate: Fraction | None = None,
                                       checkpoint: str | None = None) -> dict:
    """Return whether current evidence is enough for the first decision.

    A positive result never weakens readiness.  READY is only sufficient after
    all immediate refresh work has settled; NOT_READY is sufficient when an
    existing GLOBAL REQUIRED blocker is conclusive.
    """
    rows = list(queue_rows or ())
    summary = _queue_summary(rows)
    if readiness.get("global_required_ready"):
        if summary["immediate_pending_count"] == 0:
            return {
                "version": FAST_DECISION_VERSION,
                "sufficient": True,
                "decision": "READY",
                "checkpoint": checkpoint,
                "reason": "ALL_REQUIRED_GATES_PASS_AND_NO_IMMEDIATE_REFRESH_PENDING",
                "queue": {k: v for k, v in summary.items() if k != "rows"},
            }
        return {
            "version": FAST_DECISION_VERSION,
            "sufficient": False,
            "decision": "CONTINUE_REFRESH",
            "checkpoint": checkpoint,
            "reason": "READY_REQUIRES_SETTLED_IMMEDIATE_REFRESH_EVIDENCE",
            "queue": {k: v for k, v in summary.items() if k != "rows"},
        }
    considered = []
    for blocker in readiness.get("blocking_requirements") or ():
        if blocker.get("scope") != "GLOBAL" or blocker.get("requiredness") != "REQUIRED":
            continue
        conclusive = _conclusive_blocker(readiness, blocker, summary, gate)
        considered.append({
            "code": blocker.get("code"),
            "status": blocker.get("status"),
            "conclusive": conclusive is not None,
            "basis": (conclusive or {}).get("basis"),
        })
        if conclusive is not None:
            return {
                "version": FAST_DECISION_VERSION,
                "sufficient": True,
                "decision": "NOT_READY",
                "checkpoint": checkpoint,
                "reason": "GLOBAL_REQUIRED_BLOCKER_CONCLUSIVE",
                "selected_blocker": conclusive,
                "blockers_considered": considered,
                "queue": {k: v for k, v in summary.items() if k != "rows"},
            }
    return {
        "version": FAST_DECISION_VERSION,
        "sufficient": False,
        "decision": "CONTINUE_REFRESH",
        "checkpoint": checkpoint,
        "reason": "MISSING_EVIDENCE_CAN_STILL_CHANGE_REQUIRED_GATE",
        "blockers_considered": considered,
        "queue": {k: v for k, v in summary.items() if k != "rows"},
    }
