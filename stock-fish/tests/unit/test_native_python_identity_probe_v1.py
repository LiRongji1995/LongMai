"""TEST_FIXTURE: observe launcher differences without changing identity guards."""
import json
from pathlib import Path

import pytest

from scripts import native_python_identity_probe_v1 as probe


def test_real_child_and_grandchild_are_compared_without_normalization(tmp_path):
    cwd = (tmp_path / "TEST_FIXTURE_identity").resolve()
    cwd.mkdir()
    result = probe.probe(cwd)
    assert result["identity_modified"] is False
    assert result["runtime_acceptance"] == "NOT_CLAIMED"
    assert set(result["cases"]) == {"minimal", "inherited"}
    for case in result["cases"].values():
        assert case["returncode"] == 0
        assert case["argv_exact"] == (case["expected"]["orig_argv"] == case["observed"]["identity"]["orig_argv"])
        assert case["cwd_exact"] == (case["expected"]["cwd"] == case["observed"]["identity"]["cwd"])
        grandchild = case["observed"]["grandchild"]
        assert grandchild["returncode"] == 0
        assert grandchild["argv_exact"] == (grandchild["expected"]["orig_argv"] == grandchild["observed"]["identity"]["orig_argv"])
        exact = case["argv_exact"] and case["cwd_exact"] and grandchild["status"] == "EXACT"
        assert case["status"] == ("EXACT" if exact else "MISMATCH")
        assert case["observed"]["identity"]["native_executable"]["source"] in {"_NSGetExecutablePath", "/proc/self/exe", "UNAVAILABLE"}


def test_mismatch_is_recorded_and_require_exact_fails_closed(tmp_path, monkeypatch, capsys):
    observed = {"schema": "NATIVE_PYTHON_IDENTITY_PROBE_V1", "status": "MISMATCH"}
    monkeypatch.setattr(probe, "probe", lambda cwd: observed)
    output = tmp_path / "TEST_FIXTURE_identity.json"
    assert probe.main(["--output", str(output), "--require-exact"]) == 3
    assert json.loads(output.read_text()) == observed
    assert json.loads(capsys.readouterr().out) == observed
    with pytest.raises(FileExistsError):
        probe.main(["--output", str(output)])


def test_probe_never_prints_inherited_environment(tmp_path, monkeypatch):
    marker = "TEST_FIXTURE_SECRET_MUST_NOT_APPEAR"
    monkeypatch.setenv("PROBE_TEST_SECRET", marker)
    assert marker not in json.dumps(probe.probe(tmp_path))


def test_invalid_cwd_is_rejected_before_any_spawn(tmp_path):
    path = tmp_path / "TEST_FIXTURE_not_directory"
    path.write_text("fixture")
    with pytest.raises(ValueError, match="PROBE_CWD_MUST_BE_DIRECTORY"):
        probe.probe(path)
