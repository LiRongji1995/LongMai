"""TEST_FIXTURE: source changes select complete suites, including real git diff."""
import json
from pathlib import Path
import subprocess

import pytest

from scripts import engineering_ci_scope_v1 as old
from scripts import engineering_ci_scope_v2 as scope


@pytest.mark.parametrize("path,required", [
    ("scripts/codex_daily_v2.py", {"tests/unit/test_codex_daily_entry_v2.py", "tests/unit/test_codex_workspace_api.py"}),
    ("scripts/codex_runtime_recovery_v1.py", {"tests/unit/test_codex_runtime_recovery_v1.py", "tests/unit/test_codex_runtime_recovery_v2.py"}),
    ("stockfish_core/yaogu/short_horizon_outcomes_v1.py", {"tests/yaogu/test_short_horizon_outcomes_v1.py", "tests/yaogu/test_short_horizon_outcome_encoding_contract.py", "tests/yaogu/test_dated_identity_outcome_closure_v1.py"}),
])
def test_original_source_only_counterexamples(path, required):
    paths = ["stock-fish/" + path]
    assert not required.issubset(old.classify(paths)["daily_tests"])
    assert required.issubset(scope.classify(paths)["daily_tests"])
    assert set(old.DAILY_TESTS).issubset(scope.classify(paths)["daily_tests"])


def test_unknown_critical_source_selects_all_specialist_groups():
    result = scope.classify(["stock-fish/stockfish_core/future_unknown.py"])
    assert set(result["selected_groups"]) == set(scope.GROUPS)
    assert result["conservative_fallback"] == ["stock-fish/stockfish_core/future_unknown.py"]
    assert "tests/backtest/test_execution_exposure.py" in result["daily_tests"]
    assert scope.classify(["README.md"])["selected_groups"] == []


def test_source_bootstrap_changes_keep_dependency_and_safety_gates():
    result = scope.classify(["stock-fish/scripts/active_source_bootstrap_v1.py"])
    assert set(result["selected_groups"]) == set(scope.GROUPS)
    assert {"tests/unit/test_active_source_bootstrap_v1.py",
            "tests/unit/test_business_authority_v1.py",
            "tests/unit/test_codex_runtime_recovery_v2.py",
            "tests/unit/test_codex_current_calendar_v2.py",
            "tests/yaogu/test_premarket_capture_cache_v1.py",
            "tests/unit/test_premarket_source_bundle_v3.py",
            "tests/unit/test_codex_research_protocol_v2.py"}.issubset(result["daily_tests"])


def test_scope_cannot_drop_dependency_tests_or_rename_groups(tmp_path):
    result = scope.classify(["stock-fish/scripts/codex_daily_v2.py"])
    result["daily_tests"].remove("tests/unit/test_codex_daily_entry_v2.py")
    with pytest.raises(ValueError, match="RECOMPUTATION_MISMATCH"):
        scope.run_tests(result, scope.ROOT, tmp_path / "TEST_FIXTURE", tmp_path / "junit.xml")


def test_actual_git_diff_selects_source_specialists(tmp_path, monkeypatch, capsys):
    root = tmp_path / "TEST_FIXTURE_git"
    root.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    git("init", "--quiet")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "TEST_FIXTURE")
    for p in ("THIRD_PARTY_NOTICES.md", "stock-fish/LICENSE", "qlib/LICENSE", "stock-fish/MiroFish/LICENSE"):
        file = root / p
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("TEST_FIXTURE license placeholder\n")
    target = root / "stock-fish/scripts/codex_daily_v2.py"
    target.parent.mkdir()
    target.write_text("# TEST_FIXTURE initial\n")
    tests = root / "stock-fish/tests/unit"
    tests.mkdir(parents=True)
    (tests / "test_codex_daily_entry_v2.py").write_text("# TEST_FIXTURE\n")
    git("add", "--all")
    git("commit", "--quiet", "-m", "TEST_FIXTURE base")
    base = git("rev-parse", "HEAD")
    target.write_text("# TEST_FIXTURE source only change\n")
    git("add", "stock-fish/scripts/codex_daily_v2.py")
    git("commit", "--quiet", "-m", "TEST_FIXTURE source")
    head = git("rev-parse", "HEAD")
    output = tmp_path / "scope.json"
    assert scope.main(["--root", str(root), "--base", base, "--head", head, "--output", str(output)]) == 0
    value = json.loads(output.read_bytes())
    assert value["changed_paths"] == ["stock-fish/scripts/codex_daily_v2.py"]
    assert "tests/unit/test_codex_daily_entry_v2.py" in value["daily_tests"]
    assert value["head_sha"] == head


def test_real_workflow_uses_v2_selection_and_execution():
    raw = (scope.ROOT / ".github/workflows/engineering-gates.yml").read_text()
    assert "engineering_ci_scope_v1.py" not in raw
    assert "engineering_ci_scope_v2.py --run-scope" in raw
    assert "engineering_ci_scope_v2.py --base" in raw
    assert "engineering_ci_scope_v2.py --run-live" in raw
    assert 'shard: [0, 1, 2]' in raw
    assert '--shard-count 3 --partition-output' in raw


def test_complete_partitions_preserve_fixed_and_selected_modules_exactly_once():
    selected = scope.classify(["stock-fish/scripts/active_runtime_v1.py"])["daily_tests"]
    groups = [scope.partition_tests(selected, index, 3) for index in range(3)]
    flattened = [name for group in groups for name in group["selected_tests"]]
    assert sorted(flattened) == selected and len(flattened) == len(set(flattened))
    assert set(old.DAILY_TESTS).issubset(flattened)
    assert set(scope.MANDATORY).issubset(flattened)
    for missing in (old.DAILY_TESTS[0], "tests/unit/test_active_read_control_v1.py"):
        with pytest.raises(ValueError, match="COMPLETE_SAFETY_SET"):
            scope.partition_tests([name for name in selected if name != missing], 0, 3)


@pytest.mark.parametrize("index,count", [(-1, 3), (3, 3), (0, 0), (0, 9), (True, 3)])
def test_invalid_partition_cannot_silently_drop_tests(index, count):
    with pytest.raises(ValueError, match="PARTITION_BOUNDS"):
        scope.partition_tests(sorted(set(scope.MANDATORY)), index, count)


def test_runner_executes_exact_recomputed_partition_and_preserves_failure(tmp_path, monkeypatch):
    value = scope.classify(["stock-fish/scripts/codex_daily_v2.py"])
    calls = []
    def execute(argv, **kwargs):
        calls.append((argv, kwargs)); return 17
    monkeypatch.setattr(scope.subprocess, "call", execute)
    output = tmp_path / "partition.json"
    assert scope.run_tests(value, scope.ROOT, tmp_path / "TEST_FIXTURE", tmp_path / "junit.xml",
                           shard_index=1, shard_count=3, partition_output=output) == 17
    record = json.loads(output.read_bytes())
    assert calls[0][0][4:-5] == record["selected_tests"]
    assert record["complete_tests"] == value["daily_tests"]
    assert calls[0][1]["cwd"] == scope.ROOT
