"""Explicit TEST_FIXTURE archives exercise the unchanged account engine."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from scripts import codex_r2_execution_v1 as subject
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
from tests.support.short_horizon_strength_fixture import strength_bundle, payload


def seal(run):
    run["content_hash"] = digest_json_stream({k: v for k, v in run.items() if k != "content_hash"})
    return run


@pytest.fixture(scope="module")
def native_run():
    bundle, cutoff = strength_bundle()
    value = payload(bundle)
    for bar in value["bars"]:
        bar["turnover_fraction"] = .06
    next(row for row in value["receipts"] if row["receipt_id"] == "TEST_FIXTURE_BARS")["content_hash"] = digest(value["bars"])
    run = run_short_horizon_strategy(MarketInput.model_validate(value), cutoff=cutoff,
                                    strategy_id=subject.STRATEGY)
    target = bundle.observation_date + timedelta(days=1)
    while target.weekday() > 4:
        target += timedelta(days=1)
    # Explicit fixture metadata; no historical or live-source claim is made.
    run.update(generated_at=cutoff.isoformat(), target_session=target.isoformat())
    assert run["results"][subject.STRATEGY]["research_selected_ids"]
    return seal(run)


def archive(tmp_path, run, edit=None):
    days = [run["observation_date"], run["target_session"]]
    while len(days) < 8:
        next_day = date.fromisoformat(days[-1]) + timedelta(days=1)
        while next_day.weekday() > 4:
            next_day += timedelta(days=1)
        days.append(next_day.isoformat())
    ids = run["results"][subject.STRATEGY]["research_selected_ids"]
    end = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
    artifacts = []

    def add(kind, records, day, clock="08:00:00"):
        rid = "TEST_FIXTURE_" + str(len(artifacts))
        instant = day + "T" + clock + "+08:00"
        clocks = {key: instant for key in ("event_time", "source_published_at", "first_seen_at", "ingested_at")}
        rows = []
        for raw in records:
            item = dict(raw, source_refs=[rid], **clocks)
            if edit is not None:
                item = edit(kind, day, item)
            rows.append({"kind": kind, "value": item})
        raw = json.dumps({"records": rows}).encode()
        name = rid + ".json"
        (tmp_path / name).write_bytes(raw)
        artifacts.append({"path": name, "receipt": dict(receipt_id=rid, provider="TEST_FIXTURE",
            dataset="TEST_FIXTURE", content_hash=hashlib.sha256(raw).hexdigest(),
            field_version="TEST_FIXTURE_R2_EXECUTION_V1", row_count=len(rows),
            pagination_complete=True, status="COMPLETE", **clocks)})

    add("CALENDAR", [{"sessions": days}], days[0])
    add("COST", [dict(effective_from=days[0], effective_to=end, commission_rate=".0003",
        minimum_commission="5", transfer_rate=".00001", sell_tax_rate=".0005",
        commission_includes_transfer_or_tax=False, commission_status="PREDECLARED_HYPOTHETICAL")], days[0])
    for day in days:
        add("SECURITY", [dict(instrument_id=iid, exchange="SSE", board="MAIN", security_type="A_COMMON",
            listed_on="2020-01-01", effective_from=day,
            effective_to=(date.fromisoformat(day) + timedelta(days=1)).isoformat(), st=False, suspended=False)
            for iid in ids], day)
        add("RULE", [dict(instrument_id=iid, session=day, exchange="SSE", rule_id="TEST_FIXTURE_RULE",
            effective_from=days[0], effective_to=end, lot_size=100, tick_size=".01", settlement="T_PLUS_1")
            for iid in ids], day)
        add("ORDER_LIMIT", [dict(instrument_id=iid, session=day, rule_id="TEST_FIXTURE_RULE", limit_up="11")
                            for iid in ids], day)
        add("BAR", [dict(instrument_id=iid, session=day, open="10", high="11", low="9", close="10.05",
            limit_up="11", limit_down="9", limit_applicable=True, limit_verified=True,
            rule_id="TEST_FIXTURE_RULE") for iid in ids], day, "15:00:00")
        add("ACTION_COVERAGE", [dict(instrument_id=iid, session=day, from_session=days[0], through_session=day,
                                    complete=True, events=[]) for iid in ids], day, "15:00:00")
    return dict(contract="R1_ARCHIVED_EXECUTION_EVIDENCE_V2", coverage_type="TEST_FIXTURE",
                sessions=days, report_as_of=days[-1] + "T16:00:00+08:00", artifacts=artifacts)


def evaluate(run, declaration, tmp_path, **kwargs):
    return subject.evaluate_r2(run, expected_content_hash=run["content_hash"],
        execution_archive=declaration, archive_base=tmp_path, allow_test_fixture=True, **kwargs)


def test_readiness_cannot_restore_source_or_returns(native_run):
    result = subject.readiness(native_run, expected_content_hash=native_run["content_hash"])
    assert result["status"] == "BLOCKED_DATA"
    assert result["source_authenticated"] is False and result["account_return"] is None
    assert result["selected_ids"] == native_run["results"][subject.STRATEGY]["research_selected_ids"]


def test_native_r2_to_real_archived_fixture_account(native_run, tmp_path):
    before = deepcopy(native_run)
    declaration = archive(tmp_path, native_run)
    result = evaluate(native_run, declaration, tmp_path)
    assert result["status"] == "SIMULATED_COMPLETE"
    assert result["selected_ids"] == before["results"][subject.STRATEGY]["research_selected_ids"]
    assert result["funnel"]["closed"] == result["selected_count"]
    assert result["account_simulation"]["contract"] == "EXECUTION_RESEARCH_R1"
    for row in result["account_simulation"]["records"]:
        assert row["quantity"] % 100 == 0
        assert Decimal(row["buy_fees"]) >= 5 and Decimal(row["sell_fees"]) >= 5
        assert row["actual_exit_session"] == declaration["sessions"][6]
        assert row["entry_session"] == declaration["sessions"][1]
        assert row["net_return"] < 0
    assert native_run == before
    assert result["formal_focus_count"] == 0 and result["market_edge"] == "NOT_ESTABLISHED"
    assert result["capacity_status"] == "NOT_VALIDATED"
    assert result["theme_risk_status"].startswith("UNKNOWN")
    assert result["publication_policy"] == "FROZEN_GENERATION_PROXY_NOT_USER_RECEIPT"
    assert result["execution_evidence_role"] == "LOCAL_ARCHIVE_CONSISTENCY_ONLY"
    assert result["source_reauthenticated_now"] is False
    assert result["evaluation_admissible_for_market_validation"] is False
    assert result["strategy_account_return"] is None
    assert digest_json_stream({k: v for k, v in result.items() if k != "content_hash"}) == result["content_hash"]


def test_cost_sensitivity_uses_unchanged_engine(native_run, tmp_path):
    declaration = archive(tmp_path, native_run)
    low = evaluate(native_run, declaration, tmp_path, slippage_bps=0)
    high = evaluate(native_run, declaration, tmp_path, slippage_bps=30)
    assert low["account_simulation"]["account_return"] > high["account_simulation"]["account_return"]


def test_limit_up_preserves_unfilled_selected_signal(native_run, tmp_path):
    target = native_run["target_session"]
    def edit(kind, day, item):
        if kind == "BAR" and day == target:
            item["open"] = "11"
        return item
    result = evaluate(native_run, archive(tmp_path, native_run, edit), tmp_path)
    assert result["funnel"]["assumed_entries"] == 0
    assert result["funnel"]["status_counts"] == {"ENTRY_NOT_FILLED": result["selected_count"]}
    assert all(row["entry_evidence"] == "BUY_OPEN_LIMIT_UP" for row in result["account_simulation"]["records"])


def test_missing_action_evidence_preserves_uncertainty(native_run, tmp_path):
    def edit(kind, day, item):
        if kind == "ACTION_COVERAGE":
            item["complete"] = False
        return item
    result = evaluate(native_run, archive(tmp_path, native_run, edit), tmp_path)
    assert result["status"] == "SIMULATED_INCOMPLETE"
    assert result["account_simulation"]["account_return"] is None
    assert result["funnel"]["status_counts"] == {"ENTRY_UNCERTAIN": result["selected_count"]}
    assert any(Decimal(point["reserved_uncertain_cny"]) > 0 for point in result["account_simulation"]["account_curve"])


def test_late_original_generation_not_backfilled(native_run, tmp_path):
    run = deepcopy(native_run)
    run["generated_at"] = run["target_session"] + "T09:16:00+08:00"
    seal(run)
    result = evaluate(run, archive(tmp_path, run), tmp_path)
    assert result["funnel"]["status_counts"] == {"LATE_SIGNAL": result["selected_count"]}
    assert result["account_simulation"]["account_return"] is None


def test_plain_real_run_and_forged_owner_cannot_execute(native_run, tmp_path):
    run = deepcopy(native_run)
    run["coverage_type"] = "RECONSTRUCTED_HISTORICAL"
    seal(run)
    result = subject.evaluate_r2(run, expected_content_hash=run["content_hash"])
    assert result["status"] == "BLOCKED_DATA" and result["account_return"] is None
    with pytest.raises(ValueError, match="GENUINE_SOURCE_OWNER"):
        subject.evaluate_r2(run, expected_content_hash=run["content_hash"], source_owner={"verified": True})


def test_fixture_requires_explicit_opt_in(native_run):
    with pytest.raises(ValueError, match="FIXTURE_OPT_IN"):
        subject.evaluate_r2(native_run, expected_content_hash=native_run["content_hash"])


@pytest.mark.parametrize("fault", ["truncated", "selection", "focus", "duplicate"])
def test_frozen_pool_and_safety_rejected_even_with_recomputed_hash(native_run, fault):
    run = deepcopy(native_run)
    arm = run["results"][subject.STRATEGY]
    if fault == "truncated":
        arm["items"].pop()
    elif fault == "selection":
        arm["research_selected_ids"] = []
    elif fault == "focus":
        arm["items"][0]["formal_focus"] = True
    else:
        arm["eligible_pool_ids"].append(arm["eligible_pool_ids"][0])
    seal(run)
    with pytest.raises(ValueError):
        subject.readiness(run, expected_content_hash=run["content_hash"])


def test_self_consistent_truncation_still_bound_to_original_qualification(native_run):
    run = deepcopy(native_run)
    arm = run["results"][subject.STRATEGY]
    removed = arm["items"].pop(0)["instrument_id"]
    arm["eligible_pool_ids"].remove(removed)
    arm["research_selected_ids"].remove(removed)
    arm["counts"]["eligible"] -= 1
    arm["counts"]["evaluated"] -= 1
    arm["counts"]["research_observation"] -= 1
    arm["counts"]["watch"] -= 1
    arm["counts"]["decision_counts"]["WATCH"] -= 1
    view = run["selection_results"][subject.STRATEGY + "_OBSERVATION"]
    view["selected_ids"].remove(removed)
    view["count"] -= 1
    view["items"] = [row for row in view["items"] if row["instrument_id"] != removed]
    seal(run)
    with pytest.raises(ValueError, match="QUALIFICATION_POOL_BINDING_MISMATCH"):
        subject.readiness(run, expected_content_hash=run["content_hash"])


@pytest.mark.parametrize("mode", ["selected_unknown", "watch_unselected"])
def test_selection_boolean_and_decision_must_agree(native_run, mode):
    run = deepcopy(native_run)
    arm = run["results"][subject.STRATEGY]
    row = arm["items"][0]
    assert row["research_selected"] is True and row["decision_status"] == "WATCH"
    if mode == "selected_unknown":
        row["decision_status"] = "UNKNOWN"
    else:
        row["research_selected"] = False
    seal(run)
    with pytest.raises(ValueError, match="SELECTION_DECISION_MISMATCH"):
        subject.readiness(run, expected_content_hash=run["content_hash"])


def test_universe_hash_binding_rejects_changed_qualification(native_run):
    run = deepcopy(native_run)
    run["qualification"]["universe_evidence_reason_codes"].append("TEST_FIXTURE_CHANGED")
    run["results"][subject.STRATEGY]["qualification"] = deepcopy(run["qualification"])
    seal(run)
    with pytest.raises(ValueError, match="QUALIFICATION_POOL_BINDING_MISMATCH"):
        subject.readiness(run, expected_content_hash=run["content_hash"])


def test_default_config_read_is_in_audited_read_count(native_run, tmp_path):
    declaration = archive(tmp_path, native_run)
    result = evaluate(native_run, declaration, tmp_path)
    config_size = next(ref["size"] for ref in result["implementation_references"]
                       if ref["path"].endswith("/config/stage_aware_research_r1.json"))
    assert result["default_execution_config_read_bytes"] == config_size
    assert result["verified_read_bytes"] == (2 * sum(ref["size"] for ref in result["implementation_references"])
        + 3 * sum(ref["size"] for ref in result["execution_source_references"]) + config_size)


def test_archive_corruption_rejected(native_run, tmp_path):
    declaration = archive(tmp_path, native_run)
    (tmp_path / declaration["artifacts"][0]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="RECEIPT_HASH_MISMATCH"):
        evaluate(native_run, declaration, tmp_path)


def test_final_source_change_revokes_result(native_run, tmp_path, monkeypatch):
    from stockfish_core.yaogu import execution_research_r1 as engine
    declaration = archive(tmp_path, native_run)
    original = engine.simulate
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path = tmp_path / declaration["artifacts"][0]["path"]
        path.write_bytes(path.read_bytes())
        return result
    monkeypatch.setattr(engine, "simulate", changed)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_AT_TERMINAL"):
        evaluate(native_run, declaration, tmp_path)


def test_borrowed_frozen_run_mutation_revokes_result(native_run, tmp_path, monkeypatch):
    from stockfish_core.yaogu import execution_research_r1 as engine
    run = deepcopy(native_run)
    declaration = archive(tmp_path, run)
    original = engine.simulate
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        run["results"][subject.STRATEGY]["items"][0]["reason_codes"].append("TEST_FIXTURE_LATE_CHANGE")
        return result
    monkeypatch.setattr(engine, "simulate", changed)
    with pytest.raises(ValueError, match="BORROWED_INPUT_CHANGED"):
        evaluate(run, declaration, tmp_path)


def test_plain_resolved_tape_not_accepted_as_archive(native_run, tmp_path):
    from stockfish_core.yaogu.research_r1_adapter import ResolvedTape
    with pytest.raises(ValueError, match="DECLARATION_REQUIRED_NOT_TAPE"):
        evaluate(native_run, ResolvedTape(coverage_type="TEST_FIXTURE").seal(), tmp_path)


def test_same_day_exit_policy_rejected(native_run, tmp_path):
    from stockfish_core.yaogu.research_r1 import config
    spec = config()
    spec.update(entry_offset=1, exit_offset=1, holding_session_intervals=0)
    with pytest.raises(ValueError, match="INVALID_EXECUTION_SPEC"):
        evaluate(native_run, archive(tmp_path, native_run), tmp_path, execution_spec=spec)


def test_read_budget_and_path_escape_fail_closed(native_run, tmp_path):
    declaration = archive(tmp_path, native_run)
    with pytest.raises(ValueError, match="READ_BUDGET"):
        evaluate(native_run, declaration, tmp_path, max_read_bytes=100)
    declaration["artifacts"][0]["path"] = "../outside.json"
    with pytest.raises(ValueError, match="PATH_ESCAPE"):
        evaluate(native_run, declaration, tmp_path)


def test_target_session_mismatch_rejected(native_run, tmp_path):
    declaration = archive(tmp_path, native_run)
    run = deepcopy(native_run)
    run["target_session"] = declaration["sessions"][2]
    seal(run)
    with pytest.raises(ValueError, match="TARGET_CALENDAR_MISMATCH"):
        evaluate(run, declaration, tmp_path)


def test_symlinked_archive_parent_is_rejected(native_run, tmp_path):
    original = tmp_path / "TEST_FIXTURE_original"
    original.mkdir()
    declaration = archive(original, native_run)
    link = tmp_path / "TEST_FIXTURE_link"
    link.symlink_to(original, target_is_directory=True)
    with pytest.raises(ValueError, match="ROOT_INVALID"):
        evaluate(native_run, declaration, link)
