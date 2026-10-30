"""Independent TEST_FIXTURE coverage populations; no real data or database."""
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json

import pytest

from scripts import codex_research_coverage_v1 as audit


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def seal(run):
    run["content_hash"] = digest({key: value for key, value in run.items() if key != "content_hash"})
    return run


def seal_outcome(product):
    # The existing OUTCOME_V1 identity, independently assembled in the fixture.
    keys = ("schema_version", "run_id", "signal_content_hash", "signal_input_id", "outcome_input_id",
            "horizons", "calendar_hash", "calendar_evidence", "evaluator_source_hash",
            "dependency_source_hashes", "calendar_implementation_hashes", "effective_evidence")
    identity = {key: product[key] for key in keys}
    identity["rows"] = product["items"]
    product["revision_id"] = "short_outcome_" + digest(identity)
    product["counts"] = dict(Counter(row["status"] for row in product["items"]))
    return product


def fixture_run():
    sid = "SHORT_HORIZON_STRENGTH_R2"
    day, cutoff = "2026-10-15", "2026-10-15T20:00:00+08:00"
    ids = ["TEST_FIXTURE_A", "TEST_FIXTURE_B", "TEST_FIXTURE_C"]
    items = [{"instrument_id": iid, "strategy_id": sid, "observation_date": day,
        "primary_stage": stage, "research_selected": index == 0, "formal_focus": False,
        "decision_status": state, "reason_codes": reasons}
        for index, (iid, stage, state, reasons) in enumerate(zip(ids,
            ["STRONG_START", None, "STRONG_CONTINUATION"], ["WATCH", "UNKNOWN", "REJECTED"],
            [["TEST_FIXTURE_CONTEXT_UNKNOWN"], ["TEST_FIXTURE_MISSING_PRICE"], ["TEST_FIXTURE_LOW_PARTICIPATION"]]))]
    qualification = {"eligible_ids": ids, "eligible": 3, "known_excluded": 1,
        "known_excluded_records": [{"instrument_id": "TEST_FIXTURE_ST", "reason": "ST_POOL_DISABLED"}],
        "eligibility_unknown": 2,
        "eligibility_unknown_records": [{"instrument_id": "TEST_FIXTURE_UNKNOWN", "reason": "UNKNOWN_IDENTITY"}],
        "identity_absent": 1, "identity_observed": 5, "master_expected": 6,
        "universe_evidence_reason_codes": [],
        "selected_records": [{"instrument_id": iid, "exchange": exchange}
            for iid, exchange in zip(ids, ("SSE", "SZSE", "SSE"))]}
    arm = {"strategy_id": sid, "observation_date": day, "decision_cutoff": cutoff,
        "input_hash": "a" * 64, "qualification": deepcopy(qualification), "items": items,
        "eligible_pool_ids": ids, "full_eligible_pool_retained": True,
        "research_selected_ids": ids[:1], "counts": {"eligible": 3, "evaluated": 3,
            "focus": 0, "watch": 1, "rejected": 1, "unknown": 1, "research_observation": 1,
            "decision_counts": {"FOCUS": 0, "WATCH": 1, "REJECTED": 1, "UNKNOWN": 1}},
        "market_benchmarks": {"1": {"status": "COMPLETE", "valid_count": 2, "expected_count": 3,
            "coverage": {"numerator": 2, "denominator": 3},
            "minimum_coverage": {"numerator": 3, "denominator": 5}, "excluded_peer_count": 1,
            "exclusion_reason_counts": {"TEST_FIXTURE_PRICE_GAP": 1}}}}
    return seal({"strategy_id": sid, "execution_profile": "SHORT_HORIZON_RESEARCH_SINGLE",
        "run_id": "TEST_FIXTURE_RUN", "input_id": "market_" + "a" * 64, "input_hash": "a" * 64,
        "coverage_type": "TEST_FIXTURE", "observation_date": day, "decision_cutoff": cutoff,
        "generated_at": cutoff, "qualification": qualification, "universe_hash": digest(qualification),
        "results": {sid: arm}, "selection_results": {sid + "_OBSERVATION": {
            "strategy_id": sid, "selected_ids": ids[:1], "count": 1,
            "items": [{"instrument_id": ids[0]}]}}})


def fixture_outcome(run, horizons=audit.HORIZONS):
    rows = []
    for index, item in enumerate(run["results"][run["strategy_id"]]["items"]):
        state = ("MATURE", "UNKNOWN", "PENDING")[index]
        for horizon in horizons:
            first_day = date(2026, 11, 2) if state == "PENDING" else date(2026, 10, 16)
            dates = [str(first_day + timedelta(days=i)) for i in range(horizon)]
            session_state = {"MATURE": "OBSERVED", "UNKNOWN": "UNKNOWN", "PENDING": "PENDING"}[state]
            rows.append({**item, "run_id": run["run_id"], "horizon": horizon, "status": state,
                "calendar_mature": state != "PENDING", "label_end": dates[-1],
                "required_sessions": horizon, "observed_sessions": horizon if state == "MATURE" else 0,
                "pending_sessions": horizon if state == "PENDING" else 0,
                "unknown_sessions": horizon if state == "UNKNOWN" else 0,
                "session_states": [{"session": day, "status": session_state} for day in dates],
                "metrics": {key: .1 if state == "MATURE" else None for key in
                    ("close_reference_change", "next_open_gap", "next_open_reference_change", "mfe_bound", "mae_bound")}})
    product = {"schema_version": "SHORT_HORIZON_OUTCOME_V1", "run_id": run["run_id"],
        "signal_content_hash": run["content_hash"], "signal_input_id": run["input_id"],
        "outcome_input_id": "market_" + "b" * 64, "horizons": list(horizons),
        "calendar_hash": "c" * 64, "calendar_evidence": {"role": "TEST_FIXTURE"},
        "evaluator_source_hash": "d" * 64, "dependency_source_hashes": {},
        "calendar_implementation_hashes": {}, "effective_evidence": {},
        "strategy_id": run["strategy_id"], "observation_date": run["observation_date"],
        "evaluated_at": "2026-10-30T20:00:00+08:00", "signal_coverage_type": "TEST_FIXTURE",
        "outcome_coverage_type": "TEST_FIXTURE", "product": "outcomes", "status": "AVAILABLE", "items": rows}
    return seal_outcome(product)


def test_full_denominators_and_threshold_are_not_full_coverage():
    run = fixture_run()
    outcome = fixture_outcome(run)
    saved = deepcopy((run, outcome))
    report = audit.build_coverage_report(run, outcome)
    assert (run, outcome) == saved
    assert report["qualification"]["master_expected"] == 6
    assert report["qualification"]["eligibility_unknown"] == 2
    assert report["qualification"]["identity_absent"] == 1
    exchange_population = report["qualification"]["by_recorded_exchange"]
    assert sum(row["declared_population"] for row in exchange_population) == 6
    assert next(row for row in exchange_population if row["exchange"] == "UNOBSERVED_IDENTITY")["identity_absent"] == 1
    assert report["cohorts"]["ALL_ELIGIBLE"]["population"] == 3
    h1 = report["cohorts"]["ALL_ELIGIBLE"]["horizons"][0]
    assert h1["states"] == {"MATURE": 1, "UNKNOWN": 1, "PENDING": 1, "NOT_PROVIDED": 0}
    assert h1["observed_fraction_of_population"]["denominator"] == 3
    assert h1["mean_close_reference_change_mature_only"] == .1
    assert report["cohorts"]["RESEARCH_NOT_SELECTED"]["horizons"][0]["mean_close_reference_change_mature_only"] is None
    benchmark = report["market_benchmarks"][0]
    assert benchmark["threshold_met"] is True and benchmark["full_pool_coverage"] is False
    assert {row["value"]: row["population"] for row in report["strata"]["exchange"]} == {"SSE": 2, "SZSE": 1}
    assert report["source_reauthenticated_now"] is False
    assert report["historical_pit_verified"] is False
    assert report["market_edge"] == "NOT_ESTABLISHED"


def test_absent_outcome_is_not_provided_never_zero_return():
    report = audit.build_coverage_report(fixture_run())
    assert report["outcome_status"] == "NOT_PROVIDED"
    for row in report["cohorts"]["ALL_ELIGIBLE"]["horizons"]:
        assert row["states"]["NOT_PROVIDED"] == 3
        assert row["mean_close_reference_change_mature_only"] is None


@pytest.mark.parametrize("fault,reason", [
    ("stale_hash", "CONTENT_HASH"), ("duplicate", "DUPLICATE"), ("truncated", "TRUNCATED"),
    ("selection", "SELECTED_IDS"), ("count", "COUNT_MISMATCH"), ("formal", "FORMAL_FOCUS"),
    ("qualification", "QUALIFICATION_COUNT"), ("exchange", "EXCHANGE"),
    ("benchmark", "BENCHMARK_RATIO"), ("view", "FILTERED_VIEW"),
])
def test_inconsistent_saved_run_is_rejected_even_with_recomputed_content_hash(fault, reason):
    run = fixture_run()
    arm = run["results"][run["strategy_id"]]
    if fault == "stale_hash": arm["items"][0]["name"] = "changed"
    elif fault == "duplicate": arm["items"].append(deepcopy(arm["items"][0]))
    elif fault == "truncated": arm["items"].pop()
    elif fault == "selection": arm["research_selected_ids"] = []
    elif fault == "count": arm["counts"]["unknown"] = 0
    elif fault == "formal": arm["items"][0]["formal_focus"] = True
    elif fault == "qualification":
        run["qualification"]["master_expected"] = 5
        arm["qualification"] = deepcopy(run["qualification"])
        run["universe_hash"] = digest(run["qualification"])
    elif fault == "exchange":
        run["qualification"]["selected_records"][0]["exchange"] = "UNKNOWN"
        arm["qualification"] = deepcopy(run["qualification"])
        run["universe_hash"] = digest(run["qualification"])
    elif fault == "benchmark": arm["market_benchmarks"]["1"]["coverage"] = 1
    elif fault == "view": run["view_content_hash"] = "f" * 64
    if fault != "stale_hash": seal(run)
    with pytest.raises(audit.CoverageError, match=reason):
        audit.build_coverage_report(run)


@pytest.mark.parametrize("fault,reason", [
    ("hash", "REVISION_HASH"), ("run", "BINDING"), ("input", "BINDING"),
    ("signal", "BINDING"), ("duplicate", "DUPLICATE"), ("truncated", "CARTESIAN"),
    ("horizons", "HORIZONS"), ("selected", "FROZEN_SELECTION"),
    ("zero", "NOT_ZERO"), ("premature", "CALENDAR_CLOCK"),
    ("count", "COUNTS_MISMATCH"), ("clock", "PRECEDES_SIGNAL"),
])
def test_wrong_or_partial_outcome_never_becomes_complete_evaluation(fault, reason):
    run = fixture_run()
    product = fixture_outcome(run)
    if fault == "hash": product["items"][0]["metrics"]["close_reference_change"] = .2
    elif fault == "run": product["run_id"] = "TEST_FIXTURE_OTHER"
    elif fault == "input": product["signal_input_id"] = "market_other"
    elif fault == "signal": product["signal_content_hash"] = "f" * 64
    elif fault == "duplicate": product["items"].append(deepcopy(product["items"][0]))
    elif fault == "truncated": product["items"].pop()
    elif fault == "horizons": product["horizons"] = [1]
    elif fault == "selected": product["items"][0]["research_selected"] = False
    elif fault == "zero": product["items"][5]["metrics"]["close_reference_change"] = 0
    elif fault == "premature": product["items"][0]["calendar_mature"] = False
    elif fault == "clock": product["evaluated_at"] = "2026-10-15T01:00:00+08:00"
    if fault != "hash": seal_outcome(product)
    if fault == "count": product["counts"]["UNKNOWN"] = 0
    with pytest.raises(audit.CoverageError, match=reason):
        audit.build_coverage_report(run, product)


def test_narrower_horizon_requires_explicit_original_scope():
    run = fixture_run()
    product = fixture_outcome(run, (1, 3))
    with pytest.raises(audit.CoverageError, match="HORIZONS"):
        audit.build_coverage_report(run, product)
    report = audit.build_coverage_report(run, product, expected_horizons=(1, 3))
    assert report["expected_horizons"] == [1, 3]


def test_empty_pool_is_not_claimed_as_authenticated_legal_empty():
    run = fixture_run()
    arm = run["results"][run["strategy_id"]]
    qualification = run["qualification"]
    qualification.update(eligible=0, eligible_ids=[], identity_observed=2, master_expected=3)
    qualification["selected_records"] = []
    arm.update(qualification=deepcopy(qualification), items=[], eligible_pool_ids=[], research_selected_ids=[])
    arm["counts"] = {"eligible": 0, "evaluated": 0, "focus": 0, "watch": 0, "unknown": 0,
        "rejected": 0, "research_observation": 0, "decision_counts": dict.fromkeys(audit.DECISIONS, 0)}
    arm["market_benchmarks"]["1"].update(status="UNKNOWN", valid_count=0, expected_count=0,
        coverage={"numerator": 0, "denominator": 1}, excluded_peer_count=0)
    run["selection_results"][run["strategy_id"] + "_OBSERVATION"].update(selected_ids=[], count=0, items=[])
    run["universe_hash"] = digest(qualification)
    report = audit.build_coverage_report(seal(run))
    assert report["qualification"]["legal_empty_source_verified"] is False
    assert report["qualification"]["eligibility_unknown"] == 2
    assert report["cohorts"]["ALL_ELIGIBLE"]["horizons"][0]["mean_close_reference_change_mature_only"] is None


@pytest.mark.parametrize("strategy", sorted(audit.STRATEGIES))
def test_native_frozen_run_and_outcome_contract_are_consumed_without_mutation(strategy):
    from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
    from stockfish_core.yaogu.short_horizon_outcomes_v1 import evaluate
    from stockfish_core.yaogu.history_artifacts import digest_json_stream
    from tests.support.short_horizon_strength_fixture import strength_bundle

    bundle, cutoff = strength_bundle()
    run = run_short_horizon_strategy(bundle, cutoff=cutoff, strategy_id=strategy)
    run["content_hash"] = digest_json_stream(run)
    product = evaluate(run, bundle, cutoff)["outcomes"]
    # The CLI consumes saved JSON, not live source-capability Python objects.
    saved_run = json.loads(json.dumps(run, default=str))
    saved_product = json.loads(json.dumps(product, default=str))
    original = deepcopy((saved_run, saved_product))
    report = audit.build_coverage_report(saved_run, saved_product)
    assert (saved_run, saved_product) == original
    assert report["cohorts"]["ALL_ELIGIBLE"]["population"] == 4
    assert report["cohorts"]["ALL_ELIGIBLE"]["horizons"][0]["states"]["UNKNOWN"] == 4
    assert report["market_edge"] == "NOT_ESTABLISHED"
