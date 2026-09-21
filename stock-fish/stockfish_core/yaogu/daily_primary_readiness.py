"""Daily Primary strategy-input readiness checks.

This module deliberately sits outside the Stage Aware strategy.  It decides
whether the official Daily Primary path is allowed to start the strategy; it
does not rank, reject, or reinterpret instruments.
"""
from __future__ import annotations

from fractions import Fraction

from .coverage_diagnostics import DEFAULT_FULL_MARKET_GATE, EXCHANGES


READINESS_VERSION = "DAILY_PRIMARY_RELIABILITY_V1_1"
REQUIRED = "REQUIRED"
OPTIONAL = "OPTIONAL"
ENHANCEMENT = "ENHANCEMENT"
GLOBAL = "GLOBAL"
INSTRUMENT = "INSTRUMENT"

MISSING_REASON_TYPES = {
    "MISSING_CURRENT_RAW": "MISSING_BAR",
    "MISSING_HISTORY_SESSION": "INCOMPLETE_WINDOW",
    "MISSING_AMOUNT": "MISSING_AMOUNT",
    "MISSING_TURNOVER": "MISSING_TURNOVER",
    "MISSING_ADJUSTMENT_FACTOR": "MISSING_ADJUSTMENT_FACTOR",
    "MISSING_COMPARABLE_CLOSE": "MISSING_COMPARABLE_CLOSE",
    "COMPARABLE_BASIS_MISMATCH": "INCOMPARABLE_BASIS",
    "INSUFFICIENT_MA_HISTORY": "INCOMPLETE_WINDOW",
}

INSTRUMENT_REQUIRED_REASON_TYPES = {
    "MISSING_CURRENT_RAW",
    "MISSING_HISTORY_SESSION",
    "MISSING_COMPARABLE_CLOSE",
    "COMPARABLE_BASIS_MISMATCH",
    "INSUFFICIENT_MA_HISTORY",
}

GLOBAL_PRICE_LAYERS = {
    "has_current_raw_bar": "CURRENT_RAW_BAR_COVERAGE",
    "has_valid_comparable_basis": "CURRENT_COMPARABLE_PRICE_COVERAGE",
    "trend_computable": "CORE_TREND_INPUT_COVERAGE",
}


def _ratio_pass(row: dict, gate: Fraction) -> bool:
    denominator = int(row.get("denominator") or 0)
    numerator = int(row.get("numerator") or 0)
    return denominator > 0 and numerator > 0 and Fraction(numerator, denominator) >= gate


def _gate_pass(stats: dict, gate: Fraction) -> bool:
    return all(_ratio_pass(stats.get(exchange) or {}, gate)
               for exchange in EXCHANGES
               if int((stats.get(exchange) or {}).get("denominator") or 0) > 0)


def _requirement(code: str, *, passed: bool, scope: str = GLOBAL,
                 requiredness: str = REQUIRED, details: dict | None = None) -> dict:
    return {
        "code": code,
        "scope": scope,
        "requiredness": requiredness,
        "status": "PASS" if passed else "FAIL",
        "details": details or {},
    }


def _typed_reasons(reasons) -> list[str]:
    typed = []
    for reason in reasons or ():
        if reason == "NONE":
            continue
        typed.append(MISSING_REASON_TYPES.get(reason, reason))
    return sorted(set(typed))


def evaluate_daily_primary_readiness(input_id: str | None, bundle, cutoff, *,
                                     config_hash: str | None = None,
                                     gate: Fraction = DEFAULT_FULL_MARKET_GATE) -> dict:
    from .strategy_experiments import _prepare_context
    from .coverage_diagnostics import build_coverage_diagnostics, json_safe

    ctx = _prepare_context(bundle, cutoff)
    diagnostics = json_safe(build_coverage_diagnostics(ctx, pv_result=None, stage_aware_result=None, gate=gate))
    qualification = ctx.qualification
    requirements = []
    sessions = list(ctx.sessions)
    observation_date = str(bundle.observation_date)

    requirements.append(_requirement(
        "OBSERVATION_SESSION_RESOLVED",
        passed=bool(observation_date and observation_date in sessions),
        details={"observation_date": observation_date, "session_count": len(sessions)},
    ))
    requirements.append(_requirement(
        "DATED_UNIVERSE_MASTER_USABLE",
        passed=bool(ctx.universe_complete and qualification.get("master_expected", 0) > 0
                    and qualification.get("identity_absent", 0) == 0
                    and qualification.get("eligibility_unknown", 0) == 0),
        details={key: qualification.get(key) for key in (
            "master_expected", "identity_observed", "identity_absent",
            "eligible", "known_excluded", "eligibility_unknown",
            "universe_evidence_reason_codes")},
    ))
    eligible_ids = qualification.get("eligible_ids")
    selected_records = qualification.get("selected_records") or []
    requirements.append(_requirement(
        "ELIGIBLE_UNIVERSE_CANONICAL",
        passed=bool(isinstance(eligible_ids, list)
                    and qualification.get("eligible") == len(eligible_ids)
                    and (qualification.get("eligible", 0) == 0
                         or set(eligible_ids).issubset({row.get("instrument_id") for row in selected_records}))
                    and all(row.get("source_refs") for row in selected_records)),
        details={"eligible": qualification.get("eligible"),
                 "eligible_id_count": len(eligible_ids) if isinstance(eligible_ids, list) else None,
                 "selected_record_count": len(selected_records)},
    ))

    funnel = diagnostics.get("coverage_funnel") or {}
    for layer, code in GLOBAL_PRICE_LAYERS.items():
        stats = funnel.get(layer) or {}
        requirements.append(_requirement(
            code,
            passed=_gate_pass(stats, gate),
            details={"gate_fraction": float(gate), "coverage": stats},
        ))

    context_scope = (diagnostics.get("coverage_scope") or {}).get("CONTEXT_READINESS") or {}
    requirements.append(_requirement(
        "SECTOR_CONTEXT_OPTIONAL",
        passed=(context_scope.get("status") in {"FULL_MARKET", "LIMITED_SCOPE"}),
        requiredness=OPTIONAL,
        details={"status": context_scope.get("status"),
                 "sector": context_scope.get("sector")},
    ))
    requirements.append(_requirement(
        "CAPITAL_CONTEXT_ENHANCEMENT",
        passed=(context_scope.get("status") in {"FULL_MARKET", "LIMITED_SCOPE"}),
        requiredness=ENHANCEMENT,
        details={"status": context_scope.get("status"),
                 "capital": context_scope.get("capital")},
    ))

    instrument_incomplete = []
    instrument_optional_gaps = []
    for row in diagnostics.get("missing_reason_ledger") or ():
        reasons = [reason for reason in (row.get("missing_reasons") or ()) if reason != "NONE"]
        if not reasons:
            continue
        required_reasons = [reason for reason in reasons if reason in INSTRUMENT_REQUIRED_REASON_TYPES]
        target = instrument_incomplete if required_reasons else instrument_optional_gaps
        target.append({
            "instrument_id": row.get("instrument_id"),
            "scope": INSTRUMENT,
            "requiredness": REQUIRED if required_reasons else OPTIONAL,
            "status": "INCOMPLETE",
            "reason_codes": sorted(set(reasons)),
            "typed_reason_codes": _typed_reasons(reasons),
            "required_reason_codes": sorted(set(required_reasons)),
            "primary_reason": row.get("primary_reason"),
            "trend_computable": row.get("trend_computable"),
            "pv_complete": row.get("pv_complete"),
        })

    blocking = [
        row for row in requirements
        if row["scope"] == GLOBAL and row["requiredness"] == REQUIRED and row["status"] != "PASS"
    ]
    optional_missing = [
        row for row in requirements
        if row["scope"] == GLOBAL and row["requiredness"] in {OPTIONAL, ENHANCEMENT}
        and row["status"] != "PASS"
    ]
    return {
        "readiness_version": READINESS_VERSION,
        "input_id": input_id,
        "config_hash": config_hash,
        "observation_date": observation_date,
        "decision_cutoff": cutoff.isoformat(),
        "gate_fraction": float(gate),
        "final_readiness": "READY_FOR_STRATEGY" if not blocking else "NOT_READY",
        "global_required_ready": not blocking,
        "blocking_requirements": blocking,
        "requirements": requirements,
        "optional_or_enhancement_limitations": optional_missing,
        "instrument_required_incomplete_count": len(instrument_incomplete),
        "instrument_required_incomplete_sample": instrument_incomplete[:100],
        "instrument_optional_evidence_gap_count": len(instrument_optional_gaps),
        "instrument_optional_evidence_gap_sample": instrument_optional_gaps[:100],
        "instrument_incomplete_reason_aggregates": diagnostics.get("reason_aggregates") or {},
        "data_coverage_diagnostics": {k: v for k, v in diagnostics.items() if k != "missing_reason_ledger"},
        "missing_reason_ledger_count": len(diagnostics.get("missing_reason_ledger") or []),
        "qualification": qualification,
    }
