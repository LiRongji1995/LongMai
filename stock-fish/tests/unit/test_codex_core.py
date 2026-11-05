"""TEST_FIXTURE shared coordination and original native-core integration.

CoordinatorTests use only stdlib; NativeCoreTests require the project runtime.
All files belong to temporary directories; no network or real business DB.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import itertools
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_core as core
from scripts import codex_workspace as workspace


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="stockfish-core-TEST_FIXTURE-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.report = self.root / "report"
        self.state.mkdir()
        self.report.mkdir()
        self.registry = self.root / "registry"
        config = self.root / "config.json"
        config.write_text(json.dumps({"state_root": str(self.state)}))
        profile = {"schema_version": workspace.PROFILE_VERSION, "name": "TEST_FIXTURE-core",
                   "backend": "premarket", "state_root": str(self.state), "report_root": str(self.report),
                   "source_root": str(ROOT), "python": sys.executable, "config": str(config),
                   "storage": {"roots": [{"name": "runtime", "path": str(self.root), "role": "runtime"}],
                               "target_root": "runtime", "expected_growth_bytes": 1024,
                               "min_free_bytes": 0, "max_managed_bytes": 512 * core.MIB}}
        path = self.root / "profile.json"
        path.write_text(json.dumps(profile))
        workspace.register_profile(path, self.registry)
        self.runtime = workspace.load_runtime("TEST_FIXTURE-core", self.registry)
        self.manifest = self.root / "request.json"
        self.guards = [patch.object(socket.socket, "connect", side_effect=AssertionError("TEST_FIXTURE_NETWORK")),
                       patch.object(sqlite3, "connect", side_effect=AssertionError("TEST_FIXTURE_DB"))]
        for guard in self.guards:
            guard.start()
            self.addCleanup(guard.stop)



class RuntimeIdentityTests(Base):
    def test_same_base_python_at_other_invocation_path_is_rejected(self):
        other = self.root / "other-python"
        other.symlink_to(sys.executable)
        profile = deepcopy(self.runtime.profile)
        profile["python"] = str(other)
        with self.assertRaisesRegex(core.CoreError, "REGISTERED_INTERPRETER_REQUIRED"):
            core._snapshot(profile)

    def test_different_registered_source_root_is_rejected(self):
        other = self.root / "other-source"
        (other / "stockfish_core").mkdir(parents=True)
        profile = deepcopy(self.runtime.profile)
        profile["source_root"] = str(other)
        with self.assertRaisesRegex(core.CoreError, "REGISTERED_SOURCE_MUST_MATCH_EXECUTING_MODULE"):
            core._snapshot(profile)


class ContentReaderTests(Base):
    def test_fifo_and_symlink_refused_without_reading(self):
        fifo = self.root / "TEST_FIXTURE_fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(core.CoreError, "REGULAR_FILE_REQUIRED"):
            core._reference(fifo)
        target = self.root / "TEST_FIXTURE_regular"
        target.write_bytes(b"TEST_FIXTURE")
        alias = self.root / "TEST_FIXTURE_alias"
        alias.symlink_to(target)
        with self.assertRaisesRegex(core.CoreError, "REGULAR_FILE_REQUIRED"):
            core._reference(alias)

    def test_read_limit_rejects_before_payload_read(self):
        path = self.root / "TEST_FIXTURE_input.json"
        path.write_text('{"TEST_FIXTURE":true}')
        with self.assertRaisesRegex(core.CoreError, "SOURCE_FILE_LIMIT"):
            core._control(path, maximum=1)
        value, reference = core._control(path)
        self.assertEqual(value, {"TEST_FIXTURE": True})
        self.assertEqual(reference["bytes"], path.stat().st_size)

    def test_snapshot_binds_configuration_and_strategy_content(self):
        before = core._snapshot(self.runtime.profile)
        config = Path(self.runtime.profile["config"])
        value = json.loads(config.read_text())
        value["limits"] = {"max_seconds": 1}
        config.write_text(json.dumps(value))
        self.assertNotEqual(before["configuration"]["sha256"], core._snapshot(self.runtime.profile)["configuration"]["sha256"])
