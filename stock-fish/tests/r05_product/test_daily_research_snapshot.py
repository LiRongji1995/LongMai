"""Phase 1 frozen product projections; all inputs are explicit TEST_FIXTURE."""
from copy import deepcopy
import json

import pytest

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.daily_research import (
    STRATEGY_ID, recommendation_snapshot, render_daily_report, research_identity,
)


def frozen_run():
    items = []
    for number, decision in enumerate(("FOCUS", "WATCH", "UNKNOWN", "REJECTED")):
        conditions = {
            "trend_state": {"status": "PASS", "slope": 0.03},
            "setup_identification": {"status": "PASS", "primary_stage": "START"},
            "same_theme_context": {"status": "UNKNOWN" if decision == "WATCH" else "PASS",
                                   "reason_codes": ["TEST_FIXTURE_THEME_MISSING"] if decision == "WATCH" else []},
            "critical_counterevidence": {"status": "FAIL" if decision == "REJECTED" else "PASS"},
        }
        items.append({
            "instrument_id": f"TEST_FIXTURE_{number}.SH", "name": f"测试证券{number}",
            "decision_status": decision, "status": decision, "primary_stage": "START",
            "reason_codes": ["SAM_WATCH_SAME_THEME_CONTEXT_INCOMPLETE"] if decision == "WATCH" else ["TEST_FIXTURE_ORIGINAL_REASON"],
            "watch_reasons": ["SAM_WATCH_SAME_THEME_CONTEXT_INCOMPLETE"] if decision == "WATCH" else [],
            "condition_statuses": {key: value["status"] for key, value in conditions.items()},
            "conditions": conditions, "comparable_close": "10", "comparable_basis": "TEST_FIXTURE_BASIS",
            "market_context": {"status": "UNKNOWN", "ecosystem_state": "UNKNOWN",
                               "market_width": {"known_limit_event_count": 2, "unknown_limit_event_count": 2},
                               "reason_codes": ["TEST_FIXTURE_LIMIT_COVERAGE_MISSING"]},
            "display_order": number + 1,
        })
    return {
        "run_id": "TEST_FIXTURE_FROZEN_RUN", "input_id": "TEST_FIXTURE_INPUT",
        "input_hash": "TEST_FIXTURE_INPUT_HASH", "content_hash": "TEST_FIXTURE_SOURCE_HASH",
        "observation_date": "2026-09-18", "decision_cutoff": "2026-09-18T21:00:00+08:00",
        "generated_at": "2026-09-18T21:02:00+08:00", "coverage_type": "TEST_FIXTURE",
        "source_refs": ["TEST_FIXTURE_RECEIPT"], "validation_epoch_id": "TEST_FIXTURE_EPOCH",
        "signal_implementation_version": "TEST_FIXTURE_OLD_IMPLEMENTATION",
        "signal_config_hash": "TEST_FIXTURE_OLD_CONFIG", "signal_spec_revision": "TEST_FIXTURE_OLD_SPEC",
        "prospective_validation": {"target_session": "2026-09-21", "signal_run_persisted": True,
                                   "official_primary_capture": False},
        "results": {STRATEGY_ID: {"items": items, "status": "COMPLETE", "scope_label": "FULL_SCOPE",
                                  "counts": {"focus": 1, "watch": 1, "unknown": 1, "rejected": 1},
                                  "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED"}},
    }


def test_snapshot_preserves_all_decisions_order_and_original_versions_without_mutation():
    run = frozen_run()
    before = json.dumps(run, ensure_ascii=False, sort_keys=True)
    snapshot = recommendation_snapshot(run, attempt={"final_status": "READY"})
    assert [row["decision_status"] for row in snapshot["items"]] == ["FOCUS", "WATCH", "UNKNOWN", "REJECTED"]
    assert [row["instrument_id"] for row in snapshot["items"]] == [row["instrument_id"] for row in run["results"][STRATEGY_ID]["items"]]
    assert snapshot["counts"] == {"FOCUS": 1, "WATCH": 1, "UNKNOWN": 1, "REJECTED": 1}
    assert snapshot["identity"]["strategy_version"] == "TEST_FIXTURE_OLD_IMPLEMENTATION"
    assert snapshot["identity"]["config_version"] == "TEST_FIXTURE_OLD_CONFIG"
    assert snapshot["identity"]["target_session"] == "2026-09-21"
    assert snapshot["source_run_hash"] == run["content_hash"]
    assert snapshot["snapshot_hash"] == digest({key: value for key, value in snapshot.items() if key != "snapshot_hash"})
    assert snapshot == recommendation_snapshot(run, attempt={"final_status": "READY"})
    snapshot["source_refs"].append("TEST_FIXTURE_LATER_SOURCE")
    snapshot["items"][0]["reason_codes"].append("TEST_FIXTURE_LATER_REASON")
    assert json.dumps(run, ensure_ascii=False, sort_keys=True) == before


def test_frozen_watch_reason_is_not_upgraded_when_details_are_expanded():
    run = frozen_run()
    original = deepcopy(run["results"][STRATEGY_ID]["items"])
    for row in run["results"][STRATEGY_ID]["items"]:
        row.pop("conditions")
        row.pop("market_context")
        row.update(detail_ref="TEST_FIXTURE_DETAIL_" + row["instrument_id"], detail_hash="TEST_FIXTURE_HASH")
    snapshot = recommendation_snapshot(run, expanded_items=original)
    watch = snapshot["items"][1]
    assert watch["decision_status"] == "WATCH"
    assert "同题材情境证据尚不完整" in watch["reason_summary"]
    assert any("同题材情境=UNKNOWN" in value for value in watch["unknowns"])
    assert snapshot["market_evidence"]["ecosystem_state"] == "UNKNOWN"
    assert "TEST_FIXTURE_LIMIT_COVERAGE_MISSING" in snapshot["market_evidence"]["reason_codes"]
    assert original[0]["comparable_close"] == snapshot["items"][0]["comparable_close"]


@pytest.mark.parametrize("field,new_value", [("decision_status", "FOCUS"), ("comparable_close", "999"),
                                            ("comparable_basis", "TEST_FIXTURE_LATER_BASIS"),
                                            ("reason_codes", ["TEST_FIXTURE_LATER_REASON"])])
def test_new_decision_or_later_price_cannot_replace_frozen_detail(field, new_value):
    run = frozen_run()
    details = deepcopy(run["results"][STRATEGY_ID]["items"])
    details[1][field] = new_value
    with pytest.raises(ValueError, match="FROZEN_FIELD_MISMATCH"):
        recommendation_snapshot(run, expanded_items=details)


def test_incomplete_or_duplicate_detail_scope_is_rejected():
    run = frozen_run()
    details = deepcopy(run["results"][STRATEGY_ID]["items"])
    with pytest.raises(ValueError, match="DETAIL_SCOPE_MISMATCH"):
        recommendation_snapshot(run, expanded_items=details[:-1])
    with pytest.raises(ValueError, match="DETAIL_SCOPE_MISMATCH"):
        recommendation_snapshot(run, expanded_items=details + details[:1])


def test_missing_historical_identity_and_price_are_explicit_without_current_defaults():
    run = frozen_run()
    for key in ("signal_implementation_version", "signal_config_hash", "signal_spec_revision", "generated_at", "prospective_validation"):
        run.pop(key)
    first = run["results"][STRATEGY_ID]["items"][0]
    first.pop("comparable_close")
    first.pop("comparable_basis")
    first.pop("primary_stage")
    snapshot = recommendation_snapshot(run)
    identity = snapshot["identity"]
    assert identity["strategy_version"] is None and identity["config_version"] is None
    assert identity["generated_at"] is None and identity["target_session"] is None
    assert identity["execution_status"] == "UNKNOWN"
    assert snapshot["items"][0]["comparable_close"] is None
    assert snapshot["items"][0]["primary_setup"] is None
    assert any("原冻结可比价格基准缺失" in value for value in snapshot["items"][0]["unknowns"])


def test_target_session_uses_supplied_verified_calendar_and_attempt_cannot_rebind_run():
    run = frozen_run()
    run["prospective_validation"].pop("target_session")
    run["sessions"] = ["2026-09-18", "2026-09-28"]
    assert research_identity(run)["target_session"] == "2026-09-28"
    assert research_identity(run)["execution_status"] == "SAVED"
    assert research_identity(run, {"target_session": "2026-09-29"})["target_session"] == "2026-09-29"
    with pytest.raises(ValueError, match="ATTEMPT_RUN_MISMATCH"):
        research_identity(run, {"run_id": "TEST_FIXTURE_DIFFERENT_RUN"})


def test_market_conflict_and_missing_frozen_context_remain_unknown():
    run = frozen_run()
    rows = run["results"][STRATEGY_ID]["items"]
    rows[1]["market_context"]["ecosystem_state"] = "HOT"
    assert recommendation_snapshot(run)["market_evidence"]["reason_codes"] == ["FROZEN_MARKET_CONTEXT_CONFLICT"]
    for row in rows:
        row.pop("market_context")
    assert recommendation_snapshot(run)["market_evidence"]["reason_codes"] == ["FROZEN_MARKET_CONTEXT_UNAVAILABLE"]


def test_report_lists_all_focus_and_watch_without_top_ten_cutoff_and_preserves_unknown():
    run = frozen_run()
    rows = run["results"][STRATEGY_ID]["items"]
    for number in range(12):
        extra = deepcopy(rows[1])
        extra["instrument_id"] = f"TEST_FIXTURE_EXTRA_{number:02d}.SZ"
        rows.append(extra)
    report = render_daily_report(recommendation_snapshot(run))
    for row in rows:
        if row["decision_status"] in {"FOCUS", "WATCH"}:
            assert row["instrument_id"] in report
    assert "WATCH=13" in report and "UNKNOWN=1" in report and "REJECTED=1" in report
    assert "原市场判断：UNKNOWN" in report
    assert "NO_OUTCOME_SUMMARY" in report
    assert "不生成投资建议" in report and "MARKET_EDGE=NOT_ESTABLISHED" in report


@pytest.mark.parametrize("status", ["NOT_READY", "FAILED", "MISSED", "INTERRUPTED"])
def test_failure_report_does_not_invent_snapshot_or_zero_candidates(status):
    report = render_daily_report(None, delivery={"status": status, "target_session": "2026-09-21",
                                               "reason_codes": ["TEST_FIXTURE_BLOCKER"]})
    assert status in report and "TEST_FIXTURE_BLOCKER" in report and "2026-09-21" in report
    assert "不是合法空名单" in report and "FOCUS=0" not in report
    assert "原运行：UNKNOWN" in report


def test_report_preserves_all_twenty_offsets_and_missing_metric_states_without_recalculation():
    summary = {"validation_status": "NOT_VERIFIED", "as_of": "2026-10-30T20:30:00+08:00", "groups": []}
    for offset in range(1, 21):
        summary["groups"].append({"strategy_version": "TEST_FIXTURE_OLD_IMPLEMENTATION", "config_version": "TEST_FIXTURE_OLD_CONFIG",
                                  "decision_status": "WATCH", "primary_setup": "START", "offset": offset,
                                  "counts": {"signals": 2, "matured": 2, "valid_return": 1, "valid_path": 0,
                                             "missing_labels": 1, "status_counts": {"MISSING_PRICE": 1}},
                                  "up_rate": 1, "return_distribution": {"median": "0.04"},
                                  "mfe_distribution": {"median": None}, "mae_distribution": {"median": None}})
    report = render_daily_report(recommendation_snapshot(frozen_run()), validation_summary=summary)
    assert all(f"| T+{offset} |" in report for offset in range(1, 21))
    assert "MISSING_PRICE" in report and "| 0.04 | UNKNOWN | UNKNOWN |" in report
    assert "NOT_VERIFIED" in report and "TEST_FIXTURE_OLD_CONFIG" in report


def test_existing_strategy_fixture_projects_without_reclassification():
    from tests.yaogu.fixtures import make_bundle
    from stockfish_core.yaogu.strategy_experiments import run_active_daily_strategy

    bundle, cutoff = make_bundle(6, session_count=85)
    run = run_active_daily_strategy(bundle, cutoff=cutoff, input_id="TEST_FIXTURE_INPUT")
    before = digest(run)
    snapshot = recommendation_snapshot(run)
    original = run["results"][STRATEGY_ID]["items"]
    assert snapshot["coverage_type"] == "TEST_FIXTURE"
    assert [(row["instrument_id"], row["decision_status"], row["primary_setup"]) for row in snapshot["items"]] == [
        (row["instrument_id"], row["decision_status"], row["primary_stage"]) for row in original]
    assert digest(run) == before


def test_flat_delivery_contract_exposes_failure_stage_time_blockers_and_error():
    delivery = {
        "execution_status": "NOT_READY", "target_session": "2026-09-21", "run_id": None,
        "reason": "TEST_FIXTURE_CRITICAL_INPUT_MISSING", "stage": "TEST_FIXTURE_READINESS",
        "timestamp": "2026-09-18T21:01:00+08:00",
        "blockers": [{"code": "TEST_FIXTURE_PRICE_COVERAGE"}],
        "missing_evidence": [{"code": "TEST_FIXTURE_COMPARABLE_BASIS"}],
        "error": {"code": "TEST_FIXTURE_PROVIDER_UNAVAILABLE"},
    }
    report = render_daily_report(None, delivery=delivery)
    assert "交付状态：NOT_READY" in report and "执行状态：NOT_READY" in report
    for key in ("reason", "stage", "timestamp"):
        assert delivery[key] in report
    for value in ("TEST_FIXTURE_PRICE_COVERAGE", "TEST_FIXTURE_COMPARABLE_BASIS", "TEST_FIXTURE_PROVIDER_UNAVAILABLE"):
        assert value in report
    assert "不是合法空名单" in report


def test_key_evidence_retains_frozen_ma_values_nested_details_and_selected_setup():
    run = frozen_run()
    row = run["results"][STRATEGY_ID]["items"][0]
    row["conditions"]["trend_state"].update(ma_values={"20": 9.83, "60": 8.21}, ma_slopes={"60": 0.013},
                                           dist_ma={"20": 0.017}, source_refs=["TEST_FIXTURE_MA_SOURCE"])
    row["conditions"]["liquidity_bottom"] = {"status": "PASS", "details": {"amount_cny": 735000000, "turnover_fraction": 0.12}}
    row["conditions"]["setup_identification"]["stage_candidates"] = {
        "START": {"status": "PASS", "breakout_level": 9.5, "supporting_evidence": ["TEST_FIXTURE_BREAKOUT_CONFIRMED"]}}
    row["conditions"]["behavioral_confirmation"] = {
        "status": "PASS", "required_events": ["TEST_FIXTURE_BREAKOUT"],
        "events": {"TEST_FIXTURE_BREAKOUT": {"status": "PASS", "value": 10.0}}}
    row.update(amount_cny=735000000, turnover_fraction=0.12, detail_ref="TEST_FIXTURE_DETAIL_REF", detail_hash="TEST_FIXTURE_DETAIL_HASH")
    snapshot = recommendation_snapshot(run)
    evidence = {value["key"]: value for value in snapshot["items"][0]["key_evidence"]}
    assert evidence["trend_state"]["values"]["ma_values"]["20"] == 9.83
    assert evidence["liquidity_bottom"]["values"]["amount_cny"] == 735000000
    assert evidence["setup_identification"]["values"]["selected_setup_evidence"]["breakout_level"] == 9.5
    assert evidence["behavioral_confirmation"]["values"]["event_evidence"]["TEST_FIXTURE_BREAKOUT"]["value"] == 10.0
    report = render_daily_report(snapshot)
    for fact in ("9.83", "0.013", "735000000", "TEST_FIXTURE_MA_SOURCE", "TEST_FIXTURE_DETAIL_REF", "TEST_FIXTURE_DETAIL_HASH"):
        assert fact in report
    assert "原 run 来源凭据（非逐股归因）" in report
