"""TEST_FIXTURE: complete native authority/job/journal integration, no market I/O."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from scripts import daily_delivery_contract_v1 as contract
from scripts import durable_stage_metrics_v1 as metrics
from scripts import codex_sdk_tree_supervisor_v3 as supervisor
from tests.unit.test_business_authority_v1 import authority
from tests.unit.test_codex_delivery_audit_v1 import evidence
from tests.unit.test_codex_operational_report_v1 import attach_run
from tests.unit.test_codex_research_coverage_v1 import fixture_outcome


def prepare(authority, evidence):
    run, path, sha = attach_run(evidence)
    claim = authority.claim({"operation": "PREMARKET", **{k: run[k] for k in
        ("strategy_id", "observation_date", "target_session")}}, epoch_id="TEST_FIXTURE_epoch",
        limits={"requests": 10, "bytes": 10000})
    authority.start(claim)
    root = evidence.root.resolve()
    journal = root/"TEST_FIXTURE_worker_metrics"
    return run, path, sha, claim, root, journal


def complete_parent_journal(journal, claim):
    writer = metrics.StageJournal(journal, claim, test_fixture=True)
    with writer.stage("core"):
        pass
    return writer.finish("COMPLETE")


def native_worker(root, journal, claim):
    script = root/"TEST_FIXTURE_native_worker.py"
    script.write_text("""import json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from scripts.durable_stage_metrics_v1 import StageJournal
from scripts.codex_sdk_tree_supervisor_v3 import validate_worker_context
validate_worker_context(sys.argv[4])
j = StageJournal(pathlib.Path(sys.argv[2]), json.loads(sys.argv[3]), test_fixture=True)
with j.stage('capture', counters={'returned_read_bytes': {'capture': 23}}):
    time.sleep(.15)
with j.stage('core', counters={'returned_read_bytes': {'capture': 23, 'core': 19}}):
    pass
receipt = j.finish('COMPLETE')
with pathlib.Path(sys.argv[5]).open('x') as stream:
    json.dump(receipt, stream)
time.sleep(.15)
""")
    control = root/"TEST_FIXTURE_supervisor"
    receipt = root/"TEST_FIXTURE_writer_receipt.json"
    result = supervisor.run_supervised([sys.executable, "-I", "-B", str(script),
        str(metrics.ROOT), str(journal), json.dumps(claim), str(control), str(receipt)],
        cwd=root, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        limits={"tree_rss_bytes": 512*1024**2, "self_rss_bytes": 2*1024**3,
            "root_reap_seconds": 1., "max_seconds": 10., "output_bytes": 2*1024**2,
            "supervisor_read_bytes": 64*1024**2, "sample_interval_seconds": .03,
            "term_grace_seconds": .1, "kill_grace_seconds": 3.},
        control_dir=control, source_references=[supervisor.reference(script)[1]], test_fixture=True)
    assert result["status"] == "COMPLETE", result
    return control/"completion.json", json.loads(receipt.read_text())


def test_actual_authority_native_process_original_carriers_and_all_horizons(authority, evidence, capsys):
    run, path, sha, claim, root, journal = prepare(authority, evidence)
    process_report, writer = native_worker(root, journal, claim)
    binding = contract.bind_terminal_evidence(job=evidence.job, journal_directory=journal,
        canonical_run_path=path, supervisor_report_path=process_report, writer_receipt=writer)
    authority.charge(claim, {"requests": 2, "bytes": 42})
    authority.finish(claim, success=True, receipt=binding)
    outcomes = root/"TEST_FIXTURE_outcomes.json"
    outcomes.write_text(json.dumps(fixture_outcome(run)))
    out_sha = hashlib.sha256(outcomes.read_bytes()).hexdigest()
    report = contract.audit_delivery(authority, claim["business_id"], job=evidence.job,
        journal_directory=journal, run_path=path, run_sha256=sha,
        outcomes_path=outcomes, outcomes_sha256=out_sha)
    assert report["status"] == "COMPLETE" and report["authority_terminal_bound"] is True
    assert report["native_process_cleanup"]["verified"] is True
    assert report["horizon_cells"]["expected"] == 15
    assert report["horizon_cells"]["full_cartesian_product_provided"] is True
    assert [h["horizon"] for h in report["horizon_cells"]["by_horizon"]] == [1, 2, 3, 5, 10]
    assert all(h["states"] == {"MATURE": 1, "PENDING": 1, "UNKNOWN": 1, "NOT_PROVIDED": 0}
               for h in report["horizon_cells"]["by_horizon"])
    assert report["population"]["qualification"]["master_expected"] == 6
    assert report["population"]["qualification"]["identity_absent"] == 1
    assert report["resources"]["cumulative_spent"] == {"requests": 2, "bytes": 42}
    assert report["test_fixture"] and report["source_reauthenticated_now"] is False
    assert report["market_edge"] == "NOT_ESTABLISHED"
    assert contract.main(["--authority-db", str(authority.path), "--workspace-id", authority.workspace_id,
        "--business-id", claim["business_id"], "--job", str(evidence.job), "--journal", str(journal),
        "--run", str(path), "--run-sha256", sha]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert cli["horizon_cells"]["full_cartesian_product_provided"] is False
    assert all(h["states"]["NOT_PROVIDED"] == 3 for h in cli["horizon_cells"]["by_horizon"])
    # A later supervisor failure authority overrides an earlier COMPLETE file.
    process_report.with_name("failure.json").write_text('{"TEST_FIXTURE": true}')
    with pytest.raises(ValueError, match="TERMINAL_FAILURE"):
        contract.audit_delivery(authority, claim["business_id"], job=evidence.job,
            journal_directory=journal, run_path=path, run_sha256=sha)


def test_success_authority_without_native_cleanup_cannot_promote_archive(authority, evidence):
    _, path, sha, claim, _, journal = prepare(authority, evidence)
    complete_parent_journal(journal, claim)
    binding = contract.bind_terminal_evidence(job=evidence.job, journal_directory=journal, canonical_run_path=path)
    authority.finish(claim, success=True, receipt=binding)
    result = contract.audit_delivery(authority, claim["business_id"], job=evidence.job,
        journal_directory=journal, run_path=path, run_sha256=sha)
    assert result["status"] == "UNKNOWN"
    assert result["native_process_cleanup"] == {"verified": False, "status": "NOT_PROVIDED"}


def test_failed_authority_cannot_be_replaced_by_complete_core(authority, evidence):
    _, path, sha, claim, _, journal = prepare(authority, evidence)
    complete_parent_journal(journal, claim)
    authority.finish(claim, success=False, budget_known=False)
    result = contract.audit_delivery(authority, claim["business_id"], job=evidence.job,
        journal_directory=journal, run_path=path, run_sha256=sha)
    assert result["status"] == "FAILED_OR_UNKNOWN"
    assert result["workflow"]["workflow_status"] == "COMPLETE"
    assert result["resources"]["budget_known"] is False


def test_other_business_attempt_cannot_attach_same_core_or_journal(authority, evidence):
    run, path, sha, claim, _, journal = prepare(authority, evidence)
    complete_parent_journal(journal, claim)
    other = authority.claim({"operation": "PREMARKET", "observation_date": "2026-10-16",
        "target_session": "2026-10-19", "strategy_id": run["strategy_id"]}, epoch_id="TEST_FIXTURE_epoch")
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        contract.audit_delivery(authority, other["business_id"], job=evidence.job,
            journal_directory=journal, run_path=path, run_sha256=sha)


def test_complete_saved_dict_is_not_native_authority(authority, evidence):
    _, path, sha, claim, _, journal = prepare(authority, evidence)
    complete_parent_journal(journal, claim)
    with pytest.raises(ValueError, match="NATIVE_BUSINESS_AUTHORITY"):
        contract.audit_delivery(claim, claim["business_id"], job=evidence.job,
            journal_directory=journal, run_path=path, run_sha256=sha)


def calendar_rows():
    start = date(2050, 1, 1)
    return [{"calendar_date": str(start+timedelta(days=i)),
             "is_trading_day": "1" if (start+timedelta(days=i)).weekday() < 5 else "0"}
            for i in range(90)]


def protocol():
    rows = calendar_rows()
    sessions = [r["calendar_date"] for r in rows if r["is_trading_day"] == "1"]
    return contract.plan_protocol(rows, first_target_session=sessions[1],
        strategy_id="SHORT_HORIZON_STRENGTH_R2", calendar_reference={"sha256": "a"*64})


def test_twenty_days_are_not_synthesized_and_native_freeze_is_separate(authority):
    plan = protocol()
    assert len(plan["expected_sessions"]) == 20 and plan["real_acceptance"] == "NOT_RUN"
    frozen = contract.freeze_protocol(authority, plan, epoch_id="TEST_FIXTURE_epoch")
    result = contract.audit_sequence(authority, plan, [], protocol_business_id=frozen["business_id"])
    assert result["status"] == "NOT_ESTABLISHED" and result["audited_sessions"] == 0
    assert result["completion_rate"] == {"numerator": 0, "denominator": 20}
    assert result["workflow_status_counts"] == {"NOT_RUN": 20}
    assert result["latency"]["median_seconds"] is None
    assert "NATIVE_CALENDAR_NOT_REVERIFIED" in result["global_gaps"]


@pytest.mark.parametrize("mutation", ["gap", "nineteen", "skip", "changed_hash"])
def test_calendar_and_frozen_protocol_cannot_hide_missing_days(mutation):
    plan = protocol()
    if mutation in ("gap", "nineteen"):
        rows = calendar_rows()
        if mutation == "gap": rows.pop(4)
        with pytest.raises(ValueError):
            contract.plan_protocol(rows, first_target_session=plan["expected_sessions"][0]["target_session"],
                strategy_id=plan["strategy_id"], calendar_reference={"sha256": "a"*64},
                session_count=19 if mutation == "nineteen" else 20)
    else:
        plan["expected_sessions"][1]["observation_date"] = "2049-01-01"
        if mutation == "skip":
            plan["protocol_hash"] = metrics.digest({k: v for k, v in plan.items() if k != "protocol_hash"})
        with pytest.raises(ValueError):
            contract.audit_sequence(None, plan, [])


def test_production_calendar_cannot_be_substituted_by_json_or_fixture(authority):
    with pytest.raises(ValueError, match="NATIVE_CALENDAR_CACHE"):
        contract.audit_sequence(authority, protocol(), [], calendar_cache={"verified": True},
                                calendar_record={"status": "COMPLETE"})


def test_writer_cost_cannot_erase_final_write_or_sampling_expense(authority, evidence):
    _, path, _, claim, _, journal = prepare(authority, evidence)
    receipt = complete_parent_journal(journal, claim)
    receipt["publication_cost"]["written_bytes"] = 0
    with pytest.raises(ValueError, match="COST_UNDERSTATED"):
        contract.bind_terminal_evidence(job=evidence.job, journal_directory=journal,
            canonical_run_path=path, writer_receipt=receipt)


def test_journal_same_bytes_new_inode_breaks_authority_binding(authority, evidence):
    _, path, sha, claim, _, journal = prepare(authority, evidence)
    complete_parent_journal(journal, claim)
    binding = contract.bind_terminal_evidence(job=evidence.job, journal_directory=journal, canonical_run_path=path)
    authority.finish(claim, success=True, receipt=binding)
    target = journal/"000000.json"
    replacement = journal.parent/"TEST_FIXTURE_same_bytes.json"
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement, target)
    report = contract.audit_delivery(authority, claim["business_id"], job=evidence.job,
        journal_directory=journal, run_path=path, run_sha256=sha)
    assert report["authority_terminal_bound"] is False and report["status"] == "UNKNOWN"


def test_shared_read_budget_rejects_before_reading_oversize_event(authority, evidence):
    _, path, sha, claim, _, journal = prepare(authority, evidence)
    complete_parent_journal(journal, claim)
    with pytest.raises(ValueError, match="READ_LIMIT"):
        contract.audit_delivery(authority, claim["business_id"], job=evidence.job,
            journal_directory=journal, run_path=path, run_sha256=sha, _budget=contract._AuditBudget(1, 2))


def test_retrospective_window_cannot_be_frozen_by_relabeling(authority):
    plan = protocol()
    for row in plan["expected_sessions"]:
        row["observation_date"] = row["observation_date"].replace("2050", "2020")
        row["target_session"] = row["target_session"].replace("2050", "2020")
        row["deadline"] = row["deadline"].replace("2050", "2020")
    plan["protocol_hash"] = metrics.digest({k:v for k,v in plan.items() if k != "protocol_hash"})
    with pytest.raises(ValueError, match="FREEZE_TOO_LATE"):
        contract.freeze_protocol(authority, plan, epoch_id="TEST_FIXTURE_epoch")


@pytest.mark.parametrize("state", ["MATURE", "PENDING", "UNKNOWN", "NOT_PROVIDED"])
def test_complete_grid_does_not_imply_mature_twenty_day_acceptance(monkeypatch, state):
    # TEST_FIXTURE isolates acceptance arithmetic; it never fabricates a native
    # calendar, prospective authority receipt or authenticated real day.
    plan = protocol()
    def day_evidence(authority, *, pair, _budget):
        return {"business": {**pair, "strategy_id": plan["strategy_id"]}, "status": "COMPLETE",
            "test_fixture": False, "population": {"signal_coverage_type": "PROSPECTIVE_CAPTURED",
                "qualification": {"saved_qualification_has_no_reported_gaps": True}},
            "horizon_cells": {"full_cartesian_product_provided": True, "expected": 15,
                "by_horizon": [{"horizon": h, "states": {s: 3 if s == state else 0
                    for s in ("MATURE", "PENDING", "UNKNOWN", "NOT_PROVIDED")}}
                    for h in (1, 2, 3, 5, 10)]},
            "workflow": {"timing": {"final_sealed_at": pair["deadline"]}},
            "resources": {"budget_known": True}, "stage_metrics": {"status": "COMPLETE"},
            "native_process_cleanup": {"verified": True}, "identity": {"TEST_FIXTURE": True},
            "content_hash": "TEST_FIXTURE", "manual_interventions": None}
    monkeypatch.setattr(contract, "audit_delivery", day_evidence)
    result = contract.audit_sequence(None, plan, [{"pair": p} for p in plan["expected_sessions"]])
    assert result["operational_accepted_sessions"] == 20
    assert result["accepted_sessions"] == 0  # MATURE without original outcome authority is descriptive only
    assert result["label_maturity"]["complete_sessions"] == (20 if state == "MATURE" else 0)
    assert result["status"] == "NOT_ESTABLISHED"  # no native authority/calendar
    assert all(not row["operational_gaps"] for row in result["days"])
    assert all("OUTCOME_NATIVE_AUTHORITY_NOT_PROVEN" in row["acceptance_gaps"] for row in result["days"])


def test_maturity_requires_complete_counts_for_each_horizon():
    cells = {"full_cartesian_product_provided": True, "expected": 5,
        "by_horizon": [{"horizon": h, "states": {"MATURE": 1, "PENDING": 0, "UNKNOWN": 0, "NOT_PROVIDED": 0}}
                       for h in (1, 2, 3, 5, 10)]}
    assert contract._label_maturity(cells)["status"] == "COMPLETE"
    for mutation in ("drop_horizon", "drop_state", "wrong_total"):
        changed = deepcopy(cells)
        if mutation == "drop_horizon": changed["by_horizon"].pop()
        elif mutation == "drop_state": changed["by_horizon"][0]["states"].pop("UNKNOWN")
        else: changed["by_horizon"][0]["states"]["MATURE"] = 2
        assert contract._label_maturity(changed)["status"] == "NOT_ESTABLISHED"


def test_first_observation_must_be_immediate_native_predecessor():
    plan = protocol()
    actual = contract._sessions(calendar_rows())
    contract._verify_calendar_window(actual, plan["expected_sessions"])
    # Remaining twenty target sessions are unchanged; skipping only the first
    # predecessor must still fail, even when a caller recomputes the plan hash.
    plan["expected_sessions"][0]["observation_date"] = "2049-12-31"
    plan["protocol_hash"] = metrics.digest({k: v for k, v in plan.items() if k != "protocol_hash"})
    contract._protocol(plan)
    with pytest.raises(ValueError, match="FIRST_OBSERVATION_NOT_PREVIOUS_SESSION"):
        contract._verify_calendar_window(actual, plan["expected_sessions"])
