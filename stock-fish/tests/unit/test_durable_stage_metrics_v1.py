"""TEST_FIXTURE: native interruption, bounded journal and genuine Budget wiring."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts import durable_stage_metrics_v1 as metrics


AUTHORITY = {"workspace_id": "TEST_FIXTURE_workspace", "business_id": "business_" + "b" * 64,
             "attempt_id": "TEST_FIXTURE_attempt"}


def journal(tmp_path, **kwargs):
    return metrics.StageJournal(tmp_path.resolve()/"TEST_FIXTURE_journal", AUTHORITY,
                                test_fixture=True, **kwargs)


def test_real_resources_reads_failures_and_publication_cost_are_retained(tmp_path):
    value = journal(tmp_path)
    counts = {"returned_read_bytes": {"capture": 0, "core": 0}}
    with value.stage("capture", counters=lambda: counts):
        counts["returned_read_bytes"]["capture"] += 17
        value.metrics_snapshot(counts)
    with pytest.raises(RuntimeError, match="original failure"):
        with value.stage("core", counters=lambda: counts):
            raise RuntimeError("original failure")
    with value.stage("process_cleanup", cleanup=True):
        pass
    receipt = value.finish("FAILED", counters=counts)
    report = metrics.audit_journal(value.root, expected_identity=AUTHORITY)
    assert report["status"] == "FAILED" and report["open_stages"] == []
    assert report["stages"][0]["returned_read_bytes"] == {"capture": 17, "core": 0}
    assert report["stages"][0]["process_peak_rss_bytes"] > 0
    assert report["stages"][0]["process_cpu_seconds"] >= 0
    assert report["stages"][-1]["event"] == "CLEANUP_END"
    assert receipt["publication_cost"]["written_bytes"] == sum(p.stat().st_size for p in value.root.iterdir())
    assert receipt["publication_cost"]["fsync_calls"] == 2*report["event_count"]
    assert receipt["publication_cost"]["wall_seconds"] > report["publication_cost_lower_bound"]["wall_seconds"] > 0
    assert report["last_publication_cost"].startswith("UNKNOWN")
    assert report["process_exit_status"] == "NOT_OBSERVED_BY_JOURNAL"
    with pytest.raises(ValueError, match="CLOSED"):
        value.metrics_snapshot(counts)


@pytest.mark.parametrize("kill_signal", [signal.SIGTERM, signal.SIGKILL])
def test_native_hard_interruption_preserves_started_stage_and_never_claims_complete(tmp_path, kill_signal):
    root = tmp_path.resolve()/"TEST_FIXTURE_native_child"
    script = """
import json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from scripts.durable_stage_metrics_v1 import StageJournal
j = StageJournal(pathlib.Path(sys.argv[2]), json.loads(sys.argv[3]), test_fixture=True)
with j.stage('capture'):
    print('READY', flush=True)
    time.sleep(10)
"""
    process = subprocess.Popen([sys.executable, "-I", "-B", "-c", script,
        str(metrics.ROOT), str(root), json.dumps(AUTHORITY)], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        import selectors
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(8), "native child failed to enter the persisted stage"
        assert process.stdout.readline().strip() == "READY"
        os.kill(process.pid, kill_signal)
        process.communicate(timeout=5)
        assert process.returncode == -kill_signal
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    result = metrics.audit_journal(root, expected_identity=AUTHORITY)
    assert result["status"] == "UNKNOWN"
    assert result["open_stages"] == ["stage-1"] and result["terminal_event_hash"] is None
    assert result["publication_cost_lower_bound"]["written_bytes"] > 0


def test_incomplete_tail_is_unknown_and_complete_event_tampering_is_rejected(tmp_path):
    value = journal(tmp_path)
    value.root.joinpath("000001.json").write_bytes(b'{"event":')
    result = metrics.audit_journal(value.root)
    assert result["status"] == "UNKNOWN" and result["partial_tail"] == "000001.json"
    value.root.joinpath("000001.json").unlink()
    value.finish("COMPLETE")
    path = value.root/"000001.json"
    data = json.loads(path.read_text()); data["details"]["status"] = "FAILED"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="CHAIN_INVALID"):
        metrics.audit_journal(value.root)


def test_sample_failure_is_persisted_and_original_failure_keeps_priority(tmp_path):
    native = metrics.native_sample
    fail = {"value": False}
    def sampler():
        if fail["value"]:
            raise PermissionError("TEST_FIXTURE")
        return native()
    value = journal(tmp_path, sampler=sampler)
    with pytest.raises(RuntimeError, match="business error"):
        with value.stage("core"):
            fail["value"] = True
            raise RuntimeError("business error")
    fail["value"] = False
    value.finish("FAILED")
    result = metrics.audit_journal(value.root)
    assert result["status"] == "FAILED" and len(result["sampling_errors"]) == 1
    assert result["stages"][0]["wall_seconds"] is None


def test_existing_directory_alias_wrong_business_and_counter_reset_refused(tmp_path):
    value = journal(tmp_path)
    with pytest.raises(FileExistsError):
        journal(tmp_path)
    bad = dict(AUTHORITY, business_id="business_" + "a"*64)
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        metrics.audit_journal(value.root, expected_identity=bad)
    value.metrics_snapshot({"returned_read_bytes": {"capture": 17}})
    with pytest.raises(ValueError, match="SAMPLE_FAILED"):
        value.metrics_snapshot({"returned_read_bytes": {"capture": 1}})
    value.finish("FAILED")
    assert metrics.audit_journal(value.root)["status"] == "FAILED"


def test_actual_budget_phase_adapter_and_cli(tmp_path, capsys):
    value = journal(tmp_path)
    class FixtureBudget:
        def __init__(self):
            self.count = 0
        @property
        def metrics(self):
            return {"returned_read_bytes": {"core": self.count}}
        @contextmanager
        def phase(self, name):
            self.count += 1
            yield self
    budget = value.wrap_budget(FixtureBudget)()
    with budget.phase("core"):
        budget.count += 20
    value.finish("COMPLETE")
    report = metrics.audit_journal(value.root)
    assert report["stages"][0]["returned_read_bytes"] == {"core": 21}
    assert metrics.main([str(value.root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "COMPLETE"


def test_limits_and_nonfixture_sampler_are_not_bypasses(tmp_path):
    with pytest.raises(ValueError, match="NATIVE_SAMPLER"):
        metrics.StageJournal(tmp_path.resolve()/"new", AUTHORITY, sampler=metrics.native_sample)
    value = journal(tmp_path, max_events=1)
    with pytest.raises(ValueError, match="EVENT_LIMIT"):
        value.finish("COMPLETE")
    assert metrics.audit_journal(value.root)["status"] == "UNKNOWN"


def test_real_partial_write_is_preserved_and_writer_cannot_retry(tmp_path, monkeypatch):
    value = journal(tmp_path)
    native_write = os.write
    calls = []
    def short_then_fail(fd, raw):
        calls.append(len(raw))
        if len(calls) == 1:
            return native_write(fd, raw[:len(raw)//2])
        raise OSError("TEST_FIXTURE_DISK_FAILURE")
    with monkeypatch.context() as scope:
        scope.setattr(metrics.os, "write", short_then_fail)
        with pytest.raises(OSError, match="DISK_FAILURE"):
            value.metrics_snapshot({"returned_read_bytes": {"capture": 2}})
    assert len(calls) == 2
    partial = (value.root/"000001.json").read_bytes()
    assert partial and not partial.endswith(b"\n")
    with pytest.raises(ValueError, match="BROKEN"):
        value.finish("UNKNOWN")
    assert (value.root/"000001.json").read_bytes() == partial
    report = metrics.audit_journal(value.root)
    assert report["status"] == "UNKNOWN" and report["partial_tail"] == "000001.json"
