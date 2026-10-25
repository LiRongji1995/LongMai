"""TEST_FIXTURE: new shared entry dispatch and original write policy boundaries."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import codex_chat as chat
from scripts import codex_premarket as premarket
from scripts import codex_factor_incremental as factor


class OptimizationEntryTests(unittest.TestCase):
    def setUp(self):
        self.locked = False
        @contextmanager
        def exclusive():
            self.assertFalse(self.locked)
            self.locked = True
            try:
                yield
            finally:
                self.locked = False
        self.runtime = SimpleNamespace(profile={"backend": "premarket", "name": "TEST_FIXTURE"},
            registry_root=Path("/TEST_FIXTURE/registry"), check_binding=Mock(),
            exclusive=exclusive, preflight=Mock(return_value={"allowed": True}))
        self.prepared = dict(source=chat.ROOT, python=Path(sys.executable).absolute(),
            state=Path("/TEST_FIXTURE/state"), config=Path("/TEST_FIXTURE/config.json"))
        self.config = dict(capture={"offline": False, "request_timeout_seconds": 30},
                           limits={"cache_bytes": 64 * 1024**2, "max_seconds": 180})
        self.calls = []
        def operation(**kw):
            self.assertTrue(self.locked)
            self.calls.append(kw)
            self.assertEqual(kw["admit"](1024), {"allowed": True})
            return {"status": "TEST_FIXTURE"}
        for p in (patch.object(premarket, "_prepared", return_value=self.prepared),
                  patch.object(premarket, "_control", side_effect=lambda _: (self.config, {})),
                  patch.object(factor, "_read_control", return_value={"TEST_FIXTURE": True}),
                  patch.object(factor, "plan", side_effect=operation),
                  patch.object(factor, "reuse", side_effect=operation),
                  patch.object(factor, "sync", side_effect=operation)):
            p.start()
            self.addCleanup(p.stop)

    def args(self, op, *extra):
        return chat.parser().parse_args(["factor-" + op, "--baseline-record", "TEST_FIXTURE-base.json",
            "--calendar-record", "TEST_FIXTURE-calendar.json", "--target-session", "2026-10-23",
            "--profile", "FACTORS_DELTA_DIAGNOSTIC_V1" if op == "sync" else "FACTORS_READONLY_V1",
            *(["--purpose", "DELTA_FACTS_ONLY"] if op == "sync" else []), *extra])

    def test_readonly_calls_hold_lock_and_never_enable_network(self):
        for op in ("plan", "reuse"):
            with self.subTest(operation=op):
                chat.shared_factor_command(self.runtime, self.args(op))
                self.assertTrue(self.calls[-1]["offline"])
                self.assertIsNone(self.calls[-1]["purpose"])
                self.assertEqual(self.calls[-1]["limits"], factor.CAPS)
        self.runtime.preflight.assert_called_with(expected_growth_bytes=1024,
            destination=Path("/TEST_FIXTURE/state/cache"))

    def test_sync_defaults_offline_and_clamps_original_write_policy(self):
        chat.shared_factor_command(self.runtime, self.args("sync"))
        call = self.calls[-1]
        self.assertTrue(call["offline"])
        self.assertEqual(call["purpose"], "DELTA_FACTS_ONLY")
        self.assertEqual(call["limits"]["cache_bytes"], 64 * 1024**2)
        self.assertEqual(call["limits"]["request_timeout_seconds"], 30)
        self.assertEqual(call["limits"]["max_seconds"], 180)

    def test_network_requires_explicit_flag_and_registered_online_policy(self):
        chat.shared_factor_command(self.runtime, self.args("sync", "--execute-network"))
        self.assertFalse(self.calls[-1]["offline"])
        self.config["capture"]["offline"] = True
        with self.assertRaisesRegex(chat.ResearchError, "REGISTERED_OFFLINE_POLICY"):
            chat.shared_factor_command(self.runtime, self.args("sync", "--execute-network"))
        self.assertEqual(len(self.calls), 1)

    def test_bad_policy_never_reaches_factor(self):
        for value in (float("nan"), float("inf"), -1, True, None):
            with self.subTest(limit=value):
                self.config["limits"]["cache_bytes"] = value
                with self.assertRaisesRegex(chat.ResearchError, "CONFIGURED_LIMIT_INVALID"):
                    chat.shared_factor_command(self.runtime, self.args("sync"))
        self.config["limits"] = {}
        for value in (0.5, float("nan"), True):
            self.config["capture"]["request_timeout_seconds"] = value
            with self.assertRaisesRegex(chat.ResearchError, "CONFIGURED_LIMIT_INVALID"):
                chat.shared_factor_command(self.runtime, self.args("sync"))
        self.assertEqual(self.calls, [])

    def test_source_interpreter_purpose_must_match(self):
        args = self.args("sync")
        args.purpose = "SILENT_CORE_REPLACEMENT"
        with self.assertRaisesRegex(chat.ResearchError, "DIAGNOSTIC_PURPOSE_REQUIRED"):
            chat.shared_factor_command(self.runtime, args)
        self.prepared["source"] = Path("/TEST_FIXTURE/foreign")
        with self.assertRaisesRegex(chat.ResearchError, "REGISTERED_SOURCE_AND_INTERPRETER"):
            chat.shared_factor_command(self.runtime, self.args("reuse"))
        self.assertEqual(self.calls, [])

    def test_archive_and_compact_dispatch_keep_profile_and_global_argument_order(self):
        cases = [
            (["core-run-compact", "--manifest", "TEST_FIXTURE.json", "--profile", "FULL_CORE_COMPACT_V1"],
             "codex_compact_core.py", None),
            (["core-archive-status"], "codex_core_archive.py", "status"),
            (["core-archive-register", "--manifest", "TEST_FIXTURE.json", "--worker-dir", "TEST_FIXTURE-worker",
              "--supervisor-dir", "TEST_FIXTURE-parent", "--profile", "FULL_CORE_ARCHIVE_READONLY_V1"],
             "codex_core_archive.py", "register"),
            (["core-archive-reuse", "--archive-id", "a" * 64, "--profile", "FULL_CORE_ARCHIVE_READONLY_V1",
              "--target-session", "2026-10-26"], "codex_core_archive.py", "reuse")]
        with patch.object(premarket, "_run", return_value=(0, b'{"status":"TEST_FIXTURE"}', 25)) as run:
            for argv, script, operation in cases:
                with self.subTest(command=argv[0]):
                    chat.shared_local_command(self.runtime, chat.parser().parse_args(argv))
                    cmd = run.call_args.args[0]
                    self.assertEqual(cmd[3], str(chat.ROOT / "scripts" / script))
                    self.assertEqual(cmd[4:8], ["--runtime", "TEST_FIXTURE", "--registry-root", "/TEST_FIXTURE/registry"])
                    if operation:
                        self.assertEqual(cmd[8], operation)
                    self.assertNotIn("--allow-test-fixture", cmd)

    def test_new_profiles_are_required_and_no_fixture_override(self):
        for argv in (["core-run-compact", "--manifest", "TEST_FIXTURE.json"],
                     ["core-archive-reuse", "--archive-id", "a"*64],
                     ["core-archive-status", "--allow-test-fixture"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                chat.parser().parse_args(argv)
            self.assertEqual(failure.exception.code, 2)

    def test_factor_error_is_a_controlled_nonzero_json_response(self):
        with patch.object(chat, "load_runtime", return_value=self.runtime), \
             patch.object(factor, "reuse", side_effect=factor.FactorIncrementalError("TEST_FIXTURE_GATE")), \
             redirect_stdout(io.StringIO()) as output:
            args = ["factor-reuse", "--baseline-record", "TEST_FIXTURE.json", "--calendar-record", "TEST_FIXTURE.json",
                    "--target-session", "2026-10-23", "--profile", "FACTORS_READONLY_V1"]
            self.assertEqual(chat.main(args), 2)
        self.assertEqual(json.loads(output.getvalue())["reason"], "TEST_FIXTURE_GATE")
