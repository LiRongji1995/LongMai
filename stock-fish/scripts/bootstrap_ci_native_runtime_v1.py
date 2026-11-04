"""Create a new CI-only symlink venv using the unchanged daily runtime checks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import uuid
import venv

ROOT = Path(__file__).resolve().parents[1]
LEGACY = runpy.run_path(str(ROOT / "scripts/bootstrap_daily_runtime.py"))
PROBE_PATH = ROOT / "scripts/native_python_identity_probe_v1.py"
PROBE = runpy.run_path(str(PROBE_PATH))


class NativeSymlinkBuilder(venv.EnvBuilder):
    """Never use EnvBuilder's copy fallback when symlink creation fails."""

    def symlink_or_copy(self, src, dst, relative_symlinks_ok=False):
        if not self.symlinks:
            raise ValueError("CI_SYMLINK_BUILDER_REQUIRED")
        target = os.path.basename(src) if relative_symlinks_ok else src
        os.symlink(target, dst)


def native_source():
    identity = PROBE["native_executable"]()
    if sys.platform != "darwin" or identity["source"] != "_NSGetExecutablePath":
        raise ValueError("CI_NATIVE_MACH_O_REQUIRED")
    native = Path(identity["path"]).resolve(strict=True)
    if not native.is_file() or not os.access(native, os.X_OK):
        raise ValueError("CI_NATIVE_INTERPRETER_NOT_EXECUTABLE")
    if (not sys.flags.isolated or Path(sys.executable).resolve(strict=True) != native
            or Path(sys._base_executable).resolve(strict=True) != native
            or Path(sys.orig_argv[0]).resolve(strict=True) != native
            or any(name in os.environ for name in ("__PYVENV_LAUNCHER__", "PYTHONEXECUTABLE"))):
        raise ValueError("CI_DIRECT_NATIVE_INTERPRETER_REQUIRED")
    return native


def require_native_link(destination, native):
    python = destination / "bin/python"
    if not python.is_symlink() or python.resolve(strict=True) != native:
        raise ValueError("CI_NATIVE_SYMLINK_REQUIRED_NO_COPY_FALLBACK")


def create(destination):
    destination = destination.expanduser().absolute()
    if not destination.name.startswith("TEST_FIXTURE_"):
        raise ValueError("CI_TEST_FIXTURE_DESTINATION_REQUIRED")
    if destination.exists() or destination.is_symlink():
        raise ValueError("DESTINATION_ALREADY_EXISTS")
    manifest = LEGACY["validate_lock"]()
    host = LEGACY["native_identity"](manifest)
    native = native_source()
    native_hash = LEGACY["sha256"](native)
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    report_dir = destination / "acceptance" / uuid.uuid4().hex
    report_dir.mkdir(parents=True, mode=0o700)
    status = {"builder": "CI_NATIVE_SYMLINK_RUNTIME_V1", "contract": manifest["contract"],
              "status": "STARTED", "host": host, "native_executable": str(native),
              "native_executable_sha256": native_hash, "symlinks": True,
              "manifest_sha256": LEGACY["sha256"](LEGACY["MANIFEST"]),
              "destination": str(destination), "report_directory": str(report_dir),
              "production_switched": False, "qlib_installed_or_validated": False}
    try:
        NativeSymlinkBuilder(with_pip=True, system_site_packages=False, clear=False,
                             symlinks=True).create(destination)
        require_native_link(destination, native)
        pip = [destination / "bin/python", "-I", "-B", "-m", "pip", "--isolated",
               "--disable-pip-version-check", "install", "--no-cache-dir", "--index-url",
               manifest["index_url"], "--require-hashes"]
        LEGACY["execute"]([*pip, "--only-binary=:all:", "-r", LEGACY["LOCK_ROOT"] / "build-tools-py312.lock"],
                          report_dir, report_dir, "install-build-tools")
        LEGACY["execute"]([*pip, "--no-build-isolation", "-r", LEGACY["LOCK_ROOT"] / "daily-review-py312-macos-x86_64.lock"],
                          report_dir, report_dir, "install-runtime")
        status["runtime"] = LEGACY["verify"](destination, report_dir, manifest)
        require_native_link(destination, native)
        if (status["runtime"]["base_executable"] != str(native)
                or status["runtime"]["base_executable_sha256"] != native_hash):
            raise ValueError("CI_VERIFIED_NATIVE_IDENTITY_CHANGED")
        raw = LEGACY["execute"]([destination / "bin/python", "-I", "-B", PROBE_PATH,
                                  "--cwd", report_dir, "--output", report_dir / "native_identity_probe.json",
                                  "--require-exact"], report_dir, report_dir, "strict-native-identity", timeout=60)
        if json.loads(raw).get("status") != "EXACT":
            raise ValueError("CI_NATIVE_PROBE_NOT_EXACT")
        status["status"] = "ACCEPTED_TEST_FIXTURE_RUNTIME"
    except Exception as exc:
        status.update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        with (report_dir / "summary.json").open("x", encoding="utf-8") as stream:
            json.dump(status, stream, indent=2)
        print(json.dumps({key: status[key] for key in ("status", "destination", "report_directory")}), flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create",))
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    return create(args.destination)


if __name__ == "__main__":
    raise SystemExit(main())
