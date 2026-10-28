"""TEST_FIXTURE: recovery preserves original controls and forbids epoch retries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_runtime_recovery_v1 as r
from scripts import codex_workspace as w

class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE-runtime-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        data = self.root / "data"
        state, reports = data / "state", data / "reports"
        state.mkdir(parents=True)
        reports.mkdir()
        self.venv = self.root / "venv"
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin/python").symlink_to(Path(sys.executable).resolve())
        (self.venv / "pyvenv.cfg").write_text("TEST_FIXTURE")
        self.registry = self.root / "registry"
        old_config = data / "old-config.json"
        old_config.write_text(json.dumps({"state_root": str(state), "limits": {"max_requests": 1}}))
        self.old_profile = {"schema_version": w.PROFILE_VERSION, "name": "TEST_FIXTURE-old",
            "backend": "premarket", "state_root": str(state), "report_root": str(reports),
            "source_root": str(ROOT), "python": str(self.venv / "bin/python"),
            "config": str(old_config), "storage": {"roots": [
                {"name": "data", "path": str(data), "role": "runtime"},
                {"name": "environment", "path": str(self.venv), "role": "runtime"}],
                "target_root": "data", "expected_growth_bytes": 1024,
                "min_free_bytes": 0, "max_managed_bytes": 64 * 1024**2}}
        config = self.root / "old-profile.json"
        config.write_text(json.dumps(self.old_profile))
        old_binding = w._binding(self.old_profile)
        for value in old_binding.values():
            value["device"] -= 100
        with patch.object(w, "_binding", return_value=old_binding):
            w.register_profile(config, self.registry, make_default=True)
        self.old = w.Runtime(self.registry, self.old_profile, old_binding)
        self.existing = {"backend": "premarket", "request_key": "TEST_FIXTURE-existing",
                         "observation_date": "2026-10-23", "target_session": "2026-10-26"}
        self.add_old(self.existing, "FAILED_OR_UNKNOWN")
        self.new_profile = deepcopy(self.old_profile)
        self.new_profile["name"] = "TEST_FIXTURE-new"
        new_state = state / "epochs/TEST_FIXTURE-epoch"
        new_state.mkdir(parents=True)
        new_config = data / "new-config.json"
        new_config.write_text(json.dumps({"state_root": str(new_state), "limits": {"max_requests": 100}}))
        self.new_profile.update(state_root=str(new_state), config=str(new_config))
        self.new_profile["storage"]["max_managed_bytes"] *= 2
        self.environment = r.verify_environment(self.venv / "bin/python", self.venv,
            self.root / "TEST_FIXTURE-acceptance", ROOT,
            _fixture_verifier=lambda result, report: {"TEST_FIXTURE": True}, allow_test_fixture=True)

    def add_old(self, identity, phase):
        key = w._digest({"scope": self.old.scope, "identity": identity})
        w._write(self.old.directory / "operations" / (key + ".json"), {
            "schema_version": w.VERSION, "scope": self.old.scope, "operation_id": key,
            "identity": identity, "phase": phase})

    def plan(self):
        return r.plan_recovery(self.registry, self.old_profile["name"], self.new_profile,
                               self.environment, allow_test_fixture=True)

    def recover(self):
        plan = self.plan()
        r.apply_recovery(plan, allow_test_fixture=True)
        return r.load_runtime(self.new_profile["name"], self.registry, allow_test_fixture=True)

    def fresh_identity(self):
        return {"backend": "premarket", "request_key": "TEST_FIXTURE-new-request",
                "observation_date": "2026-10-26", "target_session": "2026-10-27"}

    def test_plan_is_read_only_and_policy_changes_explicit(self):
        before = {str(p): r.reference(p) for p in self.registry.rglob("*.json")}
        plan = self.plan()
        self.assertEqual(before, {str(p): r.reference(p) for p in self.registry.rglob("*.json")})
        self.assertNotEqual(plan["policy_change"]["old_storage"], plan["policy_change"]["new_storage"])
        self.assertEqual(plan["policy_change"]["old_limits"], {"max_requests": 1})
        self.assertFalse(plan["source_authority_restored"])

    def test_apply_preserves_old_controls_and_allows_fresh_once(self):
        plan = self.plan()
        refs = [plan["history"]["registry"], plan["history"]["default"], plan["history"]["config"],
                *[x["reference"] for x in plan["history"]["operations"]]]
        r.apply_recovery(plan, allow_test_fixture=True)
        r._verify_refs(refs)
        runtime = r.load_runtime(self.new_profile["name"], self.registry, allow_test_fixture=True)
        calls = []
        first = runtime.run_once(self.fresh_identity(), lambda: calls.append(1) or {}, lambda x: True)
        second = runtime.run_once(self.fresh_identity(), lambda: self.fail("duplicate"), lambda x: True)
        self.assertEqual((first["status"], second["status"], calls), ("RECORDED", "REUSED", [1]))
        self.assertEqual(w._read(self.registry / "default.json")["name"], self.old_profile["name"])

    def test_legacy_loader_rejects_recovered_schema(self):
        self.recover()
        with self.assertRaisesRegex(w.WorkspaceError, "RUNTIME_REGISTRY_VERSION"):
            w.load_runtime(self.new_profile["name"], self.registry)

    def test_failed_and_completed_ancestors_never_retry(self):
        done = {**self.existing, "request_key": "TEST_FIXTURE-done", "observation_date": "2026-10-22", "target_session": "2026-10-23"}
        self.add_old(done, "RECORDED")
        runtime = self.recover()
        for old in (self.existing, done):
            for identity in (old, {**old, "source": "new-epoch"}, {**old, "request_key": "changed-key"}):
                with self.assertRaisesRegex(w.WorkspaceError, "ANCESTOR_REQUEST_RECONCILE_NO_RETRY"):
                    runtime.run_once(identity, lambda: self.fail("retry"), lambda x: True)
        self.assertFalse((runtime.directory / "operations").exists())

    def test_old_key_cannot_move_to_new_dates(self):
        runtime = self.recover()
        with self.assertRaisesRegex(w.WorkspaceError, "ANCESTOR_REQUEST_RECONCILE_NO_RETRY"):
            runtime.run_once({**self.fresh_identity(), "request_key": self.existing["request_key"]},
                             lambda: self.fail("retry"), lambda x: True)

    def test_history_added_after_plan_rejected(self):
        plan = self.plan()
        self.add_old(self.fresh_identity(), "SUBMITTING")
        with self.assertRaisesRegex(w.WorkspaceError, "ANCESTOR_HISTORY_CHANGED"):
            r.apply_recovery(plan, allow_test_fixture=True)
        self.assertFalse((self.registry / "runtimes" / (self.new_profile["name"] + ".json")).exists())

    def test_history_change_after_load_blocks_action(self):
        runtime = self.recover()
        self.add_old(self.fresh_identity(), "SUBMITTING")
        with self.assertRaisesRegex(w.WorkspaceError, "ANCESTOR_HISTORY_CHANGED"):
            runtime.run_once(self.fresh_identity(), lambda: self.fail("ran"), lambda x: True)

    def test_same_bytes_new_inode_rejected(self):
        plan = self.plan()
        path = Path(plan["history"]["operations"][0]["reference"]["path"])
        old = path.read_bytes()
        path.rename(path.with_suffix(".preserved"))
        path.write_bytes(old)
        with self.assertRaisesRegex(w.WorkspaceError, "ANCESTOR_HISTORY_CHANGED"):
            r.apply_recovery(plan, allow_test_fixture=True)

    def test_late_history_mutation_marks_current_request_unknown(self):
        runtime = self.recover()
        def action():
            self.add_old({"TEST_FIXTURE": "late"}, "SUBMITTING")
            return {}
        with self.assertRaisesRegex(w.WorkspaceError, "ANCESTOR_HISTORY_CHANGED"):
            runtime.run_once(self.fresh_identity(), action, lambda x: True)
        records = list((runtime.directory / "operations").glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(w._read(records[0])["phase"], "FAILED_OR_UNKNOWN")

    def test_old_resource_lock_blocks_new_runtime(self):
        runtime = self.recover()
        with w._lock(self.registry / "resources" / (self.old.resources[0] + ".lock")):
            with self.assertRaisesRegex(w.WorkspaceError, "RUNTIME_BUSY"):
                runtime.run_once(self.fresh_identity(), lambda: self.fail("unlocked"), lambda x: True)

    def test_current_resource_policy_conflict_rejected(self):
        plan = self.plan()
        resource = w._resources(plan["binding"])[0]
        w._write(self.registry / "resources" / (resource + ".json"), {"TEST_FIXTURE": "conflict"})
        with self.assertRaisesRegex(w.WorkspaceError, "SHARED_RESOURCE_POLICY_CONFLICT"):
            r.apply_recovery(plan, allow_test_fixture=True)
        self.assertFalse((self.registry / "recovery-epochs").exists())

    def test_fixture_cannot_be_used_as_production(self):
        plan = self.plan()
        with self.assertRaisesRegex(w.WorkspaceError, "TEST_FIXTURE_NOT_PRODUCTION"):
            r.apply_recovery(plan)
        self.recover()
        with self.assertRaisesRegex(w.WorkspaceError, "TEST_FIXTURE_NOT_PRODUCTION"):
            r.load_runtime(self.new_profile["name"], self.registry)

    def test_config_replaced_after_plan_rejected(self):
        plan = self.plan()
        path = Path(self.new_profile["config"])
        value = path.read_bytes()
        path.rename(path.with_suffix(".old"))
        path.write_bytes(value)
        with self.assertRaisesRegex(w.WorkspaceError, "HISTORY_OR_ACCEPTANCE_CHANGED"):
            r.apply_recovery(plan, allow_test_fixture=True)

    def test_existing_epoch_is_never_overwritten(self):
        plan = self.plan()
        r.apply_recovery(plan, allow_test_fixture=True)
        with self.assertRaisesRegex(w.WorkspaceError, "RUNTIME_EXISTS_PRESERVED"):
            r.apply_recovery(plan, allow_test_fixture=True)

    def test_verify_failure_is_saved_without_registry_change(self):
        before = {str(p): r.reference(p) for p in self.registry.rglob("*.json")}
        def fail(result, report):
            raise RuntimeError("TEST_FIXTURE failure")
        report = self.root / "TEST_FIXTURE-failed"
        with self.assertRaises(RuntimeError):
            r.verify_environment(self.venv / "bin/python", self.venv, report, ROOT,
                                 _fixture_verifier=fail, allow_test_fixture=True)
        self.assertEqual(w._read(report / "environment-reacceptance.json")["status"], "FAILED")
        self.assertEqual(before, {str(p): r.reference(p) for p in self.registry.rglob("*.json")})

    def test_each_python_child_gets_a_distinct_absent_bytecode_path(self):
        argv = [sys.executable, "-I", "-B", "-c", "import json,sys; print(json.dumps([sys.pycache_prefix,sys.dont_write_bytecode]))"]
        first, absent = r._fresh_python_command(argv, sys.executable, self.root)
        second, other = r._fresh_python_command(argv, sys.executable, self.root)
        self.assertNotEqual(absent, other)
        process = subprocess.run(first, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(process.stdout), [str(absent), True])
        self.assertFalse(absent.exists())
        with self.assertRaisesRegex(w.WorkspaceError, "FOREIGN_CHILD"):
            r._fresh_python_command(["/bin/echo", "bad"], sys.executable, self.root)

    def test_reference_rejects_symlink_and_hardlink(self):
        target = self.root / "real"
        target.write_text("TEST_FIXTURE")
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaisesRegex(w.WorkspaceError, "SINGLE_LINK"):
            r.reference(link)
        link.unlink()
        os.link(target, link)
        with self.assertRaisesRegex(w.WorkspaceError, "SINGLE_LINK"):
            r.reference(target)

if __name__ == "__main__":
    unittest.main()
