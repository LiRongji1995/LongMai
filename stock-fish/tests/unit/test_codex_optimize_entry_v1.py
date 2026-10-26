"""TEST_FIXTURE-only routing and read-only posterior profiling contracts."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import codex_optimize as entry
from scripts import codex_outcome_profile_v1 as profile


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="optimize-entry-TEST_FIXTURE-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name, _ in entry.COMMANDS.values():
            (scripts / name).write_text("# TEST_FIXTURE dispatcher stub\n")
        self.runtime = SimpleNamespace(profile={"name": "TEST_FIXTURE", "backend": "premarket"},
            registry_root=self.root / "registry", check_binding=Mock())
        self.prepared = {"source": self.root, "python": self.root / "python"}
        self.prepare = patch.object(entry.premarket, "_prepared", return_value=self.prepared)
        self.run = patch.object(entry.premarket, "_run", return_value=(0, b'{"status":"TEST_FIXTURE"}', 0))
        self.prepare.start()
        self.run_mock = self.run.start()
        self.addCleanup(self.prepare.stop)
        self.addCleanup(self.run.stop)

    def command(self):
        return self.run_mock.call_args.args[0]

    def test_registered_interpreter_source_and_child_profile_are_explicit(self):
        entry.execute(self.runtime, "core-run", ["--manifest", "TEST_FIXTURE.json", "--profile", "FULL_CORE_COMPACT_VERIFY_V2"])
        self.assertEqual(self.command()[:4], [str(self.prepared["python"]), "-I", "-B", "-X"])
        self.assertTrue(self.command()[4].startswith("pycache_prefix="))
        self.assertFalse(Path(self.command()[4].split("=", 1)[1]).exists())
        self.assertEqual(self.command()[5], str(self.root / "scripts/codex_compact_core_v2.py"))
        self.assertEqual(self.command()[6:10], ["--runtime", "TEST_FIXTURE", "--registry-root", str(self.runtime.registry_root)])
        self.assertEqual(self.command()[-2:], ["--profile", "FULL_CORE_COMPACT_VERIFY_V2"])
        self.assertEqual(self.runtime.check_binding.call_count, 2)

    def test_network_not_added_implicitly(self):
        entry.execute(self.runtime, "capture", ["--request", "TEST_FIXTURE.json"])
        self.assertNotIn("--execute-network", self.command())
        self.assertIn("capture", self.command())

    def test_internal_worker_fixture_or_locator_arguments_rejected(self):
        for flag in ("--worker-result", "--allow-test-fixture", "--allow-test-fixture=true",
                     "--runtime", "--registry-root=/TEST_FIXTURE"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "OVERRIDE_FORBIDDEN"):
                entry.execute(self.runtime, "capture", [flag])
        self.run_mock.assert_not_called()

    def test_abbreviated_internal_options_cannot_bypass_child_parser_boundary(self):
        for flag in ("--worker-resul", "--worker-resul=/TEST_FIXTURE/result",
                     "--supervisor-contro", "--allow-test-fixtur", "--allow-test-fixtur=true",
                     "--runtim", "--registry-roo=/TEST_FIXTURE/registry"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "OVERRIDE_FORBIDDEN"):
                entry.execute(self.runtime, "core-run", [flag])
        self.run_mock.assert_not_called()

    def test_nonzero_exit_and_runtime_identity_cannot_be_spoofed_by_child(self):
        self.run_mock.return_value = (4, b'{"command_exit_code":0,"runtime":"spoof"}', 0)
        value = entry.execute(self.runtime, "storage-plan", [])
        self.assertEqual(value["command_exit_code"], 4)
        self.assertEqual(value["runtime"], "TEST_FIXTURE")

    def test_scheduler_and_outcome_children_keep_their_existing_db_arguments(self):
        for command, args in (("scheduler-status", ["--database", "/TEST_FIXTURE/database"]),
                              ("outcome-profile", ["r2-daily-status", "job", "--db", "/TEST_FIXTURE/database", "--state", "/TEST_FIXTURE/state"])):
            with self.subTest(command=command):
                entry.execute(self.runtime, command, args)
                self.assertNotIn("--runtime", self.command()[4:])
                self.assertEqual(self.command()[-len(args):], args)

    def test_missing_version_and_bad_response_fail_without_retry(self):
        (self.root / "scripts/codex_storage_growth_v1.py").unlink()
        with self.assertRaisesRegex(ValueError, "NOT_INSTALLED"):
            entry.execute(self.runtime, "storage-plan", [])
        self.run_mock.assert_not_called()
        for raw in (b"[]", b"null", b"not json"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "INVALID_RESPONSE"):
                self.run_mock.return_value = (0, raw, 0)
                entry.execute(self.runtime, "core-run", [])
        self.assertEqual(self.run_mock.call_count, 3)

    def test_main_returns_child_failure_and_defaults_no_other_work(self):
        self.run_mock.return_value = (3, b'{"status":"FAILED"}', 0)
        stream = io.StringIO()
        with patch.object(entry.workspace, "load_runtime", return_value=self.runtime), redirect_stdout(stream):
            code = entry.main(["--runtime", "TEST_FIXTURE", "core-run", "--profile", "FULL_CORE_COMPACT_VERIFY_V2"])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stream.getvalue())["command_exit_code"], 3)
        self.assertEqual(self.run_mock.call_count, 1)


class OutcomeProfileTests(unittest.TestCase):
    def call(self, argv):
        output = io.StringIO()
        with redirect_stdout(output):
            code = profile.main(argv)
        return code, json.loads(output.getvalue())

    def test_all_writing_commands_rejected_before_any_original_parse_or_execute(self):
        with patch.object(profile.original, "execute") as execute:
            for command in sorted(profile.original.COMMANDS - profile.READ_COMMANDS):
                with self.subTest(command=command):
                    code, result = self.call([command])
                    self.assertEqual(code, 2)
                    self.assertEqual(result["status"], "BLOCKED")
            execute.assert_not_called()

    def test_original_read_command_result_and_exit_code_are_preserved(self):
        with patch.object(profile.original, "execute", return_value=(4, {"status": "TEST_FIXTURE_PENDING"})) as execute:
            code, result = self.call(["r2-daily-plan", "--db", "/TEST_FIXTURE/db", "--state", "/TEST_FIXTURE/state",
                                      "--run-id", "TEST_FIXTURE-run", "--input-id", "TEST_FIXTURE-input"])
        self.assertEqual(code, 4)
        self.assertEqual(result["original_result"]["status"], "TEST_FIXTURE_PENDING")
        self.assertIsNone(execute.call_args.args[0].verification_profile)
        self.assertFalse(result["worker_started"])
        self.assertFalse(result["performance_acceptance"])
        self.assertEqual(result["diagnostics"]["status"], "RETURNED")

    def test_failure_preserves_diagnostic_and_sanitizes_exception_message(self):
        with patch.object(profile.original, "execute", side_effect=ValueError("sensitive arbitrary error")):
            code, result = self.call(["r2-daily-status", "TEST_FIXTURE-job", "--db", "/TEST_FIXTURE/db", "--state", "/TEST_FIXTURE/state"])
        self.assertEqual(code, 3)
        self.assertEqual(result["original_result"]["reason"], "LOCAL_R2_CLOSURE_FAILED")
        self.assertEqual(result["diagnostics"]["status"], "FAILED")

    def test_keyboard_interrupt_stays_130(self):
        with patch.object(profile.original, "execute", side_effect=KeyboardInterrupt):
            code, result = self.call(["r2-daily-status", "TEST_FIXTURE-job", "--db", "/TEST_FIXTURE/db", "--state", "/TEST_FIXTURE/state"])
        self.assertEqual(code, 130)
        self.assertEqual(result["original_result"]["status"], "INTERRUPTED")


if __name__ == "__main__":
    unittest.main()
