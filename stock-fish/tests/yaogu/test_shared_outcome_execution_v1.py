"""TEST_FIXTURE: genuine native archives, bridge proofs, SQLite and JobService.

No archive verifier is mocked. Fault injection occurs after real plan
authentication or at the original transaction terminal. No vendor data/network.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import socket
import sqlite3
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import codex_shared_outcomes_v1 as shared
from stockfish_core.common import canonical_json
from stockfish_core.jobs import JobService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as original
from stockfish_core.yaogu import provider_normalization
from stockfish_core.yaogu.short_horizon_outcomes_v1 import NAMESPACES
from tests.support.short_horizon_archive_fixture import fixture_archive
from tests.support import short_horizon_archive_fixture as archive_fixture


@pytest.fixture(scope="module")
def genuine_seed(tmp_path_factory):
    root = tmp_path_factory.mktemp("TEST_FIXTURE_shared_seed")
    # This is a fixed simulated October chronology. Freeze the fixture creator
    # before writing/sealing it, rather than rewriting authenticated timestamps
    # or letting November's wall clock preempt the late-admission assertion.
    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 26, 9, 3, tzinfo=timezone.utc).astimezone(tz or timezone.utc)
    with patch.object(archive_fixture, "datetime", FixtureClock):
        archives = [fixture_archive(root / ("TEST_FIXTURE_archive_" + day), observation_date=day,
                    generated_at=day + "T09:02:00+00:00") for day in ("2026-10-23", "2026-10-26")]
    store = SQLiteStore(root / "TEST_FIXTURE_seed.sqlite")
    jobs = JobService(store, recover=False)
    service = object.__new__(CandidateService)
    service.store, service.jobs, service.root, service.allow_test_fixture = store, jobs, root, True
    try:
        certificates = [bridge.commit_bridge(service, bridge.authenticate_archive(
            archive["job"], expected_final_sha256=archive["final_sha256"], allow_test_fixture=True))
            for archive in archives]
    finally:
        jobs.shutdown(wait=True, cancel_pending=False)
    return SimpleNamespace(path=store.path, archives=archives, certificates=certificates)


@pytest.fixture
def fixture(tmp_path, genuine_seed, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("TEST_FIXTURE_NETWORK_FORBIDDEN"))
    path = tmp_path / "TEST_FIXTURE_shared.sqlite"
    # Both sides are explicitly isolated fixture DBs; SQLite backup closes all
    # connections before the new independent service takes its writer lock.
    with sqlite3.connect(genuine_seed.path) as source, sqlite3.connect(path) as destination:
        source.backup(destination)
    store = SQLiteStore(path)
    jobs = JobService(store, max_workers=1, recover=False)
    service = object.__new__(CandidateService)
    service.store, service.jobs, service.root, service.allow_test_fixture = store, jobs, tmp_path, True
    evaluated = datetime(2026, 10, 26, 9, 10, tzinfo=timezone.utc)
    cycle = original.ShortHorizonDailyClosure(service, clock=lambda: evaluated)
    run_id = genuine_seed.certificates[0]["run_id"]
    input_id = genuine_seed.certificates[1]["input_id"]
    try:
        yield SimpleNamespace(service=service, store=store, jobs=jobs, cycle=cycle,
                              evaluated=evaluated, run_id=run_id, input_id=input_id)
    finally:
        service.store = store
        jobs.shutdown(wait=True, cancel_pending=False)


def execute(fixture):
    return shared.execute_shared(fixture.cycle, [fixture.run_id], fixture.input_id,
                                 evaluated_at=fixture.evaluated)


def assert_no_products(fixture):
    assert fixture.store.list_documents(shared.LEDGERS) == []
    for namespace in NAMESPACES.values():
        assert fixture.store.documents_for_run(namespace, fixture.run_id) == []


def corrupt_fixture_document(fixture, namespace, document_id):
    assert fixture.store.path.name.startswith("TEST_FIXTURE_")
    value = fixture.store.get_document(namespace, document_id)
    value["TEST_FIXTURE_offline_corruption"] = True
    raw = canonical_json(value)
    with fixture.store.transaction() as connection:
        connection.execute("DROP TRIGGER documents_no_update")
        connection.execute("UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?",
                           (raw, hashlib.sha256(raw.encode()).hexdigest(), namespace, document_id))
        connection.execute("CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END")


def test_real_archives_use_one_session_original_checks_and_frozen_products(fixture, monkeypatch):
    original_bridge_global = original._bridge_module
    before = fixture.store.get_document_bytes("strategy_experiment_runs", fixture.run_id)
    phases, normalizers = [], []
    terminal, normalize = shared._Scope.terminal, provider_normalization.normalize_capture_with_report
    def observed_terminal(scope, *args):
        phases.append(scope.phase)
        return terminal(scope, *args)
    def observed_normalizer(*args, **kwargs):
        normalizers.append(1)
        return normalize(*args, **kwargs)
    monkeypatch.setattr(shared._Scope, "terminal", observed_terminal)
    monkeypatch.setattr(provider_normalization, "normalize_capture_with_report", observed_normalizer)
    result = execute(fixture)
    assert result["status"] == "SUCCEEDED", result
    assert phases == ["PLAN", "WORKER", "WORKER"]
    assert len(normalizers) == 2, "each distinct real archive normalizes once in this invocation"
    audit = result["session_audit"]
    assert audit["session_objects_created"] == 1 and audit["session_factory_calls"] == 2
    assert audit["actual_session_audit"]["limits"] == dict(
        total_read_bytes=8*1024**3, unique_bytes=4*1024**3, files=4096, seconds=300, rss_bytes=2*1024**3)
    assert audit["actual_session_audit"]["terminal_rereads"] >= 5
    assert result["ledger"]["policy"]["shared_execution"]["budget_scope"] == "PLAN_THROUGH_TRANSACTION_TERMINAL"
    assert fixture.store.get_document_bytes("strategy_experiment_runs", fixture.run_id) == before
    assert original._bridge_module is original_bridge_global
    assert original.ShortHorizonDailyClosure.plan.__globals__["_bridge_module"] is original_bridge_global
    assert original.ShortHorizonDailyClosure._worker.__globals__["_bridge_module"] is original_bridge_global
    assert [event["state"] for event in fixture.jobs.events(result["job"]["job_id"])] == ["QUEUED", "RUNNING", "SUCCEEDED"]


def test_numeric_products_equal_original_separate_session_worker(fixture):
    old_plan = fixture.cycle.plan([fixture.run_id], fixture.input_id, evaluated_at=fixture.evaluated)
    assert old_plan["can_submit"]
    fixture.jobs.create(fixture.cycle.job_kind, old_plan["request"], job_id=old_plan["job_id"])
    fixture.jobs._run(old_plan["job_id"], fixture.cycle._worker)
    assert fixture.jobs.get(old_plan["job_id"])["state"] == "SUCCEEDED"
    old = {kind: fixture.store.documents_for_run(namespace, fixture.run_id)
           for kind, namespace in NAMESPACES.items()}
    assert all(old.values()), "real archive fixture must exercise all three persisted products"
    assert any(item["metrics"].get("close_reference_change") is not None
               for doc in old["outcomes"] for item in doc["items"]), "fixture must contain numeric H1"
    result = execute(fixture)
    assert result["status"] == "SUCCEEDED", result
    new = {kind: fixture.store.documents_for_run(namespace, fixture.run_id)
           for kind, namespace in NAMESPACES.items()}
    assert old == new
    assert fixture.store.get_document(original.LEDGERS, old_plan["job_id"]) is not None
    assert result["job"]["kind"] != fixture.cycle.job_kind
    assert result["job"]["job_id"] != old_plan["job_id"]


def test_exact_completed_request_reuses_without_source_authentication(fixture, monkeypatch):
    first = execute(fixture)
    assert first["status"] == "SUCCEEDED"
    monkeypatch.setattr(bridge, "authenticate_archive", lambda *a, **k: pytest.fail("REUSE_MUST_NOT_REAUTHENTICATE"))
    monkeypatch.setattr(fixture.jobs, "_run", lambda *a, **k: pytest.fail("REUSE_MUST_NOT_RUN_WORKER"))
    again = execute(fixture)
    assert again["status"] == "REUSED_SAVED_LEDGER"
    assert again["source_reauthenticated_now"] is False and again["session_audit"] is None
    assert again["job"] == first["job"] and again["ledger"] == first["ledger"]


@pytest.mark.parametrize("fault", ["parent", "request", "store", "cancel", "elapsed_budget"])
def test_real_plan_boundary_failure_never_commits_products_or_retries(fixture, monkeypatch, fault):
    enter = shared._Scope.enter_worker
    def injected(scope, context):
        enter(scope, context)
        if fault == "parent":
            corrupt_fixture_document(fixture, "strategy_experiment_runs", fixture.run_id)
        elif fault == "request":
            changed = deepcopy(context.payload)
            changed["TEST_FIXTURE_late_request_change"] = True
            with fixture.store.transaction() as connection:
                connection.execute("UPDATE jobs SET payload=? WHERE job_id=?", (canonical_json(changed), context.job_id))
        elif fault == "store":
            fixture.service.store = SQLiteStore(fixture.store.path)
        elif fault == "cancel":
            fixture.jobs.cancel(context.job_id)
        else:
            scope.session.started -= 301
    monkeypatch.setattr(shared._Scope, "enter_worker", injected)
    result = execute(fixture)
    assert result["status"] == ("CANCELLED" if fault == "cancel" else "FAILED"), result
    fixture.service.store = fixture.store
    assert_no_products(fixture)
    with pytest.raises(shared.SharedExecutionError, match="NO_AUTOMATIC_RETRY|SAVED_JOB_REQUEST_CHANGED"):
        execute(fixture)


@pytest.mark.parametrize("fault", ["borrowed_run", "terminal", "adapter_source_identity", "adapter_source_fp", "keyboard_interrupt"])
def test_original_commit_terminal_detects_late_change_and_rolls_back_whole_batch(fixture, monkeypatch, fault):
    source = None
    if fault == "adapter_source_fp":
        # Alter only a private byte-identical fixture copy, never running source.
        source = fixture.service.root / "TEST_FIXTURE_adapter.py"
        source.write_bytes(Path(shared.__file__).read_bytes())
        monkeypatch.setattr(shared, "__file__", str(source))
    terminal = shared._Scope.terminal
    worker_calls = []
    def injected(scope, adapter, session):
        if scope.phase == "WORKER":
            worker_calls.append(1)
        if scope.phase == "WORKER" and len(worker_calls) == 2:
            if fault == "keyboard_interrupt":
                raise KeyboardInterrupt("TEST_FIXTURE_MAIN_THREAD_INTERRUPT")
            if fault == "adapter_source_identity":
                monkeypatch.setattr(shared, "source_identity", lambda: {**scope.adapter_identity, "sha256": "0" * 64})
                return terminal(scope, adapter, session)
            if fault == "adapter_source_fp":
                assert str(source.resolve()) in session.files
                replacement = source.with_suffix(".replacement")
                replacement.write_bytes(source.read_bytes())
                replacement.replace(source)
                assert shared.source_identity() == scope.adapter_identity, "same SHA must not hide changed physical identity"
                return terminal(scope, adapter, session)
            if fault == "borrowed_run":
                values = [value for value, _, typed in session._borrowed_values.values() if not typed]
                assert values, "real worker must borrow the mature frozen run"
                values[0]["TEST_FIXTURE_late_change"] = True
                return terminal(scope, adapter, session)
            terminal(scope, adapter, session)
            raise ValueError("TEST_FIXTURE_FAILURE_AFTER_REAL_TRANSACTION_TERMINAL")
        return terminal(scope, adapter, session)
    monkeypatch.setattr(shared._Scope, "terminal", injected)
    result = execute(fixture)
    assert result["status"] == ("CANCELLED" if fault == "keyboard_interrupt" else "FAILED"), result
    assert len(worker_calls) == 2
    assert_no_products(fixture)


def test_real_late_plan_rechecks_admission_before_any_request_or_job(fixture, monkeypatch):
    before = datetime(2026, 10, 27, 6, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc)
    calls = []
    def clock():
        calls.append(1)
        return before if len(calls) <= 2 else after
    cycle = original.LateCompletedSessionClosure(fixture.service, clock=clock)
    admission = shared._require_submission_admission
    def compare_original(closure, plan, mode):
        assert plan["can_submit"] and len(calls) == 2
        with pytest.raises(ValueError, match="LATE_BUSINESS_NOT_LATEST_COMPLETED_AT_ADMISSION"):
            closure._submit_plan(plan)
        return admission(closure, plan, mode)
    monkeypatch.setattr(shared, "_require_submission_admission", compare_original)
    with pytest.raises(ValueError, match="LATE_BUSINESS_NOT_LATEST_COMPLETED_AT_ADMISSION"):
        shared.execute_shared(cycle, [fixture.run_id], fixture.input_id, mode="late",
                              business_session="2026-10-26", evaluated_at=before)
    assert len(calls) == 4
    assert fixture.jobs.list() == [] and fixture.store.list_documents(shared.REQUESTS) == []
    assert_no_products(fixture)


def test_local_copy_admission_preserves_original_source_identity_precondition(monkeypatch):
    closure = object.__new__(original.LocalCopyLateCompletedSessionClosure)
    def changed():
        raise ValueError("TEST_FIXTURE_LOCAL_COPY_SOURCE_CHANGED")
    monkeypatch.setattr(closure, "_require_source_identity", changed)
    with pytest.raises(ValueError, match="LOCAL_COPY_SOURCE_CHANGED"):
        shared._require_submission_admission(closure, {"request": {}}, "late")


def test_thread_switch_is_rejected_by_genuine_scope(fixture, monkeypatch):
    original_run = fixture.jobs._run
    def switched(*args):
        thread = Thread(target=original_run, args=args)
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive()
    monkeypatch.setattr(fixture.jobs, "_run", switched)
    result = execute(fixture)
    assert result["status"] == "FAILED"
    assert_no_products(fixture)


def test_existing_queued_work_is_preserved_and_no_scope_created(fixture, monkeypatch):
    queued = fixture.jobs.create("TEST_FIXTURE_unrelated", {})
    monkeypatch.setattr(bridge.ArchiveVerificationSession, "__init__",
                        lambda *a, **k: pytest.fail("ACTIVE_JOB_MUST_BLOCK_BEFORE_SOURCE_SESSION"))
    with pytest.raises(shared.SharedExecutionError, match="ACTIVE_JOBS_NO_AUTOMATIC_RECOVERY"):
        execute(fixture)
    assert fixture.jobs.get(queued["job_id"])["state"] == "QUEUED"


def test_resource_profile_cannot_expand_original_300_seconds(fixture):
    fixture.cycle.verification_profile = "FULL_ARCHIVE_900S_V1"
    with pytest.raises(shared.SharedExecutionError, match="BUDGET_MUST_NOT_EXPAND"):
        execute(fixture)
    assert fixture.store.list_documents(shared.REQUESTS) == []


def test_scope_rejects_reentry_arguments_and_use_after_close(fixture):
    scope = shared._Scope(fixture.cycle, [fixture.run_id], fixture.input_id, "daily")
    try:
        with pytest.raises(shared.SharedExecutionError, match="SESSION_ARGUMENTS_CHANGED"):
            scope.factory(max_seconds=299)
        session = scope.factory()
        assert type(session) is bridge.ArchiveVerificationSession
        with pytest.raises(shared.SharedExecutionError, match="SINGLE_PLAN_AND_WORKER"):
            scope.factory()
        with pytest.raises(shared.SharedExecutionError, match="AUTHENTICATION_TASK_CHANGED"):
            scope.authenticate(None, ["TEST_FIXTURE_wrong_run"], fixture.input_id, session)
    finally:
        scope.close()
    with pytest.raises(shared.SharedExecutionError, match="LIFETIME_CHANGED"):
        session.check()


def test_cli_reuses_exact_saved_ledger_through_original_existing_service_context(fixture):
    first = execute(fixture)
    assert first["status"] == "SUCCEEDED"
    (fixture.service.root / "TEST_FIXTURE_ONLY").write_bytes(b"TEST_FIXTURE\n")
    fixture.jobs.shutdown(wait=True, cancel_pending=False)
    code, result = shared.execute_cli(["r2-daily-run", "--db", str(fixture.store.path),
        "--state", str(fixture.service.root), "--run-id", fixture.run_id, "--input-id", fixture.input_id,
        "--knowledge-cutoff", fixture.evaluated.isoformat(), "--allow-test-fixture"])
    assert code == 0, result
    assert result["status"] == "REUSED_SAVED_LEDGER" and result["source_reauthenticated_now"] is False
    assert result["ledger"] == first["ledger"]


def test_cli_rejects_retry_background_wait_and_budget_expansion_before_service(fixture, monkeypatch):
    from stockfish_core.yaogu import r2_closure_cli
    monkeypatch.setattr(r2_closure_cli, "service_context", lambda *a, **k: pytest.fail("MUST_REJECT_BEFORE_WRITER"))
    base = ["r2-daily-run", "--db", str(fixture.store.path), "--state", str(fixture.service.root),
            "--run-id", fixture.run_id, "--input-id", fixture.input_id]
    for arguments, reason in ((["r2-daily-retry"], "ONLY_EXPLICIT_RUN"),
                              (base + ["--wait-seconds", "900"], "BACKGROUND_WAIT"),
                              (base + ["--verification-profile", "FULL_ARCHIVE_900S_V1"], "BUDGET_MUST_NOT_EXPAND")):
        code, value = shared.execute_cli(arguments)
        assert code == 2 and reason in value["reason"]
    code, value = shared.execute_cli(["--help"])
    assert code == 0 and len(value["commands"]) == 6
