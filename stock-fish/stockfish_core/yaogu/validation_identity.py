"""Frozen validation identities and defensive signal eligibility checks.

An attempted capture is operational evidence.  It becomes a countable signal
only after dated scope, price, identity, input integrity and persistence checks.
Neither historical flags nor the currently installed code can upgrade old runs.
"""
from __future__ import annotations

from datetime import datetime, time
import hashlib
from math import isfinite
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .contracts import digest

STRATEGY_ID = "STAGE_AWARE_MOMENTUM_V2"
SCORE_SPEC_ID = "DAILY_RECOMMENDATION_SCORE_V1"
SCORE_IMPLEMENTATION_VERSION = "R12_SCORE_INTEGRITY"
PRIMARY_CUTOFF_POLICY_ID = "SCHEDULED_2030_ACTUAL_COMPLETION"
VALIDATION_PROTOCOL_ID = "SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF"


def current_score_spec_identity() -> str:
    return digest({"score_spec_id": SCORE_SPEC_ID,
                   "score_implementation_version": SCORE_IMPLEMENTATION_VERSION,
                   "score_source_hash": hashlib.sha256(Path(__file__).with_name("recommendation_scoring.py").read_bytes()).hexdigest()})


def validation_identity_for_run(run: dict[str, Any]) -> dict[str, Any]:
    stage = (run.get("results") or {}).get(STRATEGY_ID) or {}
    prospective = run.get("prospective_validation") or {}
    policy = prospective.get("primary_cutoff_policy_id") or "LEGACY_UNSPECIFIED"
    protocol = {
        "protocol_id": prospective.get("protocol_id") or "LEGACY_UNSPECIFIED",
        "primary_cutoff_policy_id": policy,
        "checkpoint_signal_sessions": [5, 20, 60, 120],
        "countability_policy": "R12_DATED_EVALUATED_INTEGRITY_PERSISTED_V1",
        "history_separation": "RECONSTRUCTED_NEVER_PROSPECTIVE",
        "protocol_source_hash": digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                        for name in ("validation_identity.py", "strategy_experiments.py", "candidate_service.py")}),
    }
    identity = {
        "strategy_id": STRATEGY_ID,
        "signal_implementation_version": stage.get("stage_aware_implementation_version") or stage.get("implementation_version") or run.get("implementation_version"),
        "signal_spec_revision": stage.get("stage_aware_spec_revision") or stage.get("spec_revision") or run.get("spec_revision") or run.get("spec_hash") or run.get("strategy_spec_hash"),
        "signal_config_hash": stage.get("stage_aware_config_hash") or stage.get("config_hash") or run.get("config_hash"),
        "signal_source_hash": stage.get("stage_aware_source_hash") or stage.get("source_hash") or run.get("source_hash"),
        # Shared qualification/visibility code and wrapper configuration also
        # affect signals even when the stage module itself has not changed.
        "parent_implementation_version": run.get("implementation_version"),
        "parent_source_hash": run.get("source_hash"),
        "parent_config_hash": run.get("config_hash"),
        "parent_spec_hash": run.get("spec_hash") or run.get("strategy_spec_hash"),
        "validation_protocol_hash": digest(protocol),
        "primary_cutoff_policy_id": policy,
        "score_spec_identity": current_score_spec_identity(),
    }
    # A persisted signal retains its captured protocol and score specification.
    # Verify the entire frozen identity rather than trusting an epoch string or
    # allowing a saved identity to hide changes in the actual signal fields.
    saved = prospective if prospective.get("validation_epoch_id") else run
    if saved.get("validation_epoch_id"):
        frozen = {**identity,
                  "validation_protocol_hash": saved.get("validation_protocol_hash"),
                  "score_spec_identity": saved.get("score_spec_identity")}
        if (frozen["validation_protocol_hash"] and frozen["score_spec_identity"]
                and saved["validation_epoch_id"] == "validation_epoch_" + digest(frozen)):
            identity = frozen
    return {**identity, "validation_epoch_id": "validation_epoch_" + digest(identity)}


def current_validation_identity() -> dict[str, Any]:
    from .stage_aware_momentum import stage_aware_source_identity
    from .strategy_experiments import IMPLEMENTATION_VERSION, source_identity

    return validation_identity_for_run({
        **source_identity(), "implementation_version": IMPLEMENTATION_VERSION,
        "results": {STRATEGY_ID: stage_aware_source_identity()},
        "prospective_validation": {"protocol_id": VALIDATION_PROTOCOL_ID,
                                    "primary_cutoff_policy_id": PRIMARY_CUTOFF_POLICY_ID},
    })


def official_primary_capture_for_run(run: dict[str, Any]) -> bool:
    prospective = run.get("prospective_validation") or {}
    stage = (run.get("results") or {}).get(STRATEGY_ID) or {}
    return bool(
        run.get("run_id") and run.get("observation_date")
        and run.get("audit_mode") != "HISTORICAL_RECONSTRUCTION"
        and run.get("coverage_type") == "PROSPECTIVE_CAPTURED"
        and prospective.get("coverage_type") == "PROSPECTIVE_CAPTURED"
        and prospective.get("validation_role") == "PROSPECTIVE_PRIMARY"
        and prospective.get("market_session") == "EVENING"
        and prospective.get("signal_observation_date") == run.get("observation_date")
        and prospective.get("stage_aware_strategy_id") == STRATEGY_ID
        and stage.get("strategy_id", stage.get("canonical_strategy_id")) == STRATEGY_ID
        and prospective.get("official_primary_capture") is True
    )


def signal_data_quality(run: dict[str, Any]) -> dict[str, Any]:
    """Recheck persisted evidence; never trust a precomputed countability flag."""
    stage = (run.get("results") or {}).get(STRATEGY_ID) or {}
    prospective = run.get("prospective_validation") or {}
    qualification = run.get("qualification") or {}
    counts = stage.get("counts") or {}
    items = stage.get("items") or []
    eligible_ids = qualification.get("eligible_ids")
    reasons = []
    if not run.get("run_id") or not run.get("observation_date") or not stage:
        reasons.append("SIGNAL_IDENTITY_UNAVAILABLE")
    identity = validation_identity_for_run(run)
    if any(not identity.get(key) for key in ("signal_implementation_version", "signal_spec_revision", "signal_config_hash", "signal_source_hash")):
        reasons.append("SIGNAL_IMPLEMENTATION_IDENTITY_INCOMPLETE")
    if run.get("spec_conformance") == "SPEC_NONCONFORMANT" or stage.get("spec_conformance") == "SPEC_NONCONFORMANT":
        reasons.append("SIGNAL_SPEC_NONCONFORMANT")
    full_scope = stage.get("scope_label") == "FULL_SCOPE" and qualification.get("universe_complete") is not False
    if (not full_scope or not qualification or qualification.get("master_expected", 0) <= 0
            or qualification.get("identity_observed") != qualification.get("master_expected") or qualification.get("identity_absent", 0)
            or qualification.get("eligibility_unknown", 0) or qualification.get("universe_evidence_reason_codes")):
        reasons.append("DATED_UNIVERSE_OR_ELIGIBILITY_UNAVAILABLE")
    if not isinstance(eligible_ids, list):
        reasons.append("ELIGIBLE_UNIVERSE_UNAVAILABLE")
        eligible_ids = []
    expected = len(eligible_ids)
    item_ids = [row.get("instrument_id") for row in items]
    if (counts.get("evaluated") != expected or counts.get("eligible") != expected
            or len(set(eligible_ids)) != expected or sorted(item_ids) != sorted(eligible_ids)):
        reasons.append("ELIGIBLE_UNIVERSE_NOT_ACTUALLY_EVALUATED")
    if expected == 0 and not (
        full_scope and qualification.get("master_expected", 0) > 0
        and qualification.get("identity_observed") == qualification.get("master_expected")
        and qualification.get("known_excluded") == qualification.get("master_expected")
        and qualification.get("selected_records")
    ):
        reasons.append("EMPTY_ELIGIBLE_WITHOUT_COMPLETE_UNIVERSE_EVIDENCE")
    selected_records = qualification.get("selected_records") or []
    if (not selected_records or any(not row.get("source_refs") for row in selected_records)
            or not set(eligible_ids).issubset({row.get("instrument_id") for row in selected_records})):
        reasons.append("DATED_IDENTITY_SOURCE_EVIDENCE_UNAVAILABLE")
    if any(row.get("comparable_close") is None or not row.get("comparable_basis") for row in items):
        reasons.append("CURRENT_COMPARABLE_PRICE_UNAVAILABLE")
    for row in items:
        states = row.get("condition_statuses") or {}
        conditions = row.get("conditions") or {}
        core = {key: (conditions.get(key) or {}).get("status", states.get(key))
                for key in ("liquidity_bottom", "trend_state", "setup_identification")}
        determinately_excluded = row.get("decision_status") == "REJECTED" and (
            core["liquidity_bottom"] == "FAIL" or core["trend_state"] == "FAIL")
        if not determinately_excluded and any(value not in {"PASS", "FAIL"} for value in core.values()):
            reasons.append("CORE_STRATEGY_EVIDENCE_UNAVAILABLE")
    if prospective.get("input_integrity_valid") is not True:
        reasons.append("INPUT_INTEGRITY_NOT_VERIFIED")
    if prospective.get("signal_run_persisted") is not True:
        reasons.append("SIGNAL_RUN_NOT_PERSISTED")
    if prospective.get("official_primary_capture") is True and run.get("coverage_type") == "PROSPECTIVE_CAPTURED":
        reasons.extend(_primary_cutoff_reasons(run))
    reasons = sorted(set(reasons))
    return {"signal_evaluable": not reasons,
            "data_quality_status": "EVALUABLE" if not reasons else "SIGNAL_DATA_UNAVAILABLE",
            "reason_codes": reasons}


def _primary_cutoff_reasons(run: dict[str, Any]) -> list[str]:
    prospective = run.get("prospective_validation") or {}
    try:
        clocks = {key: datetime.fromisoformat(prospective[key]) for key in (
            "scheduled_at", "capture_started_at", "capture_completed_at", "actual_decision_cutoff")}
        decision = datetime.fromisoformat(run.get("decision_cutoff") or run["cutoff"])
        if any(value.tzinfo is None or value.utcoffset() is None for value in [*clocks.values(), decision]):
            return ["PRIMARY_CUTOFF_CLOCK_TIMEZONE_MISSING"]
        scheduled, started, completed, actual = (clocks[key] for key in (
            "scheduled_at", "capture_started_at", "capture_completed_at", "actual_decision_cutoff"))
        local = scheduled.astimezone(ZoneInfo("Asia/Shanghai"))
        if (not prospective.get("scheduler_job_id") or local.date().isoformat() != run.get("observation_date")
                or local.time().replace(tzinfo=None) != time(20, 30)
                or actual != decision or not started <= completed <= actual):
            return ["PRIMARY_CUTOFF_TIMING_INCONSISTENT"]
        policy = prospective.get("primary_cutoff_policy_id")
        if policy == PRIMARY_CUTOFF_POLICY_ID:
            if started < scheduled or prospective.get("protocol_id") != VALIDATION_PROTOCOL_ID:
                return ["PRIMARY_ACTUAL_CUTOFF_PROTOCOL_INCONSISTENT"]
        elif policy == "EXACT_2030":
            if actual != scheduled or prospective.get("protocol_id") != "EXACT_PRIMARY_CUTOFF_V1":
                return ["PRIMARY_EXACT_CUTOFF_NOT_EXACT"]
        else:
            return ["PRIMARY_CUTOFF_POLICY_UNVERIFIED"]
        for key, expected in (("capture_latency_seconds", (completed - started).total_seconds()),
                              ("decision_latency_seconds", (actual - scheduled).total_seconds())):
            if key not in prospective or not isfinite(float(prospective[key])) or abs(float(prospective[key]) - expected) > .001:
                return ["PRIMARY_CUTOFF_LATENCY_INCONSISTENT"]
        return []
    except (KeyError, TypeError, ValueError):
        return ["PRIMARY_CUTOFF_TIMING_UNAVAILABLE"]


def official_signal_countable(run: dict[str, Any]) -> bool:
    prospective = run.get("prospective_validation") or {}
    return bool(official_primary_capture_for_run(run)
                and prospective.get("official_validation_signal_countable") is True
                and signal_data_quality(run)["signal_evaluable"])
