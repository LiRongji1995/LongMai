"""TEST_FIXTURE: bounded delivery accounting never executes or repairs jobs."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import codex_delivery_audit_v1 as audit
from scripts import codex_workspace as workspace


@pytest.fixture
def evidence(tmp_path):
    root = tmp_path / "TEST_FIXTURE_delivery"
    state, operations = root / "state", root / "scope" / "operations"
    job = state / "jobs" / "TEST_FIXTURE_job"
    job.mkdir(parents=True)
    operations.mkdir(parents=True)

    def write(path, value):
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        return audit.original._reference(path)

    core = {name: write(job/name, ("TEST_FIXTURE " + name).encode()) for name in audit.original.CORE_FILES}
    display = dict(artifacts={name: write(job/name, b"TEST_FIXTURE display")
                             for name in ("display.json", "display.csv", "report.md")})
    completion = dict(schema_version="CODEX_PREMARKET_SHARED_WORKFLOW_V1", status="COMPLETE",
        core_verified=True, phases={name: "COMPLETE" for name in audit.PHASES},
        run_id="TEST_FIXTURE_run", observation_date="2026-10-26", target_session="2026-10-27",
        strategy_id="SHORT_HORIZON_STRENGTH_R2", execution_engine="original",
        core_artifacts=core, theme_artifacts={}, display=display, theme_status="AVAILABLE", news=None,
        completed_at="2026-10-26T20:59:00+00:00")
    final = dict(schema_version=completion["schema_version"], status=completion["status"], job=str(job),
        external_worker_exit_code=0, external_worker_reaped=True, core_verified=True,
        implementation_epoch_verified=True, source_terminal_binding_verified=True,
        configuration=write(job/"configuration.json", {"TEST_FIXTURE": True}),
        code_registry=write(job/"code-registry.json", []), display=display,
        final_sealed_at="2026-10-26T21:00:00+00:00", elapsed_seconds=1800,
        execution_engine="original")

    def seal():
        final["completion"] = write(job/"worker-completion.json", completion)
        return write(job/"final-control.json", final)

    seal()
    scope = dict(backend="premarket", device=state.stat().st_dev, inode=state.stat().st_ino)
    identity = dict(schema_version="CODEX_DAILY_EXECUTION_PIPELINE_V2", backend="premarket",
        state_root=str(state), request_key="TEST_FIXTURE_request", observation_date="2026-10-26",
        target_session="2026-10-27", execution_engine="original")

    def operation(phase="RECORDED", changed_identity=None, changed_scope=None):
        selected_identity, selected_scope = changed_identity or identity, changed_scope or scope
        key = workspace._digest(dict(identity=selected_identity, scope=selected_scope))
        record = dict(schema_version=workspace.VERSION, operation_id=key, scope=selected_scope,
            identity=selected_identity, phase=phase, started_at="2026-10-26T20:00:00+00:00")
        if phase == "RECORDED":
            record.update(completed_at="2026-10-26T21:01:00+00:00", action_seconds=1810,
                receipt=dict(schema_version="CODEX_DAILY_EXECUTION_PIPELINE_V2", status=final["status"],
                    job=str(job), final=audit.original._reference(job/"final-control.json"),
                    completion=final["completion"], execution_exit_code=final["external_worker_exit_code"],
                    **{key:completion[key] for key in ("run_id", "observation_date", "target_session",
                                                     "strategy_id", "execution_engine")}))
        elif phase == "FAILED_OR_UNKNOWN":
            record["finished_at"] = "2026-10-26T21:01:00+00:00"
        path = operations / (key + ".json")
        write(path, record)
        return path

    runtime = SimpleNamespace(profile=dict(backend="premarket", state_root=str(state), name="TEST_FIXTURE"),
        directory=operations.parent, scope=scope, check_binding=lambda: None)
    return SimpleNamespace(root=root, state=state, job=job, operations=operations, write=write,
        core=core, completion=completion, final=final, seal=seal, identity=identity, scope=scope,
        operation=operation, runtime=runtime)


def test_complete_workflow_does_not_prove_data_readiness_or_user_receipt(evidence):
    result = audit.audit_job(evidence.job)
    assert result["workflow_status"] == "COMPLETE"
    assert result["archived_workflow_complete"] is True
    assert result["data_completeness"] == "UNKNOWN_REQUIRES_FULL_POOL_COVERAGE_AUDIT"
    assert result["user_delivery_status"] == "UNKNOWN_NOT_OBSERVED"
    assert result["core_usable_for_new_delivery"] is False
    assert result["source_reauthenticated_now"] is result["retry_authorized"] is False
    assert result["network_calls"] == 0


def test_completed_unknowns_are_sealed_but_not_full_data(evidence):
    evidence.completion.update(status="COMPLETED_WITH_UNKNOWNS", theme_status="UNKNOWN")
    evidence.completion["phases"]["theme"] = "UNKNOWN"
    evidence.final.update(status="COMPLETED_WITH_UNKNOWNS", external_worker_exit_code=2)
    evidence.seal()
    result = audit.audit_job(evidence.job)
    assert result["archived_workflow_complete"] is True
    assert result["data_completeness"] == "KNOWN_STAGE_GAPS"


def test_failed_supervisor_never_upgrades_intermediate_worker_success(evidence):
    evidence.final.update(status="BUDGET_EXCEEDED", external_worker_exit_code=-9, display=None)
    evidence.seal()
    result = audit.audit_job(evidence.job)
    assert result["workflow_status"] == "BUDGET_EXCEEDED"
    assert result["archived_workflow_complete"] is False
    assert result["archived_core_status"] == "BYTES_VERIFIED_FRESH_SOURCE_AUDIT_REQUIRED"
    assert result["core_usable_for_new_delivery"] is False


def test_failed_supervisor_can_inspect_unbound_core_without_request_claim(evidence):
    evidence.final.update(status="FAILED", display=None)
    evidence.final.pop("completion")
    evidence.write(evidence.job/"final-control.json", evidence.final)
    result = audit.audit_job(evidence.job)
    assert result["completion_binding"] == "UNBOUND_WORKER_RECORD_AFTER_FAILED_SUPERVISOR"
    assert result["request_binding"] == "NOT_PROVIDED"
    assert result["archived_workflow_complete"] is False


@pytest.mark.parametrize("field,value", [("core_verified", False), ("external_worker_reaped", False),
    ("source_terminal_binding_verified", None), ("implementation_epoch_verified", False),
    ("external_worker_exit_code", 3)])
def test_success_requires_terminal_guards(evidence, field, value):
    evidence.final[field] = value
    evidence.seal()
    with pytest.raises(audit.AuditError, match="TERMINAL_GUARDS"):
        audit.audit_job(evidence.job)


def test_complete_with_partial_phase_is_rejected(evidence):
    evidence.completion["phases"]["capture"] = "PARTIAL"
    evidence.seal()
    with pytest.raises(audit.AuditError, match="STATUS_PHASE_CONFLICT"):
        audit.audit_job(evidence.job)


def test_success_with_failed_completion_is_rejected(evidence):
    evidence.completion["status"] = "FAILED"
    evidence.seal()
    with pytest.raises(audit.AuditError, match="SUCCESS_COMPLETION_CONFLICT"):
        audit.audit_job(evidence.job)


def test_complete_core_set_and_phases_cannot_be_omitted(evidence):
    evidence.completion["core_artifacts"].pop("all.csv")
    evidence.seal()
    with pytest.raises(audit.AuditError, match="SUCCESS_FULL_OUTPUT"):
        audit.audit_job(evidence.job)


def test_full_output_hash_detects_changed_csv(evidence):
    (evidence.job/"all.csv").write_bytes(b"TEST_FIXTURE changed")
    with pytest.raises(audit.AuditError, match="REFERENCE_MISMATCH"):
        audit.audit_job(evidence.job)


def test_same_bytes_new_inode_is_not_original_artifact(evidence):
    path = evidence.job/"all.csv"
    replacement = evidence.job/"replacement"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(audit.AuditError, match="REFERENCE_MISMATCH"):
        audit.audit_job(evidence.job)


def test_owned_reference_cannot_escape_job(evidence):
    ref = evidence.write(evidence.root/"outside.csv", b"TEST_FIXTURE")
    evidence.completion["core_artifacts"]["all.csv"] = ref
    evidence.seal()
    with pytest.raises(audit.AuditError, match="OUTSIDE_JOB"):
        audit.audit_job(evidence.job)


def test_symlink_and_hardlink_controls_refused(evidence):
    original = evidence.job/"final-control.json"
    alias = evidence.root/"alias"
    alias.symlink_to(evidence.job, target_is_directory=True)
    with pytest.raises(audit.AuditError, match="CANONICAL_PATH"):
        audit.audit_job(alias)
    import os
    os.link(original, evidence.root/"hardlink")
    with pytest.raises(audit.AuditError, match="SINGLE_LINK"):
        audit.audit_job(evidence.job)


def test_ancestor_redirect_after_resolve_is_refused_before_file_read(evidence, monkeypatch):
    parent = evidence.root / "TEST_FIXTURE_reader"
    parent.mkdir()
    path = parent / "control.json"
    path.write_bytes(b'{"TEST_FIXTURE":true}')
    moved = evidence.root / "TEST_FIXTURE_moved"
    original_path = audit._path

    def redirect(value, **kwargs):
        checked = original_path(value, **kwargs)
        if checked == path:
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
        return checked

    monkeypatch.setattr(audit, "_path", redirect)
    monkeypatch.setattr(audit.os, "read", lambda *args: pytest.fail("redirected evidence must not be read"))
    reader = audit._Reader(1024, 30)
    with pytest.raises(OSError):
        reader.read(path, control=True)
    assert reader.returned_bytes == 0


def test_parent_replacement_after_open_is_detected_with_pinned_descriptor(evidence, monkeypatch):
    parent = evidence.root / "TEST_FIXTURE_reader"
    parent.mkdir()
    path = parent / "control.json"
    payload = b'{"TEST_FIXTURE":true}'
    path.write_bytes(payload)
    moved = evidence.root / "TEST_FIXTURE_moved"
    original_read = audit.os.read
    replaced = False

    def replace_parent(fd, count):
        nonlocal replaced
        raw = original_read(fd, count)
        if raw and not replaced:
            replaced = True
            parent.rename(moved)
            parent.mkdir()
            path.write_bytes(payload)
        return raw

    monkeypatch.setattr(audit.os, "read", replace_parent)
    with pytest.raises(audit.AuditError, match="PARENT_DIRECTORY_CHANGED"):
        audit._Reader(1024, 30).read(path, control=True)


def test_read_and_time_limits_fail_closed(evidence):
    with pytest.raises(audit.AuditError, match="READ_LIMIT"):
        audit.audit_job(evidence.job, max_bytes=1)
    with pytest.raises(audit.AuditError, match="TIME_LIMIT"):
        audit.audit_job(evidence.job, max_seconds=1e-12)


def test_final_reread_rejects_late_mutation(evidence, monkeypatch):
    terminal = audit._Reader.terminal
    def mutate(reader):
        (evidence.job/"all.csv").write_bytes(b"TEST_FIXTURE late change")
        terminal(reader)
    monkeypatch.setattr(audit._Reader, "terminal", mutate)
    with pytest.raises(audit.AuditError, match="REFERENCE_MISMATCH"):
        audit.audit_job(evidence.job)


@pytest.mark.parametrize("exists", [True, False])
def test_unsealed_or_absent_final_is_unknown_not_assumed_running(evidence, exists):
    path = evidence.job/"final-control.json"
    if exists:
        evidence.write(path, dict(schema_version=evidence.final["schema_version"], status="UNSEALED",
                                 created_at="2026-10-26T20:00:00+00:00"))
    else:
        path.unlink()
    result = audit.audit_job(evidence.job)
    assert result["workflow_status"] == "IN_PROGRESS_OR_UNKNOWN"
    assert result["archived_core_status"] == "UNKNOWN"


def test_operation_clocks_keep_sleep_discrepancy_and_user_delivery_unknown(evidence):
    result = audit.audit_operation(evidence.operation(), state_root=evidence.state,
                                  deadline="2026-10-26T21:00:30+00:00")
    timing = result["timing"]
    assert timing["utc_operation_seconds"] == 3660
    assert timing["operation_action_monotonic_seconds"] == 1810
    assert timing["supervisor_monotonic_seconds"] == 1800
    assert timing["workflow_sealed_by_deadline"] is True
    assert timing["user_received_by_deadline"] == "UNKNOWN"
    assert result["request_binding"] == "RECORDED_RECEIPT_REFERENCES_MATCH"


@pytest.mark.parametrize("phase,status", [("SUBMITTING", "IN_PROGRESS_OR_UNKNOWN"),
                                         ("FAILED_OR_UNKNOWN", "FAILED_OR_UNKNOWN")])
def test_unresolved_operation_never_guesses_nearby_successful_job(evidence, phase, status):
    result = audit.audit_operation(evidence.operation(phase), state_root=evidence.state)
    assert result["workflow_status"] == status
    assert "job" not in result
    assert result["request_binding"] == "NO_VALIDATED_JOB_RECEIPT"


def test_recovered_operation_without_state_root_uses_exact_physical_scope(evidence):
    identity = {key:value for key,value in evidence.identity.items() if key != "state_root"}
    identity.update(schema_version="CODEX_RECOVERED_PREMARKET_V1",
                    explicit_collection_profile="REAL_PREMARKET_RECOVERY_3H_V1")
    path = evidence.operation("FAILED_OR_UNKNOWN", changed_identity=identity)
    result = audit.audit_operation(path, state_root=evidence.state)
    assert result["workflow_status"] == "FAILED_OR_UNKNOWN"
    wrong = evidence.operation("FAILED_OR_UNKNOWN", changed_identity=identity,
                               changed_scope={**evidence.scope, "device": evidence.scope["device"]+1})
    with pytest.raises(audit.AuditError, match="PHYSICAL_SCOPE"):
        audit.audit_operation(wrong, state_root=evidence.state)


def test_wrong_state_scope_and_operation_filename_are_rejected(evidence):
    path = evidence.operation()
    other_state = evidence.root/"other_state"
    other_state.mkdir()
    with pytest.raises(audit.AuditError, match="PHYSICAL_SCOPE"):
        audit.audit_operation(path, state_root=other_state)
    renamed = path.with_name("wrong.json")
    path.rename(renamed)
    with pytest.raises(audit.AuditError, match="OPERATION_KEY"):
        audit.audit_operation(renamed, state_root=evidence.state)


def test_recorded_receipt_cannot_point_to_other_session(evidence):
    path = evidence.operation()
    record = json.loads(path.read_text())
    record["receipt"]["target_session"] = "2026-10-28"
    evidence.write(path, record)
    with pytest.raises(audit.AuditError, match="RECEIPT_SESSIONS"):
        audit.audit_operation(path, state_root=evidence.state)


def test_recorded_receipt_cannot_hide_final_failure(evidence):
    path = evidence.operation()
    evidence.final["status"] = "FAILED"
    final_ref = evidence.seal()
    record = json.loads(path.read_text())
    record["receipt"]["final"] = final_ref
    record["receipt"]["completion"] = evidence.final["completion"]
    evidence.write(path, record)
    with pytest.raises(audit.AuditError, match="RECEIPT_FINAL_CONFLICT"):
        audit.audit_operation(path, state_root=evidence.state)


@pytest.mark.parametrize("admitted,status", [(False, "BLOCKED_BEFORE_COLLECTION"), (True, "ADMITTED_NOT_EXECUTED")])
def test_saved_preflight_never_means_started_or_current(evidence, admitted, status):
    path = evidence.root/"plan.json"
    evidence.write(path, dict(schema_version="CODEX_PREMARKET_PREFLIGHT_V1", execution_admitted=admitted,
        planned_at="2026-10-26T18:00:00+00:00", reasons=[] if admitted else ["FACTOR_MISSING"], stages=[]))
    result = audit.audit_preflight(path)
    assert result["workflow_status"] == status
    assert result["request_claimed"] is result["worker_started"] is False
    assert result["plan_currentness"] == "SAVED_PLAN_NOT_RECOMPUTED"


def test_runtime_inventory_counts_duplicate_days_without_selecting_winner(evidence):
    evidence.operation("RECORDED")
    evidence.operation("FAILED_OR_UNKNOWN", changed_identity={**evidence.identity, "request_key": "TEST_FIXTURE_other"})
    result = audit.audit_runtime(evidence.runtime)
    assert result["operation_count"] == 2
    assert result["distinct_session_pairs"] == 1
    assert result["multiple_operations_same_session"] is True
    assert result["continuous_delivery_accepted"] is False
    assert result["user_delivered_count"] is None
    assert result["missing_trading_days"] is None


def test_runtime_detects_inventory_growth_during_audit(evidence, monkeypatch):
    evidence.operation()
    terminal = audit._Reader.terminal
    def changed(reader):
        terminal(reader)
        evidence.operation("SUBMITTING", changed_identity={**evidence.identity, "request_key": "TEST_FIXTURE_late"})
    monkeypatch.setattr(audit._Reader, "terminal", changed)
    with pytest.raises(audit.AuditError, match="DIRECTORY_CHANGED"):
        audit.audit_runtime(evidence.runtime)


def test_cli_returns_machine_readable_failure_without_provider_error_text(evidence, capsys):
    assert audit.main(["--job", str(evidence.root/"absent")]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "AUDIT_FAILED"
    assert value["reason"] == "AUDIT_INPUT_UNAVAILABLE_OR_INVALID"


def test_cli_and_audit_do_not_modify_inputs(evidence, capsys):
    before = {str(path):audit.original._reference(path) for path in evidence.root.rglob("*") if path.is_file()}
    assert audit.main(["--job", str(evidence.job)]) == 0
    assert json.loads(capsys.readouterr().out)["source_authority_restored"] is False
    after = {str(path):audit.original._reference(path) for path in evidence.root.rglob("*") if path.is_file()}
    assert before == after
