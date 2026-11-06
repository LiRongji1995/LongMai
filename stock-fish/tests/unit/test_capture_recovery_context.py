"""TEST_FIXTURE scope locators are read-only and reach the existing capture reader."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
from unittest.mock import patch

import pytest

from tests.unit.test_codex_core import Base
from scripts import codex_premarket_shared_v1 as shared
from scripts import codex_workspace as workspace
from stockfish_core.file_integrity import verify_content_reference
from stockfish_core.yaogu import premarket_workflow_v1 as workflow


class RecoveryContextTests(Base):
    def completion(self, operation="a" * 64, *, directory=None):
        path = (directory or self.runtime.directory) / "explicit-failure-recoveries" / operation / "completion.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"TEST_FIXTURE": True, "operation_id": operation}))
        return path

    def originals(self):
        return {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_absent_recoveries_do_not_create_a_scope_or_change_registration(self):
        before = self.originals()
        self.assertIsNone(self.runtime.capture_recovery_context())
        self.assertEqual(before, self.originals())

    def test_only_existing_completions_in_exact_scope_are_content_bound(self):
        completion = self.completion()
        self.completion(directory=self.runtime.directory.parent / ("f" * 64))
        incomplete = completion.parent.parent / ("b" * 64)
        incomplete.mkdir()
        (incomplete / "claim.json").write_text('{"TEST_FIXTURE":"unfinished"}')
        before = self.originals()
        context = self.runtime.capture_recovery_context()
        self.assertEqual(set(context), {"scope", "scope_directory", "completions"})
        self.assertEqual(context["scope"], self.runtime.scope)
        self.assertEqual(context["scope_directory"], str(self.runtime.directory))
        self.assertEqual(len(context["completions"]), 1)
        reference = context["completions"][0]
        self.assertEqual(reference["path"], str(completion))
        self.assertEqual(reference["sha256"], hashlib.sha256(completion.read_bytes()).hexdigest())
        self.assertEqual(reference["bytes"], completion.stat().st_size)
        self.assertTrue(verify_content_reference(reference))
        self.assertEqual(before, self.originals())
        context["scope"]["backend"] = "TEST_FIXTURE-other"
        self.assertEqual(self.runtime.scope["backend"], "premarket")
        completion.write_text('{"TEST_FIXTURE":"changed"}')
        with self.assertRaises(ValueError):
            verify_content_reference(reference)

    def test_completion_and_operation_aliases_are_refused(self):
        completion = self.completion()
        outside = self.root / "TEST_FIXTURE-other-completion.json"
        outside.write_bytes(completion.read_bytes())
        completion.unlink()
        completion.symlink_to(outside)
        with self.assertRaisesRegex(workspace.WorkspaceError, "CONTROL_FILE_TYPE_OR_LINKS"):
            self.runtime.capture_recovery_context()
        completion.unlink()
        completion.parent.rmdir()
        completion.parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.runtime.capture_recovery_context()

    def test_recovery_root_alias_and_invalid_operation_id_are_refused(self):
        completion = self.completion("not-an-operation-id")
        with self.assertRaisesRegex(workspace.WorkspaceError, "CAPTURE_RECOVERY_OPERATION_ID_REQUIRED"):
            self.runtime.capture_recovery_context()
        completion.unlink()
        completion.parent.rmdir()
        root = completion.parent.parent
        root.rmdir()
        root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(workspace.WorkspaceError, "CAPTURE_RECOVERY_SCOPE_ALIAS_FORBIDDEN"):
            self.runtime.capture_recovery_context()

    def test_completed_locator_count_is_bounded(self):
        for number in range(65):
            self.completion(format(number, "064x"))
        with self.assertRaisesRegex(workspace.WorkspaceError, "CAPTURE_RECOVERY_COMPLETION_COUNT_BOUND"):
            self.runtime.capture_recovery_context()


def test_preflight_passes_exact_read_only_scope_context_to_source_cache():
    # Reuse source fixtures without inheriting and rerunning their complete suite.
    from tests.unit.test_codex_premarket_preflight_v1 import PreflightTests
    from stockfish_core.yaogu import premarket_capture_cache_v1 as capture
    fixture = PreflightTests(methodName="runTest")
    fixture.setUp()
    try:
        (fixture.runtime.directory / "explicit-failure-recoveries").mkdir(parents=True)
        expected = fixture.runtime.capture_recovery_context()
        before = fixture.refs()
        seen = []
        original = capture.CaptureCache.__init__

        def initialize(self, *args, **kwargs):
            seen.append(deepcopy(kwargs.get("recovery_context")))
            return original(self, *args, **kwargs)

        with patch.object(capture.CaptureCache, "__init__", initialize):
            result = fixture.plan()
        assert seen == [expected]
        assert result["network_calls"] == 0
        assert result["strategy_calls"] == 0
        assert fixture.refs() == before
    finally:
        fixture.doCleanups()


def test_worker_passes_context_without_changing_capture_policy(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_OR_DATABASE_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    config = workflow.load_config(overrides={"state_root": str(tmp_path / "TEST_FIXTURE-state"),
        "capture": {"offline": True, "allow_test_fixture": True}})
    context = {"scope": {"TEST_FIXTURE": True}, "scope_directory": str(tmp_path), "completions": []}
    config["recovery_context"] = context
    before = deepcopy(config)
    seen = []

    class Cache:
        def __init__(self, root, budget, config, *, recovery_context=None):
            seen.append((deepcopy(config), deepcopy(recovery_context)))

    def collect(*args, **kwargs):
        raise ValueError("TEST_FIXTURE_STOP_BEFORE_SOURCE_COLLECTION")

    job = tmp_path / "TEST_FIXTURE-job"
    job.mkdir()
    result = shared.execute_workflow_shared(config, job, adapters={"cache": Cache, "collect": collect})
    assert seen == [(config["capture"], context)]
    assert config == before
    assert result["status"] == "FAILED"
    assert result["error"]["reason"] == "TEST_FIXTURE_STOP_BEFORE_SOURCE_COLLECTION"
    assert result["phases"]["core"] == "NOT_RUN"


def test_supervisor_context_is_per_job_and_registered_configuration_stays_strict(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_OR_DATABASE_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    state = tmp_path / "TEST_FIXTURE-state"
    config = workflow.load_config(overrides={"state_root": str(state),
        "capture": {"offline": True, "allow_test_fixture": True}, "limits": {"max_seconds": 30}})
    before = deepcopy(config)
    context = {"scope": {"TEST_FIXTURE": True}, "scope_directory": str(tmp_path), "completions": []}
    script = "\n".join([
        "import json,pathlib",
        "job=next(pathlib.Path(" + repr(str(state / "jobs")) + ").iterdir())",
        "completion=dict(schema_version=" + repr(shared.VERSION) + ",status='FAILED',",
        " error=dict(type='ValueError',reason='TEST_FIXTURE_STOP_WITHOUT_PROVIDER'),core_verified=False)",
        "(job/'worker-completion.json').write_text(json.dumps(completion))",
        "raise SystemExit(3)",
    ])
    code, result = shared.supervise(config, progress=False, recovery_context=context,
        _fixture_worker_command=[sys.executable, "-I", "-B", "-c", script])
    assert code == 3 and result["status"] == "FAILED"
    assert result["error"]["reason"] == "TEST_FIXTURE_STOP_WITHOUT_PROVIDER"
    assert config == before
    actual = json.loads((Path(result["job"]) / "configuration.json").read_text())
    assert actual == dict(before, recovery_context=context)
    assert actual["limits"] == before["limits"]
    with pytest.raises(ValueError, match="UNSUPPORTED_PREMARKET_CONFIG"):
        workflow.load_config(str(Path(result["job"]) / "configuration.json"))
    with pytest.raises(ValueError, match="UNSUPPORTED_PREMARKET_CONFIG"):
        shared.supervise(actual, progress=False)
    with pytest.raises(ValueError, match="SHARED_PREMARKET_RECOVERY_CONTEXT_FORMAT"):
        shared.supervise(config, progress=False, recovery_context=dict(context, retry=True))
