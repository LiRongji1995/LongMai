"""TEST_FIXTURE request coordination; actual full worker tests are separate."""
from contextlib import nullcontext, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
from types import FunctionType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import codex_daily_v2 as daily
from scripts import codex_workspace as workspace
from stockfish_core.yaogu import premarket_workflow_v1 as frozen


class FixtureRuntime:
    run_once = workspace.Runtime.run_once
    _validated = staticmethod(workspace.Runtime._validated)
    _capacity_summary = staticmethod(workspace.Runtime._capacity_summary)

    def __init__(self, root):
        self.directory = root / "coordination"
        self.scope = {"backend": "premarket", "device": 1, "inode": 2}
        self.profile = dict(backend="premarket")

    def exclusive(self):
        return nullcontext()

    def check_binding(self):
        pass

    def preflight(self, **kwargs):
        return dict(allowed=True, reason_codes=[], expected_growth_bytes=1024,
                    min_free_bytes=1, max_managed_bytes=1024**3,
                    projected_unique_logical_bytes=2048, destination_checks=[])


class GatewayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_daily_v2_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(dict(state_root=str(self.state), capture=dict(allow_test_fixture=True))))
        self.runtime = FixtureRuntime(self.root)
        self.prepared = dict(state=self.state, source=daily.ROOT, python=Path(sys.executable), config=self.config)
        self.snapshot = dict(configuration=daily.original._reference(self.config), python={"TEST_FIXTURE": True})
        today = datetime.now(timezone(timedelta(hours=8)))
        self.observation = today.date() if today.hour >= 18 else today.date()-timedelta(days=1)
        self.target = self.observation+timedelta(days=1)
        self.args = daily.parser().parse_args(["premarket", "--observation-date", str(self.observation),
            "--target-session", str(self.target), "--request-key", "TEST_FIXTURE_same_request"])
        self.admission = dict(execution_admitted=True, observation_date=str(self.observation), target_session=str(self.target),
                              planned_at=datetime.now(timezone.utc).isoformat())
        self.calls = 0
        self.version = "CODEX_PREMARKET_SHARED_WORKFLOW_V1"
        verify = FunctionType(frozen.verify_job.__code__, {**frozen.verify_job.__globals__, "VERSION": self.version},
                              frozen.verify_job.__name__)
        self.worker = SimpleNamespace(VERSION=self.version, code_refs=lambda: [], verify_job=verify,
                                      supervise=self.supervise)
        for target, name, value in ((daily.original, "_prepared", lambda _: self.prepared),
                                     (daily, "_snapshot", lambda _: self.snapshot),
                                     (daily, "_workflow", lambda: self.worker),
                                     (daily, "_plan", lambda _: self.admission)):
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)

    def supervise(self, config, *, progress, started_at=None, expected_sessions=None, engine="original"):
        self.calls += 1
        assert not progress and config["capture"]["allow_test_fixture"] is True
        job = self.state / "jobs" / f"TEST_FIXTURE_{self.calls}"
        job.mkdir(parents=True)
        def write(name, data):
            path = job / name
            path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
            return frozen._ref(path)
        configuration = write("configuration.json", config)
        registry = write("code-registry.json", [])
        core = {name: write(name, b"TEST_FIXTURE " + name.encode()) for name in daily.original.CORE_FILES}
        display = dict(artifacts={name: write(name, b"TEST_FIXTURE display")
                                 for name in ("display.json", "display.csv", "report.md")})
        completion = dict(schema_version=self.version, status="COMPLETED_WITH_UNKNOWNS", core_verified=True,
            execution_engine=engine, expected_sessions=list(expected_sessions),
            run_id="TEST_FIXTURE_run", observation_date=str(self.observation), target_session=str(self.target),
            strategy_id="SHORT_HORIZON_STRENGTH_R2", theme_status="UNKNOWN", core_artifacts=core,
            theme_artifacts={}, display=display, news=None,
            calendar_rows=[dict(calendar_date=str(self.observation+timedelta(days=i)), is_trading_day="1")
                           for i in range(-3, 5)])
        completion_ref = write("worker-completion.json", completion)
        final = dict(schema_version=self.version, status=completion["status"], core_verified=True,
            execution_engine=engine, expected_sessions=list(expected_sessions),
            external_worker_exit_code=2, external_worker_reaped=True, implementation_epoch_verified=True,
            whole_workflow_wall_enforced=True, supervisor_returned_read_bytes=0,
            source_terminal_binding_verified=True, configuration=configuration, code_registry=registry,
            completion=completion_ref, display=display, job=str(job))
        write("final-control.json", final)
        return 2, final

    def test_full_command_records_then_reuses_exact_request(self):
        code, first = daily.execute(self.runtime, self.args)
        self.assertEqual(code, 2)
        self.assertEqual(first["status"], "RECORDED")
        self.assertTrue(first["complete_daily_delivery"])
        self.assertFalse(first["validation"]["source_authority_restored"])
        self.assertEqual(first["daily_v1_readiness"], "UNKNOWN_NOT_EXECUTED")
        with patch.object(daily, "_plan", side_effect=AssertionError("no recollection on reuse")):
            code, second = daily.execute(self.runtime, self.args)
        self.assertEqual(code, 2)
        self.assertEqual(second["status"], "REUSED")
        self.assertEqual(self.calls, 1)

    def test_admission_race_is_rechecked_inside_coordinator_before_worker(self):
        blocked = {**self.admission, "execution_admitted": False}
        with patch.object(daily, "_plan", side_effect=[self.admission, blocked]):
            with self.assertRaisesRegex(ValueError, "ADMISSION_CHANGED_BEFORE_EXECUTION"):
                daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 0)

    def test_coordinator_bookkeeping_cannot_overrun_delivery_deadline(self):
        self.config.write_text(json.dumps(dict(state_root=str(self.state),
            limits=dict(max_seconds=.5), capture=dict(allow_test_fixture=True))))
        actual = self.runtime.run_once
        def delayed(*args):
            result = actual(*args)
            __import__('time').sleep(.6)
            return result
        with patch.object(self.runtime, "run_once", side_effect=delayed):
            with self.assertRaisesRegex(RuntimeError, "TOTAL_TIME_LIMIT_EXCEEDED"):
                daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)
        records = list((self.runtime.directory / "operations").glob("*.json"))
        self.assertEqual(json.loads(records[0].read_text())["phase"], "RECORDED")

    def test_preflight_block_has_no_claim_or_worker(self):
        self.admission.update(execution_admitted=False, reasons=["CACHE_LIMIT"])
        code, result = daily.execute(self.runtime, self.args)
        self.assertEqual(code, 4)
        self.assertEqual(result["status"], "BLOCKED_BEFORE_COLLECTION")
        self.assertFalse(result["worker_started"])
        self.assertFalse(self.runtime.directory.exists())
        self.assertEqual(self.calls, 0)

    def test_wrong_target_refuses_before_claim(self):
        self.admission["target_session"] = str(self.target+timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "NOT_LATEST_SESSION"):
            daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 0)
        self.assertFalse(self.runtime.directory.exists())

    def test_failed_or_uncertain_claim_does_not_restart(self):
        with patch.object(self.worker, "supervise", return_value=(4, {"status": "BUDGET_EXCEEDED"})) as worker:
            with self.assertRaisesRegex(ValueError, "NO_AUTOMATIC_RETRY"):
                daily.execute(self.runtime, self.args)
            with self.assertRaisesRegex(ValueError, "STATE_UNKNOWN_RECONCILE_NO_RETRY"):
                daily.execute(self.runtime, self.args)
        self.assertEqual(worker.call_count, 1)

    def test_new_source_identity_does_not_hide_existing_request(self):
        daily.execute(self.runtime, self.args)
        self.snapshot["TEST_FIXTURE_new_source"] = "different"
        with self.assertRaisesRegex(ValueError, "EXISTING_REQUEST_RECONCILE_NO_RETRY"):
            daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)

    def test_default_engine_original_and_opt_in_cannot_retry_same_request(self):
        self.assertEqual(self.args.engine, "original")
        daily.execute(self.runtime, self.args)
        self.args.engine = "shared"
        with self.assertRaisesRegex(ValueError, "EXISTING_REQUEST_RECONCILE_NO_RETRY"):
            daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)

    def test_request_key_cannot_cross_sessions(self):
        daily.execute(self.runtime, self.args)
        self.args.observation_date += timedelta(days=1)
        self.args.target_session += timedelta(days=1)
        with self.assertRaisesRegex(ValueError, "KEY_REUSED_ACROSS_SESSIONS"):
            daily.execute(self.runtime, self.args)

    def test_post_worker_validation_cannot_restart_elapsed_budget(self):
        actual = daily._validate
        def late(*args):
            __import__('time').sleep(.6)
            return actual(*args)
        self.config.write_text(json.dumps(dict(state_root=str(self.state),
            limits=dict(max_seconds=.5), capture=dict(allow_test_fixture=True))))
        with patch.object(daily, "_validate", side_effect=late):
            with self.assertRaisesRegex(RuntimeError, "TOTAL_TIME_LIMIT_EXCEEDED"):
                daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)

    def test_archived_export_same_bytes_new_inode_rejected(self):
        _, first = daily.execute(self.runtime, self.args)
        path = Path(first["receipt"]["job"]) / "all.csv"
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        with self.assertRaisesRegex(ValueError, "PHYSICAL_IDENTITY_CHANGED"):
            daily.execute(self.runtime, self.args)
        self.assertEqual(self.calls, 1)

    def test_v1_commands_delegate_without_changing_arguments(self):
        args = daily.parser().parse_args(["show", "--role", "all.csv"])
        with patch.object(daily.legacy, "execute", return_value=(0, {"original": True})) as old:
            self.assertEqual(daily.execute(self.runtime, args), (0, {"original": True}))
        old.assert_called_once_with(self.runtime, args)

    def test_plan_is_read_only_and_not_implicit_run(self):
        args = daily.parser().parse_args(["premarket-plan"])
        code, result = daily.execute(self.runtime, args)
        self.assertEqual(code, 0)
        self.assertIs(result, self.admission)
        self.assertEqual(self.calls, 0)

    def test_main_preserves_blocked_exit_and_complete_json(self):
        self.admission["execution_admitted"] = False
        output = io.StringIO()
        with patch.object(daily.workspace, "load_runtime", return_value=self.runtime), \
             patch.object(daily, "_registered", return_value=None), redirect_stdout(output):
            code = daily.main(["premarket", "--observation-date", str(self.observation),
                "--target-session", str(self.target), "--request-key", "TEST_FIXTURE_same_request"])
        self.assertEqual(code, 4)
        result = json.loads(output.getvalue())
        self.assertEqual(result["entry_version"], daily.VERSION)
        self.assertEqual(result["command_exit_code"], 4)


if __name__ == "__main__":
    unittest.main()
