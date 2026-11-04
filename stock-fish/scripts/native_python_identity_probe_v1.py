"""Read-only child/grandchild argv/cwd diagnostic; never alter process identity."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig

SCRIPT = Path(__file__).resolve()


def native_executable():
    if sys.platform == "darwin":
        function = ctypes.CDLL(None)._NSGetExecutablePath
        function.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32)]
        function.restype = ctypes.c_int
        size = ctypes.c_uint32(4096)
        buffer = ctypes.create_string_buffer(size.value)
        if function(buffer, ctypes.byref(size)) != 0:
            if size.value > 1024 * 1024:
                raise RuntimeError("NATIVE_EXECUTABLE_PATH_TOO_LONG")
            buffer = ctypes.create_string_buffer(size.value)
            if function(buffer, ctypes.byref(size)) != 0:
                raise RuntimeError("NATIVE_EXECUTABLE_PATH_UNAVAILABLE")
        return {"source": "_NSGetExecutablePath", "path": os.fsdecode(buffer.value)}
    if sys.platform.startswith("linux"):
        return {"source": "/proc/self/exe", "path": os.readlink("/proc/self/exe")}
    return {"source": "UNAVAILABLE", "path": None}


def observe():
    return {"orig_argv": list(sys.orig_argv), "executable": sys.executable,
            "base_executable": getattr(sys, "_base_executable", None),
            "cwd": str(Path.cwd()), "cwd_resolved": str(Path.cwd().resolve()),
            "pythonframework": sysconfig.get_config_var("PYTHONFRAMEWORK"),
            "python_version": sys.version.split()[0], "platform": sys.platform,
            "native_executable": native_executable()}


def launch(cwd, environment, depth):
    command = [sys.executable, "-I", "-B", str(SCRIPT), "--child-depth", str(depth)]
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True,
                            text=True, timeout=30, check=False)
    record = {"expected": {"orig_argv": command, "cwd": str(cwd)},
              "returncode": result.returncode, "stderr": result.stderr}
    if result.returncode:
        record.update(status="CHILD_FAILED", argv_exact=False, cwd_exact=False)
        return record
    observed = json.loads(result.stdout)
    record.update(observed=observed, argv_exact=observed["identity"]["orig_argv"] == command,
                  cwd_exact=observed["identity"]["cwd"] == str(cwd))
    exact = record["argv_exact"] and record["cwd_exact"]
    if "grandchild" in observed:
        exact = exact and observed["grandchild"]["status"] == "EXACT"
    record["status"] = "EXACT" if exact else "MISMATCH"
    return record


def probe(cwd):
    cwd = Path(cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("PROBE_CWD_MUST_BE_DIRECTORY")
    cases = {}
    for name, environment in (
            ("minimal", {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}),
            ("inherited", dict(os.environ))):
        cases[name] = launch(cwd, environment, 0)
    return {"schema": "NATIVE_PYTHON_IDENTITY_PROBE_V1", "parent": observe(), "cases": cases,
            "status": "EXACT" if all(row["status"] == "EXACT" for row in cases.values()) else "MISMATCH",
            "identity_modified": False, "runtime_acceptance": "NOT_CLAIMED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-exact", action="store_true",
                        help="Exit 3 on any child/grandchild mismatch; reporting alone grants no acceptance")
    parser.add_argument("--child-depth", type=int, choices=(0, 1), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child_depth is not None:
        result = {"identity": observe()}
        if args.child_depth == 0:
            result["grandchild"] = launch(Path.cwd(), dict(os.environ), 1)
    else:
        result = probe(args.cwd)
    encoded = json.dumps(result, sort_keys=True, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
    print(encoded)
    return 3 if args.require_exact and result.get("status") != "EXACT" else 0


if __name__ == "__main__":
    raise SystemExit(main())
