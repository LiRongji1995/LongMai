"""TEST_FIXTURE frozen label boundaries and complete local trial families."""
from copy import deepcopy
from datetime import date

import pytest

from scripts import codex_research_protocol_v2 as subject
from scripts import research_trial_ledger_v1 as ledger
from tests.unit.test_codex_research_protocol_v1 import native_pair, draft, pair_report, dated_pairs
from tests.unit.test_research_trial_ledger_v1 import attempt

POLICY = dict(label="NEXT_SESSION_THROUGH_HORIZON_CLOSE_REFERENCE",
              purge="EXCLUDE_LABELS_CROSSING_PHASE_BOUNDARY", embargo_sessions=0,
              stopping_policy="ONE_EVALUATION_PER_PHASE_NO_RETRY",
              discovery_labels=[dict(id="TEST_FIXTURE_DISCOVERY", evidence_content_hash="a" * 64,
                  label_start="2026-01-06", label_end="2026-01-19", scope="DECLARED_ALL_CONSUMED_LABELS")])


def specification(native_pair, horizon=1):
    value = draft(native_pair)
    value["discovery_evidence"][0].update(observation_date="2026-01-05", known_at="2026-01-06T00:00:00Z")
    value["windows"] = dict(discovery=dict(start="2026-01-01", end="2026-01-30"),
        validation=dict(start="2026-03-02", end="2026-04-10"), holdout=dict(start="2026-04-13", end="2026-05-08"))
    schedule, _, _ = subject.old._registered_axis()
    value["expected_signal_sessions"] = [str(d) for d in schedule.sessions if date(2026,3,2) <= d <= date(2026,5,8)]
    value.update(horizons=[horizon], primary_horizon=horizon, block_lengths=[horizon + 1], primary_block_length=horizon + 1,
                 attempts=[attempt()])
    return value


def freeze(tmp_path, value, policy=None):
    value["attempts"][0]["configuration_hash"] = subject.trial_configuration_hash(value, policy or POLICY)
    root = (tmp_path / "TEST_FIXTURE_trials").resolve()
    ledger.initialize(root, "TEST_FIXTURE_scope")
    event = ledger.append_attempt(root, value["attempts"][0], expected_head=ledger.inspect(root)["head"])
    return subject.freeze_plan(value, policy or POLICY, ledger_root=root, expected_head=event["content_hash"]), root


def test_original_h10_overlap_counterexample_is_purged_not_hidden(native_pair, tmp_path):
    value = specification(native_pair, 10)
    value["windows"]["validation"] = dict(start="2026-11-09", end="2026-11-20")
    value["windows"]["holdout"] = dict(start="2026-11-23", end="2026-12-04")
    schedule, _, _ = subject.old._registered_axis()
    value["expected_signal_sessions"] = [str(d) for d in schedule.sessions if date(2026,11,9) <= d <= date(2026,12,4)]
    # The unchanged V1 accepts these disjoint signal windows.
    assert subject.old.freeze_plan(value)["draft"]["horizons"] == [10]
    plan, root = freeze(tmp_path, value)
    rows = plan["frozen"]["label_support"]["labels"]
    last = next(r for r in rows if r["signal_session"] == "2026-11-20")
    assert last["label_start"] == "2026-11-23"
    assert last["label_end"] == "2026-12-04"
    assert last["status"] == "PURGED" and not last["eligible_for_phase_estimation"]
    assert len(rows) == len(value["expected_signal_sessions"])
    subject.validate_plan(plan, ledger_root=root)


def test_embargo_and_exchange_holiday_use_real_session_axis(native_pair):
    value = specification(native_pair)
    no_gap, _ = subject.label_support(value, POLICY)
    with_gap, _ = subject.label_support(value, dict(POLICY, embargo_sessions=2))
    count = lambda support: sum(r["eligible_for_phase_estimation"] for r in support["labels"] if r["phase"] == "validation")
    assert count(no_gap) - count(with_gap) == 2
    april3 = next(r for r in no_gap["labels"] if r["signal_session"] == "2026-04-03")
    assert april3["label_start"] == "2026-04-07"  # Official holiday, not weekday enumeration.


@pytest.mark.parametrize("fault,reason", [("missing_day", "COMPLETE_PHASE"), ("2027", "FINITE_CALENDAR"), ("discovery", "DISCOVERY_LABELS"), ("negative_embargo", "EMBARGO")])
def test_unknown_or_leaking_label_support_cannot_be_frozen(native_pair, fault, reason):
    value = specification(native_pair, 10)
    policy = deepcopy(POLICY)
    if fault == "missing_day": value["expected_signal_sessions"].pop(2)
    elif fault == "2027": value["windows"]["holdout"]["end"] = "2027-01-05"
    elif fault == "discovery":
        value["discovery_evidence"][0]["observation_date"] = "2026-02-27"
        policy["discovery_labels"][0].update(label_start="2026-03-02", label_end="2026-03-13")
    else: policy["embargo_sessions"] = -1
    with pytest.raises(ValueError, match=reason):
        subject.label_support(value, policy)


def test_study_keeps_purged_rows_and_applies_current_scope_attempts(native_pair, tmp_path):
    value = specification(native_pair)
    plan, root = freeze(tmp_path, value)
    pairs = dated_pairs(plan["frozen"]["base_plan"], pair_report(native_pair))
    prior = ledger.inspect(root)
    ledger.append_attempt(root, attempt("TEST_FIXTURE_OTHER_PLAN"), expected_head=prior["head"])
    report = subject.evaluate_study(plan, pairs, ledger_root=root)
    assert report["status"] == "CONDITIONAL_ISOLATED_ARCHIVE_ESTIMATE"
    key = report["primary_key"]
    assert len(report["all_signal_series"][key]) == len(pairs)
    assert len(report["estimation_series"][key]) == len(pairs) - 1
    assert report["primary_result"]["effect"] == pytest.approx(-.035)
    assert report["local_trial_count_now"] == 2
    primary = report["primary_result"]
    assert primary["selected_scope_trial_bonferroni_p"] == min(1, primary["holm_adjusted_p"] * 2)
    assert report["sample_is_independent_holdout_verified"] is False
    with pytest.raises(ValueError, match="PHASE_ALREADY_CONSUMED_NO_RETRY"):
        subject.evaluate_study(plan, pairs, ledger_root=root)


def test_missing_outcome_and_ledger_switch_do_not_gain_authority(native_pair, tmp_path):
    plan, root = freeze(tmp_path, specification(native_pair))
    pairs = dated_pairs(plan["frozen"]["base_plan"], pair_report(native_pair, missing="TEST_CONT"))
    report = subject.evaluate_study(plan, pairs, ledger_root=root)
    assert report["status"] == "BLOCKED_DATA"
    other = (tmp_path / "TEST_FIXTURE_other").resolve()
    ledger.initialize(other, "TEST_FIXTURE_other")
    with pytest.raises(ValueError, match="LEDGER_BINDING"):
        subject.validate_plan(plan, ledger_root=other)


def test_new_plan_id_cannot_omit_old_attempts(native_pair, tmp_path):
    value = specification(native_pair)
    plan, root = freeze(tmp_path, value)
    ledger.append_attempt(root, attempt("TEST_FIXTURE_B"), expected_head=ledger.inspect(root)["head"])
    value["plan_id"] = "TEST_FIXTURE_SECOND_PLAN"
    value["attempts"] = [attempt("TEST_FIXTURE_B")]
    with pytest.raises(ValueError, match="ALL_SCOPE_ATTEMPTS"):
        subject.freeze_plan(value, POLICY, ledger_root=root, expected_head=ledger.inspect(root)["head"])


def test_rehashed_purged_label_cannot_be_promoted(native_pair, tmp_path):
    plan, root = freeze(tmp_path, specification(native_pair))
    last = next(r for r in plan["frozen"]["label_support"]["labels"] if r["status"] == "PURGED")
    last["eligible_for_phase_estimation"] = True
    subject.old.seal(plan["frozen"])
    subject.old.seal(plan)
    with pytest.raises(ValueError, match="LABEL_SUPPORT_CHANGED"):
        subject.validate_plan(plan, ledger_root=root)


def test_committed_freeze_is_recoverable_without_new_registration(native_pair, tmp_path, monkeypatch):
    import hashlib
    import json
    value = specification(native_pair)
    value["attempts"][0]["configuration_hash"] = subject.trial_configuration_hash(value, POLICY)
    root = (tmp_path / "TEST_FIXTURE_trials").resolve()
    ledger.initialize(root, "TEST_FIXTURE_scope")
    event = ledger.append_attempt(root, value["attempts"][0], expected_head=ledger.inspect(root)["head"])
    raw = json.dumps(dict(draft=value, label_policy=POLICY)).encode()
    path = (tmp_path / "TEST_FIXTURE_draft.json").resolve()
    path.write_bytes(raw)
    output = (tmp_path / "TEST_FIXTURE_output.json").resolve()
    original = subject.old.io.publish_new
    def fail_output(path, *args, **kwargs):
        if str(path) == str(output): raise OSError("TEST_FIXTURE_REPORT_PUBLISH_FAILURE")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(subject.old.io, "publish_new", fail_output)
    assert subject.main(["freeze", "--ledger-root", str(root), "--expected-head", event["content_hash"],
        "--input", str(path), "--input-sha256", hashlib.sha256(raw).hexdigest(), "--output", str(output)]) == 2
    before = ledger.inspect(root)
    recovered = subject.recover_plan(root, value["plan_id"])
    assert recovered["frozen"]["base_plan"]["draft"] == value
    assert ledger.inspect(root)["head"] == before["head"] and before["event_count"] == 2


def test_concurrent_attempt_during_estimation_revokes_result(native_pair, tmp_path, monkeypatch):
    from stockfish_core.yaogu import research_r1_io
    plan, root = freeze(tmp_path, specification(native_pair))
    pairs = dated_pairs(plan["frozen"]["base_plan"], pair_report(native_pair))
    original = research_r1_io.paired_block_comparisons
    changed = [False]
    def append_during(*args, **kwargs):
        result = original(*args, **kwargs)
        if not changed[0]:
            changed[0] = True
            ledger.append_attempt(root, attempt("TEST_FIXTURE_LATE"), expected_head=ledger.inspect(root)["head"])
        return result
    monkeypatch.setattr(research_r1_io, "paired_block_comparisons", append_during)
    with pytest.raises(ValueError, match="LEDGER_CHANGED_DURING_STUDY"):
        subject.evaluate_study(plan, pairs, ledger_root=root)


def test_recover_cli_is_read_only_and_preserves_exact_plan(native_pair, tmp_path):
    import json
    plan, root = freeze(tmp_path, specification(native_pair))
    before = ledger.inspect(root)["head"]
    output = (tmp_path / "TEST_FIXTURE_recovered.json").resolve()
    assert subject.main(["recover", "--ledger-root", str(root), "--plan-id", plan["frozen"]["base_plan"]["draft"]["plan_id"],
                         "--output", str(output)]) == 0
    assert json.loads(output.read_text())["result"] == plan
    assert ledger.inspect(root)["head"] == before


def test_new_plan_requires_a_new_attempt_bound_to_all_design_choices(native_pair, tmp_path):
    value = specification(native_pair)
    plan, root = freeze(tmp_path, value)
    value["plan_id"] = "TEST_FIXTURE_NEW_PLAN"
    with pytest.raises(ValueError, match="FRESH_DESIGN_BOUND"):
        subject.freeze_plan(value, POLICY, ledger_root=root, expected_head=ledger.inspect(root)["head"])
    pending = attempt("TEST_FIXTURE_NEXT")
    pending["configuration_hash"] = subject.trial_configuration_hash(value, POLICY)
    ledger.append_attempt(root, pending, expected_head=ledger.inspect(root)["head"])
    value["attempts"] = ledger.inspect(root)["attempts"]
    value["seed"] += 1
    with pytest.raises(ValueError, match="FRESH_DESIGN_BOUND"):
        subject.freeze_plan(value, POLICY, ledger_root=root, expected_head=ledger.inspect(root)["head"])
    value["seed"] -= 1
    second = subject.freeze_plan(value, POLICY, ledger_root=root, expected_head=ledger.inspect(root)["head"])
    assert second["frozen"]["local_trial_count"] == 2


def test_new_h1_plan_cannot_assume_historical_discovery_was_h1(native_pair):
    value = specification(native_pair, 1)
    value["windows"]["discovery"]["end"] = "2026-02-27"
    value["discovery_evidence"][0]["observation_date"] = "2026-02-24"
    policy = deepcopy(POLICY)
    policy["discovery_labels"][0].update(label_start="2026-02-25", label_end="2026-03-10")
    with pytest.raises(ValueError, match="DISCOVERY_LABELS_REACH_VALIDATION"):
        subject.label_support(value, policy)
    policy["discovery_labels"][0]["label_end"] = None
    with pytest.raises(ValueError, match="DISCOVERY_LABEL_SPAN_UNKNOWN"):
        subject.label_support(value, policy)


def test_phase_claim_precedes_outcome_read_and_failure_cannot_be_retried(native_pair, tmp_path):
    plan, root = freeze(tmp_path, specification(native_pair))
    descriptors = [dict(path="TEST_FIXTURE_NOT_READ", sha256="d" * 64)]
    def fail_read(records):
        record = ledger.inspect(root)["studies"]["TEST_FIXTURE_PLAN:validation"]
        assert record["claim"]["payload"]["input_hash"] == subject.digest(descriptors)
        assert record["result"] is None
        raise OSError("TEST_FIXTURE_OUTCOME_READ_FAILED")
    with pytest.raises(OSError, match="OUTCOME_READ_FAILED"):
        subject.evaluate_study(plan, descriptors, ledger_root=root, _pair_loader=fail_read)
    with pytest.raises(ValueError, match="PHASE_ALREADY_CONSUMED_NO_RETRY"):
        subject.evaluate_study(plan, [], ledger_root=root)
    with pytest.raises(ValueError, match="STUDY_RESULT_UNKNOWN_NO_RETRY"):
        subject.recover_study(root, "TEST_FIXTURE_PLAN", "validation")


def test_study_is_exactly_recoverable_and_changed_inputs_are_new_attempt(native_pair, tmp_path):
    plan, root = freeze(tmp_path, specification(native_pair))
    pairs = dated_pairs(plan["frozen"]["base_plan"], pair_report(native_pair))
    result = subject.evaluate_study(plan, pairs, ledger_root=root)
    before = ledger.inspect(root)["head"]
    assert subject.recover_study(root, "TEST_FIXTURE_PLAN", "validation") == result
    assert ledger.inspect(root)["head"] == before
    with pytest.raises(ValueError, match="PHASE_ALREADY_CONSUMED_NO_RETRY"):
        subject.evaluate_study(plan, pairs[:-1], ledger_root=root)
    assert result["consumed_phase_count"] == 1


def test_holdout_is_a_second_registered_look_not_free_optional_stopping(native_pair, tmp_path):
    plan, root = freeze(tmp_path, specification(native_pair))
    with pytest.raises(ValueError, match="VALIDATION_TERMINAL_REQUIRED"):
        subject.evaluate_study(plan, [], ledger_root=root, phase="holdout")
    pairs = dated_pairs(plan["frozen"]["base_plan"], pair_report(native_pair))
    subject.evaluate_study(plan, pairs, ledger_root=root)
    result = subject.evaluate_study(plan, [], ledger_root=root, phase="holdout")
    assert result["status"] == "BLOCKED_DATA"
    assert result["local_trial_count_now"] == 2 and result["consumed_phase_count"] == 2
    assert result["sample_is_independent_holdout_verified"] is False


@pytest.mark.parametrize("fault", ["phase", "study_input_hash", "frozen_plan_hash", "market_edge"])
def test_claim_cannot_accept_a_wrong_or_authority_claiming_saved_result(native_pair, tmp_path, fault):
    plan, root = freeze(tmp_path, specification(native_pair))
    pairs = dated_pairs(plan["frozen"]["base_plan"], pair_report(native_pair))
    result = subject.evaluate_study(plan, pairs, ledger_root=root)
    # A distinct holdout claim provides a legitimate unfinished slot.
    current = ledger.inspect(root)
    claim = ledger.claim_study(root, "TEST_FIXTURE_PLAN", plan["frozen"]["content_hash"], "holdout", "e" * 64, expected_head=current["head"])
    report = deepcopy(result)
    report.pop("ledger_result_head")
    report.update(phase="holdout", study_claim_hash=claim["content_hash"], study_input_hash="e" * 64, ledger_head=claim["content_hash"])
    report[fault] = "validation" if fault == "phase" else "ESTABLISHED" if fault == "market_edge" else "f" * 64
    subject.old.seal(report)
    with pytest.raises(ValueError, match="REPORT_BINDING_OR_AUTHORITY"):
        ledger.finish_study(root, "TEST_FIXTURE_PLAN", "holdout", claim["content_hash"], report, expected_head=claim["content_hash"])


def test_actual_cli_consumes_phase_and_recovers_exact_output(native_pair, tmp_path):
    import json
    from tests.unit.test_codex_research_protocol_v1 import carrier
    plan, root = freeze(tmp_path, specification(native_pair))
    saved_plan = carrier((tmp_path / "TEST_FIXTURE_plan.json").resolve(), plan)
    batch = carrier((tmp_path / "TEST_FIXTURE_batch.json").resolve(), dict(plan_hash=plan["content_hash"], pairs=[]))
    output = (tmp_path / "TEST_FIXTURE_study.json").resolve()
    argv = ["study", "--ledger-root", str(root), "--expected-head", ledger.inspect(root)["head"],
        "--plan", saved_plan["path"], "--plan-sha256", saved_plan["sha256"],
        "--input", batch["path"], "--input-sha256", batch["sha256"], "--output", str(output)]
    assert subject.main(argv) == 4
    saved = json.loads(output.read_text())["result"]
    assert saved["status"] == "BLOCKED_DATA" and saved["consumed_phase_count"] == 1
    before = ledger.inspect(root)["head"]
    recovery = (tmp_path / "TEST_FIXTURE_recovered_study.json").resolve()
    assert subject.main(["recover-study", "--ledger-root", str(root), "--plan-id", "TEST_FIXTURE_PLAN", "--output", str(recovery)]) == 4
    assert json.loads(recovery.read_text())["result"] == saved
    assert ledger.inspect(root)["head"] == before
    argv[-1] = str((tmp_path / "TEST_FIXTURE_retried.json").resolve())
    argv[argv.index("--expected-head") + 1] = before
    assert subject.main(argv) == 2
    assert ledger.inspect(root)["head"] == before


def test_recover_study_terminal_revalidates_saved_result_before_publication(native_pair, tmp_path, monkeypatch):
    plan, root = freeze(tmp_path, specification(native_pair))
    subject.evaluate_study(plan, [], ledger_root=root)
    original = subject.recover_study
    calls = [0]
    def changed(*args, **kwargs):
        calls[0] += 1
        if calls[0] > 1:
            raise ValueError("TEST_FIXTURE_TERMINAL_IMPLEMENTATION_CHANGED")
        return original(*args, **kwargs)
    monkeypatch.setattr(subject, "recover_study", changed)
    output = (tmp_path / "TEST_FIXTURE_never_published.json").resolve()
    assert subject.main(["recover-study", "--ledger-root", str(root), "--plan-id", "TEST_FIXTURE_PLAN", "--output", str(output)]) == 2
    assert not output.exists()
