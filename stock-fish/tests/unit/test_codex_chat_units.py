"""TEST_FIXTURE shared-chat child dispatch; no process, network, or source writes."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_chat as chat
from scripts import codex_premarket as premarket


class SharedLocalCommandTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(
            profile={"backend": "premarket", "name": "TEST_FIXTURE-shared"},
            registry_root=Path("/TEST_FIXTURE/shared-registry"), check_binding=Mock())
        self.prepared = {"python": Path("/TEST_FIXTURE/runtime/venv/bin/python"),
                         "source": Path("/TEST_FIXTURE/registered-source")}
        self.prepare = patch.object(premarket, "_prepared", return_value=self.prepared)
        self.run = patch.object(premarket, "_run", return_value=(0, b'{"status":"TEST_FIXTURE"}', 25))
        self.prepare_mock = self.prepare.start()
        self.run_mock = self.run.start()
        self.addCleanup(self.prepare.stop)
        self.addCleanup(self.run.stop)

    def args(self, *items):
        return chat.parser().parse_args(["--runtime", "TEST_FIXTURE-shared", *items])

    def command(self):
        return self.run_mock.call_args.args[0]

    def test_core_uses_registered_venv_isolated_flags_and_source(self):
        args = self.args("core-run", "--manifest", "TEST_FIXTURE-input.json")
        result = chat.shared_local_command(self.runtime, args)
        self.runtime.check_binding.assert_called_once_with()
        self.prepare_mock.assert_called_once_with(self.runtime.profile)
        command = self.command()
        self.assertEqual(command[:4], ["/TEST_FIXTURE/runtime/venv/bin/python", "-I", "-B",
                                      "/TEST_FIXTURE/registered-source/scripts/codex_core.py"])
        self.assertEqual(command[4:], ["--runtime", "TEST_FIXTURE-shared", "--registry-root",
                                      "/TEST_FIXTURE/shared-registry", "--manifest",
                                      str(Path("TEST_FIXTURE-input.json").absolute())])
        self.assertEqual(result["command_exit_code"], 0)
        self.assertEqual(result["runtime"], "TEST_FIXTURE-shared")
        self.run_mock.assert_called_once_with(command, self.prepared)

    def test_incremental_plan_has_no_calendar_or_offline_switch(self):
        args = self.args("market-plan", "--codes", "sh.600000", "sz.000001", "--sessions", "2026-10-23")
        chat.shared_local_command(self.runtime, args)
        command = self.command()
        self.assertEqual(command[3], "/TEST_FIXTURE/registered-source/scripts/codex_incremental.py")
        self.assertEqual(command[4], "plan")
        self.assertIn("--registry-root", command)
        self.assertNotIn("--calendar-record", command)
        self.assertNotIn("--offline", command)
        self.assertEqual(command[-5:], ["--codes", "sh.600000", "sz.000001", "--sessions", "2026-10-23"])

    def test_sync_forwards_absolute_calendar_path_and_offline(self):
        args = self.args("market-sync", "--codes", "sh.600000", "--sessions", "2026-10-23",
                         "--calendar-record", "TEST_FIXTURE calendar.json", "--offline")
        chat.shared_local_command(self.runtime, args)
        command = self.command()
        self.assertEqual(command[4], "sync")
        index = command.index("--calendar-record")
        self.assertEqual(command[index + 1], str(Path("TEST_FIXTURE calendar.json").absolute()))
        self.assertEqual(command[-1], "--offline")

    def test_unregistered_runtime_never_prepares_or_runs(self):
        with self.assertRaisesRegex(chat.ResearchError, "RUNTIME_REGISTRATION_REQUIRED"):
            chat.shared_local_command(None, self.args("core-run", "--manifest", "TEST_FIXTURE.json"))
        self.prepare_mock.assert_not_called()
        self.run_mock.assert_not_called()

    def test_api_backend_cannot_run_local_units(self):
        self.runtime.profile["backend"] = "api"
        with self.assertRaisesRegex(chat.ResearchError, "PREMARKET_BACKEND_REQUIRED"):
            chat.shared_local_command(self.runtime, self.args("core-run", "--manifest", "TEST_FIXTURE.json"))
        self.run_mock.assert_not_called()

    def test_binding_failure_does_not_start_child(self):
        self.runtime.check_binding.side_effect = chat.WorkspaceError("TEST_FIXTURE_CHANGED_BINDING")
        with self.assertRaisesRegex(chat.WorkspaceError, "CHANGED_BINDING"):
            chat.shared_local_command(self.runtime, self.args("core-run", "--manifest", "TEST_FIXTURE.json"))
        self.prepare_mock.assert_not_called()
        self.run_mock.assert_not_called()

    def test_unknown_command_is_not_silently_treated_as_sync(self):
        args = self.args("market-plan", "--codes", "sh.600000", "--sessions", "2026-10-23")
        args.command = "TEST_FIXTURE-typo"
        with self.assertRaisesRegex(chat.ResearchError, "UNKNOWN_SHARED_COMMAND"):
            chat.shared_local_command(self.runtime, args)
        self.run_mock.assert_not_called()

    def test_fixture_flag_is_not_exposed_or_forwarded(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.args("core-run", "--manifest", "TEST_FIXTURE.json", "--allow-test-fixture")
        self.assertEqual(caught.exception.code, 2)
        args = self.args("core-run", "--manifest", "TEST_FIXTURE.json")
        args.allow_test_fixture = True
        chat.shared_local_command(self.runtime, args)
        self.assertNotIn("--allow-test-fixture", self.command())

    def test_main_propagates_nonzero_child_status(self):
        self.run_mock.return_value = (4, b'{"status":"BUDGET_EXCEEDED"}', 28)
        output = io.StringIO()
        with patch.object(chat, "load_runtime", return_value=self.runtime), redirect_stdout(output):
            code = chat.main(["--runtime", "TEST_FIXTURE-shared", "--registry-root", "/TEST_FIXTURE/shared-registry",
                              "core-run", "--manifest", "TEST_FIXTURE.json"])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(output.getvalue())["command_exit_code"], 4)

    def test_child_cannot_spoof_its_exit_code_in_json(self):
        self.run_mock.return_value = (3, b'{"command_exit_code":0,"runtime":"spoof"}', 47)
        result = chat.shared_local_command(self.runtime, self.args("core-run", "--manifest", "TEST_FIXTURE.json"))
        self.assertEqual(result["command_exit_code"], 3)
        self.assertEqual(result["runtime"], "TEST_FIXTURE-shared")

    def test_invalid_response_cannot_be_reported_as_success(self):
        for raw in (b"not JSON", b"[]", b"null"):
            with self.subTest(raw=raw):
                self.run_mock.return_value = (0, raw, len(raw))
                output = io.StringIO()
                with patch.object(chat, "load_runtime", return_value=self.runtime), redirect_stdout(output):
                    code = chat.main(["core-run", "--manifest", "TEST_FIXTURE.json"])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")

    def test_parser_requires_calendar_for_actual_sync(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.args("market-sync", "--codes", "sh.600000", "--sessions", "2026-10-23")
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
