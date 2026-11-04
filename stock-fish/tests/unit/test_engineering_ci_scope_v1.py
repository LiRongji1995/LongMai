"""TEST_FIXTURE: CI routing and health contract; no listener, network or real DB."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import engineering_ci_scope_v1 as scope
from stockfish_core.api import create_app


def test_component_routes_and_strategy_changes_require_independent_oracle():
    result = scope.classify(["stock-fish/stockfish_core/yaogu/stage_aware_momentum.py",
                             "stock-fish/tests/unit/test_example.py"])
    assert result["components"] == dict(daily=True, go=True, go_core=False, live=False)
    assert "tests/unit/test_example.py" in result["daily_tests"]
    assert set(scope.DAILY_TESTS).issubset(result["daily_tests"])
    assert result["deployment_authorized"] is False
    assert scope.classify(["stockfish-live/stockfish_live/cli.py"])["components"]["live"] is True
    assert not any(scope.classify(["README.md"])["components"].values())
    assert all(scope.classify([".github/workflows/engineering-gates.yml"])["components"].values())


@pytest.mark.parametrize("path", ["../leak", "/private/data", "a\nb", "stock-fish/.env",
                                  "stock-fish/.env.production", "data/private.sqlite3", "data/model.pkl", ".venv/bin/python"])
def test_private_paths_and_runtime_outputs_fail_closed(path):
    with pytest.raises(ValueError):
        scope.classify([path])


def test_templates_remain_publishable():
    assert scope.classify(["stock-fish/.env.example"])["components"]["daily"] is True


def test_manual_dispatch_does_not_reclassify_retained_upstream_models_as_new_changes(tmp_path, capsys):
    import json
    assert scope.main(["--base", "0" * 40, "--head", "a" * 40, "--output", str(tmp_path / "scope.json")]) == 0
    value = json.loads((tmp_path / "scope.json").read_bytes())
    assert all(value["components"].values())
    assert value["private_path_check"] == "NOT_CHECKED_NO_DIFF_BASE"
    assert set(value["daily_tests"]) == set(scope.DAILY_TESTS)


def test_scope_cannot_remove_mandatory_safety_tests(tmp_path):
    value = scope.classify([])
    value["daily_tests"].remove("tests/risk/test_deterministic_risk.py")
    with pytest.raises(ValueError, match="MANDATORY_GATES_MISSING"):
        scope.run_tests(value, scope.ROOT, tmp_path / "TEST_FIXTURE_temp", tmp_path / "junit.xml")


def test_compose_uses_readiness_and_unready_application_returns_503(monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    probe = compose["services"]["stockfish"]["healthcheck"]["test"][-1]
    assert "/health/ready" in probe and "/api/health" not in probe
    core = SimpleNamespace(jobs=SimpleNamespace(register=lambda *args: None),
                           readiness=lambda: {"ready": False, "status": "not_ready"},
                           health=lambda **kwargs: {"status": "healthy"})
    http = create_app(core).test_client()
    assert http.get("/health/live").status_code == 200
    assert http.get("/health/ready").status_code == 503
    core.readiness = lambda: {"ready": True, "status": "ready"}
    assert http.get("/health/ready").status_code == 200


def test_workflow_has_no_privileged_triggers_or_secrets_and_uses_native_lock_target():
    import re
    path = scope.ROOT / ".github/workflows/engineering-gates.yml"
    raw = path.read_text()
    workflow = yaml.safe_load(raw)
    assert "pull_request_target" not in raw and "secrets." not in raw
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["daily"]["runs-on"] == "macos-15-intel"
    assert workflow["jobs"]["go"]["env"]["STOCKFISH_FORMAL_ACCEPTANCE"] == "1"
    assert "--require-hashed-inputs" in raw
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", step["uses"])
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False


def test_live_ci_installs_a_regular_wheel_before_testing_real_child_processes():
    import shlex
    workflow = yaml.safe_load((scope.ROOT / ".github/workflows/engineering-gates.yml").read_text())
    steps = workflow["jobs"]["live"]["steps"]
    commands = [line.strip() for step in steps for line in step.get("run", "").splitlines() if line.strip()]
    dependency = next(i for i, line in enumerate(commands) if "pip install -r stockfish-live/requirements-test.lock.txt" in line)
    build = next(i for i, line in enumerate(commands) if "-m pip wheel " in line)
    install = next(i for i, line in enumerate(commands) if "-m pip install " in line and "stockfish_live-*.whl" in line)
    check = next(i for i, line in enumerate(commands) if "-m pip check" in line)
    isolated_import = next(i for i, line in enumerate(commands) if 'cd "$RUNNER_TEMP"' in line and "import stockfish_live" in line)
    tests = next(i for i, line in enumerate(commands) if "--run-live " in line)
    assert dependency < build < install < check < isolated_import < tests
    assert {"--no-deps", "--wheel-dir", "./stockfish-live"}.issubset(shlex.split(commands[build]))
    assert {"--no-deps", "--no-index", "-I", "-B"}.issubset(shlex.split(commands[install]))
    assert " -I -B -c " in commands[isolated_import]
    assert "is_relative_to(Path(sys.prefix).resolve())" in commands[isolated_import]
    for command in commands:
        assert "PYTHONPATH" not in command and "--editable" not in command
        assert "--upgrade" not in command and " -e " not in command
    # Both tests and dependencies keep their existing entry points; the missing
    # installed package is repaired without filtering the real uvicorn test.
    assert " -k " not in commands[tests] and "--ignore" not in commands[tests]


def _daily_native_builder_source():
    workflow = yaml.safe_load((scope.ROOT / ".github/workflows/engineering-gates.yml").read_text())
    steps = workflow["jobs"]["daily"]["steps"]
    builder = next(step["run"] for step in steps
                   if step.get("name") == "Build a new isolated test runtime from unchanged artifact hashes")
    return builder.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0], steps


def test_ci_native_builder_executes_unchanged_bootstrap_once_without_identity_rewriting(tmp_path, monkeypatch):
    import os
    import runpy
    import sys

    source, steps = _daily_native_builder_source()
    native = tmp_path / "TEST_FIXTURE_native_macho"
    native.write_bytes(b"TEST_FIXTURE_NOT_EXECUTED")
    native.chmod(0o700)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(runpy, "run_path", lambda path: {
        "native_executable": lambda: {"source": "_NSGetExecutablePath", "path": str(native)}})
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "TEST_FIXTURE_old_launcher")
    monkeypatch.setenv("PYTHONEXECUTABLE", "TEST_FIXTURE_old_executable")
    calls = []

    class Executed(Exception):
        pass

    def execute(path, argv, environment):
        calls.append((path, argv, environment))
        raise Executed

    monkeypatch.setattr(os, "execve", execute)
    original_argv = list(sys.orig_argv)
    original_executable = sys.executable
    monkeypatch.chdir(scope.ROOT)
    with pytest.raises(Executed):
        exec(compile(source, "TEST_FIXTURE_ci_builder", "exec"), {})
    assert len(calls) == 1
    path, argv, environment = calls[0]
    assert path == str(native.resolve())
    assert argv == [path, "-I", "-B", str(scope.ROOT / "stock-fish/scripts/bootstrap_daily_runtime.py"),
                    "create", "--destination", str(tmp_path / "TEST_FIXTURE_daily_runtime")]
    assert "__PYVENV_LAUNCHER__" not in environment and "PYTHONEXECUTABLE" not in environment
    assert sys.orig_argv == original_argv and sys.executable == original_executable
    assert not (tmp_path / "TEST_FIXTURE_daily_runtime").exists()
    host = next(i for i, step in enumerate(steps) if "host-native-python-identity.json" in step.get("run", ""))
    build = next(i for i, step in enumerate(steps) if "os.execve" in step.get("run", ""))
    exact = next(i for i, step in enumerate(steps) if "--require-exact" in step.get("run", ""))
    tests = next(i for i, step in enumerate(steps) if "--run-scope" in step.get("run", ""))
    assert host < build < exact < tests
    assert not any(step.get("continue-on-error") for step in steps)


def test_ci_native_builder_refuses_missing_executable_without_fallback(tmp_path, monkeypatch):
    import os
    import runpy
    import sys

    source, _ = _daily_native_builder_source()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(runpy, "run_path", lambda path: {
        "native_executable": lambda: {"source": "_NSGetExecutablePath", "path": str(tmp_path / "TEST_FIXTURE_absent")}})
    monkeypatch.setattr(os, "execve", lambda *args: pytest.fail("must not fall back to a launcher"))
    with pytest.raises(FileNotFoundError):
        exec(compile(source, "TEST_FIXTURE_ci_builder", "exec"), {})
