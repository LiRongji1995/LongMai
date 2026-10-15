"""Read-only, standard-library runtime inspection; never imports the application.

The parent reads only explicitly selected source/requirements files. Each package
is imported by the selected interpreter in a fresh, isolated child. The child
installs an audit guard before site initialization, including .pth processing.
This diagnoses an installed environment; it does not install or repair one.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


VERSION = "RUNTIME_DOCTOR_V1"
PROFILES = {
    "rules": ("requirements-rules.txt",),
    "daily": ("requirements-daily.txt",),
    "qlib": ("requirements-rules.txt", "requirements-qlib.txt"),
    "review": ("requirements-rules.txt", "requirements-review.txt"),
    "live": (),
}
MODULES = {"python-dotenv": ("dotenv",), "pydantic-settings": ("pydantic_settings",),
           "pyyaml": ("yaml",), "pyqlib": ("qlib",), "scikit-learn": ("sklearn",),
           "pydantic": ("pydantic", "pydantic_core._pydantic_core"),
           "pyarrow": ("pyarrow", "pyarrow.lib"), "duckdb": ("duckdb", "_duckdb")}
CORE_SOURCE = ("app.py", "stockfish_core/service.py", "stockfish_core/storage.py",
               "stockfish_core/yaogu/stage_aware_momentum.py",
               "stockfish_core/yaogu/short_horizon_strength.py",
               "stockfish_core/yaogu/short_horizon_participation.py")
CORE_CONFIG = ("config/stage_aware_momentum_v2.yaml", "config/short_horizon_strength_r1.yaml",
               "config/short_horizon_strength_r2.yaml")
LIVE_SOURCE = ("stockfish_live/cli.py", "stockfish_live/models/similarity.py",
               "stockfish_live/evaluation/walk_forward.py", "stockfish_live/storage/duckdb_store.py")

# No application or project paths are inserted into the probe's import path.
# -S is intentional: the audit hook must precede user site/.pth code. site.main()
# subsequently resolves the real venv prefix, including Python 3.12 venvs.
PROBE = r'''
import sys, os, json
sys.dont_write_bytecode = True
class GuardViolation(RuntimeError): pass
violations = []
capability_probes = []
def deny(code):
    violations.append(code)
    raise GuardViolation(code)
def guard(event, args):
    if event == "socket.__new__":
        # urllib3 checks IPv6 support during import and catches OSError. No
        # socket is created. A successful import may retain this explicit
        # diagnostic; it is not evidence of connectivity or a broken install.
        capability_probes.append("SOCKET_CREATION_BLOCKED")
        raise PermissionError("SOCKET_CREATION_BLOCKED")
    if event == "open":
        path, mode, flags = args
        if isinstance(path, (str, bytes)):
            names = (os.path.basename(os.fsdecode(path)).lower(), os.path.basename(os.path.realpath(os.fsdecode(path))).lower())
            for name in names:
                if name == ".env" or name.startswith(".env."):
                    deny("ENV_FILE_READ_DENIED")
                if name.endswith((".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm")):
                    deny("DATABASE_FILE_READ_DENIED")
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            deny("FILESYSTEM_WRITE_DENIED")
    if event.startswith(("socket.", "sqlite3.connect", "subprocess.", "os.exec", "os.spawn")):
        deny("NETWORK_DATABASE_OR_PROCESS_DENIED")
    if event in ("os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.chmod", "os.chown", "os.link",
                 "os.symlink", "os.truncate", "os.utime", "os.system", "os.fork", "os.forkpty"):
        deny("FILESYSTEM_OR_PROCESS_MUTATION_DENIED")
    if event == "import" and args[0] in ("app", "stockfish_core.service", "stockfish_core.api", "stockfish_live.cli", "stockfish_live.api.app"):
        deny("APPLICATION_IMPORT_DENIED")
sys.addaudithook(guard)
request = json.loads(sys.stdin.read())
class Sink:
    def write(self, value): return len(value)
    def flush(self): pass
    def isatty(self): return False
saved_out, saved_err = sys.stdout, sys.stderr
sys.stdout = sys.stderr = Sink()
result = {"ok": False}
try:
    import site
    site.main()
    import importlib, importlib.metadata, platform, struct
    result["runtime"] = {"executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "python_version": platform.python_version(), "implementation": platform.python_implementation(),
        "machine": platform.machine(), "pointer_bits": struct.calcsize("P") * 8,
        "isolated": bool(sys.flags.isolated), "bytecode_disabled": sys.dont_write_bytecode,
        "user_site_enabled": site.ENABLE_USER_SITE}
    if request["action"] == "identity":
        result["ok"] = True
    else:
        name = request["distribution"]
        dist = importlib.metadata.distribution(name)
        info = {"name": name, "version": dist.version,
            "location": str(dist.locate_file("")), "wheel_metadata_present": dist.read_text("WHEEL") is not None,
            "record_present": dist.read_text("RECORD") is not None,
            "editable": False, "direct_url_present": False}
        direct = dist.read_text("direct_url.json")
        if direct:
            parsed = json.loads(direct)
            info.update(direct_url_present=True, editable=parsed.get("dir_info", {}).get("editable") is True)
        result["distribution"] = info
        modules = []
        for name in request["modules"]:
            module = importlib.import_module(name)
            modules.append({"name": name, "origin": getattr(module, "__file__", None)})
        result["modules"] = modules
        if request["distribution"].lower() == "pyqlib":
            expected = os.path.realpath(str(dist.locate_file("qlib/__init__.py")))
            origin = os.path.realpath(modules[0]["origin"] or "")
            result["qlib_origin_matches_distribution"] = origin == expected
        result["ok"] = True
except BaseException as exc:
    result.update(ok=False, error_type=type(exc).__name__)
finally:
    sys.stdout, sys.stderr = saved_out, saved_err
result["guard_violations"] = sorted(set(violations))
result["blocked_capability_probes"] = sorted(set(capability_probes))
if violations:
    result["ok"] = False
print(json.dumps(result, sort_keys=True, allow_nan=False))
'''


def normalized_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def file_identity(path, label):
    _allow_inspection_file(path)
    raw = path.read_bytes()
    return {"path": label, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _allow_inspection_file(path):
    for name in (Path(path).name.lower(), Path(path).resolve().name.lower()):
        if name == ".env" or name.startswith(".env."):
            raise ValueError("ENV_FILE_READ_DENIED")
        if name.endswith((".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm")):
            raise ValueError("DATABASE_FILE_READ_DENIED")


def parse_requirement(value):
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)([<>=!0-9., ]*)", value.strip())
    if not match:
        raise ValueError("UNSUPPORTED_REQUIREMENT_SYNTAX")
    name, spec = normalized_name(match[1]), match[2].replace(" ", "")
    if spec and any(not re.fullmatch(r"(==|>=|<=|>|<|!=)\d+(?:\.\d+)*", part) for part in spec.split(",")):
        raise ValueError("UNSUPPORTED_REQUIREMENT_SPECIFIER")
    return name, spec


def version_satisfies(version, spec):
    """Fail closed outside the numeric release versions used by these profiles."""
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        return False
    if not spec:
        return True
    actual = tuple(map(int, version.split(".")))
    for part in spec.split(","):
        match = re.fullmatch(r"(==|>=|<=|>|<|!=)(\d+(?:\.\d+)*)", part)
        if not match:
            return False
        expected = tuple(map(int, match[2].split(".")))
        width = max(len(actual), len(expected))
        left, right = actual + (0,) * (width-len(actual)), expected + (0,) * (width-len(expected))
        if not {"==": left == right, "!=": left != right, ">=": left >= right, "<=": left <= right,
                ">": left > right, "<": left < right}[match[1]]:
            return False
    return True


def requirements(root, profile, live_root):
    found, files, visited = {}, [], set()
    def add(line, source):
        name, spec = parse_requirement(line)
        previous = found.get(name)
        if previous and previous["specifier"] != spec:
            raise ValueError("CONFLICTING_REQUIREMENT_PINS")
        found[name] = {"distribution": name, "specifier": spec, "source": source, "declared": True}
    def read(path, active=()):
        path = path.resolve()
        if not path.is_relative_to(root) or path in active:
            raise ValueError("REQUIREMENT_INCLUDE_OUTSIDE_ROOT_OR_CYCLE")
        if path in visited:
            return
        visited.add(path)
        label = str(path.relative_to(root))
        files.append(file_identity(path, label))
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("-r "):
                read(path.parent / line[3:].strip(), (*active, path))
            else:
                add(line, label)
    if profile == "live":
        import tomllib
        path = live_root / "pyproject.toml"
        files.append(file_identity(path, "live/pyproject.toml"))
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        python_spec = project["requires-python"]
        for requirement in project["dependencies"]:
            add(requirement, "live/pyproject.toml")
    else:
        python_spec = ">=3.12,<3.13"
        for filename in PROFILES[profile]:
            read(root / filename)
    if profile == "daily" and "psutil" not in found:
        found["psutil"] = {"distribution": "psutil", "specifier": "", "declared": False,
                           "source": "runtime: scheduler process identity and bounded process tree"}
    return sorted(found.values(), key=lambda row: row["distribution"]), files, python_spec


def source_identity(root, profile, live_root):
    base = live_root if profile == "live" else root
    source = LIVE_SOURCE if profile == "live" else CORE_SOURCE
    config = ("pyproject.toml",) if profile == "live" else CORE_CONFIG
    result = {"scope": "SELECTED_SOURCE_CONFIG_SCHEMA_SUMMARY_NOT_FULL_BUILD_CLOSURE",
              "source": [file_identity(base / name, name) for name in source],
              "config": [file_identity(base / name, name) for name in config], "schema": None}
    if profile != "live":
        path = root / "stockfish_core/storage.py"
        syntax = ast.parse(path.read_text(encoding="utf-8"))
        values = [ast.literal_eval(node.value) for node in syntax.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "_SCHEMA" for target in node.targets)]
        if len(values) != 1 or not isinstance(values[0], str):
            raise ValueError("SCHEMA_LITERAL_UNAVAILABLE")
        result["schema"] = {"source": "stockfish_core/storage.py:_SCHEMA", "database_opened": False,
                            "sha256": hashlib.sha256(values[0].encode()).hexdigest()}
    result["summary_hash"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def resolve_python(explicit=None, locator=None):
    if locator:
        path = Path(locator).expanduser().absolute()
        _allow_inspection_file(path)
        if path.stat().st_size > 4096:
            raise ValueError("LOCATOR_TOO_LARGE")
        raw = path.read_text(encoding="utf-8").strip()
        if not raw or "\n" in raw or "\r" in raw or "\x00" in raw:
            raise ValueError("INVALID_PYTHON_LOCATOR")
        chosen = Path(raw).expanduser()
        executable = str(chosen if chosen.is_absolute() else path.parent / chosen)
    else:
        executable = explicit or sys.executable
    located = shutil.which(executable)
    if located is None:
        raise ValueError("PYTHON_EXECUTABLE_UNAVAILABLE")
    # Do not resolve venv executable symlinks into the base interpreter.
    return str(Path(located).absolute())


def probe(executable, request, timeout):
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    try:
        result = subprocess.run([executable, "-I", "-S", "-B", "-c", PROBE], input=json.dumps(request),
                                capture_output=True, text=True, timeout=timeout, env=env)
        if result.returncode:
            return {"ok": False, "error_type": "PROBE_PROCESS_FAILED", "exit_code": result.returncode}
        if len(result.stdout) > 1024*1024:
            return {"ok": False, "error_type": "PROBE_OUTPUT_LIMIT"}
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or type(value.get("ok")) is not bool or not isinstance(value.get("guard_violations"), list):
            raise ValueError("invalid probe protocol")
        return value
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_type": "PROBE_TIMEOUT"}
    except (OSError, ValueError):
        return {"ok": False, "error_type": "PROBE_UNAVAILABLE_OR_INVALID_OUTPUT"}


def inspect_runtime(root, profile, executable, *, live_root=None, timeout=15., budget_seconds=90., probe_runner=probe):
    start = time.monotonic()
    root = Path(root).resolve()
    live_root = Path(live_root).resolve() if live_root else root.parent / "stockfish-live"
    report = {"schema_version": VERSION, "profile": profile, "root": str(root), "requested_python": executable,
              "read_only": True, "application_imported": False, "database_opened": False, "network_used": False,
              "issues": [], "warnings": [], "packages": []}
    def run(request):
        remaining = budget_seconds - (time.monotonic()-start)
        return probe_runner(executable, request, min(timeout, remaining)) if remaining > 0 else {"ok": False, "error_type": "TOTAL_PROBE_BUDGET_EXHAUSTED"}
    try:
        expected, report["requirement_files"], python_spec = requirements(root, profile, live_root)
        report["source_identity"] = source_identity(root, profile, live_root)
        report["python_requirement"] = python_spec
    except (OSError, ValueError, KeyError, SyntaxError, ImportError) as exc:
        report["issues"].append({"code": "PROFILE_OR_SOURCE_IDENTITY_UNAVAILABLE", "error_type": type(exc).__name__})
        report.update(status="FAIL", exit_code=1)
        return report
    identity = run({"action": "identity"})
    report["runtime_probe"] = identity
    report["doctor_source"] = file_identity(Path(__file__), "scripts/runtime_doctor.py")
    if not identity.get("ok"):
        report["issues"].append({"code": "RUNTIME_PROBE_FAILED"})
    elif not version_satisfies(identity.get("runtime", {}).get("python_version", ""), python_spec):
        report["issues"].append({"code": "PYTHON_VERSION_NOT_SATISFIED"})
    for required in expected:
        name = required["distribution"]
        check = run({"action": "import", "distribution": name, "modules": MODULES.get(name, (name.replace("-", "_"),))})
        codes = []
        actual = check.get("distribution", {}).get("version", "")
        if not check.get("ok"):
            codes.append("ACTUAL_IMPORT_OR_METADATA_FAILED")
        if identity.get("ok") and check.get("ok") and any(check.get("runtime", {}).get(key) != identity.get("runtime", {}).get(key)
                for key in ("executable", "prefix", "base_prefix", "python_version", "machine", "pointer_bits")):
            codes.append("RUNTIME_IDENTITY_CHANGED_BETWEEN_PROBES")
        if not version_satisfies(actual, required["specifier"]):
            codes.append("PACKAGE_VERSION_NOT_SATISFIED")
        if name == "pyqlib":
            dist = check.get("distribution", {})
            if (actual != "0.9.7" or dist.get("editable") is not False or not dist.get("wheel_metadata_present")
                    or not dist.get("record_present") or check.get("qlib_origin_matches_distribution") is not True):
                codes.append("PYQLIB_097_WHEEL_IDENTITY_REQUIRED")
        if not required["declared"]:
            report["warnings"].append({"code": "RUNTIME_DEPENDENCY_NOT_DECLARED_IN_PROFILE_REQUIREMENTS", "distribution": name})
        if check.get("blocked_capability_probes"):
            report["warnings"].append({"code": "IMPORT_SOCKET_CAPABILITY_PROBE_BLOCKED_NO_CONNECTIVITY_TEST", "distribution": name})
        report["packages"].append({**required, "probe": check, "status": "FAIL" if codes else "PASS", "reason_codes": codes})
    if any(item["status"] == "FAIL" for item in report["packages"]):
        report["issues"].append({"code": "PROFILE_DEPENDENCIES_NOT_SATISFIED"})
    report.update(status="FAIL" if report["issues"] else "PASS", exit_code=1 if report["issues"] else 0,
                  elapsed_seconds=round(time.monotonic()-start, 3))
    return report


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 600:
        raise argparse.ArgumentTypeError("seconds must be finite and in (0, 600]")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="rules")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="business stock-fish directory")
    parser.add_argument("--live-root", type=Path, help="Live project directory; default sibling stockfish-live")
    interpreter = parser.add_mutually_exclusive_group()
    interpreter.add_argument("--python", help="explicit installed interpreter, default current executable")
    interpreter.add_argument("--python-locator", type=Path, help="explicit UTF-8 file containing one interpreter path; no automatic discovery")
    parser.add_argument("--timeout", type=positive_seconds, default=15., help="maximum seconds per isolated import")
    parser.add_argument("--budget-seconds", type=positive_seconds, default=90., help="total isolated import budget")
    parser.add_argument("--json", action="store_true", help="write the complete structured report to stdout")
    args = parser.parse_args(argv)
    try:
        executable = resolve_python(args.python, args.python_locator)
        report = inspect_runtime(args.root, args.profile, executable, live_root=args.live_root,
                                 timeout=args.timeout, budget_seconds=args.budget_seconds)
    except (OSError, ValueError) as exc:
        report = {"schema_version": VERSION, "profile": args.profile, "status": "FAIL", "exit_code": 1,
                  "issues": [{"code": "INTERPRETER_SELECTION_FAILED", "error_type": type(exc).__name__}]}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"{VERSION} {args.profile}: {report['status']}")
        runtime = report.get("runtime_probe", {}).get("runtime", {})
        if runtime:
            print(f"Python {runtime['python_version']} | {runtime['machine']} {runtime['pointer_bits']}bit | {runtime['executable']}")
        for item in report.get("packages", []):
            print(f"{item['distribution']}{item['specifier']}: {item['status']} ({', '.join(item['reason_codes']) or 'import verified'})")
        for issue in report.get("issues", []) + report.get("warnings", []):
            print(issue["code"])
        print("Read-only inspection; no application, database, network, installation or repair. Use --json for origins and identities.")
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
