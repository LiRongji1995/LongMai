"""Isolated TEST_FIXTURE continuations; no real source or network requests."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone, time as wall_time
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import codex_premarket_continuation as continuation
from scripts import codex_workspace as workspace
from stockfish_core.yaogu import premarket_capture_cache_v1 as captures
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from stockfish_core.yaogu.providers import archive_receipt


class FixtureExecutor:
    def __call__(self, endpoint, params, output, **options):
        started = datetime.now(timezone.utc).isoformat()
        failed = endpoint == "master_szse"
        status = "FETCH_FAILED" if failed else "COMPLETE"
        if endpoint == "calendar":
            first = datetime.fromisoformat(params["start_date"]).date()
            last = datetime.fromisoformat(params["end_date"]).date()
            rows = [dict(calendar_date=str(first + timedelta(days=n)),
                         is_trading_day="1" if (first + timedelta(days=n)).weekday() < 5 else "0")
                    for n in range((last - first).days + 1)]
        elif endpoint == "market_daily":
            rows = [dict(code="sh.600001", date=params["date"], adjustflag="3", open="10", high="11",
                low="9", close="10", preclose="10", amount="500000000", volume="50000000",
                turn="10", isST="0", tradestatus="1")]
        elif endpoint in ("master_szse", "lhb_summary"):
            rows = []
        else:
            raise AssertionError("TEST_FIXTURE_UNEXPECTED_ENDPOINT")
        sdk = provider_requests.ENDPOINTS[endpoint]["provider"] == "baostock"
        coverage = dict(sdk_operations=3 if sdk else 0, http_attempts=0 if sdk else 1)
        seen = datetime.now(timezone.utc).isoformat()
        payload = dict(status=status, rows=rows, pagination_complete=not failed, coverage=coverage,
            gaps=["UPSTREAM_TIMEOUT"] if failed else [], first_seen_at=seen)
        if not failed:
            payload["field_version"] = "TEST_FIXTURE_ROWS_V1"
        else:
            payload.update(failure_category="NETWORK", error_code="UPSTREAM_TIMEOUT")
        physical = archive_receipt(Path(output) / "raw", "TEST_FIXTURE", endpoint, params, payload,
            status=status, pagination_complete=not failed, field_version="TEST_FIXTURE_ROWS_V1", first_seen_at=seen)
        result = dict(endpoint=endpoint, provider="TEST_FIXTURE", parameters=params, rows=rows, status=status,
            pagination_complete=not failed, coverage=coverage, gaps=payload["gaps"],
            field_version="TEST_FIXTURE_ROWS_V1", request_receipt_id=physical["receipt_id"], receipts=[physical],
            started_at=started, completed_at=datetime.now(timezone.utc).isoformat(),
            implementation_source_hashes={name: hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                for name in captures.PRODUCERS}, diagnostics=dict(fixture="TEST_FIXTURE"))
        if failed:
            result.update(failure_category="NETWORK", error_code="UPSTREAM_TIMEOUT")
        Path(output, "request-TEST_FIXTURE.json").write_text(json.dumps(result))
        return result


class FixtureRuntime:
    def __init__(self, root, profile):
        self.profile = profile
        self.scope = dict(backend="premarket", device=1, inode=2)
        self.directory = root / "scopes" / workspace._digest(self.scope)
        self.ancestor_ledgers = []
        self.locked = False

    def check_binding(self):
        pass

    @contextmanager
    def exclusive(self):
        if self.locked:
            raise AssertionError("TEST_FIXTURE_DOUBLE_LOCK")
        self.locked = True
        try:
            yield
        finally:
            self.locked = False


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_continuation_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        self.python = self.root / "python"
        self.python.write_bytes(b"TEST_FIXTURE_PYTHON")
        self.config_path = self.root / "configuration.json"
        self.config_path.write_text(json.dumps(dict(state_root=str(self.state),
            limits=dict(max_requests=1024, max_seconds=10800, capture_read_bytes=32 * 1024**3,
                        supervisor_read_bytes=8 * 1024**3),
            capture=dict(allow_test_fixture=True, offline=False, retry_failed=False))))
        self.config = workflow.load_config(str(self.config_path))
        self.runtime = FixtureRuntime(self.root, dict(backend="premarket", state_root=str(self.state),
            source_root=str(self.source), python=str(self.python), config=str(self.config_path)))
        fixture_hashes = {}
        for name in continuation.LEGACY_PARENT_HASHES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# TEST_FIXTURE " + name + "\n")
            fixture_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.legacy_hashes = patch.dict(continuation.LEGACY_PARENT_HASHES, fixture_hashes, clear=True)
        self.legacy_hashes.start()
        self.addCleanup(self.legacy_hashes.stop)
        self.now = datetime.now(timezone.utc)
        local = self.now.astimezone(continuation.SH)
        day = local.date()
        observation = day
        while observation.weekday() >= 5 or datetime.combine(observation, wall_time(18), continuation.SH) > local:
            observation -= timedelta(days=1)
        target = observation + timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
        self.sessions = [str(observation), str(target), str(day)]
        old = captures.CaptureCache(self.state / "cache", Budget(self.config["limits"]),
            config=self.config["capture"], executor=FixtureExecutor())
        # A reused calendar is deliberately outside the failed operation.
        self.calendar = old.capture_one(captures.RequestSpec("calendar", dict(start_date=str(day-timedelta(days=200)),
            end_date=str(day+timedelta(days=30))), str(day), role="CALENDAR"))
        started = datetime.now(timezone.utc)
        self.created = datetime.now(timezone.utc)
        budget = Budget(self.config["limits"])
        cache = captures.CaptureCache(self.state / "cache", budget,
            config=self.config["capture"], executor=FixtureExecutor())
        self.failed = cache.capture_one(captures.RequestSpec("master_szse", {}, str(day), current=True, role="CURRENT_OFFICIAL"))
        cache.capture_one(captures.RequestSpec("market_daily", dict(date=str(observation)), str(observation), role="CORE_MARKET"))
        cache.capture_one(captures.RequestSpec("lhb_summary", dict(start_date=str(observation), end_date=str(observation)),
            str(observation), role="PRESCREEN_LHB"))
        self.job = self.state / "jobs" / "TEST_FIXTURE_parent"
        self.job.mkdir(parents=True)
        code = [workflow._ref(p) for p in sorted(self.source.rglob("*.py"))]
        configuration_ref = self.write(self.job / "configuration.json", self.config)
        code_ref = self.write(self.job / "code-registry.json", code)
        error = dict(type="ValueError", reason="FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY:" + "f"*64)
        metrics = budget.metrics
        self.completion = dict(schema_version="CODEX_PREMARKET_SHARED_WORKFLOW_V1", status="FAILED", error=error,
            business_database_written=False, core_verified=False, core_artifacts={}, display=None,
            phases=dict(capture="FAILED", core="NOT_RUN", news="NOT_RUN", theme_sources="NOT_RUN", theme="NOT_RUN", presentation="NOT_RUN"),
            expected_sessions=self.sessions, completed_at=datetime.now(timezone.utc).isoformat(), metrics=metrics)
        completion_ref = self.write(self.job / "worker-completion.json", self.completion)
        self.final = dict(schema_version="CODEX_PREMARKET_SHARED_WORKFLOW_V1", status="FAILED", job=str(self.job),
            external_worker_reaped=True, external_worker_exit_code=3, core_verified=False,
            source_terminal_binding_verified=False, error=error, completion=completion_ref,
            configuration=configuration_ref, code_registry=code_ref, elapsed_seconds=metrics["elapsed_seconds"],
            supervisor_returned_read_bytes=4096, peak_tree_rss_bytes=100,
            native_inner_supervision=dict(root_reaped=True, failure=None, cleanup=None,
                root_reap_evidence=dict(exit_code_proven=True), root_identity=dict(create_time=self.created.timestamp())),
            final_sealed_at=datetime.now(timezone.utc).isoformat())
        final_ref = self.write(self.job / "final-control.json", self.final)
        snapshot = dict(source_references=[r for r in code if "/stockfish_core/" in r["path"] or r["path"].endswith("/codex_daily_v2.py")],
            strategy_support=[r for r in code if r["path"].endswith("/codex_daily_v2.py")],
            configuration=workflow._ref(self.config_path), python=workflow._ref(self.python), shared_execution_sources=code)
        identity = dict(schema_version="CODEX_DAILY_EXECUTION_PIPELINE_V2", backend="premarket",
            execution_engine="original", explicit_missing_collection=True, state_root=str(self.state),
            source_root=str(self.source), python_path=str(self.python), request_key="TEST_FIXTURE_same_business",
            observation_date=self.sessions[0], target_session=self.sessions[1],
            configuration=snapshot["configuration"], python=snapshot["python"], execution_sources_sha256=workspace._digest(snapshot))
        operation_id = workspace._digest(dict(scope=self.runtime.scope, identity=identity))
        self.operation_path = self.runtime.directory / "operations" / (operation_id + ".json")
        operation_ref = self.write(self.operation_path, dict(schema_version=workspace.VERSION, scope=self.runtime.scope,
            identity=identity, operation_id=operation_id, phase="FAILED_OR_UNKNOWN", started_at=started.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat()))
        self.proof_path = self.root / "parent-snapshot-proof.json"
        self.proof = dict(schema_version=continuation.PROOF_VERSION, original_operation=operation_ref,
            original_job=str(self.job), final=final_ref, completion=completion_ref, configuration=configuration_ref,
            code_registry=code_ref, snapshot=snapshot, snapshot_sha256=workspace._digest(snapshot),
            operation_ledger_references=[operation_ref], accounting=dict(parent_read_upper_bound_bytes=0))
        self.write(self.proof_path, self.proof)
        self.old_operation = self.operation_path.read_bytes()
        self.old_final = (self.job / "final-control.json").read_bytes()
        self.old_attempt_bytes = {path: path.read_bytes() for path in (self.state / "cache/raw").rglob("*") if path.is_file()}

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(workspace._canonical(value))
        return workflow._ref(path)

    def prepare(self, **overrides):
        options = dict(operation_path=self.operation_path, source_job=self.job, snapshot_proof_path=self.proof_path,
            expected_snapshot_proof_sha256=hashlib.sha256(self.proof_path.read_bytes()).hexdigest(),
            allowed_source_key=self.failed.metadata["request_key"], allowed_receipt_id=self.failed.receipt_id,
            allow_test_fixture=True)
        options.update(overrides)
        return continuation.prepare(self.runtime, **options)

    def success_callbacks(self):
        calls = []
        parent_budget = None
        def action(limits, authorization):
            nonlocal parent_budget
            self.assertTrue(self.runtime.locked)
            calls.append(authorization)
            parent_budget = Budget(limits)
            job = self.state / "jobs" / "TEST_FIXTURE_continued"
            job.mkdir()
            worker = Budget(limits)
            completion_ref = self.write(job / "worker-completion.json", dict(status="COMPLETE", metrics=worker.metrics,
                expected_sessions=self.sessions))
            final_ref = self.write(job / "final-control.json", dict(status="COMPLETE", job=str(job),
                external_worker_reaped=True, completion=completion_ref))
            return dict(job=str(job), final=final_ref, completion=completion_ref)
        def validate(receipt, proposal):
            nonlocal parent_budget
            if parent_budget is None:
                parent_budget = Budget(proposal["remaining_limits"])
            # This must not be nested in coordinator.meter_files().
            with parent_budget.meter_files():
                self.assertTrue(Path(receipt["final"]["path"]).read_bytes())
            wrapper = Budget(continuation._reader_limits()).metrics
            return dict(verified=True, parent_verification_metrics=parent_budget.metrics,
                        wrapper_preparation_metrics=wrapper)
        return calls, action, validate

    def assert_parent_unchanged(self):
        self.assertEqual(self.old_operation, self.operation_path.read_bytes())
        self.assertEqual(self.old_final, (self.job / "final-control.json").read_bytes())
        for path, raw in self.old_attempt_bytes.items():
            self.assertEqual(raw, path.read_bytes())

    def test_prepare_reuses_old_calendar_and_recomputes_reserved_budgets(self):
        proposal = self.prepare()
        self.assertEqual(3, proposal["parent_provider_counts"]["requests"])
        self.assertEqual(3, proposal["parent_provider_counts"]["sdk_reserved"])
        self.assertEqual(128, proposal["parent_provider_counts"]["http_reserved"])
        self.assertEqual(1021, proposal["remaining_limits"]["max_requests"])
        self.assertEqual(continuation.ADMITTED, proposal["status"])
        self.assertGreater(proposal["budget_debits"]["original_parent_read_debit_bytes"], 0)
        self.assertFalse((self.operation_path.parent / self.operation_path.stem).exists())
        self.assert_parent_unchanged()

    def test_execute_claims_once_and_success_reuses_with_independent_budget_meter(self):
        proposal = self.prepare()
        calls, action, validate = self.success_callbacks()
        result = continuation.execute(self.runtime, proposal, action=action, validate=validate)
        self.assertEqual("RECORDED", result["status"])
        self.assertEqual(1, len(calls))
        owned = Path(result["authorization"]["claim"]["path"]).parent / "parent-snapshot-proof.json"
        self.assertEqual(self.proof, json.loads(owned.read_bytes()))
        self.proof_path.unlink()  # The fixed slot, not /tmp, retains the proof.
        reused = continuation.execute(self.runtime, proposal, action=action, validate=validate)
        self.assertEqual("REUSED", reused["status"])
        self.assertEqual(1, len(calls))
        self.assert_parent_unchanged()

    def test_failure_or_unknown_claim_never_starts_another_action(self):
        proposal = self.prepare()
        calls = []
        def action(*args):
            calls.append(1)
            raise RuntimeError("TEST_FIXTURE_UPSTREAM_FAILED")
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_UPSTREAM_FAILED"):
            continuation.execute(self.runtime, proposal, action=action, validate=lambda *_: {})
        slot = continuation._slot(self.runtime, proposal["operation_id"])
        for state in ("terminal", "bare_claim"):
            if state == "bare_claim":
                (slot / "terminal.json").unlink()
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "CLAIM_EXISTS_NO_RETRY"):
                continuation.execute(self.runtime, proposal, action=action, validate=lambda *_: {})
        self.assertEqual([1], calls)
        self.assert_parent_unchanged()

    def test_mutated_budget_proposal_never_claims(self):
        proposal = self.prepare()
        proposal["remaining_limits"]["max_requests"] += 1
        with self.assertRaisesRegex(ValueError, "PROPOSAL_CHANGED"):
            continuation.execute(self.runtime, proposal, action=lambda *_: self.fail("action"), validate=lambda *_: {})

    def test_rehashed_budget_increase_fails_locked_recomputation(self):
        proposal = self.prepare()
        proposal["remaining_limits"]["max_requests"] += 1
        proposal["proposal_id"] = continuation._proposal_digest(proposal)
        with self.assertRaisesRegex(ValueError, "ADMISSION_CHANGED_BEFORE_CLAIM"):
            continuation.execute(self.runtime, proposal, action=lambda *_: self.fail("action"), validate=lambda *_: {})
        self.assertFalse((continuation._slot(self.runtime, proposal["operation_id"]) / "claim.json").exists())

    def test_extra_unclaimed_attempt_between_prepare_and_execute_blocks(self):
        proposal = self.prepare()
        extra = self.state / "cache/raw" / self.failed.metadata["request_key"] / "raw/TEST_FIXTURE_unknown"
        extra.mkdir()
        with self.assertRaisesRegex(ValueError, "UNCLAIMED_OR_EXCESS_ATTEMPT"):
            continuation.execute(self.runtime, proposal, action=lambda *_: self.fail("action"), validate=lambda *_: {})
        self.assertFalse((continuation._slot(self.runtime, proposal["operation_id"]) / "claim.json").exists())

    def test_wrong_whitelist_and_future_day_rejected(self):
        for options, reason in ((dict(allowed_receipt_id="0"*64), "EXACT_ONE_TIMEOUT_SOURCE_REQUIRED"),
                                (dict(_now_value=datetime.now(timezone.utc)+timedelta(days=1)), "CURRENT_SOURCE_DAY_CHANGED")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, reason):
                self.prepare(**options)

    def test_unknown_legacy_accounting_implementation_rejected(self):
        with patch.dict(continuation.LEGACY_PARENT_HASHES, {"scripts/codex_daily_v2.py": "0"*64}):
            with self.assertRaisesRegex(ValueError, "UNKNOWN_PARENT_ACCOUNTING_IMPLEMENTATION"):
                self.prepare()

    def test_existing_index_does_not_excuse_unknown_raw_folder(self):
        folder = self.state / "cache/raw" / self.calendar.metadata["request_key"]
        (folder / "raw").rename(self.root / "TEST_FIXTURE_retained_old_attempts")
        (folder / "unexplained.txt").write_text("TEST_FIXTURE_UNKNOWN")
        with self.assertRaisesRegex(ValueError, "UNKNOWN_RAW_FOLDER"):
            self.prepare()
        self.assertEqual("TEST_FIXTURE_UNKNOWN", (folder / "unexplained.txt").read_text())

    def test_verified_inherited_scope_keeps_old_unknown_counts_without_new_request(self):
        folder = self.state / "cache/raw" / ("a" * 64)
        folder.mkdir()
        (folder / "inherited-original-attempt").mkdir()
        proved = dict(status="VERIFIED_RETAINED_ORIGINAL", receipt_ids=[self.calendar.receipt_id],
            actual_counts_status="UNKNOWN_RETAINED", statuses=[dict(receipt_id=self.calendar.receipt_id,
                status="COMPLETE", pagination_complete=True, actual_counts_status="UNKNOWN_RETAINED",
                actual_counts=dict(sdk_operations=None, http_attempts=None))])
        with patch.object(continuation, "inherited_attempt_scope", return_value=proved) as resolver:
            proposal = self.prepare()
        resolver.assert_called_once()
        self.assertEqual(continuation.ADMITTED, proposal["status"])
        self.assertEqual(3, proposal["parent_provider_counts"]["requests"])
        retained = proposal["retained_historical_inventory"][0]
        self.assertEqual("UNKNOWN_RETAINED", retained["actual_counts_status"])
        self.assertFalse(retained["actual_counts_affect_parent_budget"])
        self.assertEqual(dict(sdk_operations=None, http_attempts=None), retained["statuses"][0]["actual_counts"])
        self.assert_parent_unchanged()

    def test_same_bytes_replacement_preserves_content_identity(self):
        proposal = self.prepare()
        old = self.operation_path.read_bytes()
        swap = self.operation_path.with_suffix(".replacement")
        swap.write_bytes(old)
        swap.replace(self.operation_path)
        self.assertEqual(proposal["proposal_id"], self.prepare()["proposal_id"])

    def changed_control(self, name):
        proposal = self.prepare()
        calls, success, validate = self.success_callbacks()
        def action(limits, authorization):
            receipt = success(limits, authorization)
            target = Path(authorization["claim"]["path"]).parent / name
            raw = target.read_bytes()
            changed = raw.replace(b'"schema_version"', b'"schema_versioN"', 1)
            self.assertNotEqual(raw, changed)
            self.assertEqual(len(raw), len(changed))
            target.write_bytes(changed)
            return receipt
        with self.assertRaisesRegex(BudgetExceeded, "FILE_REFERENCE_CHANGED"):
            continuation.execute(self.runtime, proposal, action=action, validate=validate)
        terminal = json.loads((continuation._slot(self.runtime, proposal["operation_id"]) / "terminal.json").read_bytes())
        self.assertEqual("FAILED_OR_UNKNOWN", terminal["status"])
        self.assert_parent_unchanged()

    def test_claim_change_after_action_refuses_success(self):
        self.changed_control("claim.json")

    def test_owned_proof_change_after_action_refuses_success(self):
        self.changed_control("parent-snapshot-proof.json")


if __name__ == "__main__":
    unittest.main()
