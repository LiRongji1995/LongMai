"""TEST_FIXTURE: durable receipt locations and real process coordination."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_workspace as w


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-workspace-TEST_FIXTURE-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "TEST_FIXTURE-data"
        self.data.mkdir()
        self.state = self.data / "state"
        self.state.mkdir()
        self.reports = self.data / "reports"
        self.reports.mkdir()
        self.db = self.data / "TEST_FIXTURE.db"
        self.db.write_bytes(b"TEST_FIXTURE-only-not-a-real-database")
        self.registry = self.root / "registry"
        self.profile = {"schema_version": w.PROFILE_VERSION, "name": "TEST_FIXTURE",
                        "backend": "api", "base_url": "http://127.0.0.1:12345",
                        "state_root": str(self.state), "report_root": str(self.reports),
                        "db_path": str(self.db), "storage": {
                            "roots": [{"name": "data", "path": str(self.data), "role": "runtime"}],
                            "target_root": "data", "expected_growth_bytes": 1024,
                            "min_free_bytes": 0, "max_managed_bytes": 32 * 1024 * 1024}}

    def register(self, profile=None):
        profile = profile or self.profile
        path = self.root / (profile["name"] + ".json")
        path.write_text(json.dumps(profile))
        w.register_profile(path, self.registry)
        return w.load_runtime(profile["name"], self.registry)

    def replace_database(self):
        raw = self.db.read_bytes()
        self.db.rename(self.data / "TEST_FIXTURE-original.db")
        self.db.write_bytes(raw)

    def test_unconfigured_read_does_not_create_registry(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(w.load_runtime(registry_root=self.registry))
            self.assertEqual(w.runtime_status(registry_root=self.registry)["status"], "UNCONFIGURED")
        self.assertFalse(self.registry.exists())

    def test_registration_copies_no_data_and_is_idempotent(self):
        before = self.db.read_bytes()
        runtime = self.register()
        self.register()
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(list(self.reports.iterdir()), [])
        self.assertLess(sum(p.stat().st_size for p in self.registry.rglob("*") if p.is_file()), 10000)
        self.assertEqual(runtime.storage_report()["status"], "COMPLETE")

    def test_profile_and_default_are_immutable(self):
        self.register()
        path = self.root / "TEST_FIXTURE.json"
        w.register_profile(path, self.registry, make_default=True)
        self.assertEqual(w.load_runtime(registry_root=self.registry).profile["name"], "TEST_FIXTURE")
        changed = deepcopy(self.profile)
        changed["base_url"] = "http://127.0.0.1:12346"
        with self.assertRaisesRegex(w.WorkspaceError, "EXISTS_PRESERVED"):
            self.register(changed)

    def test_fresh_chat_reuses_and_always_validates(self):
        runtime = self.register()
        calls, checks = [], []
        def action():
            calls.append(1)
            return {"TEST_FIXTURE": True}
        def validate(receipt):
            checks.append(receipt)
            return {"verified": True, "actual_check": len(checks)}
        first = runtime.run_once({"request": "same"}, action, validate)
        second = w.load_runtime("TEST_FIXTURE", self.registry).run_once({"request": "same"}, action, validate)
        self.assertEqual((first["status"], second["status"]), ("RECORDED", "REUSED"))
        self.assertEqual((len(calls), len(checks)), (1, 2))
        self.assertEqual(second["validation"]["actual_check"], 2)

    def test_alias_and_hardlink_share_journal_and_locks(self):
        first = self.register()
        alias = deepcopy(self.profile)
        alias["name"] = "alias"
        second_state = self.data / "second-state"
        second_state.mkdir()
        alias["state_root"] = str(second_state)
        db_alias = self.data / "db-hardlink"
        os.link(self.db, db_alias)
        alias["db_path"] = str(db_alias)
        second = self.register(alias)
        self.assertEqual(first.directory, second.directory)
        self.assertTrue(set(first.resources) & set(second.resources))
        first.run_once({"request": "same"}, lambda: {"TEST_FIXTURE": True}, lambda r: True)
        result = second.run_once({"request": "same"}, lambda: self.fail("duplicate action"), lambda r: True)
        self.assertTrue(result["reused"])
        with first.exclusive(), self.assertRaisesRegex(w.WorkspaceError, "RUNTIME_BUSY"):
            with second.exclusive():
                self.fail("same physical DB not locked")

    def test_shared_resource_policy_cannot_split_reports_or_budget(self):
        self.register()
        changed = deepcopy(self.profile)
        changed["name"] = "alias"
        changed["storage"]["max_managed_bytes"] *= 2
        with self.assertRaisesRegex(w.WorkspaceError, "SHARED_RESOURCE_POLICY_CONFLICT"):
            self.register(changed)

    def test_legacy_shared_root_policy_survives_changed_metadata(self):
        runtime = self.register()
        # Legacy registries have only the original physical resource claims.
        for key in w._path_resources(self.profile):
            (self.registry / "resources" / (key + ".json")).unlink()
        saved_path = self.registry / "runtimes" / "TEST_FIXTURE.json"
        saved = w._read(saved_path)
        for item in saved["binding"].values():
            item["device"] += 100
            item["inode"] += 100
        w._write(saved_path, saved, replace=True)
        other_db = self.data / "TEST_FIXTURE-other.db"
        other_db.write_bytes(b"TEST_FIXTURE")
        changed = deepcopy(self.profile)
        changed.update(name="TEST_FIXTURE-other-ledger", db_path=str(other_db))
        changed["storage"]["max_managed_bytes"] *= 2
        with self.assertRaisesRegex(w.WorkspaceError, "SHARED_RESOURCE_POLICY_CONFLICT"):
            self.register(changed)
        self.assertFalse((self.registry / "runtimes" / (changed["name"] + ".json")).exists())

    def test_multiple_legacy_ledgers_at_one_location_are_not_silently_selected(self):
        runtime = self.register()
        saved = w._read(self.registry / "runtimes" / "TEST_FIXTURE.json")
        saved["profile"]["name"] = "TEST_FIXTURE-second-legacy-ledger"
        saved["binding"]["db_path"]["inode"] += 100
        w._write(self.registry / "runtimes" / (saved["profile"]["name"] + ".json"), saved)
        with self.assertRaisesRegex(w.WorkspaceError, "LEDGERS_REQUIRE_RECONCILIATION"):
            self.register({**self.profile, "name": "TEST_FIXTURE-new-alias"})
        self.assertEqual(w.load_runtime("TEST_FIXTURE", self.registry).directory, runtime.directory)

    def test_different_inputs_dates_and_strategies_do_not_reuse(self):
        runtime = self.register()
        identities = [{"input": "a", "date": "2026-10-23", "strategy": "frozen"},
                      {"input": "b", "date": "2026-10-23", "strategy": "frozen"},
                      {"input": "a", "date": "2026-10-26", "strategy": "frozen"},
                      {"input": "a", "date": "2026-10-23", "strategy": "another"}]
        for identity in identities:
            self.assertFalse(runtime.run_once(identity, lambda: {"TEST_FIXTURE": True}, lambda r: True)["reused"])

    def test_invalid_initial_validation_is_unknown_and_never_retried(self):
        runtime = self.register()
        with self.assertRaisesRegex(w.WorkspaceError, "VALIDATION_FAILED"):
            runtime.run_once({"request": "bad"}, lambda: {}, lambda r: False)
        with self.assertRaisesRegex(w.WorkspaceError, "RECONCILE_NO_RETRY"):
            runtime.run_once({"request": "bad"}, lambda: self.fail("unsafe retry"), lambda r: True)

    def test_reuse_false_or_missing_validator_result_denied(self):
        runtime = self.register()
        runtime.run_once({"request": "done"}, lambda: {}, lambda r: True)
        for invalid in (False, None, {}, {"verified": 1}):
            with self.assertRaisesRegex(w.WorkspaceError, "VALIDATION_FAILED"):
                runtime.run_once({"request": "done"}, lambda: self.fail("duplicate"), lambda r: invalid)

    def test_insufficient_capacity_blocks_before_action(self):
        self.profile["storage"]["max_managed_bytes"] = 1
        runtime = self.register()
        with self.assertRaisesRegex(w.WorkspaceError, "CAPACITY_PREFLIGHT_BLOCKED"):
            runtime.run_once({"request": "large"}, lambda: self.fail("must not run"), lambda r: True)
        self.assertFalse((runtime.directory / "operations").exists())

    def test_write_destination_selects_its_actual_root(self):
        self.profile["storage"]["roots"] = [
            {"name": "state", "path": str(self.state), "role": "runtime"},
            {"name": "output", "path": str(self.reports), "role": "output"}]
        self.db.rename(self.state / "TEST_FIXTURE.db")
        self.profile["db_path"] = str(self.state / "TEST_FIXTURE.db")
        self.profile["storage"]["target_root"] = "state"
        runtime = self.register()
        result = runtime.preflight(destination=self.reports / "future", expected_growth_bytes=3)
        self.assertEqual(result["target_root"], "output")
        self.assertEqual({x["target_root"] for x in runtime.preflight()["destination_checks"]}, {"state", "output"})
        with self.assertRaisesRegex(w.WorkspaceError, "OUTSIDE_STORAGE_POLICY"):
            runtime.preflight(destination=self.root / "outside")

    def test_protected_business_write_destination_denied(self):
        self.profile["storage"]["roots"][0]["role"] = "protected"
        with self.assertRaisesRegex(w.WorkspaceError, "DESTINATION_PROTECTED"):
            self.register()

    def test_replaced_database_keeps_original_ledger_and_registration(self):
        runtime = self.register()
        runtime.run_once({"request": "saved"}, lambda: {"TEST_FIXTURE": True}, lambda r: True)
        registration = self.registry / "runtimes" / "TEST_FIXTURE.json"
        original = registration.read_bytes()
        self.replace_database()
        self.assertNotEqual(self.db.stat().st_ino, runtime.binding["db_path"]["inode"])
        for directory in (self.state, self.reports):
            directory.rename(directory.with_name(directory.name + "-TEST_FIXTURE-old"))
            directory.mkdir()
        runtime.check_binding()
        loaded = self.register()
        self.assertEqual(registration.read_bytes(), original)
        self.assertEqual((loaded.scope, loaded.directory), (runtime.scope, runtime.directory))
        self.assertEqual(loaded.resources, runtime.resources)
        reused = loaded.run_once({"request": "saved"}, lambda: self.fail("duplicate"), lambda r: True)
        self.assertTrue(reused["reused"])
        self.assertFalse(w.runtime_status("TEST_FIXTURE", self.registry)["physical_binding_verified"])

    def test_changed_device_metadata_keeps_failed_request_and_alias_ledger(self):
        runtime = self.register()
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE failure"):
            runtime.run_once({"request": "failed"},
                lambda: (_ for _ in ()).throw(RuntimeError("TEST_FIXTURE failure")), lambda r: True)
        before = {p: p.read_bytes() for p in (runtime.directory / "operations").glob("*.json")}
        binding = w._binding
        def other_device(profile):
            value = binding(profile)
            for item in value.values():
                item["device"] += 100
                item["inode"] += 100
            return value
        with patch.object(w, "_binding", side_effect=other_device):
            loaded = w.load_runtime("TEST_FIXTURE", self.registry)
            alias = self.register({**self.profile, "name": "TEST_FIXTURE-after-remount"})
            self.assertEqual((loaded.scope, alias.scope), (runtime.scope, runtime.scope))
            self.assertEqual(alias.directory, runtime.directory)
            for selected in (loaded, alias):
                with self.assertRaisesRegex(w.WorkspaceError, "RECONCILE_NO_RETRY"):
                    selected.run_once({"request": "failed"}, lambda: self.fail("unsafe retry"), lambda r: True)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_replacement_then_new_name_reuses_and_coordinates_original(self):
        first = self.register()
        first.run_once({"request": "same"}, lambda: {"TEST_FIXTURE": True}, lambda r: True)
        self.replace_database()
        second = self.register({**self.profile, "name": "TEST_FIXTURE-new-name"})
        self.assertEqual(first.directory, second.directory)
        self.assertTrue(second.run_once({"request": "same"}, lambda: self.fail("duplicate"), lambda r: True)["reused"])
        with first.exclusive(), self.assertRaisesRegex(w.WorkspaceError, "RUNTIME_BUSY"):
            with second.exclusive():
                self.fail("overlapping same-location runtimes")

    def test_location_locks_join_different_historical_resource_keys(self):
        first = self.register()
        other_binding = deepcopy(first.binding)
        for value in other_binding.values():
            value["device"] += 100
            value["inode"] += 100
        second = w.Runtime(self.registry, self.profile, other_binding)
        self.assertNotEqual(first.directory, second.directory)
        self.assertFalse(set(w._resources(first.binding)) & set(w._resources(other_binding)))
        with first.exclusive(), self.assertRaisesRegex(w.WorkspaceError, "RUNTIME_BUSY"):
            with second.exclusive():
                self.fail("different legacy keys bypassed location lock")

    def test_python_same_content_replacement_keeps_invocation_and_scope(self):
        source = self.data / "TEST_FIXTURE-source"
        source.mkdir()
        executable = self.data / "TEST_FIXTURE-python"
        executable.write_text("#!/bin/sh\n# TEST_FIXTURE, never executed\n")
        executable.chmod(0o700)
        config = self.data / "TEST_FIXTURE-config.json"
        config.write_text("{}")
        profile = {key: deepcopy(value) for key, value in self.profile.items()
                   if key not in ("base_url", "db_path")}
        profile.update(backend="premarket", source_root=str(source), python=str(executable), config=str(config))
        runtime = self.register(profile)
        raw = executable.read_bytes()
        executable.rename(executable.with_name("TEST_FIXTURE-old-python"))
        executable.write_bytes(raw)
        executable.chmod(0o700)
        runtime.check_binding()
        loaded = self.register(profile)
        self.assertEqual(loaded.profile["python"], str(executable))
        self.assertEqual(loaded.scope, runtime.scope)
        executable.unlink()
        executable.mkdir()
        with self.assertRaisesRegex(w.WorkspaceError, "PYTHON_TARGET_TYPE_CHANGED"):
            runtime.check_binding()

    def test_invalid_types_and_redirected_state_remain_denied(self):
        runtime = self.register()
        self.db.rename(self.data / "original")
        self.db.mkdir()
        with self.assertRaisesRegex(w.WorkspaceError, "PATH_TYPE_CHANGED"):
            runtime.check_binding()
        self.db.rmdir()
        self.db.write_bytes(b"TEST_FIXTURE")
        moved = self.data / "moved-state"
        self.state.rename(moved)
        self.state.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(w.WorkspaceError, "PATH_ALIAS_CHANGED"):
            runtime.check_binding()

    def test_control_fifo_symlink_hardlink_and_duplicate_json_denied(self):
        path = self.root / "control"
        os.mkfifo(path)
        with self.assertRaisesRegex(w.WorkspaceError, "TYPE_OR_LINKS"):
            w._read(path)
        path.unlink()
        path.symlink_to(self.db)
        with self.assertRaisesRegex(w.WorkspaceError, "TYPE_OR_LINKS"):
            w._read(path)
        path.unlink()
        os.link(self.db, path)
        with self.assertRaisesRegex(w.WorkspaceError, "TYPE_OR_LINKS"):
            w._read(path)
        path.unlink()
        path.write_text('{"duplicate":1,"duplicate":2}')
        with self.assertRaisesRegex(w.WorkspaceError, "DUPLICATE_CONTROL_KEY"):
            w._read(path)

    def test_control_parent_alias_never_redirects_write(self):
        alias = self.root / "alias"
        alias.symlink_to(self.reports, target_is_directory=True)
        with self.assertRaises(OSError):
            w._write(alias / "forbidden.json", {})
        self.assertEqual(list(self.reports.iterdir()), [])

    def test_concurrent_destination_creation_is_preserved(self):
        path = self.root / "control.json"
        link = os.link
        def race(source, target, **kwargs):
            path.write_text("external TEST_FIXTURE")
            return link(source, target, **kwargs)
        with patch.object(w.os, "link", side_effect=race), self.assertRaisesRegex(w.WorkspaceError, "EXISTS_PRESERVED"):
            w._write(path, {"TEST_FIXTURE": "ours"})
        self.assertEqual(path.read_text(), "external TEST_FIXTURE")

    def child(self, body):
        code = ("import sys, os, time\nfrom pathlib import Path\n"
                "sys.path.insert(0, sys.argv[1])\nfrom scripts import codex_workspace as w\n"
                "runtime=w.load_runtime('TEST_FIXTURE', sys.argv[2])\n" + body)
        return subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(ROOT), str(self.registry)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_crashed_process_leaves_durable_unknown_no_second_action(self):
        runtime = self.register()
        child = self.child("runtime.run_once({'request':'crash'}, lambda: os._exit(17), lambda r: True)\n")
        out, err = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 17, (out, err))
        self.replace_database()
        runtime = self.register({**self.profile, "name": "TEST_FIXTURE-after-crash"})
        with self.assertRaisesRegex(w.WorkspaceError, "RECONCILE_NO_RETRY"):
            runtime.run_once({"request": "crash"}, lambda: self.fail("duplicate after crash"), lambda r: True)

    def test_real_process_concurrency_and_later_reuse(self):
        runtime = self.register()
        marker = self.registry / "active"
        release = self.registry / "release"
        child = self.child("def action():\n"
                           "    (runtime.registry_root/'active').write_text('TEST_FIXTURE')\n"
                           "    deadline=time.monotonic()+8\n"
                           "    while not (runtime.registry_root/'release').exists():\n"
                           "        if time.monotonic()>deadline: raise TimeoutError()\n"
                           "        time.sleep(.01)\n"
                           "    return {'TEST_FIXTURE':True}\n"
                           "runtime.run_once({'request':'concurrent'}, action, lambda r: True)\n")
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            with self.assertRaisesRegex(w.WorkspaceError, "RUNTIME_BUSY"):
                runtime.run_once({"request": "concurrent"}, lambda: self.fail("duplicate"), lambda r: True)
        finally:
            release.write_text("TEST_FIXTURE")
            out, err = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0, (out, err))
        result = runtime.run_once({"request": "concurrent"}, lambda: self.fail("duplicate"), lambda r: True)
        self.assertTrue(result["reused"])

    def test_first_lock_creation_races_never_overlap_business_sections(self):
        guard = threading.Lock()
        counts = {"active": 0, "peak": 0}
        for attempt in range(20):
            path = self.root / "creation-races" / (str(attempt) + ".lock")
            def enter(_):
                try:
                    with w._lock(path):
                        with guard:
                            counts["active"] += 1
                            counts["peak"] = max(counts["peak"], counts["active"])
                        time.sleep(.005)
                        with guard:
                            counts["active"] -= 1
                        return "ENTERED"
                except w.WorkspaceError as exc:
                    self.assertIn(str(exc), {"RUNTIME_BUSY_FOLLOW_EXISTING_OPERATION",
                                             "RUNTIME_LOCK_UNAVAILABLE_NO_ACTION"})
                    return "BLOCKED"
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(enter, range(3)))
            self.assertIn("ENTERED", results)
        self.assertEqual(counts["peak"], 1)


if __name__ == "__main__":
    unittest.main()
