"""Read-only coverage audit of complete saved short-horizon research products.

The caller owns safe file reads and any source authentication. Canonical hashes
and cross-product checks establish saved-content consistency only. They never
restore source authority, historical PIT, fills, or an investment conclusion.
No strategy imports, network calls, database access, or filesystem writes occur.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, time
from fractions import Fraction
import hashlib
import json
import math
from zoneinfo import ZoneInfo

VERSION = "CODEX_RESEARCH_COVERAGE_V1"
STRATEGIES = frozenset(("SHORT_HORIZON_STRENGTH_R1", "SHORT_HORIZON_STRENGTH_R2"))
HORIZONS = (1, 2, 3, 5, 10)
DECISIONS = ("FOCUS", "WATCH", "REJECTED", "UNKNOWN")
OUTCOME_STATES = ("MATURE", "PENDING", "UNKNOWN", "NOT_PROVIDED")
METRICS = frozenset(("close_reference_change", "next_open_gap", "next_open_reference_change",
                     "mfe_bound", "mae_bound"))
OUTCOME_IDENTITY = ("schema_version", "run_id", "signal_content_hash", "signal_input_id",
    "outcome_input_id", "horizons", "calendar_hash", "calendar_evidence",
    "evaluator_source_hash", "dependency_source_hashes", "calendar_implementation_hashes",
    "effective_evidence")


class CoverageError(ValueError):
    """The supplied saved products are incomplete or inconsistent."""


def _need(condition, reason):
    if not condition:
        raise CoverageError(reason)


def _digest(value):
    # Same JSON-shape canonical options as the frozen run/outcome contracts.
    # Do not coerce arbitrary objects through default=str in a saved-JSON reader.
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256()
    try:
        for chunk in encoder.iterencode(value):
            digest.update(chunk.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as error:
        raise CoverageError("COVERAGE_JSON_SHAPE_REQUIRED") from error
    return digest.hexdigest()


def _count(value, reason):
    _need(type(value) is int and value >= 0, reason)
    return value


def _ids(values, reason):
    _need(type(values) is list and all(type(v) is str and v for v in values), reason)
    _need(len(values) == len(set(values)), reason)
    return values


def _reasons(value):
    return _ids(value, "COVERAGE_REASON_CODES_INVALID")


def _aware(value):
    _need(type(value) is str, "COVERAGE_AWARE_TIME_REQUIRED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CoverageError("COVERAGE_AWARE_TIME_REQUIRED") from error
    _need(result.utcoffset() is not None, "COVERAGE_AWARE_TIME_REQUIRED")
    return result


def _ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "fraction": numerator / denominator if denominator else None}


def _fraction(value):
    if type(value) is dict and set(value) == {"numerator", "denominator"}:
        _need(type(value["numerator"]) is int and type(value["denominator"]) is int
              and value["denominator"] > 0, "COVERAGE_FRACTION_INVALID")
        return Fraction(value["numerator"], value["denominator"])
    _need(type(value) in (int, float, str), "COVERAGE_FRACTION_INVALID")
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise CoverageError("COVERAGE_FRACTION_INVALID") from error


def _qualification(run, arm):
    qualification = run.get("qualification")
    _need(type(qualification) is dict and qualification == arm.get("qualification"),
          "COVERAGE_QUALIFICATION_MISMATCH")
    eligible = _ids(qualification.get("eligible_ids"), "COVERAGE_ELIGIBLE_IDS_INVALID")
    _need(eligible == arm.get("eligible_pool_ids")
          and arm.get("full_eligible_pool_retained") is True,
          "COVERAGE_COMPLETE_ELIGIBLE_POOL_REQUIRED")
    partitions = {}
    for name in ("known_excluded", "eligibility_unknown"):
        records = qualification.get(name + "_records")
        _need(type(records) is list and all(type(row) is dict for row in records),
              "COVERAGE_QUALIFICATION_RECORDS_REQUIRED")
        ids = _ids([row.get("instrument_id") for row in records],
                   "COVERAGE_QUALIFICATION_DUPLICATE_OR_INVALID_ID")
        _need(all(type(row.get("reason")) is str and row["reason"] for row in records),
              "COVERAGE_QUALIFICATION_REASON_REQUIRED")
        partitions[name] = ids
    all_ids = eligible + partitions["known_excluded"] + partitions["eligibility_unknown"]
    _need(len(all_ids) == len(set(all_ids)), "COVERAGE_QUALIFICATION_PARTITIONS_OVERLAP")
    absent = _count(qualification.get("identity_absent"), "COVERAGE_IDENTITY_ABSENT_INVALID")
    expected = {"eligible": len(eligible), "known_excluded": len(partitions["known_excluded"]),
        "eligibility_unknown": len(partitions["eligibility_unknown"]) + absent,
        "identity_observed": len(all_ids), "master_expected": len(all_ids) + absent}
    for key, number in expected.items():
        _need(type(qualification.get(key)) is int and qualification[key] == number,
              "COVERAGE_QUALIFICATION_COUNT_MISMATCH_" + key.upper())
    _need(run.get("universe_hash") == _digest(qualification), "COVERAGE_UNIVERSE_HASH_MISMATCH")
    records = qualification.get("selected_records")
    _need(type(records) is list and all(type(row) is dict for row in records),
          "COVERAGE_SELECTED_IDENTITY_RECORDS_REQUIRED")
    record_ids = _ids([row.get("instrument_id") for row in records],
                      "COVERAGE_SELECTED_IDENTITY_DUPLICATE")
    _need(set(record_ids) <= set(all_ids) and set(eligible) <= set(record_ids),
          "COVERAGE_SELECTED_IDENTITY_SCOPE_MISMATCH")
    exchanges = {row["instrument_id"]: row.get("exchange") for row in records}
    # The existing qualified run contains explicit exchange evidence. Never
    # infer an exchange from a code prefix or silently manufacture identity.
    _need(all(exchanges.get(iid) in ("SSE", "SZSE") for iid in eligible),
          "COVERAGE_QUALIFIED_EXCHANGE_REQUIRED")
    by_exchange = defaultdict(Counter)
    for population, members in (("eligible", eligible), *partitions.items()):
        for iid in members:
            exchange = exchanges.get(iid)
            label = exchange if type(exchange) is str and exchange else "UNKNOWN"
            by_exchange[label][population] += 1
    if absent:
        # Unknown securities have no fabricated code or exchange attribution.
        by_exchange["UNOBSERVED_IDENTITY"]["identity_absent"] = absent
    exchange_summary = [{"exchange": exchange, "declared_population": sum(counts.values()),
        **{key: counts[key] for key in ("eligible", "known_excluded", "eligibility_unknown", "identity_absent")}}
        for exchange, counts in sorted(by_exchange.items())]
    summary = {**expected, "identity_absent": absent,
        "by_recorded_exchange": exchange_summary,
        "known_exclusion_reasons": dict(sorted(Counter(row["reason"] for row in
            qualification["known_excluded_records"]).items())),
        "unknown_qualification_reasons": dict(sorted(Counter(row["reason"] for row in
            qualification["eligibility_unknown_records"]).items())),
        "universe_evidence_reason_codes": deepcopy(qualification.get("universe_evidence_reason_codes", [])),
        "saved_qualification_has_no_reported_gaps": expected["eligibility_unknown"] == 0
            and not qualification.get("universe_evidence_reason_codes"),
        "legal_empty_source_verified": False,
        "qualification_does_not_prove_price_or_context_readiness": True}
    return eligible, exchanges, summary


def _signal(run):
    _need(type(run) is dict and run.get("strategy_id") in STRATEGIES
          and run.get("execution_profile") == "SHORT_HORIZON_RESEARCH_SINGLE",
          "COVERAGE_FULL_SHORT_HORIZON_RUN_REQUIRED")
    _need(not any(key in run for key in ("view_content_hash", "export_view")),
          "COVERAGE_FILTERED_VIEW_FORBIDDEN")
    expected_hash = _digest({key: value for key, value in run.items() if key != "content_hash"})
    _need(run.get("content_hash") == expected_hash, "COVERAGE_RUN_CONTENT_HASH_MISMATCH")
    for key in ("run_id", "input_id", "input_hash"):
        _need(type(run.get(key)) is str and run[key], "COVERAGE_RUN_IDENTITY_REQUIRED")
    _need(run.get("coverage_type") in ("TEST_FIXTURE", "PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL"),
          "COVERAGE_SIGNAL_COVERAGE_TYPE_UNSUPPORTED")
    try:
        date.fromisoformat(run["observation_date"])
    except (ValueError, KeyError, TypeError) as error:
        raise CoverageError("COVERAGE_OBSERVATION_DATE_REQUIRED") from error
    _aware(run.get("decision_cutoff"))
    arm = (run.get("results") or {}).get(run["strategy_id"])
    _need(type(arm) is dict and arm.get("strategy_id") == run["strategy_id"]
          and arm.get("input_hash") == run["input_hash"]
          and arm.get("observation_date") == run["observation_date"]
          and arm.get("decision_cutoff") == run["decision_cutoff"], "COVERAGE_ARM_BINDING_MISMATCH")
    eligible, exchanges, qualification = _qualification(run, arm)
    items = arm.get("items")
    _need(type(items) is list and all(type(row) is dict for row in items), "COVERAGE_ITEMS_REQUIRED")
    ids = _ids([row.get("instrument_id") for row in items], "COVERAGE_DUPLICATE_OR_INVALID_INSTRUMENT")
    _need(set(ids) == set(eligible), "COVERAGE_ELIGIBLE_ITEMS_TRUNCATED_OR_EXTENDED")
    for row in items:
        _need(row.get("strategy_id") == run["strategy_id"]
              and row.get("observation_date") == run["observation_date"], "COVERAGE_ITEM_BINDING_MISMATCH")
        _need(type(row.get("research_selected")) is bool and row.get("decision_status") in DECISIONS,
              "COVERAGE_SIGNAL_STATE_INVALID")
        _need(row.get("formal_focus") is False and row["decision_status"] != "FOCUS",
              "COVERAGE_RESEARCH_RUN_CANNOT_PROMOTE_FORMAL_FOCUS")
        _need(row["research_selected"] == (row["decision_status"] == "WATCH"),
              "COVERAGE_RESEARCH_SELECTION_STATE_MISMATCH")
        _need(row.get("primary_stage") is None or type(row["primary_stage"]) is str,
              "COVERAGE_STAGE_INVALID")
        _reasons(row.get("reason_codes", []))
    selected = [row["instrument_id"] for row in items if row["research_selected"]]
    _need(arm.get("research_selected_ids") == selected, "COVERAGE_SELECTED_IDS_MISMATCH")
    counts = Counter(row["decision_status"] for row in items)
    declared = arm.get("counts") or {}
    for key, value in {"eligible": len(eligible), "evaluated": len(items), "focus": 0,
                       "watch": counts["WATCH"], "rejected": counts["REJECTED"],
                       "unknown": counts["UNKNOWN"], "research_observation": len(selected)}.items():
        _need(type(declared.get(key)) is int and declared[key] == value,
              "COVERAGE_SIGNAL_COUNT_MISMATCH_" + key.upper())
    _need(declared.get("decision_counts") == {key: counts[key] for key in DECISIONS},
          "COVERAGE_DECISION_COUNTS_MISMATCH")
    policy = run["strategy_id"] + "_OBSERVATION"
    selection = (run.get("selection_results") or {}).get(policy)
    _need(type(selection) is dict and selection.get("strategy_id") == run["strategy_id"]
          and selection.get("selected_ids") == selected and type(selection.get("count")) is int
          and selection["count"] == len(selected), "COVERAGE_SELECTION_PRODUCT_MISMATCH")
    _need([row.get("instrument_id") for row in selection.get("items", [])] == selected,
          "COVERAGE_SELECTION_PRODUCT_ITEMS_MISMATCH")
    return arm, items, exchanges, qualification, expected_hash


def _benchmarks(arm, eligible):
    benchmarks = arm.get("market_benchmarks")
    _need(type(benchmarks) is dict, "COVERAGE_BENCHMARK_METADATA_REQUIRED")
    result = []
    for window, value in sorted(benchmarks.items()):
        _need(type(value) is dict, "COVERAGE_BENCHMARK_METADATA_INVALID")
        total = _count(value.get("expected_count"), "COVERAGE_BENCHMARK_EXPECTED_INVALID")
        valid = _count(value.get("valid_count"), "COVERAGE_BENCHMARK_VALID_INVALID")
        _need(total == eligible and valid <= total, "COVERAGE_BENCHMARK_DENOMINATOR_MISMATCH")
        coverage = Fraction(valid, total) if total else Fraction(0)
        _need(_fraction(value.get("coverage")) == coverage, "COVERAGE_BENCHMARK_RATIO_MISMATCH")
        minimum = _fraction(value.get("minimum_coverage"))
        _need(0 <= minimum <= 1, "COVERAGE_BENCHMARK_THRESHOLD_INVALID")
        admitted = valid > 0 and coverage >= minimum
        expected_status = "COMPLETE" if admitted else "INSUFFICIENT_COVERAGE" if valid else "UNKNOWN"
        _need(value.get("status") == expected_status, "COVERAGE_BENCHMARK_STATUS_MISMATCH")
        _need(value.get("excluded_peer_count") == total - valid,
              "COVERAGE_BENCHMARK_EXCLUSION_COUNT_MISMATCH")
        result.append({"window": window, "original_status": value["status"],
            "valid_count": valid, "expected_count": total, "coverage": _ratio(valid, total),
            "minimum_coverage": deepcopy(value["minimum_coverage"]),
            "threshold_met": admitted, "full_pool_coverage": total > 0 and valid == total,
            "status_semantics": "THRESHOLD_MET_IS_NOT_FULL_POOL_COVERAGE",
            "exclusion_reason_counts": deepcopy(value.get("exclusion_reason_counts", {}))})
    return result


def _outcomes(run, items, outcomes, horizons):
    if outcomes is None:
        return {}, None
    _need(type(outcomes) is dict and outcomes.get("schema_version") == "SHORT_HORIZON_OUTCOME_V1"
          and outcomes.get("product") == "outcomes" and outcomes.get("status") == "AVAILABLE",
          "COVERAGE_FULL_OUTCOME_PRODUCT_REQUIRED")
    _need(outcomes.get("run_id") == run["run_id"]
          and outcomes.get("signal_content_hash") == run["content_hash"]
          and outcomes.get("signal_input_id") == run["input_id"]
          and outcomes.get("strategy_id") == run["strategy_id"]
          and outcomes.get("observation_date") == run["observation_date"]
          and outcomes.get("signal_coverage_type") == run["coverage_type"], "COVERAGE_OUTCOME_BINDING_MISMATCH")
    _need(outcomes.get("horizons") == list(horizons), "COVERAGE_OUTCOME_HORIZONS_MISMATCH")
    evaluated_at = _aware(outcomes.get("evaluated_at"))
    _need(evaluated_at >= max(_aware(run["decision_cutoff"]),
          _aware(run.get("generated_at", run["decision_cutoff"]))), "COVERAGE_OUTCOME_PRECEDES_SIGNAL")
    _need(all(key in outcomes for key in OUTCOME_IDENTITY), "COVERAGE_OUTCOME_IDENTITY_INCOMPLETE")
    _need(type(outcomes.get("outcome_input_id")) is str and outcomes["outcome_input_id"]
          and outcomes.get("outcome_coverage_type") in
              ("TEST_FIXTURE", "PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL"),
          "COVERAGE_OUTCOME_INPUT_IDENTITY_REQUIRED")
    rows = outcomes.get("items")
    _need(type(rows) is list and all(type(row) is dict for row in rows), "COVERAGE_OUTCOME_ITEMS_REQUIRED")
    identity = {key: outcomes[key] for key in OUTCOME_IDENTITY}
    identity["rows"] = rows
    _need(outcomes.get("revision_id") == "short_outcome_" + _digest(identity),
          "COVERAGE_OUTCOME_REVISION_HASH_MISMATCH")
    original = {row["instrument_id"]: row for row in items}
    indexed = {}
    for row in rows:
        iid, horizon = row.get("instrument_id"), row.get("horizon")
        _need(iid in original and type(horizon) is int and horizon in horizons,
              "COVERAGE_OUTCOME_SCOPE_MISMATCH")
        key = (iid, horizon)
        _need(key not in indexed, "COVERAGE_OUTCOME_DUPLICATE")
        for field in ("research_selected", "formal_focus", "primary_stage", "decision_status"):
            _need(type(row.get(field)) is type(original[iid].get(field))
                  and row.get(field) == original[iid].get(field), "COVERAGE_OUTCOME_FROZEN_SELECTION_MISMATCH")
        _need(row.get("run_id") == run["run_id"] and row.get("strategy_id") == run["strategy_id"],
              "COVERAGE_OUTCOME_ROW_BINDING_MISMATCH")
        _need(row.get("status") in OUTCOME_STATES[:3], "COVERAGE_OUTCOME_STATUS_INVALID")
        _reasons(row.get("reason_codes", []))
        window = row.get("session_states")
        _need(type(window) is list and len(window) <= horizon
              and all(type(state) is dict and state.get("status") in
                      ("OBSERVED", "PENDING", "UNKNOWN") for state in window),
              "COVERAGE_OUTCOME_SESSION_STATES_INVALID")
        dates = _ids([state.get("session") for state in window], "COVERAGE_OUTCOME_SESSION_IDS_INVALID")
        _need(dates == sorted(dates) and all(day > run["observation_date"] for day in dates),
              "COVERAGE_OUTCOME_SESSION_ORDER_INVALID")
        for day in dates:
            try:
                date.fromisoformat(day)
            except ValueError as error:
                raise CoverageError("COVERAGE_OUTCOME_SESSION_DATE_INVALID") from error
        session_counts = Counter(state["status"] for state in window)
        for field, state in (("observed_sessions", "OBSERVED"), ("pending_sessions", "PENDING"),
                             ("unknown_sessions", "UNKNOWN")):
            _need(type(row.get(field)) is int and row[field] == session_counts[state],
                  "COVERAGE_OUTCOME_SESSION_COUNTS_MISMATCH")
        _need(type(row.get("required_sessions")) is int and row["required_sessions"] == horizon,
              "COVERAGE_OUTCOME_REQUIRED_SESSIONS_MISMATCH")
        metrics = row.get("metrics")
        _need(type(metrics) is dict and set(metrics) == METRICS, "COVERAGE_OUTCOME_METRICS_REQUIRED")
        mature = row.get("calendar_mature")
        _need(mature is True or mature is False or mature is None, "COVERAGE_OUTCOME_MATURITY_INVALID")
        if mature is None:
            _need(row.get("label_end") is None, "COVERAGE_OUTCOME_UNKNOWN_CALENDAR_END")
        else:
            try:
                end = date.fromisoformat(row["label_end"])
            except (KeyError, TypeError, ValueError) as error:
                raise CoverageError("COVERAGE_OUTCOME_LABEL_END_INVALID") from error
            _need(str(end) > run["observation_date"]
                  and mature == (datetime.combine(end, time(15), ZoneInfo("Asia/Shanghai")) <= evaluated_at),
                  "COVERAGE_OUTCOME_CALENDAR_CLOCK_MISMATCH")
        if row["status"] == "MATURE":
            _need(row.get("calendar_mature") is True and session_counts["OBSERVED"] == horizon
                  and row.get("label_end") == dates[-1], "COVERAGE_MATURE_WINDOW_INCOMPLETE")
            _need(all(type(value) in (int, float) and math.isfinite(value) for value in metrics.values()),
                  "COVERAGE_MATURE_METRIC_REQUIRED")
        else:
            _need((row["status"] == "PENDING" and row.get("calendar_mature") is False)
                  or (row["status"] == "UNKNOWN" and (mature is True or mature is None)),
                  "COVERAGE_OUTCOME_MATURITY_STATE_MISMATCH")
            _need(all(value is None for value in metrics.values()), "COVERAGE_UNKNOWN_OR_PENDING_NOT_ZERO")
        indexed[key] = row
    _need(set(indexed) == {(iid, horizon) for iid in original for horizon in horizons},
          "COVERAGE_OUTCOME_CARTESIAN_POOL_INCOMPLETE")
    counts = Counter(row["status"] for row in rows)
    declared = outcomes.get("counts")
    _need(type(declared) is dict and not set(declared) - set(OUTCOME_STATES[:3])
          and all(type(v) is int and v >= 0 for v in declared.values())
          and all(declared.get(key, 0) == counts[key] for key in OUTCOME_STATES[:3]),
          "COVERAGE_OUTCOME_COUNTS_MISMATCH")
    return indexed, _digest(outcomes)


def _cohort(items, indexed, horizons):
    decisions = Counter(row["decision_status"] for row in items)
    result = {"population": len(items), "research_selected": sum(row["research_selected"] for row in items),
        "formal_focus": sum(row["formal_focus"] for row in items),
        "decisions": {state: decisions[state] for state in DECISIONS}, "horizons": []}
    for horizon in horizons:
        states, reasons, values = Counter(), Counter(), []
        for item in items:
            outcome = indexed.get((item["instrument_id"], horizon))
            state = outcome["status"] if outcome else "NOT_PROVIDED"
            states[state] += 1
            if outcome:
                reasons.update(outcome.get("reason_codes", []))
                if state == "MATURE":
                    values.append(outcome["metrics"]["close_reference_change"])
        result["horizons"].append({"horizon": horizon, "population": len(items),
            "states": {state: states[state] for state in OUTCOME_STATES},
            "observed_fraction_of_population": _ratio(states["MATURE"], len(items)),
            "mean_close_reference_change_mature_only": math.fsum(values) / len(values) if values else None,
            "reason_counts": dict(sorted(reasons.items())), "missing_imputation": "NONE",
            "statistical_role": "OBSERVED_SUBSET_DESCRIPTION_NOT_POPULATION_EFFECT_OR_TRADING_RETURN"})
    return result


def build_coverage_report(run, outcomes=None, *, expected_horizons=HORIZONS):
    """Audit complete saved R1/R2 content; return no source or trading authority.

    By default all H1/H2/H3/H5/H10 rows are required if an outcome is supplied.
    A deliberately narrower original product needs an explicit matching scope.
    A missing outcome product is NOT_PROVIDED, never an empty/zero-return success.
    """
    horizons = tuple(expected_horizons)
    _need(bool(horizons) and all(type(h) is int and h in HORIZONS for h in horizons)
          and tuple(sorted(set(horizons))) == horizons, "COVERAGE_EXPECTED_HORIZONS_INVALID")
    arm, items, exchanges, qualification, run_hash = _signal(run)
    indexed, outcome_hash = _outcomes(run, items, outcomes, horizons)
    strata = {}
    for axis, classify in (("exchange", lambda row: exchanges[row["instrument_id"]]),
                           ("primary_stage", lambda row: row.get("primary_stage") or "UNASSIGNED"),
                           ("decision_status", lambda row: row["decision_status"])):
        grouped = defaultdict(list)
        for item in items:
            grouped[classify(item)].append(item)
        strata[axis] = [{"value": key, **_cohort(rows, indexed, horizons)}
                        for key, rows in sorted(grouped.items())]
    reasons = defaultdict(list)
    for item in items:
        for reason in item.get("reason_codes") or ["NO_REPORTED_REASON"]:
            reasons[reason].append(item)
    strata["signal_reason"] = [{"value": key, **_cohort(rows, indexed, horizons)}
                               for key, rows in sorted(reasons.items())]
    report = {"schema_version": VERSION, "role": "SAVED_CONTENT_COVERAGE_AUDIT_ONLY",
        "run_id": run["run_id"], "input_id": run["input_id"], "input_hash": run["input_hash"],
        "signal_content_hash": run_hash, "strategy_id": run["strategy_id"],
        "observation_date": run["observation_date"], "decision_cutoff": run["decision_cutoff"],
        "signal_coverage_type": run["coverage_type"], "expected_horizons": list(horizons),
        "outcome_revision_id": outcomes["revision_id"] if outcomes else None,
        "outcome_product_hash": outcome_hash, "outcome_status": "AVAILABLE" if outcomes else "NOT_PROVIDED",
        "qualification": qualification,
        "cohorts": {"ALL_ELIGIBLE": _cohort(items, indexed, horizons),
            "RESEARCH_SELECTED": _cohort([r for r in items if r["research_selected"]], indexed, horizons),
            "RESEARCH_NOT_SELECTED": _cohort([r for r in items if not r["research_selected"]], indexed, horizons)},
        "strata": strata, "signal_reason_strata_overlap": True,
        "market_benchmarks": _benchmarks(arm, len(items)),
        "unavailable_strata": {"market_cap": "DATED_SIZE_EVIDENCE_NOT_IN_SAVED_RUN",
                               "liquidity_bands": "NO_PRE_REGISTERED_BANDS_IN_THIS_POLICY"},
        "source_reauthenticated_now": False, "historical_pit_verified": False,
        "source_authority_restored": False, "signal_policy_mutated": False,
        "market_edge": "NOT_ESTABLISHED", "execution_status": "NOT_VERIFIED",
        "daily_v1_readiness": "UNKNOWN_NOT_EVALUATED",
        "inference_status": "DESCRIPTIVE_SINGLE_SIGNAL_DATE_NO_MISSING_AT_RANDOM_ASSUMPTION"}
    _need(run_hash == _digest({key: value for key, value in run.items() if key != "content_hash"})
          and run.get("content_hash") == run_hash, "COVERAGE_RUN_CHANGED_DURING_AUDIT")
    _need(outcomes is None or outcome_hash == _digest(outcomes), "COVERAGE_OUTCOME_CHANGED_DURING_AUDIT")
    return report
