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

    def request(self, captures, observed="2026-09-30", target="2026-10-01", cutoff="2026-10-01T15:00:00+00:00"):
        self.value = {"schema_version": core.INPUT_VERSION, "captures": captures,
                      "observation_date": str(observed), "target_session": str(target),
                      "cutoff": str(cutoff), "coverage_type": "TEST_FIXTURE"}
        self.save_request()

    def save_request(self):
        self.manifest.write_text(json.dumps(self.value))

    def execute(self, manifest=None):
        return core.execute_core(self.runtime, manifest or self.manifest, allow_test_fixture=True)


class CoordinatorTests(Base):
    def setUp(self):
        super().setUp()
        self.capture = self.root / "capture"
        self.capture.mkdir()
        refs = {}
        for name in ("result", "raw", "receipt"):
            path = self.capture / (name + ".json")
            path.write_text(json.dumps({"provider": "TEST_FIXTURE", "rows": [], "part": name}))
            refs[name] = core._reference(path)
        self.request([{**refs, "archive_root": str(self.capture)}])
        guard = patch.object(core, "_snapshot", return_value={"TEST_FIXTURE": "source-closure"})
        guard.start()
        self.addCleanup(guard.stop)
        self.calls = 0
        guard = patch.object(core, "_run_original", side_effect=self.original)
        guard.start()
        self.addCleanup(guard.stop)

    def original(self, request, job, limits):
        self.calls += 1
        folder = job / "core"
        folder.mkdir()
        summary = {"run_id": "TEST_FIXTURE-run", "input_hash": "a" * 64,
                   "canonical_content_hash": "b" * 64, "counts": {"UNKNOWN": 2},
                   "observation_date": request["observation_date"], "target_session": request["target_session"],
                   "decision_cutoff": request["cutoff"], "coverage_type": "TEST_FIXTURE",
                   "source_authority_restored": False, "premarket_completeness": "NOT_EXECUTED_CORE_ONLY"}
        artifacts = {}
        for name in sorted(core.FILES):
            path = folder / name
            path.write_bytes(json.dumps(summary).encode() if name == "manifest.json" else b"TEST_FIXTURE original\n")
            artifacts[name] = core._reference(path)
        refs = [ref for c in request["captures"] for ref in (c["result"], c["raw"], c["receipt"])]
        return summary, artifacts, refs, {"TEST_FIXTURE": True}

    def test_reloaded_runtime_reuses_original_eight_files_without_compute(self):
        first = self.execute()
        paths = sorted(p for p in self.report.rglob("*") if p.is_file())
        originals = {str(p): core._reference(p) for p in paths}
        self.runtime = workspace.load_runtime("TEST_FIXTURE-core", self.registry)
        second = self.execute()
        self.assertEqual(self.calls, 1)
        self.assertEqual(second["status"], "REUSED")
        self.assertEqual(second["native_r2_calls"], 0)
        self.assertEqual(first["summary"], second["summary"])
        self.assertFalse(second["validation"]["source_authority_restored"])
        self.assertEqual(originals, {str(p): core._reference(p) for p in self.report.rglob("*") if p.is_file()})

    def test_same_semantic_request_at_another_path_reuses(self):
        first = self.execute()
        another = self.root / "another-chat-request.json"
        another.write_text(json.dumps(self.value, indent=2))
        second = self.execute(another)
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertEqual(self.calls, 1)

    def test_changed_source_is_rejected_without_recompute(self):
        self.execute()
        (self.capture / "raw.json").write_text("changed original")
        with self.assertRaisesRegex(core.CoreError, "ORIGINAL_BYTES_OR_IDENTITY_CHANGED"):
            self.execute()
        self.assertEqual(self.calls, 1)

    def test_changed_artifact_is_rejected_without_recompute(self):
        result = self.execute()
        job = Path(result["receipt"]["evidence"]["path"]).parent
        (job / "core" / "all.csv").write_bytes(b"changed result")
        with self.assertRaisesRegex(core.CoreError, "ORIGINAL_BYTES_OR_IDENTITY_CHANGED"):
            self.execute()
        self.assertEqual(self.calls, 1)

    def test_receipt_summary_cannot_override_original_manifest(self):
        result = self.execute()
        record = self.runtime.directory / "operations" / (result["operation_id"] + ".json")
        value = workspace._read(record)
        value["receipt"]["summary"]["counts"] = {"FOCUS": 2}
        workspace._write(record, value, replace=True)
        with self.assertRaisesRegex(core.CoreError, "REUSE_EVIDENCE_CHANGED"):
            self.execute()

    def test_changed_cutoff_is_a_distinct_operation(self):
        self.execute()
        other = self.root / "new-request.json"
        value = deepcopy(self.value)
        value["cutoff"] = "2026-10-01T15:01:00+00:00"
        other.write_text(json.dumps(value))
        self.assertFalse(self.execute(other)["reused"])
        self.assertEqual(self.calls, 2)

    def test_fixture_requires_explicit_switch(self):
        with self.assertRaisesRegex(core.CoreError, "TEST_FIXTURE_EXPLICIT_AND_SEPARATE"):
            core.execute_core(self.runtime, self.manifest)
        self.assertEqual(self.calls, 0)

    def test_mixed_real_and_fixture_is_rejected_before_native(self):
        path = self.capture / "result.json"
        path.write_text(json.dumps({"provider": "baostock", "field_version": "REAL"}))
        self.value["captures"][0]["result"] = core._reference(path)
        self.save_request()
        with self.assertRaisesRegex(core.CoreError, "MIXED_TEST_FIXTURE_AND_REAL_FORBIDDEN"):
            self.execute()
        self.assertEqual(self.calls, 0)

    def test_missing_source_full_reference_rejected(self):
        del self.value["captures"][0]["raw"]["fp"]
        self.save_request()
        with self.assertRaisesRegex(core.CoreError, "FULL_PHYSICAL_REFERENCE_REQUIRED"):
            self.execute()

    def test_unknown_action_is_not_retried(self):
        with patch.object(core, "_run_original", side_effect=RuntimeError("TEST_FIXTURE crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.execute()
        with self.assertRaisesRegex(workspace.WorkspaceError, "OPERATION_STATE_UNKNOWN"):
            self.execute()
        self.assertEqual(self.calls, 0)

    def test_capacity_rejected_before_output(self):
        with patch.object(self.runtime, "preflight", return_value={"allowed": False}):
            with self.assertRaisesRegex(workspace.WorkspaceError, "CAPACITY_PREFLIGHT_BLOCKED"):
                self.execute()
        self.assertEqual(self.calls, 0)
        self.assertEqual(list(self.report.iterdir()), [])

    def test_fifo_and_symlink_refused_without_reading(self):
        path = self.root / "pipe"
        os.mkfifo(path)
        with self.assertRaisesRegex(core.CoreError, "REGULAR_FILE_REQUIRED"):
            core._reference(path)
        link = self.root / "alias"
        link.symlink_to(self.manifest)
        with self.assertRaisesRegex(core.CoreError, "REGULAR_FILE_REQUIRED"):
            core._reference(link)

    def test_lower_registered_budget_is_preserved(self):
        path = Path(self.runtime.profile["config"])
        path.write_text(json.dumps({"state_root": str(self.state),
                                   "limits": {"core_read_bytes": 4 * core.MIB, "max_seconds": 7}}))
        self.assertEqual(core._limits(self.runtime.profile)["core_read_bytes"], 4 * core.MIB)
        self.assertEqual(core._limits(self.runtime.profile)["max_seconds"], 7)

    def test_total_read_budget_rejects_before_native_or_output(self):
        path = Path(self.runtime.profile["config"])
        path.write_text(json.dumps({"state_root": str(self.state), "limits": {"core_read_bytes": 1}}))
        with self.assertRaisesRegex(core.CoreError, "CORE_TOTAL_READ_BUDGET_EXCEEDED"):
            self.execute()
        self.assertEqual(self.calls, 0)
        self.assertEqual(list(self.report.iterdir()), [])

    def test_total_deadline_includes_wrapper_authentication(self):
        path = Path(self.runtime.profile["config"])
        path.write_text(json.dumps({"state_root": str(self.state), "limits": {"max_seconds": 1}}))
        clock = itertools.chain([0.0], itertools.repeat(2.0))
        with patch.object(core.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(core.CoreError, "CORE_TOTAL_TIME_BUDGET_EXCEEDED"):
                self.execute()
        self.assertEqual(self.calls, 0)

    def test_outer_read_reserves_remaining_bytes_before_read(self):
        meter = core._InvocationBudget()
        meter.maximum = 1
        token = core._ACTIVE_BUDGET.set(meter)
        try:
            with self.assertRaisesRegex(core.CoreError, "CORE_TOTAL_READ_BUDGET_EXCEEDED"):
                core._reference(self.manifest)
            self.assertEqual(meter.read_bytes, 0)
        finally:
            core._ACTIVE_BUDGET.reset(token)


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


class NativeCoreTests(Base):
    def test_original_one_day_core_and_five_exports_then_exact_reuse(self):
        from tests.yaogu.test_premarket_native_core_v1 import captures
        from stockfish_core.yaogu import strategy_experiments
        items, observed, target, cutoff = captures(self.root / "TEST_FIXTURE-native", sessions=60, history=False)
        self.request(items, observed, target, cutoff.isoformat())
        original = strategy_experiments.run_short_horizon_strategy
        with patch.object(strategy_experiments, "run_short_horizon_strategy", wraps=original) as counted:
            first = self.execute()
            second = self.execute()
        self.assertEqual(counted.call_count, 1)
        self.assertEqual(first["summary"]["counts"], second["summary"]["counts"])
        self.assertEqual(first["summary"]["premarket_completeness"], "NOT_EXECUTED_CORE_ONLY")
        self.assertEqual(second["status"], "REUSED")
        self.assertEqual(first["summary"]["daily_v1_readiness"], "UNKNOWN_NOT_EXECUTED")
        job = Path(first["receipt"]["evidence"]["path"]).parent
        self.assertEqual(set(p.name for p in (job / "core").iterdir()), core.FILES)

    def test_original_two_stock_history_needs_no_filtered_market_or_master(self):
        from tests.yaogu.test_premarket_native_core_v1 import captures
        from tests.support.saved_r2_v3_fixture import write
        from stockfish_core.yaogu import providers
        items, observed, target, cutoff = captures(self.root / "TEST_FIXTURE-native", sessions=60, history=False)
        originals = [(item, json.loads(Path(item["result"]["path"]).read_bytes())) for item in items]
        selected = [item for item, result in originals if result["endpoint"] not in {"master_all", "market_daily"}]
        # This is an explicitly synthetic native-contract fixture. The real
        # runner does not manufacture histories or trim provider market rows.
        market = next(result for item, result in originals if result["endpoint"] == "market_daily")
        history = []
        for offset in (2, 1, 0):
            for row in market["rows"]:
                history.append({**row, "date": str(observed - timedelta(days=offset))})
        seen = cutoff - timedelta(hours=1)
        parameters = {"codes": ["sh.600001", "sz.000001"], "start_date": str(observed - timedelta(days=2)),
                      "end_date": str(observed)}
        archive = self.root / "TEST_FIXTURE-native" / "capture"
        payload = {"status": "COMPLETE", "pagination_complete": True, "field_version": "TEST_FIXTURE_HISTORY_V1",
                   "first_seen_at": seen.isoformat(), "rows": history, "coverage": {}, "gaps": []}
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return seen.astimezone(tz or timezone.utc)
        with patch.object(providers, "datetime", Clock):
            receipt = providers.archive_receipt(archive / "cache/raw", "baostock", "history", parameters, payload,
                status="COMPLETE", pagination_complete=True, field_version=payload["field_version"],
                first_seen_at=seen.isoformat(), source_url="https://TEST_FIXTURE.invalid/original")
        result = {**payload, "provider": "baostock", "endpoint": "history", "parameters": parameters,
                  "request_receipt_id": receipt["receipt_id"], "receipts": [receipt],
                  "started_at": seen.isoformat(), "completed_at": seen.isoformat()}
        item = {"archive_root": str(archive), "result": write(archive / "results/history-TEST_FIXTURE.json", result),
                "raw": {"path": str(archive / "cache/raw" / (receipt["content_hash"] + ".raw.json"))},
                "receipt": {"path": str(archive / "cache/raw" / (receipt["receipt_id"] + ".receipt.json"))}}
        for key in ("result", "raw", "receipt"):
            item[key] = core._reference(Path(item[key]["path"]))
        selected.append(item)
        self.request(selected, observed, target, cutoff.isoformat())
        result = self.execute()
        self.assertEqual(result["summary"]["counts"]["eligible"], 2)
        self.assertEqual(result["summary"]["counts"]["evaluated"], 2)
        self.assertEqual(result["summary"]["counts"]["complete_25_window"], 0)
        self.assertEqual(result["summary"]["premarket_completeness"], "NOT_EXECUTED_CORE_ONLY")


if __name__ == "__main__":
    unittest.main()
