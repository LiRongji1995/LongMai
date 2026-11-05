from __future__ import annotations

import pytest
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.recommendation_scoring import (
    FOCUS_SCORE,
    DEFAULT_RANDOM_BASKETS,
    OFFICIAL_PROSPECTIVE,
    PRELIMINARY_RESEARCH,
    RECONSTRUCTED_HISTORICAL,
    SCORE_SPEC_ID,
    WATCH_RESEARCH_SCORE,
    build_score_revision,
    compute_score_records,
    summarize_score_history,
)
from stockfish_core.yaogu.stage_aware_momentum import STAGE_AWARE_ID

SH = ZoneInfo("Asia/Shanghai")


def _run(rows, *, coverage_type="PROSPECTIVE_CAPTURED", official=True, validation_role="PROSPECTIVE_PRIMARY"):
    return {
        "run_id": "strategy_experiment_score_fixture",
        "observation_date": "2026-09-08",
        "decision_cutoff": "2026-09-08T20:30:00+08:00",
        "coverage_type": coverage_type,
        "implementation_version": "fixture",
        "source_hash": "fixture-source",
        "config_hash": "fixture-config",
        "spec_hash": "fixture-spec",
        "prospective_validation": {
            "validation_role": validation_role,
            "official_primary_signal_countable": official,
            "official_primary_capture": official,
            "official_validation_signal_countable": official,
            "input_integrity_valid": True,
            "signal_run_persisted": True,
            "market_session": "EVENING",
            "coverage_type": coverage_type,
            "signal_observation_date": "2026-09-08",
            "stage_aware_strategy_id": STAGE_AWARE_ID,
            "protocol_id": "EXACT_PRIMARY_CUTOFF_V1",
            "primary_cutoff_policy_id": "EXACT_2030",
            "scheduler_job_id": "TEST_FIXTURE_EVENING",
            "scheduled_at": "2026-09-08T20:30:00+08:00",
            "capture_started_at": "2026-09-08T20:29:00+08:00",
            "capture_completed_at": "2026-09-08T20:29:50+08:00",
            "actual_decision_cutoff": "2026-09-08T20:30:00+08:00",
            "capture_latency_seconds": 50,
            "decision_latency_seconds": 0,
        },
        "qualification": {
            "eligible_ids": [row["instrument_id"] for row in rows],
            "master_expected": len(rows), "identity_observed": len(rows),
            "identity_absent": 0, "eligibility_unknown": 0,
            "universe_evidence_reason_codes": [],
            "selected_records": [{"instrument_id": row["instrument_id"], "source_refs": ["TEST_FIXTURE"]} for row in rows],
        },
        "results": {
            STAGE_AWARE_ID: {
                "strategy_id": STAGE_AWARE_ID,
                "canonical_strategy_id": STAGE_AWARE_ID,
                "scope_label": "FULL_SCOPE",
                "counts": {"eligible": len(rows), "evaluated": len(rows)},
                "items": rows,
            }
        },
        "selection_results": {},
    }


def _row(iid, status, setup="START", rank=1):
    return {
        "instrument_id": iid,
        "rank": rank,
        "decision_status": status,
        "status": status,
        "primary_stage": setup,
        "comparable_close": "10",
        "comparable_basis": "TEST",
        "condition_statuses": {"liquidity_bottom": "PASS", "trend_state": "PASS", "setup_identification": "PASS"},
    }


def _label(iid, horizon, value=None, *, status="MATURE", run_id="strategy_experiment_score_fixture"):
    metrics = {"close_reference_change": value}
    logical = {
        "run_id": run_id,
        "strategy_id": STAGE_AWARE_ID,
        "instrument_id": iid,
        "observation_date": "2026-09-08",
        "horizon": horizon,
        "parameter_version": "fixture-config",
        "config_hash": "fixture-config",
        "implementation_version": "fixture",
        "implementation_source_hash": "fixture-source",
        "spec_hash": "fixture-spec",
        "metric_policy_id": "fixture-label-policy",
        "label_kind": "SIGNAL",
    }
    value = {
        **logical,
        "evaluated_at": "2026-09-15T20:30:00+08:00",
        "status": status,
        "metrics": metrics,
        "logical_label_id": "strategy_label_" + digest(logical),
    }
    value["observation_id"] = "strategy_outcome_" + digest(value)
    return value


def _records(run, labels, **kwargs):
    return compute_score_records(run, labels, datetime(2026, 9, 15, 20, 30, tzinfo=SH), **kwargs)


def _find(records, tier, horizon=1):
    return next(row for row in records if row["selection_tier"] == tier and row["horizon"] == horizon)


def test_selection_return_uses_stock_returns_not_average_absolute_prices():
    run = _run([_row("A", "FOCUS", rank=1), _row("B", "FOCUS", rank=2)])
    labels = [_label("A", 1, 0.10), _label("B", 1, -0.01)]

    focus = _find(_records(run, labels), FOCUS_SCORE)

    assert focus["equal_weight_return"] == 0.045
    assert focus["equal_weight_return"] != 0


def test_focus_m_zero_is_na_not_zero_or_perfect_score():
    run = _run([_row("A", "WATCH")])
    labels = [_label("A", 1, 0.10)]

    focus = _find(_records(run, labels), FOCUS_SCORE)

    assert focus["M"] == 0
    assert focus["score"] is None
    assert focus["score_status"] == "N_A_NO_SELECTION"


def test_random_comparator_uses_same_m_without_replacement():
    rows = [_row(f"S{i}", "FOCUS" if i < 3 else "WATCH", rank=i + 1) for i in range(10)]
    labels = [_label(f"S{i}", 1, i / 100) for i in range(10)]

    focus = _find(_records(_run(rows), labels, random_basket_count=25, exact_enumeration_limit=0), FOCUS_SCORE)

    assert focus["sampling_policy"]["sample_member_count"] == focus["M"] == 3
    assert focus["sampling_policy"]["replacement"] == "WITHOUT_REPLACEMENT"
    assert focus["random_basket_count"] == 25


def test_deterministic_seed_and_exact_rerun_same_score():
    rows = [_row(f"S{i}", "FOCUS" if i < 4 else "WATCH", rank=i + 1) for i in range(12)]
    labels = [_label(f"S{i}", 1, (i - 4) / 100) for i in range(12)]
    run = _run(rows)

    first = _find(_records(run, labels, random_basket_count=100, exact_enumeration_limit=0), FOCUS_SCORE)
    second = _find(_records(run, labels, random_basket_count=100, exact_enumeration_limit=0), FOCUS_SCORE)

    assert first["random_seed_hash"] == second["random_seed_hash"]
    assert first["score"] == second["score"]


def test_score_is_50_when_selection_return_equals_exact_random_median():
    run = _run([_row("A", "WATCH", rank=1), _row("B", "FOCUS", rank=2), _row("C", "WATCH", rank=3)])
    labels = [_label("A", 1, 0.00), _label("B", 1, 0.05), _label("C", 1, 0.10)]

    focus = _find(_records(run, labels, exact_enumeration_limit=10), FOCUS_SCORE)

    assert focus["score"] == 50.0
    assert focus["sampling_policy"]["sampling_mode"] == "EXACT_ENUMERATION"


def test_incomplete_selected_outcome_does_not_produce_headline_score():
    run = _run([_row("A", "FOCUS", rank=1), _row("B", "FOCUS", rank=2)])
    labels = [_label("A", 1, 0.10), _label("B", 1, None, status="NOT_MATURED")]

    focus = _find(_records(run, labels), FOCUS_SCORE)

    assert focus["score"] is None
    assert focus["score_status"] == "NOT_MATURED"


def test_insufficient_benchmark_coverage_blocks_headline_score():
    rows = [_row(f"S{i}", "FOCUS" if i == 0 else "WATCH", rank=i + 1) for i in range(100)]
    labels = [_label("S0", 1, 0.10)]

    focus = _find(_records(_run(rows), labels), FOCUS_SCORE)

    assert focus["score"] is None
    assert focus["score_status"] == "INSUFFICIENT_BENCHMARK_COVERAGE"
    assert focus["benchmark_coverage"] == 0.01


def test_preliminary_run_never_enters_official_score_history():
    run = _run([_row("A", "FOCUS")], official=False, validation_role="PROSPECTIVE_PRELIMINARY")
    doc = build_score_revision(run, [_label("A", 1, 0.10)], datetime(2026, 9, 15, 20, 30, tzinfo=SH))

    assert doc["evidence_type"] == PRELIMINARY_RESEARCH
    assert summarize_score_history([doc], evidence_type=OFFICIAL_PROSPECTIVE)["official_signal_days"] == 0


def test_reconstructed_score_is_separate_from_official_prospective_history():
    run = _run([_row("A", "FOCUS")], coverage_type="RECONSTRUCTED_HISTORICAL", official=False, validation_role="MANUAL_OR_REPLAY")
    doc = build_score_revision(run, [_label("A", 1, 0.10)], datetime(2026, 9, 15, 20, 30, tzinfo=SH))

    assert doc["evidence_type"] == RECONSTRUCTED_HISTORICAL
    assert summarize_score_history([doc], evidence_type=OFFICIAL_PROSPECTIVE)["daily"] == []
    assert summarize_score_history([doc], evidence_type=RECONSTRUCTED_HISTORICAL)["official_signal_days"] == 1


def test_watch_research_score_does_not_masquerade_as_focus_score():
    run = _run([_row("A", "FOCUS", rank=1), _row("B", "WATCH", rank=2)])
    labels = [_label("A", 1, 0.01), _label("B", 1, 0.20)]

    records = _records(run, labels)
    focus = _find(records, FOCUS_SCORE)
    watch = _find(records, WATCH_RESEARCH_SCORE)

    assert focus["selection_tier"] == FOCUS_SCORE
    assert watch["selection_tier"] == WATCH_RESEARCH_SCORE
    assert focus["M"] == watch["M"] == 1
    assert watch["official_recommendation_score"] is False


def test_setup_scores_do_not_cross_setup_boundaries():
    run = _run([
        _row("A", "FOCUS", setup="START", rank=1),
        _row("B", "FOCUS", setup="ACCELERATION", rank=2),
        _row("C", "WATCH", setup="START", rank=3),
    ])
    labels = [_label("A", 1, 0.01), _label("B", 1, 0.02), _label("C", 1, 0.03)]

    records = _records(run, labels)

    assert _find(records, FOCUS_SCORE)["M"] == 2
    assert _find(records, "FOCUS:START")["M"] == 1
    assert _find(records, "FOCUS:ACCELERATION")["M"] == 1
    assert _find(records, "WATCH:START")["M"] == 1


def test_future_d3_outcome_is_not_matured():
    run = _run([_row("A", "FOCUS")])
    labels = [_label("A", 1, 0.10), _label("A", 3, None, status="NOT_MATURED")]

    focus_d3 = _find(_records(run, labels, horizons=(1, 3)), FOCUS_SCORE, horizon=3)

    assert focus_d3["score"] is None
    assert focus_d3["score_status"] == "NOT_MATURED"


def test_score_revision_does_not_modify_signal_run():
    run = _run([_row("A", "FOCUS")])
    before = digest(run)

    build_score_revision(run, [_label("A", 1, 0.10)], datetime(2026, 9, 15, 20, 30, tzinfo=SH))

    assert digest(run) == before


def test_same_m_comparator_changes_with_selection_count():
    rows = [_row(f"S{i}", "FOCUS" if i < 2 else "WATCH", rank=i + 1) for i in range(8)]
    labels = [_label(f"S{i}", 1, i / 100) for i in range(8)]

    focus = _find(_records(_run(rows), labels, random_basket_count=10, exact_enumeration_limit=0), FOCUS_SCORE)

    assert focus["M"] == 2
    assert focus["sampling_policy"]["sample_member_count"] == 2


def test_focus_empty_day_counts_in_signal_coverage_but_not_rolling_mean():
    run = _run([_row("A", "WATCH")])
    doc = build_score_revision(run, [_label("A", 1, 0.10)], datetime(2026, 9, 15, 20, 30, tzinfo=SH))
    summary = summarize_score_history([doc], evidence_type=OFFICIAL_PROSPECTIVE)

    assert summary["official_signal_days"] == 1
    assert summary["focus_nonempty_days"] == 0
    assert summary["focus_signal_coverage"] == 0
    assert summary["rolling"]["20D"]["valid_score_days"] == 0
    assert summary["rolling"]["20D"]["mean_score"] is None


def test_recommendation_score_api_reads_saved_revision_and_does_not_accept_formula_overrides(tmp_path):
    from tests.support.local_service import ResearchService
    from stockfish_core.storage import SQLiteStore

    core = ResearchService(root=tmp_path / "state", store=SQLiteStore(tmp_path / "score.sqlite3"),
                              allow_test_fixture=True, data_dir=tmp_path / "no_model")
    try:
        run = _run([_row(f"S{i:02d}", "FOCUS" if i < 15 else "WATCH", rank=i + 1) for i in range(30)])
        labels = [_label(f"S{i:02d}", 1, (i - 12) / 100) for i in range(30)]
        score = build_score_revision(run, labels, datetime(2026, 9, 15, 20, 30, tzinfo=SH))
        core.store.put_document("strategy_experiment_runs", run["run_id"], run)
        core.store.put_document("recommendation_score_revisions", score["score_run_id"], score)
        latest = core.candidates.latest_recommendation_scores(tier="FOCUS_SCORE", horizon=1,
                                                            validation_epoch_id=score["validation_epoch_id"])
        by_run = core.candidates.recommendation_scores_for_run(run["run_id"], tier="FOCUS_SCORE", horizon=1,
                                                              validation_epoch_id=score["validation_epoch_id"])
        assert latest["latest"]["score_spec_id"] == SCORE_SPEC_ID
        assert latest["latest"]["records"][0]["random_basket_count"] == DEFAULT_RANDOM_BASKETS
        assert by_run["items"][0]["signal_run_id"] == run["run_id"]
        with pytest.raises(TypeError):
            core.candidates.latest_recommendation_scores(random_basket_count=1)
    finally:
        core.close()
