"""TEST_FIXTURE daily entry boundaries for one bounded same-session refresh."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from scripts import codex_daily_v2 as daily
from scripts import codex_premarket_refresh as refresh
from scripts import codex_premarket_continuation as continuation
from stockfish_core.yaogu import premarket_workflow_v1 as frozen
from tests.unit import test_codex_daily_entry_v2 as gateway_fixture


class DailyRefreshTests(unittest.TestCase):
    setUp = gateway_fixture.GatewayTests.setUp

    def supervise(self, config, *, require_premarket=False, **kwargs):
        code, final = gateway_fixture.GatewayTests.supervise(self, config, **kwargs)
        if require_premarket:
            path = daily.Path(final["completion"]["path"])
            value = json.loads(path.read_text())
            value["premarket_deadline"] = self.proposal["premarket_deadline"]
            path.write_text(json.dumps(value))
            final["completion"] = frozen._ref(path)
            final["premarket_deadline"] = value["premarket_deadline"]
            (path.parent / "final-control.json").write_text(json.dumps(final))
        return code, final

    def fixture(self, *, planning=False):
        configured = json.loads(self.config.read_text())
        configured["limits"] = {"max_seconds": 3600}
        self.config.write_text(json.dumps(configured))
        self.snapshot["configuration"] = daily.original._reference(self.config)
        self.original_config = frozen.load_config(str(self.config))
        limits = {**self.original_config["limits"], "max_requests": self.original_config["limits"]["max_requests"] - 72,
                  "max_seconds": 1800}
        self.worker._expected_gate = lambda _: None
        self.operation = self.root / "TEST_FIXTURE_original_failure.json"
        self.operation.write_text('{"phase":"FAILED_OR_UNKNOWN"}')
        self.proposal = {"status": "TEST_FIXTURE_ADMITTED", "remaining_limits": limits,
            "premarket_deadline": str(self.target) + "T09:30:00+08:00",
            "expected_sessions": [str(self.observation), str(self.target),
                                  str(datetime.now(timezone(timedelta(hours=8))).date())]}
        command = "premarket-refresh-plan" if planning else "premarket-refresh"
        argv = [command, "--operation", str(self.operation), "--expected-terminal-sha256", "a" * 64]
        if not planning:
            argv.append("--authorize-missing-refresh")
        self.args = daily.parser().parse_args(argv)
        self.authorization = {"schema_version": "TEST_FIXTURE_REFRESH_AUTHORIZATION", "claim": "TEST_FIXTURE_claim"}
        context = patch.object(refresh, "prepare", return_value=deepcopy(self.proposal))
        self.prepare_mock = context.start()
        self.addCleanup(context.stop)
        context = patch.object(refresh, "execute", side_effect=self.execute)
        self.execute_mock = context.start()
        self.addCleanup(context.stop)

    def execute(self, runtime, proposal, *, action, validate):
        self.assertIs(runtime, self.runtime)
        receipt = action(deepcopy(proposal["remaining_limits"]), deepcopy(self.authorization))
        self.assertEqual(receipt["refresh_authorization"], self.authorization)
        verification = validate(receipt, deepcopy(proposal))
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["parent_budget_scope"], "REFRESH_ACTION_AND_VALIDATION")
        return {"status": "RECORDED", "receipt": receipt, "validation": verification, "new_action_calls": 1}

    def test_refresh_reuses_scope_preserves_original_and_never_supplies_retry_context(self):
        self.fixture()
        original = (self.operation.read_bytes(), self.config.read_bytes())
        with patch.object(self.worker, "supervise", wraps=self.supervise) as supervise:
            code, result = daily.execute(self.runtime, self.args)
        self.assertEqual(code, 2)
        self.assertTrue(result["complete_daily_delivery"])
        self.assertEqual(self.calls, 1)
        self.assertNotIn("continuation_context", supervise.call_args.kwargs)
        self.assertIs(supervise.call_args.kwargs["require_premarket"], True)
        self.assertEqual(supervise.call_args.args[0]["limits"], self.proposal["remaining_limits"])
        self.assertEqual((self.operation.read_bytes(), self.config.read_bytes()), original)
        self.prepare_mock.assert_called_once_with(self.runtime, operation_path=self.operation,
            expected_terminal_sha256="a" * 64, allow_test_fixture=False)

    def test_plan_is_readonly_and_never_starts_coordinator_or_worker(self):
        self.fixture(planning=True)
        code, result = daily.execute(self.runtime, self.args)
        self.assertEqual((code, result), (0, self.proposal))
        self.execute_mock.assert_not_called()
        self.assertEqual(self.calls, 0)

    def test_explicit_collection_authorization_required_before_preparation(self):
        self.fixture()
        self.args.authorize_missing_refresh = False
        with self.assertRaisesRegex(ValueError, "REFRESH_EXPLICIT_MISSING_COLLECTION_AUTHORIZATION_REQUIRED"):
            daily.execute(self.runtime, self.args)
        self.prepare_mock.assert_not_called()
        self.execute_mock.assert_not_called()
        self.assertEqual(self.calls, 0)

    def test_remaining_budget_cannot_be_reset_by_worker(self):
        self.fixture()
        def reset(config, **kwargs):
            return self.supervise({**config, "limits": self.original_config["limits"]}, **kwargs)
        with patch.object(self.worker, "supervise", side_effect=reset):
            with self.assertRaisesRegex(ValueError, "DAILY_SHARED_EXECUTED_CONFIG_CHANGED") as caught:
                daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)
        self.assertIn("worker_final", caught.exception.refresh_failure)
        self.assertIn("parent_verification_metrics", caught.exception.refresh_failure)

    def test_missing_recovery_context_is_rejected_at_final_validation(self):
        self.fixture()
        self.runtime.recovery_context = {"TEST_FIXTURE": "original_recovery"}
        def drop(config, **kwargs):
            kwargs.pop("recovery_context", None)
            return self.supervise(config, **kwargs)
        with patch.object(self.worker, "supervise", side_effect=drop):
            with self.assertRaisesRegex(ValueError, "DAILY_SHARED_EXECUTED_CONFIG_CHANGED"):
                daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)

    def test_preparation_reads_are_bounded_before_claim(self):
        self.fixture()
        with patch.object(continuation, "WRAPPER_PREPARE_READ_BYTES", 1):
            with self.assertRaisesRegex(RuntimeError, "READ_LIMIT_EXCEEDED"):
                daily.execute(self.runtime, self.args)
        self.execute_mock.assert_not_called()
        self.assertEqual(self.calls, 0)

    def test_worker_must_seal_the_same_absolute_premarket_deadline(self):
        self.fixture()
        def drop(config, **kwargs):
            kwargs.pop("require_premarket")
            return self.supervise(config, **kwargs)
        with patch.object(self.worker, "supervise", side_effect=drop):
            with self.assertRaisesRegex(ValueError, "REFRESH_PREMARKET_DEADLINE_BINDING"):
                daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)

    def test_refresh_cli_accepts_no_replacement_key_dates_budget_or_retry_flags(self):
        for extra in (["--request-key", "TEST_FIXTURE_new"], ["--target-session", "2030-01-01"],
                      ["--max-seconds", "999999"], ["--authorize-one-source-retry"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                daily.parser().parse_args(["premarket-refresh", "--operation", "TEST_FIXTURE_old.json",
                    "--expected-terminal-sha256", "a" * 64, "--authorize-missing-refresh", *extra])


if __name__ == "__main__":
    unittest.main()
