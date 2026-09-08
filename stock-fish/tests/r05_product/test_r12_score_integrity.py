"""P0 identity, operational missingness and research-only cohort regressions."""
from copy import deepcopy
from datetime import datetime
from itertools import permutations

import pytest

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.recommendation_scoring import (
    FOCUS_SCORE, OFFICIAL_PROSPECTIVE, RECONSTRUCTED_HISTORICAL,
    build_score_revision, compute_score_records, evidence_type_for_run, summarize_score_history,
)
from stockfish_core.yaogu.validation_identity import (
    STRATEGY_ID, current_validation_identity, official_signal_countable,
    signal_data_quality, validation_identity_for_run,
)
from tests.r05_product.test_recommendation_scoring_v1 import SH, _find, _label, _records, _row, _run

NOW = datetime(2026, 9, 15, 20, 30, tzinfo=SH)


def _dated_run(day, rows, run_id):
    run = _run(rows)
    run.update(run_id=run_id, observation_date=day, decision_cutoff=day + "T20:30:00+08:00")
    run["prospective_validation"]["signal_observation_date"] = day
    for key in ("scheduled_at", "capture_started_at", "capture_completed_at", "actual_decision_cutoff"):
        run["prospective_validation"][key] = day + run["prospective_validation"][key][10:]
    return run


def _bound_label(run, iid, value):
    label = _label(iid, 1, value, run_id=run["run_id"])
    label.update(observation_date=run["observation_date"], implementation_version=run["implementation_version"],
                 implementation_source_hash=run["source_hash"], config_hash=run["config_hash"], spec_hash=run["spec_hash"])
    label["observation_id"] = "strategy_outcome_" + digest(label)
    return label


def test_same_instrument_run_outcomes_cannot_contaminate_in_either_direction_or_order():
    rows = [_row("601086.SH", "FOCUS"), _row("600000.SH", "WATCH")]
    a = _dated_run("2026-09-08", rows, "Run A")
    b = _dated_run("2026-09-09", rows, "Run B")
    a_labels = [_bound_label(a, "601086.SH", .10), _bound_label(a, "600000.SH", 0)]
    b_labels = [_bound_label(b, "601086.SH", -.10), _bound_label(b, "600000.SH", 0)]
    before_a = _records(a, a_labels)
    before_b = _records(b, b_labels)
    for ordered in permutations(a_labels + b_labels):
        assert _records(a, list(ordered)) == before_a
        assert _records(b, list(ordered)) == before_b
    assert _find(before_a, FOCUS_SCORE)["equal_weight_return"] == .10
    assert _find(before_b, FOCUS_SCORE)["equal_weight_return"] == -.10
    # Advancing report time without new labels does not reseed Monte Carlo or
    # manufacture a new outcome revision for an old signal.
    assert compute_score_records(a, a_labels + b_labels, datetime(2026, 9, 20, 20, 30, tzinfo=SH)) == before_a


@pytest.mark.parametrize("field", ["run_id", "strategy_id", "observation_date", "implementation_version",
                                    "implementation_source_hash", "config_hash", "spec_hash", "horizon"])
def test_mismatched_signal_identity_or_horizon_is_not_a_label_for_this_run(field):
    run = _run([_row("601086.SH", "FOCUS")])
    wrong = _label("601086.SH", 1, .10)
    wrong[field] = 99 if field == "horizon" else "OTHER_IDENTITY"
    assert _find(_records(run, [wrong], horizons=(1,)), FOCUS_SCORE)["score_status"] == "NOT_MATURED"


def test_equal_time_revision_tie_and_different_logical_ids_are_order_independent():
    run = _run([_row("A", "FOCUS"), _row("B", "WATCH")])
    first = _label("A", 1, .10)
    second = _label("A", 1, .20)
    second["logical_label_id"] = "malformed_duplicate_logical_identity"
    labels = [first, second, _label("B", 1, 0)]
    assert _records(run, labels) == _records(run, list(reversed(labels)))


def test_later_other_horizon_outcomes_do_not_reseed_mature_d1_score():
    run = _run([_row(str(i), "FOCUS" if i < 2 else "WATCH") for i in range(8)])
    d1 = [_label(str(i), 1, i / 100) for i in range(8)]
    later_d3 = [_label(str(i), 3, (7 - i) / 100) for i in range(8)]
    first = _records(run, d1, random_basket_count=100, exact_enumeration_limit=0)
    later = _records(run, d1 + later_d3, random_basket_count=100, exact_enumeration_limit=0)
    assert [row for row in first if row["horizon"] == 1] == [row for row in later if row["horizon"] == 1]


@pytest.mark.parametrize("historical_field", ["coverage_type", "audit_mode", "nested_coverage_type"])
def test_reconstructed_malformed_official_flags_never_upgrade_evidence(historical_field):
    run = _run([_row("A", "FOCUS")])
    if historical_field == "nested_coverage_type":
        run["prospective_validation"]["coverage_type"] = RECONSTRUCTED_HISTORICAL
    else:
        run[historical_field] = "HISTORICAL_RECONSTRUCTION" if historical_field == "audit_mode" else RECONSTRUCTED_HISTORICAL
    assert evidence_type_for_run(run) == RECONSTRUCTED_HISTORICAL
    assert official_signal_countable(run) is False
    doc = build_score_revision(run, [_label("A", 1, .10)], NOW)
    assert doc["official_primary_signal_countable"] is False
    assert summarize_score_history([doc])["daily"] == []


@pytest.mark.parametrize("field", ["implementation_version", "spec_revision", "config_hash", "source_hash"])
def test_stage_and_parent_semantic_identity_changes_create_new_epoch(field):
    run = _run([_row("A", "FOCUS")])
    before = validation_identity_for_run(run)
    stage_change = deepcopy(run)
    stage_change["results"][STRATEGY_ID][field] = "NEW_SIGNAL_SEMANTICS"
    assert validation_identity_for_run(stage_change)["validation_epoch_id"] != before["validation_epoch_id"]
    parent_change = deepcopy(run)
    parent_change[field] = "NEW_PARENT_SEMANTICS"
    assert validation_identity_for_run(parent_change)["validation_epoch_id"] != before["validation_epoch_id"]
    assert validation_identity_for_run(run) == before


@pytest.mark.parametrize("filename", ["scheduler.py", "candidate_service.py", "validation_identity.py"])
def test_actual_protocol_implementation_file_change_creates_new_epoch(filename, monkeypatch):
    from pathlib import Path

    run = _run([_row("A", "WATCH")])
    before = validation_identity_for_run(run)
    original = Path.read_bytes

    def changed(path):
        value = original(path)
        return value + b"\n# TEST_FIXTURE_DIFFERENT_PROTOCOL_IMPLEMENTATION\n" if path.name == filename else value

    monkeypatch.setattr(Path, "read_bytes", changed)
    after = validation_identity_for_run(run)
    assert before["validation_protocol_hash"] != after["validation_protocol_hash"]
    assert before["validation_epoch_id"] != after["validation_epoch_id"]


def test_cutoff_policy_and_protocol_are_frozen_and_change_epochs():
    run = _run([_row("A", "WATCH")])
    exact_epoch = validation_identity_for_run(run)["validation_epoch_id"]
    run["prospective_validation"]["primary_cutoff_policy_id"] = "SCHEDULED_2030_ACTUAL_COMPLETION"
    run["prospective_validation"]["protocol_id"] = "SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF"
    run["decision_cutoff"] = "2026-09-08T20:38:00+08:00"
    run["prospective_validation"]["actual_decision_cutoff"] = run["decision_cutoff"]
    run["prospective_validation"].update(capture_started_at="2026-09-08T20:30:00+08:00",
                                         capture_completed_at="2026-09-08T20:38:00+08:00",
                                         capture_latency_seconds=480, decision_latency_seconds=480)
    doc = build_score_revision(run, [_label("A", 1, .10)], NOW)
    assert doc["validation_epoch_id"] != exact_epoch
    assert doc["primary_cutoff_policy_id"] == "SCHEDULED_2030_ACTUAL_COMPLETION"
    assert doc["actual_decision_cutoff"].endswith("20:38:00+08:00")
    assert doc["official_validation_signal_countable"] is True
    assert current_validation_identity()["primary_cutoff_policy_id"] == "SCHEDULED_2030_ACTUAL_COMPLETION"


def test_rolling_only_one_epoch_and_selection_and_excess_medians_are_distinct():
    old = _dated_run("2026-09-08", [_row("A", "FOCUS"), _row("B", "WATCH")], "old")
    new = _dated_run("2026-09-09", [_row("A", "FOCUS"), _row("B", "WATCH")], "new")
    new["results"][STRATEGY_ID]["implementation_version"] = "R13_TEST_FIXTURE"
    docs = [build_score_revision(old, [_bound_label(old, "A", -.20), _bound_label(old, "B", 0)], NOW),
            build_score_revision(new, [_bound_label(new, "A", .10), _bound_label(new, "B", .02)], NOW)]
    summary = summarize_score_history(docs)
    assert summary["validation_epoch_id"] == docs[1]["validation_epoch_id"]
    assert summary["official_signal_days"] == 1
    assert summary["daily"][0]["signal_observation_date"] == "2026-09-09"
    rolling = summary["rolling"]["20D"]
    assert rolling["median_daily_selection_return"] == .10
    assert rolling["median_daily_excess_return"] == .04
    assert "median_daily_return" not in rolling
    assert summarize_score_history(docs, validation_epoch_id=docs[0]["validation_epoch_id"])["daily"][0]["focus_selection_return"] == -.20


def test_source_failure_empty_eligible_is_operational_attempt_not_no_selection():
    unavailable = _dated_run("2026-09-08", [], "unavailable")
    unavailable["qualification"]["universe_evidence_reason_codes"] = ["SOURCE_UNAVAILABLE"]
    no_focus = _dated_run("2026-09-09", [_row("A", "WATCH")], "no-focus")
    # Missing optional context does not prevent a price/identity-valid evaluation.
    no_focus["results"][STRATEGY_ID]["items"][0]["funds_status"] = "UNKNOWN"
    no_focus["results"][STRATEGY_ID]["items"][0]["sector_status"] = "UNKNOWN"
    docs = [build_score_revision(unavailable, [], NOW), build_score_revision(no_focus, [_bound_label(no_focus, "A", .10)], NOW)]
    assert docs[0]["headline_status"] == "N_A_SIGNAL_DATA_UNAVAILABLE"
    assert docs[0]["official_primary_capture"] is True
    assert docs[0]["official_validation_signal_countable"] is False
    assert docs[1]["headline_status"] == "N_A_NO_SELECTION"
    assert docs[1]["official_validation_signal_countable"] is True
    summary = summarize_score_history(docs)
    assert summary["primary_capture_attempted_days"] == 2
    assert summary["evaluable_official_signal_days"] == 1
    assert summary["data_unavailable_primary_days"] == 1
    assert summary["valid_no_focus_days"] == 1
    assert summary["official_signal_days"] == 1
    assert summary["focus_signal_coverage"] == 0
    assert summary["rolling"]["20D"]["valid_score_days"] == 0


def test_fully_proven_legitimate_empty_market_is_valid_no_selection():
    run = _run([])
    run["qualification"].update(master_expected=1, identity_observed=1, known_excluded=1,
                                 selected_records=[{"instrument_id": "A", "source_refs": ["TEST_FIXTURE_EXCLUDED"]}])
    assert official_signal_countable(run) is True
    assert _find(_records(run, []), FOCUS_SCORE)["score_status"] == "N_A_NO_SELECTION"


def test_short_history_core_unknown_is_unavailable_even_with_complete_current_prices():
    run = _run([_row("A", "UNKNOWN")])
    run["results"][STRATEGY_ID]["items"][0]["condition_statuses"].update(trend_state="UNKNOWN", setup_identification="UNKNOWN")
    assert "CORE_STRATEGY_EVIDENCE_UNAVAILABLE" in signal_data_quality(run)["reason_codes"]
    assert official_signal_countable(run) is False
    assert _find(_records(run, [_label("A", 1, .10)]), FOCUS_SCORE)["score_status"] == "N_A_SIGNAL_DATA_UNAVAILABLE"


def test_known_hard_liquidity_rejection_is_determinate_without_long_history():
    run = _run([_row("A", "REJECTED")])
    run["results"][STRATEGY_ID]["items"][0]["condition_statuses"].update(liquidity_bottom="FAIL", trend_state="UNKNOWN", setup_identification="UNKNOWN")
    assert official_signal_countable(run) is True
    assert _find(_records(run, [_label("A", 1, .10)]), FOCUS_SCORE)["score_status"] == "N_A_NO_SELECTION"


@pytest.mark.parametrize("failure", ["price", "scope", "evaluated", "input_integrity", "persisted", "identity"])
def test_nonempty_signal_with_fatal_data_failure_is_not_countable(failure):
    run = _run([_row("A", "WATCH")])
    if failure == "price":
        run["results"][STRATEGY_ID]["items"][0]["comparable_close"] = None
    elif failure == "scope":
        run["results"][STRATEGY_ID]["scope_label"] = "LIMITED_SCOPE"
    elif failure == "evaluated":
        run["results"][STRATEGY_ID]["counts"]["evaluated"] = 0
    elif failure == "identity":
        run["qualification"]["selected_records"][0]["source_refs"] = []
    else:
        run["prospective_validation"]["input_integrity_valid" if failure == "input_integrity" else "signal_run_persisted"] = False
    assert signal_data_quality(run)["signal_evaluable"] is False
    assert official_signal_countable(run) is False
    assert _find(_records(run, [_label("A", 1, .10)]), FOCUS_SCORE)["score_status"] == "N_A_SIGNAL_DATA_UNAVAILABLE"


def test_primary_and_all_passing_setup_cohorts_differ_and_research_can_overlap():
    rows = [_row("A", "FOCUS", "START"), _row("B", "WATCH", "ACCELERATION")]
    rows[0]["stage_candidates"] = {"START": {"status": "PASS"}, "ACCELERATION": {"status": "PASS"}}
    rows[1]["stage_candidates"] = {"START": {"status": "UNKNOWN"}, "ACCELERATION": {"status": "PASS"}}
    run = _run(rows)
    before = digest(run)
    scores = _records(run, [_label("A", 1, .10), _label("B", 1, .02)])
    assert _find(scores, "FOCUS:ACCELERATION")["M"] == 0
    assert _find(scores, "ALL_PASSING_SETUP:ACCELERATION")["M"] == 2
    assert _find(scores, "ALL_PASSING_SETUP:START")["M"] == 1
    assert _find(scores, "FOCUS:START")["cohort_policy_id"] == "PRIMARY_SETUP_COHORT"
    assert _find(scores, "ALL_PASSING_SETUP:ACCELERATION")["official_recommendation_score"] is False
    assert digest(run) == before


def test_monte_carlo_percentile_records_sample_uncertainty_and_exact_has_no_mc_error():
    run = _run([_row(str(i), "FOCUS" if i in {2, 3} else "WATCH") for i in range(8)])
    labels = [_label(str(i), 1, i / 100) for i in range(8)]
    sampled = _find(_records(run, labels, random_basket_count=200, exact_enumeration_limit=0), FOCUS_SCORE)
    exact = _find(_records(run, labels), FOCUS_SCORE)
    assert sampled["percentile_sampling_mode"] == "DETERMINISTIC_MONTE_CARLO"
    assert sampled["K"] == 200
    assert 0 < sampled["percentile_mc_standard_error"] < 4
    assert exact["percentile_sampling_mode"] == "EXACT_ENUMERATION"
    assert exact["percentile_mc_standard_error"] is None
    assert exact["score_id"] != sampled["score_id"]


@pytest.mark.parametrize("field,value", [
    ("actual_decision_cutoff", "2026-09-08T20:38:00+08:00"),
    ("scheduled_at", "2026-09-08T20:38:00+08:00"),
    ("capture_completed_at", "2026-09-08T20:38:00+08:00"),
    ("capture_started_at", "2026-09-08T20:29:00"),
    ("capture_latency_seconds", -50),
    ("decision_latency_seconds", float("nan")),
    ("scheduler_job_id", None),
])
def test_malformed_primary_cutoff_metadata_cannot_be_countable(field, value):
    run = _run([_row("A", "WATCH")])
    run["prospective_validation"][field] = value
    assert official_signal_countable(run) is False
    assert any("CUTOFF" in reason for reason in signal_data_quality(run)["reason_codes"])


def test_frozen_epoch_is_retained_but_cannot_hide_changed_signal_identity():
    run = _run([_row("A", "WATCH")])
    identity = validation_identity_for_run(run)
    run.update(identity)
    run["prospective_validation"].update(identity)
    assert validation_identity_for_run(run) == identity
    run["results"][STRATEGY_ID]["implementation_version"] = "DIFFERENT_IMPLEMENTATION"
    assert validation_identity_for_run(run)["validation_epoch_id"] != identity["validation_epoch_id"]


def test_new_scorer_cannot_recompute_old_frozen_score_spec(monkeypatch):
    import stockfish_core.yaogu.recommendation_scoring as scoring

    run = _run([_row("A", "WATCH")])
    identity = validation_identity_for_run(run)
    run.update(identity)
    run["prospective_validation"].update(identity)
    saved = build_score_revision(run, [_label("A", 1, .10)], NOW)
    monkeypatch.setattr(scoring, "current_score_spec_identity", lambda: "DIFFERENT_INSTALLED_SCORER_BYTES")
    with pytest.raises(ValueError, match="FROZEN_SCORE_SPEC_UNAVAILABLE_READ_ONLY_EPOCH"):
        build_score_revision(run, [_label("A", 1, .20)], NOW)
    # Read-only saved revisions remain viewable without executing another scorer.
    assert summarize_score_history([saved], validation_epoch_id=identity["validation_epoch_id"])["valid_no_focus_days"] == 1


def test_score_api_defaults_active_epoch_and_unknown_epoch_does_not_fallback(tmp_path):
    from stockfish_core.api import create_app
    from stockfish_core.service import ApplicationService
    from stockfish_core.storage import SQLiteStore
    from stockfish_core.yaogu.stage_aware_momentum import stage_aware_source_identity
    from stockfish_core.yaogu.strategy_experiments import IMPLEMENTATION_VERSION, source_identity
    from stockfish_core.yaogu.validation_identity import PRIMARY_CUTOFF_POLICY_ID, VALIDATION_PROTOCOL_ID

    core = ApplicationService(root=tmp_path / "state", store=SQLiteStore(tmp_path / "score.sqlite3"),
                              allow_test_fixture=True, data_dir=tmp_path / "no_model")
    try:
        old = _dated_run("2026-09-09", [_row("A", "FOCUS")], "old-later-day")
        current = _dated_run("2026-09-08", [_row("A", "WATCH")], "current")
        current.update(source_identity(), implementation_version=IMPLEMENTATION_VERSION)
        current["results"][STRATEGY_ID].update(stage_aware_source_identity())
        current["prospective_validation"].update(protocol_id=VALIDATION_PROTOCOL_ID, primary_cutoff_policy_id=PRIMARY_CUTOFF_POLICY_ID,
                                                 capture_started_at="2026-09-08T20:30:00+08:00",
                                                 capture_completed_at="2026-09-08T20:30:00+08:00", capture_latency_seconds=0)
        # The active epoch capture is unavailable even though an older epoch has
        # a later signal day and a numerical score. Never silently fall back.
        current["results"][STRATEGY_ID]["items"][0]["comparable_close"] = None
        old_doc = build_score_revision(old, [_bound_label(old, "A", .10)], NOW)
        current_doc = build_score_revision(current, [], NOW)
        assert current_doc["validation_epoch_id"] == current_validation_identity()["validation_epoch_id"]
        for run, doc in ((old, old_doc), (current, current_doc)):
            core.store.put_document("strategy_experiment_runs", run["run_id"], run)
            core.store.put_document("recommendation_score_revisions", doc["score_run_id"], doc)
        client = create_app(core).test_client()
        latest = client.get("/api/recommendation-scores/latest").json
        old_view = client.get("/api/recommendation-scores/latest?validation_epoch_id=" + old_doc["validation_epoch_id"]).json
        unknown = client.get("/api/recommendation-scores/latest?validation_epoch_id=UNKNOWN_EPOCH").json
        assert latest["latest"]["signal_run_id"] == "current"
        assert latest["latest"]["headline_status"] == "N_A_SIGNAL_DATA_UNAVAILABLE"
        assert latest["official_prospective_score"]["official_signal_days"] == 0
        assert latest["official_prospective_score"]["data_unavailable_primary_days"] == 1
        assert old_view["latest"]["signal_run_id"] == "old-later-day"
        assert unknown["latest"] is None
        assert unknown["official_prospective_score"]["daily"] == []
    finally:
        core.close()
