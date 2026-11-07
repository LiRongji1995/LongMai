"""TEST_FIXTURE daily/coordinator integration; providers remain isolated doubles."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from scripts import codex_daily_v2 as daily
from scripts import codex_premarket_continuation as continuation
from stockfish_core.yaogu import premarket_workflow_v1 as frozen
from tests.unit import test_codex_daily_entry_v2 as gateway_fixture


class DailyContinuationTests(unittest.TestCase):
    setUp = gateway_fixture.GatewayTests.setUp
    supervise = gateway_fixture.GatewayTests.supervise

    def prepare_continuation(self, command="premarket-continue"):
        configured = json.loads(self.config.read_text())
        configured["limits"] = dict(max_seconds=3600)
        self.config.write_text(json.dumps(configured))
        self.snapshot["configuration"] = daily.original._reference(self.config)
        config = frozen.load_config(str(self.config))
        limits = {**config["limits"], "max_requests": config["limits"]["max_requests"]-7}
        self.worker._expected_gate = lambda _: None
        self.operation = self.root / "original-failure.json"
        self.operation.write_text('{"phase":"FAILED_OR_UNKNOWN"}')
        self.proposal = dict(schema_version=continuation.VERSION, status=continuation.ADMITTED,
            operation_id="a"*64, scope=self.runtime.scope, scope_directory=str(self.runtime.directory),
            identity=dict(observation_date=str(self.observation), target_session=str(self.target),
                          state_root=str(self.state)), request_key="TEST_FIXTURE_original_key",
            source_job=str(self.state / "TEST_FIXTURE_failed_job"),
            original_operation_ref=daily.original._reference(self.operation),
            snapshot_proof_ref=daily.original._reference(self.config), parent_snapshot_proof={},
            remaining_limits=limits, original_limits=config["limits"],
            expected_sessions=[str(self.observation), str(self.target),
                               str(datetime.now(timezone(timedelta(hours=8))).date())],
            parent_provider_counts=dict(requests=7, sdk_reserved=9, http_reserved=256, sdk_actual=9, http_actual=5),
            source_retry=dict(request_spec=dict(business_session=str(datetime.now(timezone(timedelta(hours=8))).date())),
                             request_key="b"*64, prior_receipt_id="c"*64),
            preparation_arguments={}, preparation_metrics={}, validation_references=[daily.original._reference(self.operation)])
        self.proposal["proposal_id"] = continuation._proposal_digest(self.proposal)
        argv = [command, "--operation", str(self.operation), "--source-job", self.proposal["source_job"],
            "--snapshot-proof", str(self.config), "--expected-proof-sha256", "d"*64,
            "--source-key", "b"*64, "--source-receipt", "c"*64]
        if command == "premarket-continue":
            argv.append("--authorize-one-source-retry")
        self.args = daily.parser().parse_args(argv)
        for name, value in (("prepare", lambda *a, **kw: deepcopy(self.proposal)),
                            ("_identity", lambda *a, **kw: ({}, self.proposal["original_operation_ref"]))):
            context = patch.object(continuation, name, value)
            context.start()
            self.addCleanup(context.stop)
        return continuation._slot(self.runtime, self.proposal["operation_id"])

    def test_real_coordinator_and_daily_meters_complete_then_readonly_reuse(self):
        slot = self.prepare_continuation()
        original = self.operation.read_bytes(), self.config.read_bytes()
        code, result = daily.execute(self.runtime, self.args)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "RECORDED")
        self.assertTrue(result["validation"]["verified"])
        self.assertEqual(result["validation"]["parent_budget_scope"], "CONTINUATION_ACTION_AND_VALIDATION")
        actual = json.loads((Path(result["receipt"]["job"])/"configuration.json").read_text())
        self.assertEqual(actual["limits"], self.proposal["remaining_limits"])
        self.assertEqual(actual["continuation_context"], result["authorization"])
        terminal = (slot/"terminal.json").read_bytes()
        _, reused = daily.execute(self.runtime, self.args)
        self.assertEqual(reused["status"], "REUSED")
        self.assertEqual(reused["new_action_calls"], 0)
        self.assertEqual(self.calls, 1)
        self.assertEqual((slot/"terminal.json").read_bytes(), terminal)
        self.assertEqual((self.operation.read_bytes(), self.config.read_bytes()), original)

    def test_plan_creates_no_slot_and_never_starts_worker(self):
        slot = self.prepare_continuation("premarket-continue-plan")
        code, result = daily.execute(self.runtime, self.args)
        self.assertEqual((code, result["status"]), (0, continuation.ADMITTED))
        self.assertFalse(slot.exists())
        self.assertEqual(self.calls, 0)

    def test_authorization_flag_is_required_before_claim(self):
        slot = self.prepare_continuation()
        self.args.authorize_one_source_retry = False
        with self.assertRaisesRegex(ValueError, "EXPLICIT_ONE_SOURCE_AUTHORIZATION_REQUIRED"):
            daily.execute(self.runtime, self.args)
        self.assertFalse(slot.exists())
        self.assertEqual(self.calls, 0)

    def test_worker_cannot_reset_limits_or_omit_authorization(self):
        slot = self.prepare_continuation()
        actual_supervise = self.supervise
        def reset(config, **kwargs):
            config = {**config, "limits": self.proposal["original_limits"]}
            kwargs.pop("continuation_context")
            return actual_supervise(config, **kwargs)
        with patch.object(self.worker, "supervise", side_effect=reset):
            with self.assertRaisesRegex(ValueError, "EXECUTED_CONFIG_CHANGED"):
                daily.execute(self.runtime, self.args)
        terminal = json.loads((slot/"terminal.json").read_text())
        self.assertEqual(terminal["status"], "FAILED_OR_UNKNOWN")
        failure = terminal["continuation_failure"]
        self.assertEqual(failure["job"], str(self.state / "jobs" / "TEST_FIXTURE_1"))
        self.assertEqual(failure["worker_final"]["path"], str(Path(failure["job"])/"final-control.json"))
        self.assertGreater(failure["parent_verification_metrics"]["returned_read_bytes"]["capture"], 0)
        with self.assertRaisesRegex(ValueError, "CLAIM_EXISTS_NO_RETRY"):
            daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)

    def test_wrapper_snapshot_is_metered_before_any_claim(self):
        slot = self.prepare_continuation()
        with patch.object(continuation, "WRAPPER_PREPARE_READ_BYTES", 1):
            with self.assertRaisesRegex(RuntimeError, "READ_LIMIT_EXCEEDED"):
                daily.execute(self.runtime, self.args)
        self.assertFalse(slot.exists())
        self.assertEqual(self.calls, 0)

    def test_reconciliation_requires_bound_evidence_and_separate_execution_authorization(self):
        slot = self.prepare_continuation()
        self.args.factor_reconciliation = self.root / "TEST_FIXTURE_review.json"
        with self.assertRaisesRegex(ValueError, "RECONCILIATION_PATH_AND_SHA_REQUIRED"):
            daily.execute(self.runtime, self.args)
        self.args.expected_reconciliation_sha256 = "e"*64
        with self.assertRaisesRegex(ValueError, "EXPLICIT_FACTOR_RECONCILIATION_AUTHORIZATION_REQUIRED"):
            daily.execute(self.runtime, self.args)
        self.args.factor_reconciliation = None
        self.args.expected_reconciliation_sha256 = None
        self.args.authorize_existing_factor_reconciliation = True
        with self.assertRaisesRegex(ValueError, "RECONCILIATION_EVIDENCE_REQUIRED"):
            daily.execute(self.runtime, self.args)
        self.assertFalse(slot.exists())
        self.assertEqual(self.calls, 0)

    def test_reconciliation_plan_binds_exact_manifest_without_claim(self):
        slot = self.prepare_continuation("premarket-continue-plan")
        path = self.root / "TEST_FIXTURE_review.json"
        self.args.factor_reconciliation = path
        self.args.expected_reconciliation_sha256 = "e"*64
        with patch.object(continuation, "prepare", return_value=deepcopy(self.proposal)) as prepare:
            code, result = daily.execute(self.runtime, self.args)
        self.assertEqual(code, 0)
        self.assertEqual(prepare.call_args.kwargs["factor_reconciliation"], dict(path=str(path),sha256="e"*64))
        self.assertFalse(slot.exists())
        self.assertEqual(self.calls, 0)

    def test_reconciliation_execution_forwards_explicit_authorization_only(self):
        slot = self.prepare_continuation()
        self.args.factor_reconciliation = self.root / "TEST_FIXTURE_review.json"
        self.args.expected_reconciliation_sha256 = "e"*64
        self.args.authorize_existing_factor_reconciliation = True
        with patch.object(continuation, "execute", side_effect=ValueError("TEST_FIXTURE_STOP_BEFORE_CLAIM")) as execute:
            with self.assertRaisesRegex(ValueError, "TEST_FIXTURE_STOP_BEFORE_CLAIM"):
                daily.execute(self.runtime, self.args)
        self.assertIs(execute.call_args.kwargs["authorize_existing_factor_reconciliation"], True)
        self.assertFalse(slot.exists())
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
