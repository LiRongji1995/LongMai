"""TEST_FIXTURE research plans, native pairs and bounded archived consumers."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from scripts import codex_research_protocol_v1 as subject
from scripts import codex_research_coverage_v1 as coverage
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
from tests.support.short_horizon_strength_fixture import strength_bundle, payload, START
from tests.unit.test_codex_research_coverage_v1 import seal_outcome


@pytest.fixture(scope="module")
def native_pair():
    bundle, cutoff = strength_bundle()
    data = payload(bundle)
    for bar in data["bars"]:
        if bar["instrument_id"] != START:
            bar["turnover_fraction"] = .06
    next(row for row in data["receipts"] if row["receipt_id"] == "TEST_FIXTURE_BARS")["content_hash"] = digest(data["bars"])
    bundle = MarketInput.model_validate(data)
    runs = []
    for sid in (subject.R1, subject.R2):
        run = run_short_horizon_strategy(bundle, cutoff=cutoff, strategy_id=sid)
        run = json.loads(json.dumps(run, default=str))
        run.update(generated_at=cutoff.isoformat(), target_session="2026-06-01")
        runs.append(subject.seal(run))
    return runs


def outcome(run, missing=None):
    from stockfish_core.observed_data import exchange_calendar_2026
    calendar_raw = (subject.ROOT / "stockfish_core/exchange_calendar_2026.json").read_bytes()
    metadata = json.loads(calendar_raw)
    schedule = exchange_calendar_2026()
    next_session = str(schedule.sessions[schedule.sessions.index(date.fromisoformat(run["observation_date"])) + 1])
    evidence = dict(policy="REGISTERED_AXIS_CHECKED_AGAINST_FINITE_OFFICIAL_2026_SCHEDULE",
        source_hash=hashlib.sha256(calendar_raw).hexdigest(), sources=metadata["sources"],
        verified_at=metadata["verified_at"], coverage_start=str(schedule.coverage_start),
        coverage_end=str(schedule.coverage_end), test_fixture_clock_exception=True)
    product = {"schema_version": "SHORT_HORIZON_OUTCOME_V1", "run_id": run["run_id"],
        "signal_content_hash": run["content_hash"], "signal_input_id": run["input_id"],
        "outcome_input_id": "market_" + "b" * 64, "horizons": [1],
        "calendar_hash": "c" * 64, "calendar_evidence": evidence,
        "evaluator_source_hash": "d" * 64, "dependency_source_hashes": {},
        "calendar_implementation_hashes": {}, "effective_evidence": {},
        "strategy_id": run["strategy_id"], "observation_date": run["observation_date"],
        "evaluated_at": "2026-10-30T20:00:00+08:00", "signal_coverage_type": "TEST_FIXTURE",
        "outcome_coverage_type": "TEST_FIXTURE", "product": "outcomes", "status": "AVAILABLE",
        "items": [dict(deepcopy(row), run_id=run["run_id"], horizon=1) for row in run["results"][run["strategy_id"]]["items"]]}
    returns = {"TEST_START": .10, "TEST_CONT": .02, "TEST_PULL": -.03, "TEST_WEAK": 0.0}
    for row in product["items"]:
        unknown = row["instrument_id"] == missing
        row.update(status="UNKNOWN" if unknown else "MATURE", calendar_mature=True,
            label_end=next_session, required_sessions=1, observed_sessions=0 if unknown else 1,
            pending_sessions=0, unknown_sessions=1 if unknown else 0,
            session_states=[dict(session=next_session, status="UNKNOWN" if unknown else "OBSERVED")],
            metrics={key: None if unknown else returns[row["instrument_id"]] for key in coverage.METRICS})
    return seal_outcome(product)


def pair_report(native_pair, *, missing=None, matching=None):
    return subject.compare_pair(*native_pair, left_outcomes=outcome(native_pair[0], missing),
        right_outcomes=outcome(native_pair[1], missing), expected_horizons=(1,),
        risk_matching=matching or {"categorical_axes": ["exchange"], "amount_boundaries_cny": []})


def draft(native_pair, *, future=False):
    year = 2030 if future else 2026
    days = [(date(year, 1, 5) + timedelta(days=i)).isoformat() for i in range(14)
            if (date(year, 1, 5) + timedelta(days=i)).weekday() < 5][:10]
    return dict(plan_id="TEST_FIXTURE_PLAN", hypothesis="TEST_FIXTURE: R2 improves the frozen reference cohort",
        discovery_evidence=[dict(id="TEST_FIXTURE_DISCOVERY", content_hash="a" * 64,
            observation_date="2025-12-01", known_at="2025-12-02T00:00:00+00:00")],
        windows={"discovery": dict(start="2025-01-01", end="2025-12-31"),
            "validation": dict(start=days[0], end=days[-2]), "holdout": dict(start=days[-1], end=days[-1])},
        expected_signal_sessions=days,
        strategy_identities={run["strategy_id"]: {key: run[key] for key in subject.IDENTITY_FIELDS} for run in native_pair},
        comparison_family=["R2_MINUS_R1", "R2_MINUS_ELIGIBLE", "R2_MINUS_RISK_MATCHED"],
        primary_comparison="R2_MINUS_R1", horizons=[1], primary_horizon=1, block_lengths=[2, 4],
        primary_block_length=2, bootstrap_repetitions=100, seed=17,
        attempts=[dict(attempt_id="TEST_FIXTURE_1", configuration_hash="b" * 64, status="PLANNED",
            registered_at="2025-12-02T00:00:00+00:00", result_hash=None)],
        stopping_rule="TEST_FIXTURE: complete every declared day, retain missing, stop at registered end",
        risk_matching={"categorical_axes": ["exchange"], "amount_boundaries_cny": []})


def dated_pairs(plan, pair):
    result = []
    window = plan["draft"]["windows"]["validation"]
    for day in plan["draft"]["expected_signal_sessions"]:
        if window["start"] <= day <= window["end"]:
            value = dict(deepcopy(pair), observation_date=day)
            for strategy in (subject.R1, subject.R2):
                value["coverage"][strategy]["observation_date"] = day
                value["run_hashes"][strategy] = hashlib.sha256((day + strategy).encode()).hexdigest()
                value["coverage"][strategy]["signal_content_hash"] = value["run_hashes"][strategy]
            result.append(subject.seal(value))
    return result


def carrier(path, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    path.write_bytes(raw)
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())


def test_native_pair_full_pool_equal_facts_and_explicit_negative_increment(native_pair):
    saved = deepcopy(native_pair)
    report = pair_report(native_pair)
    assert native_pair == saved
    assert len(report["full_pool"]) == 4
    assert report["observations"]["1"]["cohorts"]["R1"]["population"] == 3
    assert report["observations"]["1"]["cohorts"]["R2"]["population"] == 2
    assert report["observations"]["1"]["differences"]["R2_MINUS_R1"] == pytest.approx(-.035)
    assert report["observations"]["1"]["risk_matched"]["mean"] == pytest.approx(.0225)
    assert report["strategy_account_return"] is None
    assert report["source_reauthenticated_now"] is False
    assert report["historical_pit_verified"] is False


@pytest.mark.parametrize("fault", ["input", "cutoff", "universe", "fact", "parent_source", "decision", "outcome"])
def test_pair_rejects_mismatched_origin_even_after_rehash(native_pair, fault):
    left, right = deepcopy(native_pair)
    if fault == "input": right["input_id"] = "TEST_FIXTURE_OTHER"
    elif fault == "cutoff":
        right["decision_cutoff"] = "2026-05-29T21:00:00+08:00"
        right["results"][subject.R2]["decision_cutoff"] = right["decision_cutoff"]
    elif fault == "universe": right["qualification"]["eligible_ids"] = list(reversed(right["qualification"]["eligible_ids"]))
    elif fault == "fact": right["results"][subject.R2]["items"][0]["exact_features"]["amount"] = {"numerator": 3, "denominator": 1}
    elif fault == "parent_source": right["results"][subject.R2]["base_run_provenance"]["base_source_hash"] = "c" * 64
    elif fault == "decision": right["results"][subject.R2]["items"][0]["r1_decision_status"] = "UNKNOWN"
    subject.seal(right)
    lo, ro = outcome(left), outcome(right)
    if fault == "outcome":
        ro["items"][0]["metrics"]["close_reference_change"] = .99
        seal_outcome(ro)
    with pytest.raises(ValueError):
        subject.compare_pair(left, right, left_outcomes=lo, right_outcomes=ro, expected_horizons=(1,))


def test_unknown_outcome_is_retained_and_disables_population_effect(native_pair):
    report = pair_report(native_pair, missing="TEST_CONT")
    assert len(report["full_pool"]) == 4
    values = report["observations"]["1"]
    assert values["cohorts"]["R2"]["states"]["UNKNOWN"] == 1
    assert values["cohorts"]["R2"]["observed_subset_mean"] == -.03
    assert values["cohorts"]["R2"]["mean"] is None
    assert all(values["differences"][key] is None for key in ("R2_MINUS_R1", "R2_MINUS_ELIGIBLE",
        "R2_MINUS_RISK_MATCHED", "R2_MINUS_R1:STRONG_CONTINUATION"))
    assert values["differences"]["R2_MINUS_R1:FIRST_PULLBACK"] == 0


def test_absent_outcomes_and_empty_stage_never_become_zero(native_pair):
    report = subject.compare_pair(*native_pair, expected_horizons=(1,))
    assert report["coverage"][subject.R1]["outcome_status"] == "NOT_PROVIDED"
    assert report["observations"]["1"]["differences"]["R2_MINUS_R1:STRONG_START"] is None


def test_identical_but_wrong_horizon_dates_cannot_pass_pair_calendar_check(native_pair):
    products = [outcome(run) for run in native_pair]
    for product in products:
        for row in product["items"]:
            row["session_states"][0]["session"] = "2026-10-16"
            row["label_end"] = "2026-10-16"
        seal_outcome(product)
    with pytest.raises(ValueError, match="WRONG_EXCHANGE_SESSION"):
        subject.compare_pair(*native_pair, left_outcomes=products[0], right_outcomes=products[1], expected_horizons=(1,))


def test_plan_creation_clock_not_backdated_and_append_history_retained(native_pair):
    historical = subject.freeze_plan(draft(native_pair))
    future = subject.freeze_plan(draft(native_pair, future=True))
    assert historical["design_timing"] == "RETROSPECTIVE_DESCRIPTIVE_ONLY"
    assert future["design_timing"] == "FROZEN_BEFORE_DECLARED_VALIDATION"
    assert future["historical_pit_verified"] is False
    changed = draft(native_pair, future=True)
    changed["attempts"].append(dict(changed["attempts"][0], attempt_id="TEST_FIXTURE_2"))
    revised = subject.freeze_plan(changed, previous=future)
    assert revised["parent_plan_hash"] == future["content_hash"]
    changed["attempts"].pop(0)
    with pytest.raises(ValueError, match="PRIOR_ATTEMPTS"):
        subject.freeze_plan(changed, previous=future)


@pytest.mark.parametrize("fault,reason", [("overlap", "WINDOWS"), ("missing_holdout", "BOTH_WINDOWS"),
    ("duplicate_date", "EXPECTED_SESSIONS"), ("future_knowledge", "FUTURE_DECLARED"),
    ("short_block", "BLOCKS"), ("unsupported_risk", "RISK_AXIS"), ("unbound_version", "STRATEGY_HASH")])
def test_bad_protocol_rejected(native_pair, fault, reason):
    value = draft(native_pair)
    if fault == "overlap": value["windows"]["holdout"]["start"] = value["windows"]["validation"]["end"]
    elif fault == "missing_holdout": value["expected_signal_sessions"].pop()
    elif fault == "duplicate_date": value["expected_signal_sessions"].append(value["expected_signal_sessions"][-1])
    elif fault == "future_knowledge": value["discovery_evidence"][0]["known_at"] = "2099-01-01T00:00:00Z"
    elif fault == "short_block": value["block_lengths"] = [1, 2, 4]
    elif fault == "unsupported_risk": value["risk_matching"]["categorical_axes"] = ["current_industry"]
    else: value["strategy_identities"][subject.R1]["source_hash"] = None
    with pytest.raises(ValueError, match=reason):
        subject.freeze_plan(value)


def test_study_reuses_registered_block_estimator_and_all_family(native_pair):
    from stockfish_core.yaogu.research_r1_io import paired_block_comparisons
    plan = subject.freeze_plan(draft(native_pair))
    pairs = dated_pairs(plan, pair_report(native_pair))
    report = subject.evaluate_study(plan, pairs)
    assert report["status"] == "CONDITIONAL_ARCHIVE_ESTIMATE"
    for block in (2, 4):
        expected = paired_block_comparisons(report["series"], seed=17, block_length=block, repetitions=100)
        assert report["block_length_sensitivity"][str(block)] == expected
    assert report["primary_result"]["effect"] == pytest.approx(-.035)
    assert report["evaluation_admissible_for_market_validation"] is False
    assert report["strategy_account_return"] is None


def test_missing_date_not_dropped_and_missing_outcome_blocks_statistics(native_pair):
    plan = subject.freeze_plan(draft(native_pair))
    pairs = dated_pairs(plan, pair_report(native_pair))
    absent = pairs.pop(3)["observation_date"]
    report = subject.evaluate_study(plan, pairs)
    assert report["missing_dates"] == [absent]
    assert report["status"] == "BLOCKED_DATA"
    assert len(report["series"]["R2_MINUS_R1:H1"]) == 9
    assert report["series"]["R2_MINUS_R1:H1"][3]["difference"] is None
    missing = dated_pairs(plan, pair_report(native_pair, missing="TEST_CONT"))
    assert subject.evaluate_study(plan, missing)["status"] == "BLOCKED_DATA"


def test_declared_date_list_cannot_compress_a_real_trading_day(native_pair):
    data = draft(native_pair)
    skipped = data["expected_signal_sessions"].pop(3)
    plan = subject.freeze_plan(data)
    report = subject.evaluate_study(plan, dated_pairs(plan, pair_report(native_pair)))
    assert skipped in report["calendar"]["undeclared_trading_sessions"]
    assert skipped in report["missing_dates"]
    rows = report["series"]["R2_MINUS_R1:H1"]
    assert len(rows) == 9 and rows[3]["difference"] is None
    assert rows[4]["session_index"] == rows[2]["session_index"] + 2
    assert all(item["status"] == "UNAVAILABLE" for arm in report["block_length_sensitivity"].values() for item in arm.values())
    assert report["status"] == "BLOCKED_DATA"


@pytest.mark.parametrize("fault", ["missing", "hash", "outside_year"])
def test_no_verified_contiguous_calendar_means_no_estimate(native_pair, fault):
    plan = subject.freeze_plan(draft(native_pair, future=fault == "outside_year"))
    pairs = dated_pairs(plan, pair_report(native_pair))
    if fault == "missing":
        pairs[0]["outcome_calendar_evidence"] = None
    elif fault == "hash":
        pairs[0]["outcome_calendar_evidence"]["source_hash"] = "f" * 64
    subject.seal(pairs[0])
    report = subject.evaluate_study(plan, pairs)
    assert report["status"] == "BLOCKED_DATA"
    assert report["primary_result"]["status"] == "UNAVAILABLE"
    assert report["calendar"]["status"] == "BLOCKED_CALENDAR"


def test_risk_matching_uses_full_predeclared_stratum_and_rejects_missing_axis(native_pair):
    matching = {"categorical_axes": ["exchange"], "amount_boundaries_cny": [200000000]}
    report = pair_report(native_pair, matching=matching)
    # The three 300m names include the unselected zero-return control; the 150m
    # start is in another fixed stratum, not selected post-outcome as a control.
    result = report["observations"]["1"]["risk_matched"]
    assert result["mean"] == pytest.approx((.02 - .03 + 0) / 3)
    assert sum(row["eligible_count"] for row in result["strata"]) == 4
    missing = pair_report(native_pair, matching={"categorical_axes": ["primary_stage"], "amount_boundaries_cny": []})
    assert missing["observations"]["1"]["risk_matched"]["status"] == "UNKNOWN"
    with pytest.raises(ValueError, match="RISK_AXIS"):
        pair_report(native_pair, matching={"categorical_axes": ["market_cap"], "amount_boundaries_cny": []})


@pytest.mark.parametrize("fault", ["duplicate", "extra_day", "identity", "risk", "horizon", "authority"])
def test_study_rejects_selection_of_dates_versions_or_risk_scheme(native_pair, fault):
    plan = subject.freeze_plan(draft(native_pair))
    pairs = dated_pairs(plan, pair_report(native_pair))
    if fault == "duplicate": pairs.append(deepcopy(pairs[0]))
    elif fault == "extra_day": pairs[0]["observation_date"] = "2026-01-16"
    elif fault == "identity": pairs[0]["strategy_identities"][subject.R2]["source_hash"] = "a" * 64
    elif fault == "risk": pairs[0]["risk_matching"] = None
    elif fault == "horizon": pairs[0]["observations"]["2"] = deepcopy(pairs[0]["observations"]["1"])
    else: pairs[0]["evaluation_admissible_for_market_validation"] = True
    subject.seal(pairs[0])
    with pytest.raises(ValueError):
        subject.evaluate_study(plan, pairs)


def test_source_owner_json_cannot_grant_source_authority(native_pair):
    with pytest.raises(ValueError, match="GENUINE_SOURCE_OWNER"):
        subject.compare_pair(*native_pair, left_owner={"authenticated": True})


def test_execution_readiness_keeps_real_returns_and_capacity_blocked(native_pair):
    report = subject.execution_readiness(native_pair[1])
    assert report["status"] == "BLOCKED_DATA"
    assert report["layers"]["auction_capacity"] == "UNKNOWN"
    assert report["original_readiness"]["account_return"] is None


def test_conditional_scenarios_delegate_old_engine_and_cost_direction(native_pair, tmp_path):
    from tests.unit.test_codex_r2_execution_v1 import archive
    run = native_pair[1]
    declaration = archive(tmp_path, run)
    scenarios = [dict(scenario_id="TEST_FIXTURE_" + str(bps), execution_spec=None, slippage_bps=bps) for bps in (0, 30)]
    report = subject.execution_scenarios(run, scenarios, execution_archive=declaration,
        archive_base=tmp_path, allow_test_fixture=True)
    low, high = [x["result"]["account_simulation"]["account_return"] for x in report["scenarios"]]
    assert low > high
    assert report["strategy_account_return"] is None
    assert all(x["result"]["strategy_account_return"] is None for x in report["scenarios"])
    assert report["capacity_status"].startswith("UNKNOWN")


def test_missing_genuine_owner_blocks_scenario_instead_of_claiming_simulation(native_pair):
    run = deepcopy(native_pair[1])
    run["coverage_type"] = "RECONSTRUCTED_HISTORICAL"
    subject.seal(run)
    report = subject.execution_scenarios(run, [dict(scenario_id="TEST_FIXTURE_ABSENT_OWNER", execution_spec=None, slippage_bps=10)])
    assert report["status"] == "BLOCKED_DATA" and report["simulated_scenarios"] == 0
    assert report["strategy_account_return"] is None


def test_template_cannot_be_mistaken_for_real_registered_plan():
    template = json.loads((subject.ROOT / "scripts/contracts/research_protocol_v1.template.json").read_text())
    with pytest.raises(ValueError):
        subject.freeze_plan(template)


def test_cli_freeze_then_pair_consumes_its_own_envelope_without_overwrite(native_pair, tmp_path):
    d = carrier(tmp_path / "draft.json", draft(native_pair))
    plan_path = tmp_path / "plan.json"
    assert subject.main(["freeze", "--draft", d["path"], "--draft-sha256", d["sha256"], "--output", str(plan_path)]) == 0
    raw = plan_path.read_bytes()
    plan_sha = hashlib.sha256(raw).hexdigest()
    source = {}
    for key, value in zip(("left", "right", "left_outcomes", "right_outcomes"),
                          (*native_pair, outcome(native_pair[0]), outcome(native_pair[1]))):
        source[key] = carrier(tmp_path / (key + ".json"), value)
    p = carrier(tmp_path / "pair.json", source)
    output = tmp_path / "report.json"
    arguments = ["pair", "--plan", str(plan_path), "--plan-sha256", plan_sha,
        "--pair", p["path"], "--pair-sha256", p["sha256"], "--output", str(output)]
    assert subject.main(arguments) == 0
    report = json.loads(output.read_text())
    assert report["result"]["observations"]["1"]["differences"]["R2_MINUS_R1"] == pytest.approx(-.035)
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    assert subject.main(arguments) == 2
    assert output.read_bytes() == before and plan_path.read_bytes() == raw


def test_cli_empty_batch_reports_missing_dates_and_nonzero_readiness(native_pair, tmp_path):
    plan = subject.freeze_plan(draft(native_pair))
    p = carrier(tmp_path / "plan.json", plan)
    b = carrier(tmp_path / "batch.json", dict(plan_hash=plan["content_hash"], pairs=[]))
    out = tmp_path / "report.json"
    assert subject.main(["study", "--plan", p["path"], "--plan-sha256", p["sha256"],
        "--batch", b["path"], "--batch-sha256", b["sha256"], "--output", str(out)]) == 4
    report = json.loads(out.read_text())["result"]
    assert len(report["missing_dates"]) == 9
    assert report["primary_result"]["status"] == "UNAVAILABLE"


def test_input_changed_during_publish_retracts_only_owned_report(native_pair, tmp_path, monkeypatch):
    d = carrier(tmp_path / "draft.json", draft(native_pair))
    out = tmp_path / "plan.json"
    original = subject.io.verify_snapshots
    calls = []
    def changing(refs):
        calls.append(1)
        if len(calls) == 3:
            Path(d["path"]).write_text("{}")
        original(refs)
    monkeypatch.setattr(subject.io, "verify_snapshots", changing)
    assert subject.main(["freeze", "--draft", d["path"], "--draft-sha256", d["sha256"], "--output", str(out)]) == 2
    assert not out.exists()
