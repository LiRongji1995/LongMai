"""TEST_FIXTURE gateway contracts; no real runtime, database, or provider calls."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import codex_daily as daily
from scripts import codex_daily_plan_v1 as planner
from scripts import codex_result_view_v1 as views
from scripts import codex_workspace as workspace


def saved_view():
    artifacts = {name: dict(path="/TEST_FIXTURE/" + name, sha256="d" * 64, bytes=100)
                 for name in views.EXPORT_ROLES}
    return dict(status="VERIFIED_SAVED_RESULT_VIEW", view_id="a" * 64, archive_id="b" * 64,
        archive_profile="FULL_CORE_ARCHIVE_READONLY_V1", local_seal_authenticated_now=True,
        all_five_exports_verified_now=True, source_reauthenticated_now=False,
        last_full_authenticated_at="2026-10-24T00:00:00+00:00", artifacts=artifacts,
        summary=dict(run_id="TEST_FIXTURE-run", input_hash="c" * 64, canonical_content_hash="e" * 64,
            observation_date="2026-10-23", target_session="2026-10-26", decision_cutoff="2026-10-23T21:00:00+08:00",
            strategy_id="SHORT_HORIZON_STRENGTH_R2", coverage_type="TEST_FIXTURE",
            counts=dict(FOCUS=0, WATCH=1, REJECTED=1, UNKNOWN=1)))


class ViewPlanGatewayTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(profile=dict(backend="premarket", storage=dict(
            max_managed_bytes=1024**3, min_free_bytes=1024, expected_growth_bytes=4096)))
        self.value = saved_view()
        self.reader = SimpleNamespace(read_view=Mock(return_value=self.value),
            list_views=Mock(return_value=dict(views=[dict(view_id=self.value["view_id"],
                observation_date="2026-10-23", decision_cutoff="2026-10-23T21:00:00+08:00")])) )
        self.capacity = dict(inventory=dict(scan_complete=True, totals=dict(unique_logical_bytes=100),
            roots=[dict(name="data", filesystem=dict(free_bytes=1024**3))]), destinations=[dict(name="data")])

    def test_default_selection_uses_flat_signed_locator_fields(self):
        self.reader.list_views.return_value = dict(views=[
            dict(view_id="a" * 64, observation_date="2026-10-22", decision_cutoff="2026-10-22T23:00:00+08:00"),
            dict(view_id="b" * 64, observation_date="2026-10-23", decision_cutoff="2026-10-23T20:00:00+08:00"),
            dict(view_id="c" * 64, observation_date="2026-10-23", decision_cutoff="2026-10-23T21:00:00+08:00")])
        with patch.object(daily, "_views", return_value=self.reader):
            self.assertEqual(daily._select_view(self.runtime, None), "c" * 64)
            self.assertEqual(daily._select_view(self.runtime, "explicit-view"), "explicit-view")
        self.assertEqual(self.reader.list_views.call_count, 1)

    def test_no_saved_view_stays_missing_and_does_not_compute(self):
        self.reader.list_views.return_value = dict(views=[])
        with patch.object(daily, "_views", return_value=self.reader):
            with self.assertRaisesRegex(workspace.WorkspaceError, "NO_SEALED_RESULT"):
                daily._select_view(self.runtime, None)
        self.reader.read_view.assert_not_called()

    def test_show_passes_explicit_dates_and_preserves_formal_role(self):
        args = daily.parser().parse_args(["show", "--view-id", self.value["view_id"],
            "--role", "all.csv", "--observation-date", "2026-10-23", "--target-session", "2026-10-26"])
        with patch.object(daily, "_views", return_value=self.reader):
            code, result = daily.execute(self.runtime, args)
        self.assertEqual(code, 0)
        self.assertIs(result, self.value)
        self.reader.read_view.assert_called_once_with(self.runtime, self.value["view_id"], role="all.csv",
            observation_date="2026-10-23", target_session="2026-10-26")

    def test_plan_supplies_inventory_policy_string_date_and_one_final_check(self):
        args = SimpleNamespace(view_id=self.value["view_id"], target_session=date(2026, 10, 27))
        with patch.object(daily, "_views", return_value=self.reader), \
             patch.object(daily, "_capacity", return_value=self.capacity) as capacity, \
             patch.object(planner, "saved_view_plan_request", wraps=planner.saved_view_plan_request) as request, \
             patch.object(planner, "build_plan", wraps=planner.build_plan) as build:
            result = daily._plan(self.runtime, args)
        request.assert_called_once_with(self.value, target_session="2026-10-27")
        arguments = build.call_args.kwargs
        self.assertIs(arguments["capacity_observation"], self.capacity["inventory"])
        self.assertEqual(arguments["policy"]["max_managed_bytes"], 1024**3)
        self.assertEqual(arguments["policy"]["target_roots"], ["data"])
        self.assertEqual(arguments["policy"]["min_free_bytes"], 1024)
        self.assertEqual(self.reader.read_view.call_count, 2)
        capacity.assert_called_once_with(self.runtime)
        verify = arguments["verify_result"]
        self.assertTrue(verify(dict(artifact=self.value["artifacts"]["observation.json"],
                                    details=dict(view_id=self.value["view_id"]))))
        self.assertFalse(verify(dict(artifact=self.value["artifacts"]["observation.json"], details=dict(view_id="wrong"))))
        self.assertFalse(verify(dict(artifact=dict(sha256="f" * 64), details=dict(view_id=self.value["view_id"]))))
        self.assertFalse(result["source_reauthenticated_now"])
        self.assertNotIn("EXPLICIT_RUNTIME_CAPACITY_POLICY_REQUIRED", result["budget"]["reason_codes"])
        self.assertNotIn("COMPLETE_CAPACITY_OBSERVATION_REQUIRED", result["budget"]["reason_codes"])
        self.assertTrue(any(row["node_id"] == "core/2026-10-27" for row in result["nodes"]))

    def test_plan_fails_if_saved_summary_changes_before_terminal_check(self):
        changed = deepcopy(self.value)
        changed["summary"]["counts"]["WATCH"] = 99
        self.reader.read_view.side_effect = [self.value, changed]
        args = SimpleNamespace(view_id=self.value["view_id"], target_session=None)
        with patch.object(daily, "_views", return_value=self.reader), \
             patch.object(daily, "_capacity", return_value=self.capacity):
            with self.assertRaisesRegex(workspace.WorkspaceError, "SAVED_VIEW_CHANGED_DURING_PLAN"):
                daily._plan(self.runtime, args)

    def test_plan_fails_if_original_export_reference_changes(self):
        changed = deepcopy(self.value)
        changed["artifacts"]["observation.json"]["sha256"] = "f" * 64
        self.reader.read_view.side_effect = [self.value, changed]
        with patch.object(daily, "_views", return_value=self.reader), \
             patch.object(daily, "_capacity", return_value=self.capacity):
            with self.assertRaisesRegex(workspace.WorkspaceError, "SAVED_VIEW_CHANGED_DURING_PLAN"):
                daily._plan(self.runtime, SimpleNamespace(view_id=self.value["view_id"], target_session=None))

    def test_main_plan_emits_one_complete_json_with_entry_version(self):
        output = io.StringIO()
        with patch.object(daily.workspace, "load_runtime", return_value=self.runtime), \
             patch.object(daily, "_registered", return_value=None), \
             patch.object(daily, "_views", return_value=self.reader), \
             patch.object(daily, "_capacity", return_value=self.capacity), redirect_stdout(output):
            code = daily.main(["plan", "--view-id", self.value["view_id"], "--target-session", "2026-10-27"])
        self.assertEqual(code, 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        result = json.loads(lines[0])
        self.assertEqual(result["entry_version"], daily.VERSION)
        self.assertEqual(result["command_exit_code"], 0)
        self.assertEqual(result["view_id"], self.value["view_id"])
        self.assertFalse(result["source_reauthenticated_now"])
        self.assertIn("nodes", result)
        self.assertEqual(self.reader.read_view.call_count, 2)


class DispatchAndErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE-daily-dispatch-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        (self.source / "scripts").mkdir(parents=True)
        self.script = self.source / "scripts" / "codex_daily.py"
        self.script.write_text("# TEST_FIXTURE registered gateway\n")
        self.runtime = SimpleNamespace(profile={})
        self.prepared = dict(source=self.source, python=Path(sys.executable).absolute())

    def test_outcomes_shared_parses_remaining_arguments_and_preserves_helper_result(self):
        arguments = ["r2-daily-run", "--db", "/TEST_FIXTURE/data with spaces/research.sqlite3",
            "--state", "/TEST_FIXTURE/state", "--run-id", "TEST_FIXTURE-run-one",
            "--run-id", "TEST_FIXTURE-run-two", "--input-id", "TEST_FIXTURE-input",
            "--knowledge-cutoff", "2026-10-23T21:00:00+08:00", "--max-runs", "2"]
        args = daily.parser().parse_args(["outcomes-shared", *arguments])
        self.assertEqual(args.command, "outcomes-shared")
        self.assertEqual(args.arguments, arguments)
        runtime = SimpleNamespace(profile=dict(backend="premarket"))
        expected = (3, dict(status="TEST_FIXTURE_NOT_SUCCEEDED", new_worker_calls=0))
        with patch("scripts.codex_shared_outcomes_v1.execute_cli", return_value=expected) as execute_cli:
            result = daily.execute(runtime, args)
        execute_cli.assert_called_once_with(arguments)
        self.assertIs(result, expected)

    def test_registered_dispatch_pins_source_python_and_absent_bytecode_lookup(self):
        captured = {}
        def call(command, **kwargs):
            captured.update(command=command, **kwargs)
            self.assertFalse(Path(command[4].split("=", 1)[1]).exists())
            return 7
        with patch.object(daily.premarket, "_prepared", return_value=self.prepared), \
             patch.object(daily.subprocess, "call", side_effect=call), \
             patch.dict(os.environ, {"PYTHONPATH": "/TEST_FIXTURE/forbidden", "PYTHONHOME": "/TEST_FIXTURE/forbidden"}):
            self.assertEqual(daily._registered(self.runtime, ["show", "--view-id", "a" * 64]), 7)
        command = captured["command"]
        self.assertEqual(command[:4], [str(self.prepared["python"]), "-I", "-B", "-X"])
        self.assertEqual(command[5], str(self.script))
        self.assertEqual(captured["cwd"], self.source)
        self.assertNotIn("PYTHONPATH", captured["env"])
        self.assertNotIn("PYTHONHOME", captured["env"])
        self.assertEqual(captured["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_actual_registered_child_observes_isolation_and_no_bytecode_directory(self):
        self.script.write_text("import pathlib, sys\n"
            "ok = sys.flags.isolated == 1 and sys.dont_write_bytecode and sys.pycache_prefix is not None\n"
            "ok = ok and not pathlib.Path(sys.pycache_prefix).exists()\n"
            "raise SystemExit(0 if ok else 9)\n")
        with patch.object(daily.premarket, "_prepared", return_value=self.prepared):
            self.assertEqual(daily._registered(self.runtime, ["TEST_FIXTURE"]), 0)
        self.assertFalse((self.source / "scripts" / "__pycache__").exists())

    def test_already_registered_process_does_not_redispatch(self):
        prepared = dict(source=daily.ROOT, python=Path(sys.executable).absolute())
        with patch.object(daily.premarket, "_prepared", return_value=prepared), \
             patch.object(daily.sys, "pycache_prefix", str(self.root / "absent-bytecode")), \
             patch.object(daily.subprocess, "call", side_effect=AssertionError("NO_REDISPATCH")):
            self.assertIsNone(daily._registered(self.runtime, ["show"]))

    def test_safe_machine_reason_keeps_view_and_plan_codes_not_private_payload(self):
        self.assertEqual(daily._safe_reason(views.ResultViewError("VIEW_LOCAL_SIGNATURE_INVALID")), "VIEW_LOCAL_SIGNATURE_INVALID")
        self.assertEqual(daily._safe_reason(planner.PlanError("INVALID_TARGET_SESSION")), "INVALID_TARGET_SESSION")
        secret = "TEST_FIXTURE private payload /private/config token=do-not-display"
        self.assertEqual(daily._safe_reason(ValueError(secret)), "ValueError")
        output = io.StringIO()
        with patch.object(daily.workspace, "load_runtime", return_value=self.runtime), \
             patch.object(daily, "_registered", return_value=None), \
             patch.object(daily, "execute", side_effect=ValueError(secret)), redirect_stdout(output):
            self.assertEqual(daily.main(["show"]), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["reason"], "ValueError")
        self.assertNotIn(secret, output.getvalue())
        self.assertEqual(result["command_exit_code"], 2)

    def test_console_reason_is_bounded_and_discards_non_machine_payload(self):
        self.assertEqual(daily._console_reason(io.BytesIO(b'ignored debug\n{"reason":"TEST_FIXTURE_BUDGET_FAILED"}\n')),
                         "TEST_FIXTURE_BUDGET_FAILED")
        self.assertIsNone(daily._console_reason(io.BytesIO(b'{"reason":"private token=TEST_FIXTURE"}\n')))
        self.assertEqual(daily._console_reason(io.BytesIO(b"x" * (daily.MIB + 1))), "DAILY_THEME_CONSOLE_LIMIT")

    def test_stop_owned_signals_group_even_after_leader_exits(self):
        process = SimpleNamespace(pid=123456, poll=lambda: 7, wait=Mock(return_value=7))
        with patch.object(daily.os, "killpg") as kill, \
             patch.object(daily, "_owned_group_exists", return_value=False):
            self.assertTrue(daily._stop_owned(process))
        kill.assert_called_once_with(process.pid, signal.SIGTERM)
        self.assertGreaterEqual(process.wait.call_count, 1)

    def test_actual_owned_group_is_terminated_and_reaped(self):
        process = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def cleanup():
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        self.addCleanup(cleanup)
        self.assertTrue(daily._stop_owned(process))
        self.assertIsNotNone(process.returncode)
        self.assertFalse(daily._owned_group_exists(process))


class ThemeSupervisorFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE-daily-supervisor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source, self.report, self.state = (self.root / name for name in ("source", "report", "state"))
        (self.source / "scripts").mkdir(parents=True)
        self.report.mkdir(); self.state.mkdir()
        self.script = self.source / "scripts" / "codex_daily.py"
        # This is an explicit failed TEST_FIXTURE worker, not a fake successful
        # strategy or source issuer. The real supervisor launches and reaps it.
        self.script.write_text("import hashlib, json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "request = pathlib.Path(args[args.index('--request')+1])\n"
            "wanted = args[args.index('--request-sha256')+1]\n"
            "ok = hashlib.sha256(request.read_bytes()).hexdigest() == wanted\n"
            "print('TEST_FIXTURE_PRIVATE_DEBUG_TOKEN=not-a-real-secret')\n"
            "print(json.dumps({'reason': 'TEST_FIXTURE_WORKER_FAILED' if ok else 'TEST_FIXTURE_REQUEST_BINDING_FAILED'}))\n"
            "raise SystemExit(7 if ok else 8)\n")
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(dict(state_root=str(self.state), limits=dict(max_seconds=5, output_bytes=daily.MIB))))
        profile = dict(schema_version=workspace.PROFILE_VERSION, name="TEST_FIXTURE-daily-entry", backend="premarket",
            source_root=str(self.source), python=sys.executable, config=str(self.config), state_root=str(self.state),
            report_root=str(self.report), storage=dict(roots=[dict(name="fixture", path=str(self.root), role="runtime")],
                target_root="fixture", min_free_bytes=0, max_managed_bytes=64*daily.MIB, expected_growth_bytes=4096))
        profile_path = self.root / "profile.json"
        profile_path.write_text(json.dumps(profile))
        workspace.register_profile(profile_path, self.root / "registry")
        self.runtime = workspace.load_runtime(profile["name"], self.root / "registry")
        request = dict(schema_version="CODEX_DAILY_THEME_REQUEST_V1", context_cutoff="2026-10-23T21:00:00+08:00",
                       membership_mode="DATED_ONLY")
        for key in ("run_path", "run_manifest_path", "input_path", "sources_manifest_path"):
            path = self.root / ("TEST_FIXTURE-" + key + ".json")
            path.write_text('{"TEST_FIXTURE":true}')
            request[key] = str(path)
        self.request = self.root / "TEST_FIXTURE-request.json"
        self.request.write_text(json.dumps(request))
        self.reference = daily.premarket._reference(self.request, maximum=daily.MIB)
        self.prepared = dict(source=self.source, python=Path(sys.executable).absolute())

    def controls(self):
        return [json.loads(path.read_text()) for path in (self.report / "daily-execution-v1").glob("theme-*.json")]

    def test_actual_failed_child_preserves_exit_code_reason_and_original_request(self):
        with patch.object(daily.premarket, "_prepared", return_value=self.prepared):
            with self.assertRaisesRegex(workspace.WorkspaceError, "DAILY_THEME_WORKER_FAILED"):
                daily._theme_run(self.runtime, self.request)
        records = self.controls()
        self.assertEqual(len(records), 1)
        failure = records[0]
        self.assertEqual(failure["phase"], "FAILED_OR_UNKNOWN")
        self.assertEqual(failure["exit_code"], 7)
        self.assertEqual(failure["worker_reason"], "TEST_FIXTURE_WORKER_FAILED")
        self.assertEqual(failure["request_reference"], self.reference)
        self.assertTrue(failure["owned_group_reaped"])
        self.assertFalse(failure["retry_authorized"])
        self.assertNotIn("PRIVATE_DEBUG_TOKEN", json.dumps(failure))
        self.assertEqual(daily.premarket._reference(self.request, maximum=daily.MIB), self.reference)

    def test_parent_rejects_changed_request_even_if_child_claims_success(self):
        finished = SimpleNamespace(pid=987654, returncode=0, poll=lambda: 0, wait=lambda **kwargs: 0)
        def launch(command, **kwargs):
            output = Path(command[command.index("--output") + 1])
            output.mkdir(mode=0o700)
            (output / "session").mkdir()
            result_file, manifest = output / "theme-result.json", output / "session" / "manifest.json"
            result_file.write_text('{"TEST_FIXTURE":true}')
            manifest.write_text('{"TEST_FIXTURE":true}')
            receipt = dict(schema_version=daily.VERSION, status="COMPUTED_WITH_EXPLICIT_UNKNOWNS",
                output_directory=str(output), request_reference=self.reference,
                execution_source_references=[daily.premarket._reference(self.script, maximum=4*daily.MIB)],
                result=daily.premarket._reference(result_file), manifest=daily.premarket._reference(manifest))
            workspace._write(output / "completion.json", receipt)
            value = json.loads(self.request.read_text())
            value["context_cutoff"] = "2026-10-24T21:00:00+08:00"
            self.request.write_text(json.dumps(value))
            return finished
        with patch.object(daily.premarket, "_prepared", return_value=self.prepared), \
             patch.object(daily.subprocess, "Popen", side_effect=launch), \
             patch.object(daily, "_owned_group_exists", return_value=False), \
             patch.object(daily, "_stop_owned", return_value=True):
            with self.assertRaisesRegex(workspace.WorkspaceError, "WORKER_REQUEST_CHANGED"):
                daily._theme_run(self.runtime, self.request)
        failure = self.controls()[0]
        self.assertEqual(failure["request_reference"], self.reference)
        self.assertEqual(failure["exit_code"], 0)
        self.assertEqual(failure["reason"], "DAILY_THEME_WORKER_REQUEST_CHANGED")

    def test_child_request_sha_mismatch_is_rejected_before_source_computation(self):
        from stockfish_core.yaogu import saved_r2_stream_delivery_v3 as delivery
        output = self.report / "daily-execution-v1" / "theme-TEST_FIXTURE"
        with patch.object(delivery, "_freeze_run", side_effect=AssertionError("NO_SOURCE_COMPUTATION")):
            with self.assertRaisesRegex(workspace.WorkspaceError, "DAILY_THEME_REQUEST_CHANGED"):
                daily._theme_child(self.runtime, self.request, output, "0" * 64)
        self.assertFalse(output.exists())

    def test_supervisor_fails_closed_when_process_memory_is_unobservable(self):
        import psutil
        running = SimpleNamespace(pid=987654, returncode=-15, poll=lambda: None, wait=lambda **kwargs: -15)
        with patch.object(daily.premarket, "_prepared", return_value=self.prepared), \
             patch.object(daily.subprocess, "Popen", return_value=running), \
             patch.object(psutil, "Process", side_effect=psutil.AccessDenied(pid=running.pid)), \
             patch.object(daily, "_stop_owned", return_value=True) as stop:
            with self.assertRaisesRegex(workspace.WorkspaceError, "PROCESS_SAMPLE_DENIED"):
                daily._theme_run(self.runtime, self.request)
        stop.assert_called_once_with(running)
        failure = self.controls()[0]
        self.assertEqual(failure["reason"], "DAILY_THEME_PROCESS_SAMPLE_DENIED")
        self.assertEqual(failure["exit_code"], -15)
        self.assertTrue(failure["owned_group_reaped"])


if __name__ == "__main__":
    unittest.main()
