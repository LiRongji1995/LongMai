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
