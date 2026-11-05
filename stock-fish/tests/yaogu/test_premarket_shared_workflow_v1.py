"""Full isolated TEST_FIXTURE workflow differential and real supervisor checks."""
from copy import deepcopy
from collections import Counter
from datetime import datetime, timedelta
from functools import partial
import gzip
import json
from pathlib import Path
import socket
import sqlite3
import sys
import time
from unittest.mock import patch

import pytest

from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu import premarket_native_core_v1 as native
from stockfish_core.yaogu import premarket_presentation_v1 as presentation
from stockfish_core.yaogu import premarket_theme_context_v1 as theme
from stockfish_core.yaogu import premarket_workflow_v1 as original
from stockfish_core.yaogu import short_horizon_participation as participation
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from tests.yaogu import test_premarket_workflow_v1 as fixture_module


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_AND_DATABASE_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture
def fixture():
    value = fixture_module.WorkflowFixtureTests(methodName="runTest")
    value.setUp()
    value.config["limits"]["max_seconds"] = 120
    try:
        yield value
    finally:
        value.doCleanups()


def _clock(instant):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)
    return Clock


def _execute(fixture, function, name, adapters, now):
    job = fixture.root / name
    job.mkdir()
    started = time.monotonic()
    calls = Counter()
    source_payload = participation._source_payload
    def count_payload(receipt, reader):
        if receipt.dataset in ("market_daily", "master_all"):
            calls[type(reader).__name__ + ":" + receipt.dataset] += 1
        return source_payload(receipt, reader)
    with patch.object(original, "datetime", _clock(now)), \
         patch.object(native, "datetime", _clock(now)), \
         patch.object(presentation, "datetime", _clock(now)), \
         patch.object(participation, "_source_payload", count_payload):
        result = function(fixture.config, job, now=now, adapters=adapters)
    return result, dict(elapsed_seconds=time.monotonic() - started,
        market_master_payload_decodes=dict(sorted(calls.items())),
        total_job_output_bytes=original.directory_bytes(job),
        phase_timings=result.get("phase_timings"))


def _read(path):
    path = Path(path)
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def test_full_capture_core_theme_display_terminal_differential(fixture):
    adapters, now = fixture._adapters()
    before = {name: getattr(theme, name) for name in (
        "attach_theme_context", "SourceMarketStream", "run_theme_evolution_stream")}
    old, old_metrics = _execute(fixture, original.execute_workflow, "old", adapters, now)
    default, default_metrics = _execute(fixture, shared.execute_workflow_shared,
        "default-original", adapters, now)
    assert default["status"] == old["status"] == "COMPLETED_WITH_UNKNOWNS"
    assert default["schema_version"] == shared.VERSION
    assert default["execution_engine"] == "original"
    assert default["phases"] == old["phases"]
    assert default["phases"]["theme"] == "COMPLETE"
    assert default["core_verified"] is True and default["source_capability_restored"] is False
    assert default["run_id"] == old["run_id"]
    assert default["display"]["counts"] == old["display"]["counts"]
    assert default["display"]["original_selected_items_hash"] == old["display"]["original_selected_items_hash"]
    for name in ("all.csv", "observation.csv", "focus.csv", "unselected.csv", "observation.json"):
        assert Path(default["core_artifacts"][name]["path"]).read_bytes() == Path(old["core_artifacts"][name]["path"]).read_bytes()
    first = _read(old["theme_artifacts"]["native-theme-parent.json.gz"]["path"])
    second = _read(default["theme_artifacts"]["native-theme-parent.json.gz"]["path"])
    for key in ("items", "effective_input_hash", "source_refs", "computed_exchange_sessions",
                "source_input_hash", "membership_mode", "status", "market_edge"):
        assert first[key] == second[key], key
    assert default["metrics"]["calls"]["requests"] == 0
    assert {row["phase"] for row in default["phase_timings"]} == {"capture", "core", "theme", "presentation"}
    assert {name: getattr(theme, name) for name in before} == before
    print(json.dumps(dict(scope="TEST_FIXTURE_FULL_NATIVE_WORKFLOW_ONLY",
        reference=old_metrics, original=default_metrics, real_data_speedup_established=False)))
    job = fixture.root / "default-original"
    original.atomic_json(job / "final-control.json", dict(schema_version=shared.VERSION,
        status=default["status"], completion=original._ref(job / "worker-completion.json")))
    verified = shared.verify_job(job)
    assert verified["owned_derivatives_verified"] is True
    assert verified["external_sources_reauthenticated"] is False
    fact = Path(default["theme_artifacts"]["native-theme-parent.json.gz"]["path"])
    replacement = fact.with_suffix(".TEST_FIXTURE_replacement")
    replacement.write_bytes(fact.read_bytes())
    replacement.replace(fact)
    assert shared.verify_job(job)["owned_derivatives_verified"] is True
    fact.write_bytes(b"X" + fact.read_bytes()[1:])
    with pytest.raises(ValueError, match="CHANGED"):
        shared.verify_job(job)


def test_exact_budget_and_original_bindings_are_preserved(tmp_path):
    original_phase = Budget.phase
    budget = shared._workflow_budget(dict(max_seconds=30))
    assert type(budget) is Budget
    assert Budget.phase is original_phase
    assert budget.metrics["limits"] == Budget(dict(max_seconds=30)).metrics["limits"]


def test_production_cannot_inject_adapters(tmp_path):
    config = original.load_config(overrides={"state_root": str(tmp_path / "state")})
    with pytest.raises(ValueError, match="ADAPTERS_REQUIRE_EXPLICIT_TEST_FIXTURE"):
        shared.execute_workflow_shared(config, tmp_path / "job", adapters={"core": lambda: None})
    assert not (tmp_path / "job").exists()
    with pytest.raises(ValueError, match="ADAPTERS_REQUIRE_EXPLICIT_TEST_FIXTURE"):
        shared.supervise(config, _fixture_worker_command=[sys.executable, "-c", "pass"])
    assert not (tmp_path / "state").exists()


def test_shared_theme_budget_exhaustion_retains_core_and_never_display(fixture):
    adapters, now = fixture._adapters()
    fixture.config["limits"]["theme_read_bytes"] = 1
    value, _ = _execute(fixture, shared.execute_workflow_shared, "limited", adapters, now)
    assert value["status"] == "BUDGET_EXCEEDED"
    assert value["core_verified"] is True
    assert value["display"] is None
    assert "READ_LIMIT_EXCEEDED:theme" in value["error"]["reason"]
    assert Path(value["core_artifacts"]["canonical-run.json.gz"]["path"]).is_file()


def test_new_script_reads_cannot_bypass_capture_budget(fixture):
    adapters, now = fixture._adapters()
    fixture.config["limits"]["capture_read_bytes"] = 1
    value, _ = _execute(fixture, shared.execute_workflow_shared, "limited", adapters, now)
    assert value["status"] == "BUDGET_EXCEEDED"
    assert value["core_verified"] is False and value["display"] is None
    assert "READ_LIMIT_EXCEEDED:capture" in value["error"]["reason"]


def test_theme_failure_preserves_independent_core_and_unknown(fixture):
    adapters, now = fixture._adapters()
    def failure(*args, **kwargs):
        raise ValueError("TEST_FIXTURE_THEME_FAILURE")
    adapters["theme"] = failure
    value, _ = _execute(fixture, shared.execute_workflow_shared, "unknown", adapters, now)
    assert value["status"] == "COMPLETED_WITH_UNKNOWNS"
    assert value["execution_engine"] == "original"
    assert value["core_verified"] is True and value["theme_status"] == "UNKNOWN"
    assert value["phases"]["theme"] == "FAILED"
    assert value["display"]["counts"]["direction_unknown"] == 2


def test_explicit_source_closure_includes_gateway_and_imports():
    refs = shared.code_refs()
    paths = {Path(reference["path"]).name for reference in refs}
    assert {"codex_daily_v2.py", "codex_premarket_preflight_v1.py",
        "codex_premarket_shared_v1.py", "codex_sdk_tree_supervisor_v3.py",
        "codex_capacity.py", "codex_core_sources.py",
        "premarket_workflow_v1.py", "premarket_native_core_v1.py"} <= paths
    assert len(refs) == len({reference["path"] for reference in refs})


def test_real_owned_worker_offline_failure_is_preserved(fixture):
    fixture.config["limits"]["max_seconds"] = 30
    code, result = shared.supervise(fixture.config, progress=False)
    assert code == 3, result
    assert result["schema_version"] == shared.VERSION
    assert result["external_worker_reaped"] is True
    assert result["external_worker_exit_code"] == 3
    job = Path(result["job"])
    completion = _read(job / "worker-completion.json")
    assert completion["schema_version"] == shared.VERSION
    assert completion["execution_engine"] == "original"
    assert completion["status"] == "FAILED" and not completion["core_verified"]
    assert completion["metrics"]["calls"]["requests"] == 0
    assert _read(job / "configuration.json")["limits"] == fixture.config["limits"]
    assert shared.verify_job(job)["source_authority_restored"] is False


def test_supervisor_external_exit_and_read_budget(fixture):
    code, result = shared.supervise(fixture.config, progress=False,
        _fixture_worker_command=[sys.executable, "-I", "-B", "-c", "raise SystemExit(9)"])
    assert code == 3 and result["external_worker_exit_code"] == 9
    assert result["external_worker_reaped"] is True
    fixture.config["state_root"] = str(fixture.root / "limited-state")
    fixture.config["limits"]["supervisor_read_bytes"] = 1
    code, result = shared.supervise(fixture.config, progress=False)
    assert code == 4 and result["external_worker_exit_code"] is None


def test_terminal_hashing_is_inside_whole_deadline(tmp_path):
    path = tmp_path / "TEST_FIXTURE_control"
    path.write_text("TEST_FIXTURE")
    reference = original._ref(path)
    guard = shared._SupervisorGuard({"max_seconds": 0.01, "output_bytes": 1024 * 1024})
    verify = original._verify
    def slow_verify(*args, **kwargs):
        verify(*args, **kwargs)
        time.sleep(0.02)
    with patch.object(original, "_verify", slow_verify), pytest.raises(BudgetExceeded, match="TOTAL_TIME_LIMIT"):
        guard.verify(reference)


def test_parent_deadline_is_not_restarted_in_supervisor(fixture):
    fixture.config["limits"]["max_seconds"] = 1
    code, result = shared.supervise(fixture.config, progress=False,
        started_at=time.monotonic() - 2)
    assert code == 4 and result["external_worker_exit_code"] is None
    assert "TOTAL_TIME_LIMIT" in result["error"]["reason"]
    with pytest.raises(ValueError, match="PARENT_CLOCK_INVALID"):
        shared.supervise(fixture.config, progress=False, started_at=time.monotonic() + 100)


def test_worker_requires_isolated_owned_process(tmp_path, capsys):
    assert shared.main(["_worker", "--job", str(tmp_path)]) == 3
    assert json.loads(capsys.readouterr().err)["status"] == "FAILED_BEFORE_EXECUTION"


@pytest.mark.parametrize("value", [
    ("20261026", "2026-10-27", "2026-10-27"),
    ("2026-10-27", "2026-10-26", "2026-10-27"),
    ("2026-10-26", "2026-10-27", None),
    ("2026-10-26", "2026-10-27"),
])
def test_expected_sessions_have_strict_format(value):
    with pytest.raises(ValueError, match="EXPECTED_SESSION_FORMAT"):
        shared._expected_sessions(value)


def test_expected_clock_checks_release_boundary_and_midnight():
    expected = ("2026-10-26", "2026-10-27", "2026-10-26")
    start = datetime.fromisoformat("2026-10-26T18:00:00+08:00")
    assert shared._expected_gate(expected, start) == start
    with pytest.raises(ValueError, match="SESSION_WINDOW_CHANGED"):
        shared._expected_gate(expected, start - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="PLANNING_LOCAL_DAY_CHANGED"):
        shared._expected_gate(expected, datetime.fromisoformat("2026-10-27T00:00:00+08:00"))
    expected = ("2026-10-26", "2026-10-27", "2026-10-27")
    end = datetime.fromisoformat("2026-10-27T18:00:00+08:00")
    assert shared._expected_gate(expected, end - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="SESSION_WINDOW_CHANGED"):
        shared._expected_gate(expected, end)
    with pytest.raises(ValueError, match="AWARE_CURRENT_CLOCK_REQUIRED"):
        shared._expected_gate(expected, end.replace(tzinfo=None))


def test_production_cannot_supply_historical_clock_before_capture(tmp_path):
    config = original.load_config(overrides={"state_root": str(tmp_path / "state")})
    with pytest.raises(ValueError, match="PRODUCTION_CLOCK_OVERRIDE_FORBIDDEN"):
        shared.execute_workflow_shared(config, tmp_path / "job",
            now=datetime.fromisoformat("2026-10-26T20:00:00+08:00"),
            expected_sessions=("2026-10-26", "2026-10-27", "2026-10-26"))
    assert not (tmp_path / "job").exists()


def test_worker_command_carries_explicit_session_contract(tmp_path):
    expected = ("2026-10-26", "2026-10-27", "2026-10-27")
    guard = shared._SupervisorGuard({"max_seconds": 30, "output_bytes": 1048576},
        expected_sessions=expected)
    worker = shared._WorkerSubprocess(tmp_path / "absent", guard)
    with patch.object(shared, "_expected_gate", return_value=None), \
         patch.object(shared.subprocess, "Popen", return_value=object()) as popen:
        worker.Popen([sys.executable, "-B", "-m", original.__spec__.name,
            "_worker", "--job", str(tmp_path)], env={})
    command = popen.call_args.args[0]
    assert command[-6:] == ["--observation-date", expected[0], "--target-session", expected[1],
        "--planning-local-date", expected[2]]
    assert command[1:4] == ["-I", "-B", "-X"]
    assert command[command.index("--engine") + 1] == "original"


def test_default_engine_is_original_and_unknown_engine_refuses(tmp_path):
    assert shared.parser().parse_args(["_worker", "--job", "TEST_FIXTURE"]).engine == "original"
    config = original.load_config(overrides={"state_root": str(tmp_path / "state")})
    with pytest.raises(ValueError, match="EXPLICIT_ENGINE_REQUIRED"):
        shared.execute_workflow_shared(config, tmp_path / "job", engine="unverified-fast")
    assert not (tmp_path / "job").exists()
