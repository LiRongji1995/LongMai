"""TEST_FIXTURE: new CI symlinks preserve frozen checks and retain failures."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import bootstrap_ci_native_runtime_v1 as builder


@pytest.fixture
def construction(tmp_path, monkeypatch):
    native = tmp_path / "TEST_FIXTURE_native"
    native.write_bytes(b"TEST_FIXTURE_NOT_EXECUTED")
    native.chmod(0o700)
    calls = []
    manifest = {"contract": "DAILY_REVIEW_NATIVE_V1", "index_url": "https://pypi.org/simple"}
    legacy = dict(builder.LEGACY)
    legacy["validate_lock"] = lambda: calls.append("validate-lock") or manifest
    legacy["native_identity"] = lambda value: calls.append("native-identity") or {"manifest": value}
    legacy["sha256"] = lambda path: "TEST_FIXTURE_NATIVE_HASH"

    def execute(argv, directory, report, label, **kwargs):
        calls.append((label, [str(value) for value in argv], kwargs))
        return json.dumps({"status": "EXACT"})

    legacy["execute"] = execute
    legacy["verify"] = lambda destination, report, value: calls.append("full-original-verify") or {
        "base_executable": str(native), "base_executable_sha256": "TEST_FIXTURE_NATIVE_HASH"}

    def env_builder(**kwargs):
        calls.append(("env-builder", kwargs))

        def create(destination):
            (destination / "bin").mkdir()
            (destination / "bin/python").symlink_to(native)

        return SimpleNamespace(create=create)

    monkeypatch.setattr(builder, "LEGACY", legacy)
    monkeypatch.setattr(builder, "native_source", lambda: native)
    monkeypatch.setattr(builder, "NativeSymlinkBuilder", env_builder)
    destination = tmp_path / "TEST_FIXTURE_runtime"
    return destination, calls, legacy, native


def summary(destination):
    paths = list(destination.glob("acceptance/*/summary.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_new_entry_keeps_complete_hash_install_verify_then_strict_probe(construction):
    destination, calls, _, _ = construction
    assert builder.main(["create", "--destination", str(destination)]) == 0
    assert calls[:2] == ["validate-lock", "native-identity"]
    assert calls[2] == ("env-builder", {"with_pip": True, "system_site_packages": False,
                                      "clear": False, "symlinks": True})
    assert [item[0] if isinstance(item, tuple) else item for item in calls[3:]] == [
        "install-build-tools", "install-runtime", "full-original-verify", "strict-native-identity"]
    for index, lock in ((3, "build-tools-py312.lock"), (4, "daily-review-py312-macos-x86_64.lock")):
        argv = calls[index][1]
        assert argv[:7] == [str(destination / "bin/python"), "-I", "-B", "-m", "pip", "--isolated",
                           "--disable-pip-version-check"]
        assert "--require-hashes" in argv and "--no-cache-dir" in argv
        assert argv[-2:] == ["-r", str(builder.LEGACY["LOCK_ROOT"] / lock)]
    assert "--only-binary=:all:" in calls[3][1]
    assert "--no-build-isolation" in calls[4][1]
    assert calls[-1][1][-1] == "--require-exact"
    assert summary(destination)["status"] == "ACCEPTED_TEST_FIXTURE_RUNTIME"
    assert summary(destination)["production_switched"] is False


@pytest.mark.parametrize("stage", ["install-build-tools", "install-runtime", "verify", "strict-native-identity"])
def test_every_failure_retains_new_directory_and_cannot_be_retried(construction, stage):
    destination, calls, legacy, _ = construction
    original_execute = legacy["execute"]

    def execute(argv, directory, report, label, **kwargs):
        if label == stage:
            calls.append(("failed", label))
            raise RuntimeError("TEST_FIXTURE_STEP_FAILURE")
        return original_execute(argv, directory, report, label, **kwargs)

    legacy["execute"] = execute
    if stage == "verify":
        legacy["verify"] = lambda *args: (_ for _ in ()).throw(RuntimeError("TEST_FIXTURE_STEP_FAILURE"))
    with pytest.raises(RuntimeError, match="TEST_FIXTURE_STEP_FAILURE"):
        builder.main(["create", "--destination", str(destination)])
    assert summary(destination)["status"] == "FAILED"
    before = list(calls)
    with pytest.raises(ValueError, match="DESTINATION_ALREADY_EXISTS"):
        builder.main(["create", "--destination", str(destination)])
    assert calls == before
    assert destination.exists()


def test_success_exit_cannot_replace_exact_probe_or_native_hash(construction):
    destination, _, legacy, native = construction
    legacy["verify"] = lambda *args: {"base_executable": str(native), "base_executable_sha256": "CHANGED"}
    with pytest.raises(ValueError, match="CI_VERIFIED_NATIVE_IDENTITY_CHANGED"):
        builder.create(destination)
    assert summary(destination)["status"] == "FAILED"


def test_zero_exit_with_mismatch_is_failed(construction):
    destination, _, legacy, _ = construction
    legacy["execute"] = lambda *args, **kwargs: json.dumps({"status": "MISMATCH"})
    with pytest.raises(ValueError, match="CI_NATIVE_PROBE_NOT_EXACT"):
        builder.create(destination)
    assert summary(destination)["status"] == "FAILED"


def test_venv_construction_failure_is_retained_and_no_install_starts(construction, monkeypatch):
    destination, calls, _, _ = construction

    def fail(destination):
        raise subprocess.CalledProcessError(-9, [str(destination / "bin/Python"), "-m", "ensurepip"])

    monkeypatch.setattr(builder, "NativeSymlinkBuilder", lambda **kwargs: SimpleNamespace(create=fail))
    with pytest.raises(subprocess.CalledProcessError) as error:
        builder.create(destination)
    assert error.value.returncode == -9
    assert calls == ["validate-lock", "native-identity"]
    value = summary(destination)
    assert value["status"] == "FAILED" and value["error_type"] == "CalledProcessError"
    assert "SIGKILL" in value["error"]
    with pytest.raises(ValueError, match="DESTINATION_ALREADY_EXISTS"):
        builder.create(destination)


def test_new_runtime_cannot_use_copy_fallback(construction, monkeypatch):
    destination, _, _, native = construction

    def copied(destination):
        (destination / "bin").mkdir()
        (destination / "bin/python").write_bytes(native.read_bytes())

    monkeypatch.setattr(builder, "NativeSymlinkBuilder", lambda **kwargs: SimpleNamespace(create=copied))
    with pytest.raises(ValueError, match="CI_NATIVE_SYMLINK_REQUIRED_NO_COPY_FALLBACK"):
        builder.create(destination)
    assert summary(destination)["status"] == "FAILED"


def test_symlink_failure_never_copies_or_retries(tmp_path, monkeypatch):
    source = tmp_path / "TEST_FIXTURE_source"
    source.write_text("must not be copied")
    destination = tmp_path / "TEST_FIXTURE_target"
    attempts = []

    def fail(*args):
        attempts.append(args)
        raise OSError("TEST_FIXTURE_SYMLINK_DENIED")

    monkeypatch.setattr(builder.os, "symlink", fail)
    with pytest.raises(OSError, match="TEST_FIXTURE_SYMLINK_DENIED"):
        builder.NativeSymlinkBuilder(symlinks=True).symlink_or_copy(source, destination)
    assert len(attempts) == 1 and not destination.exists()


def test_real_new_symlink_venv_has_exact_children_prefix_and_old_installed_identity(tmp_path):
    destination = tmp_path / "TEST_FIXTURE_empty_runtime"
    builder.NativeSymlinkBuilder(with_pip=False, system_site_packages=False, clear=False,
                                 symlinks=True).create(destination)
    assert (destination / "bin/python").is_symlink()
    environment = builder.LEGACY["clean_environment"]()
    environment.pop("__PYVENV_LAUNCHER__", None)
    environment.pop("PYTHONEXECUTABLE", None)
    result = subprocess.run([str(destination / "bin/python"), "-I", "-B", str(builder.PROBE_PATH),
                             "--cwd", str(tmp_path), "--require-exact"], env=environment,
                            capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout)["status"] == "EXACT"
    source = "import runpy,json; print(json.dumps(runpy.run_path(" + repr(str(builder.ROOT / "scripts/bootstrap_daily_runtime.py")) + ")[\"installed_identity\"]()))"
    result = subprocess.run([str(destination / "bin/python"), "-I", "-B", "-c", source], env=environment,
                            capture_output=True, text=True, timeout=30, check=True)
    identity = json.loads(result.stdout)
    assert identity["prefix"] == str(destination.resolve())
    assert identity["isolated"] and not identity["user_site_enabled"]
    assert identity["distributions"] == []


def test_real_cli_refuses_existing_destination_without_touching_it(tmp_path):
    destination = tmp_path / "TEST_FIXTURE_existing"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("TEST_FIXTURE_PRESERVE")
    result = subprocess.run([sys.executable, "-I", "-B", builder.__file__, "create", "--destination", str(destination)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and "DESTINATION_ALREADY_EXISTS" in result.stderr
    assert list(destination.iterdir()) == [marker]
    assert marker.read_text() == "TEST_FIXTURE_PRESERVE"


def test_actual_native_host_binding_preserves_original_argv():
    native = Path(sys._base_executable).resolve(strict=True)
    environment = builder.LEGACY["clean_environment"]()
    environment.pop("__PYVENV_LAUNCHER__", None)
    environment.pop("PYTHONEXECUTABLE", None)
    source = ("import runpy,sys,json; before=list(sys.orig_argv); m=runpy.run_path("
              + repr(builder.__file__) + "); native=m[\"native_source\"](); "
              "print(json.dumps({\"native\":str(native),\"unchanged\":before==sys.orig_argv}))")
    result = subprocess.run([str(native), "-I", "-B", "-c", source], env=environment,
                            capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout) == {"native": str(native), "unchanged": True}


@pytest.mark.parametrize("kind", ["ordinary", "dangling"])
def test_non_fixture_and_existing_dangling_destination_rejected(tmp_path, kind):
    destination = tmp_path / ("ordinary" if kind == "ordinary" else "TEST_FIXTURE_dangling")
    if kind == "dangling":
        destination.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="CI_TEST_FIXTURE_DESTINATION_REQUIRED|DESTINATION_ALREADY_EXISTS"):
        builder.create(destination)
