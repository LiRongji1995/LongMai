"""Frozen research plans and read-only R1/R2 evaluation over saved products.

This entry composes the existing coverage audit, R1 paired block estimator and
R2 account adapter. It never runs a selector, opens a business DB, captures data,
or grants source/PIT authority to JSON. A current native source owner can be
provided through the Python API; archived byte integrity remains a distinct
claim. Every CLI write uses the existing bounded, no-overwrite private writer.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_p0 as io
from scripts import codex_research_coverage_v1 as coverage

VERSION = "CODEX_RESEARCH_PROTOCOL_V1"
PLAN = "FROZEN_SHORT_HORIZON_RESEARCH_PLAN_V1"
R1, R2 = "SHORT_HORIZON_STRENGTH_R1", "SHORT_HORIZON_STRENGTH_R2"
STAGES = ("STRONG_START", "STRONG_CONTINUATION", "FIRST_PULLBACK")
COMPARISONS = ("R2_MINUS_R1", "R2_MINUS_ELIGIBLE", "R2_MINUS_RISK_MATCHED",
               *("R2_MINUS_R1:" + stage for stage in STAGES))
IDENTITY_FIELDS = ("source_hash", "config_hash", "spec_hash", "implementation_version", "feature_version")
MAX_DAYS, MAX_SCENARIOS = 366, 6
MAX_BATCH_READ_BYTES = 512 * 1024**2
IMPLEMENTATION_FILES = ("scripts/codex_research_protocol_v1.py", "scripts/codex_p0.py",
    "scripts/codex_research_coverage_v1.py", "scripts/codex_r2_execution_v1.py",
    "scripts/codex_result_view_v1.py", "scripts/codex_workspace.py",
    "stockfish_core/yaogu/physical_historical_pair_v1.py",
    "stockfish_core/yaogu/research_r1_io.py", "stockfish_core/yaogu/research_r1_statistics.py",
    "stockfish_core/yaogu/execution_research_r1.py", "stockfish_core/yaogu/research_r1_contracts.py",
    "stockfish_core/observed_data.py", "stockfish_core/data.py", "stockfish_core/exchange_calendar_2026.json")
SAFE = dict(source_reauthenticated_now=False, historical_pit_verified=False,
            evaluation_admissible_for_market_validation=False,
            strategy_account_return=None, market_edge="NOT_ESTABLISHED",
            production_promotion="DISABLED", signal_policy_mutated=False)


class ProtocolError(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise ProtocolError(reason)


def digest(value):
    return coverage._digest(value)


def now():
    return datetime.now(timezone.utc)


def iso_day(value):
    need(type(value) is str, "PROTOCOL_ISO_DAY_REQUIRED")
    try:
        valid = date.fromisoformat(value)
    except ValueError as error:
        raise ProtocolError("PROTOCOL_ISO_DAY_REQUIRED") from error
    need(valid.isoformat() == value, "PROTOCOL_ISO_DAY_REQUIRED")
    return valid


def nonempty(value, reason):
    need(type(value) is str and bool(value.strip()) and len(value) <= 8192, reason)
    return value


def hash_value(value, reason):
    need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), reason)
    return value


def seal(value):
    value["content_hash"] = digest({k: v for k, v in value.items() if k != "content_hash"})
    return value


def implementation_identity():
    """Explicit consumer closure; never substitutes for a native source hash."""
    references, hashes = [], {}
    for name in IMPLEMENTATION_FILES:
        _, reference = io.files._read_file(ROOT / name, maximum=1024**2, retain=False)
        references.append(reference)
        hashes[name] = reference["sha256"]
    return dict(version=VERSION, file_hashes=hashes, source_hash=digest(hashes)), references


def check_seal(value, schema):
    need(type(value) is dict and value.get("schema_version") == schema,
         "PROTOCOL_SCHEMA_REQUIRED")
    need(value.get("content_hash") == digest({k: v for k, v in value.items() if k != "content_hash"}),
         "PROTOCOL_CONTENT_HASH_MISMATCH")


def risk_spec(matching):
    need(type(matching) is dict and set(matching) == {"categorical_axes", "amount_boundaries_cny"},
         "PROTOCOL_RISK_MATCHING_FIELDS")
    axes = matching["categorical_axes"]
    need(type(axes) is list and len(axes) == len(set(axes)) and set(axes) <= {"exchange", "primary_stage"},
         "PROTOCOL_RISK_AXIS_UNAVAILABLE_IN_FROZEN_RUN")
    bands = matching["amount_boundaries_cny"]
    need(type(bands) is list and len(bands) <= 10, "PROTOCOL_RISK_BANDS")
    values = [coverage._fraction(value) for value in bands]
    need(values == sorted(set(values)) and all(value > 0 for value in values), "PROTOCOL_ORDERED_POSITIVE_RISK_BANDS")
    return matching


def _draft(draft):
    need(type(draft) is dict, "PROTOCOL_DRAFT_REQUIRED")
    required = {"plan_id", "hypothesis", "discovery_evidence", "windows", "expected_signal_sessions",
                "comparison_family", "primary_comparison", "horizons", "primary_horizon",
                "block_lengths", "primary_block_length", "bootstrap_repetitions", "seed",
                "attempts", "stopping_rule", "risk_matching", "strategy_identities"}
    need(set(draft) == required, "PROTOCOL_DRAFT_FIELD_SET")
    for key in ("plan_id", "hypothesis", "stopping_rule"):
        nonempty(draft[key], "PROTOCOL_" + key.upper() + "_REQUIRED")
    identities = draft["strategy_identities"]
    need(type(identities) is dict and set(identities) == {R1, R2}, "PROTOCOL_STRATEGY_IDENTITIES_REQUIRED")
    for identity in identities.values():
        need(type(identity) is dict and set(identity) == set(IDENTITY_FIELDS), "PROTOCOL_STRATEGY_IDENTITY_FIELDS")
        for key in IDENTITY_FIELDS:
            if key.endswith("_hash"):
                hash_value(identity[key], "PROTOCOL_STRATEGY_HASH_REQUIRED")
            else:
                nonempty(identity[key], "PROTOCOL_STRATEGY_VERSION_REQUIRED")
    windows = draft["windows"]
    need(type(windows) is dict and set(windows) == {"discovery", "validation", "holdout"},
         "PROTOCOL_THREE_WINDOWS_REQUIRED")
    previous = None
    for name in ("discovery", "validation", "holdout"):
        window = windows[name]
        need(type(window) is dict and set(window) == {"start", "end"}, "PROTOCOL_WINDOW_REQUIRED")
        start, end = iso_day(window["start"]), iso_day(window["end"])
        need(start <= end and (previous is None or previous < start), "PROTOCOL_WINDOWS_OVERLAP_OR_ORDER")
        previous = end
    evidence = draft["discovery_evidence"]
    need(type(evidence) is list and 0 < len(evidence) <= 1000, "PROTOCOL_DISCOVERY_EVIDENCE_REQUIRED")
    ids = []
    for item in evidence:
        need(type(item) is dict and set(item) == {"id", "content_hash", "observation_date", "known_at"},
             "PROTOCOL_DISCOVERY_EVIDENCE_FIELDS")
        ids.append(nonempty(item["id"], "PROTOCOL_DISCOVERY_ID_REQUIRED"))
        hash_value(item["content_hash"], "PROTOCOL_DISCOVERY_HASH_REQUIRED")
        day = iso_day(item["observation_date"])
        need(iso_day(windows["discovery"]["start"]) <= day <= iso_day(windows["discovery"]["end"]),
             "PROTOCOL_DISCOVERY_OUTSIDE_WINDOW")
        coverage._aware(item["known_at"])
    need(len(ids) == len(set(ids)), "PROTOCOL_DISCOVERY_DUPLICATE")
    sessions = draft["expected_signal_sessions"]
    need(type(sessions) is list and 0 < len(sessions) <= MAX_DAYS and sessions == sorted(set(sessions)),
         "PROTOCOL_UNIQUE_ORDERED_EXPECTED_SESSIONS_REQUIRED")
    for day in sessions:
        iso_day(day)
        need(any(windows[name]["start"] <= day <= windows[name]["end"] for name in ("validation", "holdout")),
             "PROTOCOL_SIGNAL_SESSION_OUTSIDE_VALIDATION_OR_HOLDOUT")
    need(all(any(windows[name]["start"] <= day <= windows[name]["end"] for day in sessions)
             for name in ("validation", "holdout")), "PROTOCOL_EXPECTED_SESSIONS_BOTH_WINDOWS_REQUIRED")
    family = draft["comparison_family"]
    need(type(family) is list and family and len(family) == len(set(family))
         and set(family) <= set(COMPARISONS) and draft["primary_comparison"] in family,
         "PROTOCOL_REGISTERED_COMPARISON_FAMILY_REQUIRED")
    horizons = draft["horizons"]
    need(type(horizons) is list and horizons and all(type(h) is int and h in coverage.HORIZONS for h in horizons)
         and horizons == sorted(set(horizons)) and draft["primary_horizon"] in horizons,
         "PROTOCOL_HORIZONS_REQUIRED")
    blocks = draft["block_lengths"]
    need(type(blocks) is list and 0 < len(blocks) <= 4 and blocks == sorted(set(blocks))
         and all(type(b) is int and max(horizons) < b <= 100 for b in blocks)
         and draft["primary_block_length"] in blocks, "PROTOCOL_BLOCKS_MUST_EXCEED_LABEL_HORIZON")
    need(type(draft["bootstrap_repetitions"]) is int and 100 <= draft["bootstrap_repetitions"] <= 10000,
         "PROTOCOL_BOOTSTRAP_BOUND_REQUIRED")
    need(type(draft["seed"]) is int and 0 <= draft["seed"] <= 2**32 - 1, "PROTOCOL_SEED_REQUIRED")
    attempts = draft["attempts"]
    need(type(attempts) is list and 0 < len(attempts) <= 1000, "PROTOCOL_ALL_ATTEMPTS_REQUIRED")
    attempt_ids = []
    for attempt in attempts:
        need(type(attempt) is dict and set(attempt) == {"attempt_id", "configuration_hash", "status", "registered_at", "result_hash"},
             "PROTOCOL_ATTEMPT_FIELDS_REQUIRED")
        attempt_ids.append(nonempty(attempt["attempt_id"], "PROTOCOL_ATTEMPT_ID_REQUIRED"))
        hash_value(attempt["configuration_hash"], "PROTOCOL_ATTEMPT_CONFIG_HASH_REQUIRED")
        coverage._aware(attempt["registered_at"])
        need(attempt["status"] in ("PLANNED", "COMPLETED", "FAILED", "UNKNOWN"), "PROTOCOL_ATTEMPT_STATUS")
        need(attempt["result_hash"] is None or hash_value(attempt["result_hash"], "PROTOCOL_ATTEMPT_RESULT_HASH"),
             "PROTOCOL_ATTEMPT_RESULT_HASH")
    need(len(attempt_ids) == len(set(attempt_ids)), "PROTOCOL_ATTEMPT_DUPLICATE")
    matching = risk_spec(draft["risk_matching"])
    axes = matching["categorical_axes"]
    bands = matching["amount_boundaries_cny"]
    if "R2_MINUS_RISK_MATCHED" in family:
        need(axes or bands, "PROTOCOL_MATCHED_COMPARISON_REQUIRES_STRATA")
    return draft


def freeze_plan(draft, *, previous=None):
    """Stamp actual creation time; caller declarations do not prove old PIT."""
    draft = deepcopy(_draft(draft))
    frozen = now()
    need(all(coverage._aware(row["known_at"]) <= frozen for row in draft["discovery_evidence"])
         and all(coverage._aware(row["registered_at"]) <= frozen for row in draft["attempts"]),
         "PROTOCOL_FUTURE_DECLARED_KNOWLEDGE")
    if previous is not None:
        validate_plan(previous)
        need(previous["draft"]["plan_id"] == draft["plan_id"], "PROTOCOL_PARENT_PLAN_ID_MISMATCH")
        need(draft["attempts"][:len(previous["draft"]["attempts"])] == previous["draft"]["attempts"],
             "PROTOCOL_PRIOR_ATTEMPTS_MUST_BE_RETAINED")
        need(frozen >= coverage._aware(previous["frozen_at"]), "PROTOCOL_PARENT_FROM_FUTURE")
    prospective = iso_day(draft["windows"]["validation"]["start"]) > frozen.astimezone(ZoneInfo("Asia/Shanghai")).date()
    return seal(dict(schema_version=PLAN, draft=draft, frozen_at=frozen.isoformat(),
        parent_plan_hash=previous["content_hash"] if previous else None,
        design_timing="FROZEN_BEFORE_DECLARED_VALIDATION" if prospective else "RETROSPECTIVE_DESCRIPTIVE_ONLY",
        sample_provenance="CALLER_DECLARATIONS_NOT_SOURCE_AUTHENTICATION",
        calendar_role="CALLER_DECLARED_EXPECTATIONS_NOT_AUTHENTICATED_EXCHANGE_CALENDAR",
        all_attempts_completeness="CALLER_DECLARED_NOT_INDEPENDENTLY_PROVEN",
        registration_authority="LOCAL_CREATION_CLOCK_NOT_EXTERNAL_PREREGISTRATION",
        selection_policy_changed=False, **SAFE))


def validate_plan(plan):
    check_seal(plan, PLAN)
    _draft(plan.get("draft"))
    frozen = coverage._aware(plan.get("frozen_at"))
    need(frozen <= now(), "PROTOCOL_FUTURE_PLAN")
    need(all(coverage._aware(row["known_at"]) <= frozen for row in plan["draft"]["discovery_evidence"])
         and all(coverage._aware(row["registered_at"]) <= frozen for row in plan["draft"]["attempts"]),
         "PROTOCOL_KNOWLEDGE_AFTER_FREEZE")
    expected = "FROZEN_BEFORE_DECLARED_VALIDATION" if iso_day(plan["draft"]["windows"]["validation"]["start"]) > frozen.astimezone(ZoneInfo("Asia/Shanghai")).date() else "RETROSPECTIVE_DESCRIPTIVE_ONLY"
    need(plan.get("design_timing") == expected and all(plan.get(k) == v for k, v in SAFE.items()),
         "PROTOCOL_PLAN_CANNOT_GRANT_MARKET_AUTHORITY")
    return plan


def _owner(run, owner):
    if owner is None:
        return "SAVED_CONTENT_CONSISTENCY_ONLY"
    need(run["strategy_id"] == R2, "PROTOCOL_R1_GENUINE_SOURCE_OWNER_NOT_SUPPORTED_BY_R2_BRIDGE")
    from scripts.codex_r2_execution_v1 import _owner_terminal
    return _owner_terminal(owner, run)


def _base_facts(left, right):
    # Reuse the frozen pair contract's metadata exclusions. No predicate,
    # threshold, liquidity formula or selector is copied or re-executed here.
    from stockfish_core.yaogu.physical_historical_pair_v1 import _ROW_OVERRIDES, _R2_ROW_ADDITIONS
    need(set(right) == set(left) | _R2_ROW_ADDITIONS, "PROTOCOL_PAIR_ROW_FIELDS_CHANGED")
    original, projected = {}, {}
    for key, value in left.items():
        if key in _ROW_OVERRIDES:
            continue
        original[key] = value
        if key in ("exact_features", "conditions"):
            need(type(value) is dict and type(right[key]) is dict
                 and set(right[key]) == set(value) | {"recent_participation", "current_liquidity"},
                 "PROTOCOL_PAIR_BASE_MAPPING_CHANGED")
            projected[key] = {name: right[key][name] for name in value}
        else:
            projected[key] = right[key]
    need(original == projected, "PROTOCOL_PAIR_BASE_FACTS_CHANGED")
    need(right.get("r1_decision_status") == left["decision_status"]
         and right.get("r1_research_selected") is left["research_selected"], "PROTOCOL_PAIR_BASE_DECISION_CHANGED")
    state = right.get("participation_status")
    need(state in ("PASS", "FAIL", "UNKNOWN") and (right.get("participation") or {}).get("status") == state,
         "PROTOCOL_PAIR_PARTICIPATION_STATE")
    need(not right["research_selected"] or left["research_selected"], "PROTOCOL_R2_ADDED_SELECTION")
    return digest(original)


def _mean(items, indexed, horizon):
    row = coverage._cohort(items, indexed, (horizon,))["horizons"][0]
    return dict(mean=row["mean_close_reference_change_mature_only"] if items and row["states"]["MATURE"] == len(items) else None,
                population=len(items), states=row["states"], observed_subset_mean=row["mean_close_reference_change_mature_only"])


def _risk_baseline(items, selected, indexed, horizon, qualification, spec):
    axes, bands = spec["categorical_axes"], [coverage._fraction(x) for x in spec["amount_boundaries_cny"]]
    exchanges = {row["instrument_id"]: row.get("exchange") for row in qualification["selected_records"]}
    strata = defaultdict(list)
    for row in items:
        key = []
        for axis in axes:
            value = exchanges.get(row["instrument_id"]) if axis == "exchange" else row.get("primary_stage")
            if value is None:
                return dict(status="UNKNOWN", reason="MISSING_FROZEN_RISK_AXIS", axis=axis, mean=None)
            key.append(str(value))
        if bands:
            value = (row.get("exact_features") or {}).get("amount")
            if value is None:
                return dict(status="UNKNOWN", reason="MISSING_FROZEN_AMOUNT", mean=None)
            number = coverage._fraction(value)
            need(number >= 0, "PROTOCOL_NEGATIVE_RISK_AMOUNT")
            key.append("amount_band_" + str(sum(number >= boundary for boundary in bands)))
        strata[tuple(key)].append(row)
    selected_ids = {row["instrument_id"] for row in selected}
    details, weighted = [], Fraction(0)
    known = bool(selected)
    for key, rows in sorted(strata.items()):
        count = sum(row["instrument_id"] in selected_ids for row in rows)
        summary = _mean(rows, indexed, horizon)
        details.append(dict(stratum=list(key), eligible_count=len(rows), selected_count=count, **summary))
        if count:
            known = known and summary["mean"] is not None
            if summary["mean"] is not None:
                weighted += Fraction(str(summary["mean"])) * count
    return dict(status="CONDITIONAL_COMPLETE" if known else "UNKNOWN", mean=float(weighted / len(selected)) if known else None,
                strata=details, role="FULL_STRATUM_MEAN_WEIGHTED_BY_FROZEN_SELECTED_COUNTS",
                included_selected_names=True, independence_claimed=False,
                missing_risk_or_outcomes_never_shrink_pool=True)


def _registered_axis():
    from stockfish_core.observed_data import exchange_calendar_2026
    raw, reference = io.files._read_file(ROOT / "stockfish_core/exchange_calendar_2026.json", maximum=64 * 1024)
    metadata = io.files._json(raw)
    schedule = exchange_calendar_2026()
    io.verify_snapshots([reference])
    evidence = dict(policy="REGISTERED_AXIS_CHECKED_AGAINST_FINITE_OFFICIAL_2026_SCHEDULE",
        source_hash=reference["sha256"], sources=metadata["sources"], verified_at=metadata["verified_at"],
        coverage_start=str(schedule.coverage_start), coverage_end=str(schedule.coverage_end))
    return schedule, evidence, reference


def _check_label_axis(run, outcomes):
    if outcomes is None:
        return False
    schedule, expected, reference = _registered_axis()
    evidence = outcomes["calendar_evidence"]
    if not isinstance(evidence, dict) or any(evidence.get(key) != value for key, value in expected.items()):
        return False
    signal = iso_day(run["observation_date"])
    need(signal in schedule.sessions, "PROTOCOL_SIGNAL_OUTSIDE_REGISTERED_CALENDAR")
    future = schedule.sessions[schedule.sessions.index(signal) + 1:]
    for row in outcomes["items"]:
        window = [str(day) for day in future[:row["horizon"]]]
        observed = [state["session"] for state in row["session_states"]]
        need(observed == window[:len(observed)], "PROTOCOL_OUTCOME_SKIPPED_OR_WRONG_EXCHANGE_SESSION")
        if row["label_end"] is not None:
            need(len(window) == row["horizon"] and row["label_end"] == window[-1],
                 "PROTOCOL_OUTCOME_LABEL_END_NOT_REGISTERED_HORIZON")
    io.verify_snapshots([reference])
    return True


def compare_pair(left, right, *, left_outcomes=None, right_outcomes=None,
                 expected_horizons=coverage.HORIZONS, risk_matching=None,
                 left_owner=None, right_owner=None):
    """Compare complete saved arms, preserving every eligible and unknown row."""
    need(left.get("strategy_id") == R1 and right.get("strategy_id") == R2, "PROTOCOL_R1_R2_PAIR_REQUIRED")
    if risk_matching is not None:
        risk_spec(risk_matching)
    lreport = coverage.build_coverage_report(left, left_outcomes, expected_horizons=expected_horizons)
    rreport = coverage.build_coverage_report(right, right_outcomes, expected_horizons=expected_horizons)
    before = digest([left, right, left_outcomes, right_outcomes])
    roles = [_owner(left, left_owner), _owner(right, right_owner)]
    for key in ("input_id", "input_hash", "observation_date", "decision_cutoff", "universe_hash", "qualification", "coverage_type", "history_artifact_ref", "history_artifact_hash"):
        need(left.get(key) == right.get(key), "PROTOCOL_PAIR_PARENT_MISMATCH:" + key)
    a, b = left["results"][R1], right["results"][R2]
    for run, arm in ((left, a), (right, b)):
        need(all(arm.get(key) == run.get(key) for key in IDENTITY_FIELDS)
             and type(arm.get("source_file_hashes")) is dict
             and arm.get("source_hash") == digest(arm["source_file_hashes"]), "PROTOCOL_ARM_SOURCE_IDENTITY_MISMATCH")
    provenance = b.get("base_run_provenance") or {}
    need(provenance.get("base_source_hash") == a.get("source_hash") and a.get("source_hash")
         and provenance.get("input_hash") == left["input_hash"]
         and provenance.get("base_decision_cutoff") == left["decision_cutoff"]
         and provenance.get("new_context_cutoff") == left["decision_cutoff"], "PROTOCOL_R2_NATIVE_BASE_IDENTITY_REQUIRED")
    need(a.get("source_file_hashes") and all(b.get("source_file_hashes", {}).get(k) == v for k, v in a["source_file_hashes"].items()),
         "PROTOCOL_PAIR_BASE_SOURCE_DEPENDENCY_CHANGED")
    if provenance.get("mode") == "NATIVE_R1_EVALUATION":
        from stockfish_core.yaogu.physical_historical_pair_v1 import _native_r1_parent
        # This helper is a pure saved-value projection. The namespace supplies
        # two history addresses only and is never passed as a source capability.
        parent = _native_r1_parent(a, SimpleNamespace(history_artifact_ref=left.get("history_artifact_ref"),
            history_artifact_hash=left.get("history_artifact_hash")), left["input_hash"])
    else:
        need(provenance.get("mode") == "VERIFIED_FROZEN_R1_INLINE_STYLE"
             and provenance.get("base_run_content_hash") == digest(left), "PROTOCOL_UNSUPPORTED_R1_PARENT_BINDING")
        parent = a
    need(b.get("base_r1_result_hash") == provenance.get("base_result_hash") == digest(parent),
         "PROTOCOL_R2_FROZEN_PARENT_CONTENT_MISMATCH")
    lrows = {r["instrument_id"]: r for r in a["items"]}
    rrows = {r["instrument_id"]: r for r in b["items"]}
    need(set(lrows) == set(rrows), "PROTOCOL_PAIR_ELIGIBLE_POOL_MISMATCH")
    transitions, detail = Counter(), []
    for iid in sorted(lrows):
        lrow, rrow = lrows[iid], rrows[iid]
        facts = _base_facts(lrow, rrow)
        transition = lrow["decision_status"] + "->" + rrow["decision_status"]
        transitions[transition] += 1
        detail.append(dict(instrument_id=iid, primary_stage=lrow.get("primary_stage"), base_facts_hash=facts,
            r1_selected=lrow["research_selected"], r2_selected=rrow["research_selected"],
            r1_decision=lrow["decision_status"], r2_decision=rrow["decision_status"], participation=rrow["participation_status"]))
    li, _ = coverage._outcomes(left, a["items"], left_outcomes, tuple(expected_horizons))
    ri, _ = coverage._outcomes(right, b["items"], right_outcomes, tuple(expected_horizons))
    calendar_labels_checked = all((_check_label_axis(left, left_outcomes), _check_label_axis(right, right_outcomes)))
    if left_outcomes is not None and right_outcomes is not None:
        for key in ("outcome_input_id", "calendar_hash", "calendar_evidence", "outcome_coverage_type", "evaluated_at"):
            need(left_outcomes.get(key) == right_outcomes.get(key), "PROTOCOL_PAIR_OUTCOME_PARENT_MISMATCH:" + key)
        for key in li:
            for field in ("status", "metrics", "session_states", "label_end", "calendar_mature"):
                need(li[key].get(field) == ri[key].get(field), "PROTOCOL_PAIR_OUTCOME_FACTS_CHANGED")
    selected_l = [r for r in a["items"] if r["research_selected"]]
    selected_r = [r for r in b["items"] if r["research_selected"]]
    observations = {}
    for horizon in expected_horizons:
        measures = {"R1": _mean(selected_l, li, horizon), "R2": _mean(selected_r, ri, horizon),
                    "ELIGIBLE": _mean(b["items"], ri, horizon)}
        matching = _risk_baseline(b["items"], selected_r, ri, horizon, right["qualification"], risk_matching) if risk_matching else dict(status="NOT_REQUESTED", mean=None)
        daily = {"R2_MINUS_R1": (measures["R2"]["mean"], measures["R1"]["mean"]),
                 "R2_MINUS_ELIGIBLE": (measures["R2"]["mean"], measures["ELIGIBLE"]["mean"]),
                 "R2_MINUS_RISK_MATCHED": (measures["R2"]["mean"], matching["mean"])}
        stages = {}
        for stage in STAGES:
            ls = _mean([r for r in selected_l if r.get("primary_stage") == stage], li, horizon)
            rs = _mean([r for r in selected_r if r.get("primary_stage") == stage], ri, horizon)
            stages[stage] = {"R1": ls, "R2": rs}
            daily["R2_MINUS_R1:" + stage] = (rs["mean"], ls["mean"])
        observations[str(horizon)] = dict(cohorts=measures, stage_cohorts=stages, risk_matched=matching,
            differences={key: left_value - right_value if left_value is not None and right_value is not None else None
                         for key, (left_value, right_value) in daily.items()},
            metric="CLOSE_REFERENCE_CHANGE_NOT_ACCOUNT_RETURN", empty_selection_is_zero=False)
    need(before == digest([left, right, left_outcomes, right_outcomes]), "PROTOCOL_PAIR_INPUT_CHANGED")
    need(roles == [_owner(left, left_owner), _owner(right, right_owner)], "PROTOCOL_SOURCE_OWNER_CHANGED")
    return seal(dict(schema_version=VERSION + "_PAIR", status="DESCRIPTIVE_SAVED_PAIR",
        observation_date=left["observation_date"], decision_cutoff=left["decision_cutoff"],
        input_hash=left["input_hash"], universe_hash=left["universe_hash"],
        strategy_identities={run["strategy_id"]: {key: run.get(key) for key in IDENTITY_FIELDS} for run in (left, right)},
        risk_matching=deepcopy(risk_matching),
        run_hashes={R1: left["content_hash"], R2: right["content_hash"]},
        source_owner_roles=roles, native_source_owners_checked=all(owner is not None for owner in (left_owner, right_owner)),
        r1_source_authentication="NOT_AVAILABLE_FROM_EXISTING_R2_ARCHIVE_BRIDGE",
        outcome_calendar_evidence=deepcopy(left_outcomes["calendar_evidence"]) if left_outcomes is not None and right_outcomes is not None else None,
        outcome_calendar_label_windows_checked=calendar_labels_checked,
        coverage={R1: lreport, R2: rreport}, full_pool=detail,
        decision_transitions=dict(sorted(transitions.items())), observations=observations,
        comparison_scope="SAME_DECLARED_INPUT_CUTOFF_ELIGIBILITY_BASE_FACTS_AND_OUTCOMES",
        **SAFE))


def _calendar_context(window, declared, pairs):
    """Reuse the same finite official axis checked by the old outcome evaluator.

    Saved evidence must bind that axis's exact source bytes. Caller date lists
    never create exchange-session indexes and cannot omit intervening sessions.
    No extrapolation outside the existing calendar's registered year is allowed.
    """
    schedule, expected_evidence, reference = _registered_axis()
    issues = []
    covered = schedule.coverage_start <= iso_day(window["start"]) <= iso_day(window["end"]) <= schedule.coverage_end
    if not covered:
        issues.append("OUTSIDE_FINITE_REGISTERED_CALENDAR")
    if now().astimezone(ZoneInfo("Asia/Shanghai")).date() < iso_day(expected_evidence["verified_at"]):
        issues.append("CALENDAR_NOT_VERIFIED_AT_ACTUAL_EVALUATION")
    full = [str(day) for day in schedule.sessions if window["start"] <= str(day) <= window["end"]] if covered else list(declared)
    undeclared = sorted(set(full) - set(declared)) if covered else []
    nontrading = sorted(set(declared) - set(full)) if covered else []
    if undeclared or nontrading:
        issues.append("DECLARED_DATES_DIFFER_FROM_COMPLETE_REGISTERED_WINDOW")
    evidence_missing = []
    for pair in pairs:
        evidence = pair.get("outcome_calendar_evidence")
        if pair.get("outcome_calendar_label_windows_checked") is not True or not isinstance(evidence, dict) or any(evidence.get(key) != value for key, value in expected_evidence.items()):
            evidence_missing.append(pair["observation_date"])
    if evidence_missing or not pairs:
        issues.append("MATCHED_OUTCOME_CALENDAR_EVIDENCE_NOT_PROVIDED_OR_MISMATCHED")
    indexes = {str(day): index for index, day in enumerate(schedule.sessions)} if covered else {}
    return dict(status="REGISTERED_CALENDAR_BOUND" if not issues else "BLOCKED_CALENDAR", sessions=full,
        global_session_indexes={day: indexes.get(day) for day in full},
        undeclared_trading_sessions=undeclared, declared_nontrading_sessions=nontrading,
        missing_or_mismatched_evidence_dates=evidence_missing, reasons=issues,
        calendar_source=expected_evidence, provider_calendar_reauthenticated_now=False), reference


def evaluate_study(plan, pairs, *, phase="validation"):
    """Use the existing block estimator; absent dates remain None in every arm."""
    validate_plan(plan)
    need(phase in ("validation", "holdout"), "PROTOCOL_PHASE_REQUIRED")
    draft = plan["draft"]
    window = draft["windows"][phase]
    declared = [day for day in draft["expected_signal_sessions"] if window["start"] <= day <= window["end"]]
    need(type(pairs) is list and len(pairs) <= MAX_DAYS, "PROTOCOL_BOUNDED_PAIR_LIST_REQUIRED")
    by_day = {}
    for pair in pairs:
        check_seal(pair, VERSION + "_PAIR")
        day = pair["observation_date"]
        need(day in declared and day not in by_day, "PROTOCOL_UNEXPECTED_OR_DUPLICATE_SIGNAL_DAY")
        need(set(pair["observations"]) == {str(h) for h in draft["horizons"]}, "PROTOCOL_PAIR_HORIZON_MISMATCH")
        need(pair.get("strategy_identities") == draft["strategy_identities"], "PROTOCOL_UNREGISTERED_STRATEGY_IDENTITY")
        need(pair.get("risk_matching") == draft["risk_matching"], "PROTOCOL_UNREGISTERED_RISK_MATCHING")
        need(all(pair.get(key) == value for key, value in SAFE.items()), "PROTOCOL_PAIR_MARKET_AUTHORITY_FORBIDDEN")
        need(all(pair["coverage"][strategy]["observation_date"] == day
                 and pair["coverage"][strategy]["signal_content_hash"] == pair["run_hashes"][strategy]
                 for strategy in (R1, R2)), "PROTOCOL_PAIR_DATE_OR_RUN_BINDING_MISMATCH")
        by_day[day] = pair
    calendar, calendar_reference = _calendar_context(window, declared, pairs)
    sessions = calendar["sessions"]
    series = {}
    for comparison in draft["comparison_family"]:
        for horizon in draft["horizons"]:
            key = comparison + ":H" + str(horizon)
            series[key] = [dict(date=day, session_index=calendar["global_session_indexes"][day],
                difference=by_day[day]["observations"][str(horizon)]["differences"][comparison] if day in by_day else None,
                exit_delay=0, inference_eligible=calendar["status"] == "REGISTERED_CALENDAR_BOUND",
                source_state="SAVED_PAIR_PRESENT" if day in by_day else "MISSING_SIGNAL_DAY") for day in sessions]
    from stockfish_core.yaogu.research_r1_io import paired_block_comparisons
    sensitivity = {str(block): paired_block_comparisons(series, seed=draft["seed"], block_length=block,
        repetitions=draft["bootstrap_repetitions"]) for block in draft["block_lengths"]}
    primary_key = draft["primary_comparison"] + ":H" + str(draft["primary_horizon"])
    primary = sensitivity[str(draft["primary_block_length"])][primary_key]
    io.verify_snapshots([calendar_reference])
    return seal(dict(schema_version=VERSION + "_STUDY", plan_hash=plan["content_hash"], phase=phase,
        status="CONDITIONAL_ARCHIVE_ESTIMATE" if primary["status"] == "ESTIMATED" else "BLOCKED_DATA",
        expected_dates=sessions, declared_dates=declared, calendar=calendar,
        missing_dates=[day for day in sessions if day not in by_day],
        observed_pair_hashes={day: pair["content_hash"] for day, pair in sorted(by_day.items())},
        series=series, block_length_sensitivity=sensitivity, primary_key=primary_key,
        primary_block_length=draft["primary_block_length"], primary_result=primary,
        family_policy="HOLM_OVER_ALL_REGISTERED_COMPARISONS_AND_HORIZONS_PER_BLOCK",
        sensitivity_policy="ALL_REGISTERED_BLOCKS_REPORTED_NO_BEST_BLOCK_SELECTION",
        calendar_role=plan["calendar_role"], metric="MARKED_REFERENCE_PRICE_CHANGE_NOT_TRADING_RETURN",
        sample_is_independent_holdout_verified=False, missing_imputation="NONE",
        two_blocks_are_computational_minimum_not_statistical_sufficiency=True, **SAFE))


def execution_readiness(run):
    from scripts.codex_r2_execution_v1 import readiness
    full = coverage.build_coverage_report(run)
    original = readiness(run, expected_content_hash=run["content_hash"])
    return seal(dict(schema_version=VERSION + "_EXECUTION_READINESS", status="BLOCKED_DATA",
        original_readiness=original, full_pool_coverage=full,
        layers={"saved_signal_integrity": "CHECKED", "current_signal_source_owner": "NOT_PROVIDED",
            "execution_provider_authentication": "NOT_ESTABLISHED", "historical_pit": "NOT_RESTORED",
            "dated_rules_costs_actions": "NOT_PROVIDED", "auction_capacity": "UNKNOWN",
            "complete_theme_risk": "UNKNOWN", "independent_market_validation": "BLOCKED"},
        **SAFE))


def execution_scenarios(run, scenarios, *, source_owner=None, execution_archive=None,
                        archive_base=None, allow_test_fixture=False, max_total_read_bytes=128 * 1024**2):
    """Explicit conditional scenarios reuse the old account engine unchanged.

    Capital/slippage scenarios are not a capacity model. JSON never supplies a
    native owner. Existing T+1, fees, limits and unknown exits remain binding.
    """
    from scripts.codex_r2_execution_v1 import evaluate_r2
    need(type(scenarios) is list and 0 < len(scenarios) <= MAX_SCENARIOS, "PROTOCOL_SCENARIO_BOUND")
    need(type(max_total_read_bytes) is int and 0 < max_total_read_bytes <= 128 * 1024**2,
         "PROTOCOL_TOTAL_EXECUTION_READ_BUDGET")
    full = coverage.build_coverage_report(run)
    identifiers, results, used = [], [], 0
    for scenario in scenarios:
        need(type(scenario) is dict and set(scenario) == {"scenario_id", "execution_spec", "slippage_bps"},
             "PROTOCOL_SCENARIO_FIELDS")
        identifiers.append(nonempty(scenario["scenario_id"], "PROTOCOL_SCENARIO_ID"))
    need(len(identifiers) == len(set(identifiers)), "PROTOCOL_DUPLICATE_SCENARIO")
    for scenario in scenarios:
        result = evaluate_r2(run, expected_content_hash=run["content_hash"], source_owner=source_owner,
            execution_archive=execution_archive, archive_base=archive_base, allow_test_fixture=allow_test_fixture,
            execution_spec=scenario["execution_spec"], slippage_bps=scenario["slippage_bps"],
            max_read_bytes=max_total_read_bytes // len(scenarios))
        used += result.get("verified_read_bytes", 0)
        need(used <= max_total_read_bytes, "PROTOCOL_TOTAL_EXECUTION_READ_BUDGET")
        need(result.get("evaluation_admissible_for_market_validation") is False
             and result.get("strategy_account_return", result.get("account_return")) is None,
             "PROTOCOL_EXECUTION_CANNOT_PROMOTE_MARKET_RETURN")
        results.append(dict(scenario_id=scenario["scenario_id"], result=result))
    completed = sum(result["result"]["status"].startswith("SIMULATED_") for result in results)
    return seal(dict(schema_version=VERSION + "_EXECUTION_SCENARIOS", status="CONDITIONAL_SIMULATION_ONLY" if completed == len(results) else "BLOCKED_DATA",
        simulated_scenarios=completed,
        scenarios=results, full_pool_coverage=full, verified_read_bytes=used,
        capacity_status="UNKNOWN_AUCTION_PARTICIPATION_AND_IMPACT_NOT_AUTHENTICATED",
        conditional_account_returns_are_not_realized_strategy_returns=True, **SAFE))


def _read_pair(record, references, budget, horizons, matching):
    need(type(record) is dict and set(record) <= {"left", "right", "left_outcomes", "right_outcomes"}
         and {"left", "right"} <= set(record), "PROTOCOL_PAIR_CARRIERS_REQUIRED")
    values = {}
    for key in ("left", "right", "left_outcomes", "right_outcomes"):
        if key not in record:
            continue
        carrier = record[key]
        need(type(carrier) is dict and set(carrier) == {"path", "sha256"}, "PROTOCOL_CARRIER_FIELDS")
        value, reference = io.read_snapshot(carrier["path"], carrier["sha256"],
            max_stored=min(io.MAX_STORED_BYTES, max(0, budget[0] // 4)),
            max_json=min(io.MAX_JSON_BYTES, max(0, budget[1])))
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        # Carrier read plus post-compute and two publication terminal reads.
        # Retained canonical JSON is separate from stored carrier I/O. Inflated
        # gzip text is additionally bounded per call by the original reader.
        budget[0] -= 4 * reference["fp"]["st_size"]
        budget[1] -= size
        need(min(budget) >= 0, "PROTOCOL_BATCH_READ_BUDGET")
        values[key] = value
        references.append(reference)
    return compare_pair(**values, expected_horizons=horizons, risk_matching=matching)


def parser():
    result = argparse.ArgumentParser(description="冻结研究计划、同源成对覆盖与条件评价；不采集、不重选", allow_abbrev=False)
    commands = result.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", allow_abbrev=False)
    pair = commands.add_parser("pair", allow_abbrev=False)
    study = commands.add_parser("study", allow_abbrev=False)
    ready = commands.add_parser("execution-readiness", allow_abbrev=False)
    for command, fields in ((freeze, ("draft",)), (pair, ("plan", "pair")), (study, ("plan", "batch")), (ready, ("run",))):
        for field in fields:
            command.add_argument("--" + field, required=True)
            command.add_argument("--" + field + "-sha256", required=True)
    freeze.add_argument("--previous")
    freeze.add_argument("--previous-sha256")
    study.add_argument("--phase", choices=("validation", "holdout"), default="validation")
    for command in commands.choices.values():
        command.add_argument("--output", required=True, help="新私有JSON文件，父目录存在，不覆盖")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    references = []
    def read(name):
        value, reference = io.read_snapshot(getattr(args, name), getattr(args, name + "_sha256"))
        references.append(reference)
        if name in ("plan", "previous") and value.get("entry_version") == VERSION:
            value = value.get("result")
        return value
    try:
        implementation, implementation_refs = implementation_identity()
        references.extend(implementation_refs)
        if args.command == "freeze":
            need(bool(args.previous) == bool(args.previous_sha256), "PROTOCOL_PREVIOUS_PATH_HASH_TOGETHER")
            value = freeze_plan(read("draft"), previous=read("previous") if args.previous else None)
        elif args.command == "execution-readiness":
            value = execution_readiness(read("run"))
        else:
            plan = validate_plan(read("plan"))
            draft = plan["draft"]
            budget = [MAX_BATCH_READ_BYTES, MAX_BATCH_READ_BYTES]
            if args.command == "pair":
                value = _read_pair(read("pair"), references, budget, draft["horizons"], draft["risk_matching"])
                need(value["strategy_identities"] == draft["strategy_identities"], "PROTOCOL_UNREGISTERED_STRATEGY_IDENTITY")
            else:
                batch = read("batch")
                need(set(batch) == {"plan_hash", "pairs"} and batch["plan_hash"] == plan["content_hash"]
                     and type(batch["pairs"]) is list and len(batch["pairs"]) <= MAX_DAYS,
                     "PROTOCOL_BATCH_PLAN_BINDING")
                pairs = [_read_pair(record, references, budget, draft["horizons"], draft["risk_matching"]) for record in batch["pairs"]]
                value = evaluate_study(plan, pairs, phase=args.phase)
        io.verify_snapshots(references)
        envelope = dict(entry_version=VERSION, inspected_at=now().isoformat(), result=value,
            implementation=implementation, input_carriers=references, network_calls=0,
            business_database_opened=False, **SAFE)
        io.publish_new(args.output, envelope, validate=lambda: io.verify_snapshots(references))
        print(json.dumps(dict(status=value.get("status", value.get("design_timing")), output=str(Path(args.output).absolute()),
                             market_edge="NOT_ESTABLISHED"), ensure_ascii=False))
        return 4 if value.get("status") == "BLOCKED_DATA" else 0
    except Exception as error:
        reason = str(error)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,160}", reason) is None:
            reason = "PROTOCOL_EVIDENCE_REJECTED_OR_UNAVAILABLE"
        print(json.dumps(dict(status="REJECTED", reason=reason, error_type=type(error).__name__), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
