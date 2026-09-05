from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import subprocess
import sys
import threading
import time

import pytest

from stockfish_core.jobs import JobService, JobConflict, TERMINAL_STATES
from stockfish_core.storage import SQLiteStore, StorageError
from stockfish_core.common import UnavailableError


def settled(service, job_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get(job_id)
        if job["state"] in TERMINAL_STATES:
            return job
        time.sleep(.01)
    raise AssertionError("job did not settle")


def test_progress_results_and_events_survive_reopen(tmp_path):
    store = SQLiteStore(tmp_path / "jobs.sqlite3")
    with JobService(store) as service:
        def work(context):
            assert not threading.current_thread().daemon
            context.update(.5, "validated", report={"id": 42})
            return {"answer": context.payload["number"] * 2}
        job = service.submit("test", {"number": 21}, work)
        result = settled(service, job["job_id"])
        assert result["state"] == "SUCCEEDED"
        assert result["result"] == {"answer": 42}
        assert result["data"]["report"] == {"id": 42}
        assert [item["state"] for item in service.events(job["job_id"])] == ["QUEUED", "RUNNING", "RUNNING", "SUCCEEDED"]
    with JobService(SQLiteStore(store.path)) as restarted:
        assert restarted.get(job["job_id"]) == result


@pytest.mark.parametrize("worker", [lambda ctx: (_ for _ in ()).throw(RuntimeError("sk-secret")),
                                  lambda ctx: {"success": False, "error": "bad"},
                                  lambda ctx: {"status": "unavailable"}, lambda ctx: {"value": float("nan")}])
def test_failure_never_becomes_success_and_does_not_leak_exception(tmp_path, worker):
    with JobService(SQLiteStore(tmp_path / "jobs.sqlite3")) as service:
        job = service.submit("fail", {}, worker)
        finished = settled(service, job["job_id"])
        assert finished["state"] == "FAILED"
        assert finished["result"] is None
        assert "sk-secret" not in str(finished)
        retry = service.retry(job["job_id"], lambda ctx: {"ok": True})
        assert settled(service, retry["job_id"])["state"] == "SUCCEEDED"
        assert retry["job_id"] != job["job_id"] and retry["attempt"] == 2
        assert service.get(job["job_id"])["state"] == "FAILED"


def test_cancel_wins_late_worker_result_and_shutdown_joins(tmp_path):
    began, release, exited = threading.Event(), threading.Event(), threading.Event()
    service = JobService(SQLiteStore(tmp_path / "jobs.sqlite3"))
    def work(context):
        began.set()
        release.wait(5)
        exited.set()
        return {"should_not_publish": True}
    job = service.submit("slow", {}, work)
    assert began.wait(2)
    assert service.cancel(job["job_id"])["state"] == "CANCEL_REQUESTED"
    release.set()
    service.shutdown()
    assert exited.is_set()
    result = service.get(job["job_id"])
    assert result["state"] == "CANCELLED" and result["result"] is None


def test_same_job_id_is_executed_once_under_race(tmp_path):
    counter, guard = [], threading.Lock()
    with JobService(SQLiteStore(tmp_path / "jobs.sqlite3")) as service:
        def work(context):
            with guard:
                counter.append(1)
            return {"done": True}
        service.register("single", work)
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _: service.submit("single", {}, job_id="same"), range(20)))
        assert settled(service, "same")["state"] == "SUCCEEDED"
        assert counter == [1]
        assert {row["job_id"] for row in rows} == {"same"}
        with pytest.raises(JobConflict):
            service.submit("single", {"different": True}, job_id="same")


def test_real_process_crash_recovers_running_and_payload(tmp_path):
    path = tmp_path / "crash.sqlite3"
    script = '''import os, sys, time
from stockfish_core.storage import SQLiteStore
from stockfish_core.jobs import JobService
s=JobService(SQLiteStore(sys.argv[1]))
def crash(ctx):
    ctx.update(.25, "checkpoint", saved={"number": 5})
    os._exit(17)
s.submit("crash", {"recover": True}, crash, job_id="interrupted")
time.sleep(3)
'''
    run = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, timeout=10)
    assert run.returncode == 17, run.stderr.decode(errors="replace")
    with JobService(SQLiteStore(path)) as recovered:
        job = recovered.get("interrupted")
        assert job["state"] == "INTERRUPTED"
        assert job["payload"] == {"recover": True}
        assert job["data"] == {"saved": {"number": 5}}
        assert job["result"] is None
        retried = recovered.retry(job["job_id"], lambda ctx: ctx.payload)
        assert settled(recovered, retried["job_id"])["result"] == {"recover": True}


def test_owner_lock_and_write_failure_fail_closed(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "jobs.sqlite3")
    service = JobService(store)
    with pytest.raises((JobConflict, StorageError)):
        JobService(SQLiteStore(store.path))
    began, finish = threading.Event(), threading.Event()
    def work(ctx):
        began.set()
        finish.wait(3)
        return {"ok": True}
    job = service.submit("disk", {}, work)
    assert began.wait(2)
    real_transaction = store.transaction
    @contextmanager
    def failed_transaction(*, write=True):
        if write:
            raise StorageError("simulated disk full")
        with real_transaction(write=write) as connection:
            yield connection
    monkeypatch.setattr(store, "transaction", failed_transaction)
    finish.set()
    deadline = time.monotonic() + 3
    while service.health()["active_workers"] and time.monotonic() < deadline:
        time.sleep(.01)
    assert service.get(job["job_id"])["state"] == "RUNNING"
    assert service.health()["status"] == "unavailable"
    service.shutdown()
    monkeypatch.setattr(store, "transaction", real_transaction)
    with JobService(store) as reopened:
        assert reopened.get(job["job_id"])["state"] == "INTERRUPTED"


@pytest.mark.parametrize("cancel_first", [True, False])
def test_atomic_domain_publication_and_cancel_have_one_winner(tmp_path, cancel_first):
    store = SQLiteStore(tmp_path / "atomic.sqlite3")
    began, publish = threading.Event(), threading.Event()
    with JobService(store) as service:
        def work(ctx):
            began.set()
            assert publish.wait(3)
            return ctx.complete({"document_id": "published"},
                                lambda connection: store._put_document(connection, "domain", "published", {"done": True}))
        job = service.submit("atomic", {}, work)
        assert began.wait(2)
        if cancel_first:
            service.cancel(job["job_id"])
        publish.set()
        finished = settled(service, job["job_id"])
        if cancel_first:
            assert finished["state"] == "CANCELLED"
            assert store.get_document("domain", "published") is None
        else:
            assert finished["state"] == "SUCCEEDED"
            assert service.cancel(job["job_id"])["state"] == "SUCCEEDED"
            assert store.get_document("domain", "published") == {"done": True}
            assert sum(event["state"] == "SUCCEEDED" for event in service.events(job["job_id"])) == 1


def test_atomic_publication_failure_rolls_back_domain_data(tmp_path):
    store = SQLiteStore(tmp_path / "atomic.sqlite3")
    with JobService(store) as service:
        def write(connection):
            store._put_document(connection, "domain", "partial", {"done": True})
            raise ValueError("domain invariant failed")
        job = service.submit("atomic", {}, lambda ctx: ctx.complete({"ok": True}, write))
        assert settled(service, job["job_id"])["state"] == "FAILED"
        assert store.get_document("domain", "partial") is None


@pytest.mark.parametrize("cancel_first", [True, False])
def test_atomic_failure_audit_is_durable_without_success(tmp_path, cancel_first):
    store = SQLiteStore(tmp_path / "failure.sqlite3")
    began, release = threading.Event(), threading.Event()
    with JobService(store) as service:
        def work(ctx):
            began.set()
            assert release.wait(3)
            return ctx.fail({"status": "unavailable", "diagnostic": "no raw price"},
                            {"code": "RISK_UNAVAILABLE", "component": "risk"},
                            lambda connection: store._put_document(connection, "domain", "failure", {"decision": "UNAVAILABLE"}))
        job = service.submit("failure", {}, work)
        assert began.wait(2)
        if cancel_first:
            service.cancel(job["job_id"])
        release.set()
        finished = settled(service, job["job_id"])
        if cancel_first:
            assert finished["state"] == "CANCELLED" and finished["result"] is None
            assert store.get_document("domain", "failure") is None
        else:
            assert finished["state"] == "FAILED"
            assert finished["error"]["code"] == "RISK_UNAVAILABLE"
            assert finished["result"]["diagnostic"] == "no raw price"
            assert store.get_document("domain", "failure")["decision"] == "UNAVAILABLE"
            assert service.cancel(job["job_id"])["state"] == "FAILED"


def test_dependency_error_has_safe_reason_and_no_secret(tmp_path):
    with JobService(SQLiteStore(tmp_path / "errors.sqlite3")) as service:
        def worker(ctx):
            raise UnavailableError("RAW_PRICE_UNAVAILABLE:sk-secret", "data")
        job = service.submit("unavailable", {}, worker)
        result = settled(service, job["job_id"])
        assert result["error"]["reason_codes"] == ["RAW_PRICE_UNAVAILABLE"]
        assert result["error"]["component"] == "data"
        assert "sk-secret" not in str(result)
