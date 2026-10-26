"""TEST_FIXTURE registered first-run dispatch; no actual network or shared state."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest
from unittest.mock import Mock, patch

from scripts import codex_chat as chat
from scripts import codex_premarket as premarket


class FirstRunEntryTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(profile={"backend": "premarket", "name": "TEST_FIXTURE"},
            registry_root=Path("/TEST_FIXTURE/registry"), check_binding=Mock())
        self.prepared = dict(source=chat.ROOT, python=Path(sys.executable).absolute())

    def dispatch(self, arguments, response=None, code=0):
        args = chat.parser().parse_args(arguments)
        with patch.object(premarket, "_prepared", return_value=self.prepared), \
             patch.object(premarket, "_run", return_value=(code, json.dumps(response or {"status": "TEST_FIXTURE"}), b"")) as run:
            value = chat.shared_first_run_command(self.runtime, args)
        return value, run.call_args.args[0]

    def test_each_operation_uses_registered_interpreter_and_exact_profile(self):
        cases = [
            (["core-first-run", "--manifest", "TEST_FIXTURE.json", "--profile", "FIRST_CORE_CANONICAL_128_V1"], "codex_first_core.py"),
            (["core-sources-plan", "--profile", "CORE_SOURCES_FIRST_V1", "--identity-policy", "NATIVE_CORE_UNKNOWN_PRESERVED_V1"], "codex_core_sources.py"),
            (["core-sources-build", "--profile", "CORE_SOURCES_FIRST_V1", "--identity-policy", "NATIVE_CORE_UNKNOWN_PRESERVED_V1"], "codex_core_sources.py"),
            (["latest-plan", "--profile", "LATEST_MARKET_SESSION_V1", "--mode", "market-session", "--calendar-record", "TEST_FIXTURE.json"], "codex_latest_capture.py"),
            (["latest-sync", "--profile", "LATEST_SCOPED_HISTORY_V1", "--mode", "scoped-history", "--calendar-record", "TEST_FIXTURE.json", "--codes", "sh.600000", "--sessions", "2026-10-26"], "codex_latest_capture.py"),
        ]
        for arguments, script in cases:
            with self.subTest(command=arguments[0]):
                value, command = self.dispatch(arguments)
                self.assertEqual(command[:4], [str(self.prepared["python"]), "-I", "-B", str(chat.ROOT / "scripts" / script)])
                self.assertEqual(command[command.index("--runtime")+1], "TEST_FIXTURE")
                self.assertEqual(command[command.index("--registry-root")+1], "/TEST_FIXTURE/registry")
                self.assertNotIn("--execute-network", command)
                self.assertEqual(value["command_exit_code"], 0)

    def test_network_first_id_and_historical_clock_are_only_explicit(self):
        _, command = self.dispatch(["latest-sync", "--profile", "LATEST_MARKET_SESSION_V1", "--mode", "market-session", "--calendar-record", "TEST_FIXTURE.json", "--execute-network"])
        self.assertIn("--execute-network", command)
        _, command = self.dispatch(["core-first-run", "--manifest", "TEST_FIXTURE.json", "--profile", "FIRST_CORE_REFERENCE_V1", "--force-first", "TEST_FIXTURE-ab"])
        self.assertEqual(command[-2:], ["--force-first", "TEST_FIXTURE-ab"])
        _, command = self.dispatch(["core-sources-plan", "--profile", "CORE_SOURCES_FIRST_V1", "--identity-policy", "NATIVE_CORE_UNKNOWN_PRESERVED_V1", "--as-of", "2026-10-25T19:48:10+00:00"])
        self.assertEqual(command[-2:], ["--as-of", "2026-10-25T19:48:10+00:00"])

    def test_child_failure_is_not_reported_as_success(self):
        value, _ = self.dispatch(["core-first-run", "--manifest", "TEST_FIXTURE.json", "--profile", "FIRST_CORE_REFERENCE_V1"], {"status": "BLOCKED", "reason": "TEST_FIXTURE"}, 3)
        self.assertEqual((value["status"], value["command_exit_code"]), ("BLOCKED", 3))

    def test_missing_registration_and_wrong_backend_fail_before_subprocess(self):
        args = chat.parser().parse_args(["latest-plan", "--profile", "LATEST_MARKET_SESSION_V1", "--mode", "market-session", "--calendar-record", "TEST_FIXTURE.json"])
        with patch.object(premarket, "_run") as run:
            for runtime in (None, SimpleNamespace(profile={"backend": "api"})):
                with self.assertRaisesRegex(chat.ResearchError, "REGISTERED_PREMARKET"):
                    chat.shared_first_run_command(runtime, args)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
