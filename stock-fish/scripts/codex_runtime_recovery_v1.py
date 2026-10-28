"""Explicit runtime recovery epochs; no old identity or source authority is repaired.

The original registry, default, configuration and operation journal remain intact.
Recovered runtimes use a distinct registry schema so an unaware V1 loader fails
closed instead of bypassing ancestor-request checks.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import types
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_workspace as w

VERSION = "CODEX_RUNTIME_RECOVERY_V1"
ENV_VERSION = "CODEX_RUNTIME_REACCEPTANCE_V1"
MAX_FILES = 10000
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
need = w._need


def _fp(info):
    return [getattr(info, key, 0) for key in FP_KEYS]


def reference(path, maximum=64 * 1024**2):
    """Full SHA and seven physical fields, without following directory aliases."""
    path = Path(path).expanduser().absolute()
    with w._directory_fd(path.parent) as parent:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and not getattr(before, "st_flags", 0) & 0x40000000,
             "RECOVERY_RESIDENT_SINGLE_LINK_FILE_REQUIRED")
        need(before.st_size <= maximum, "RECOVERY_FILE_SIZE_LIMIT")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            need(_fp(os.fstat(fd)) == _fp(before), "RECOVERY_FILE_CHANGED")
            digest = hashlib.sha256()
            with os.fdopen(fd, "rb", closefd=False) as stream:
                remaining = before.st_size
                while remaining:
                    raw = stream.read(min(65536, remaining))
                    need(bool(raw), "RECOVERY_FILE_CHANGED")
                    digest.update(raw)
                    remaining -= len(raw)
                need(not stream.read(1), "RECOVERY_FILE_CHANGED")
            need(_fp(before) == _fp(os.fstat(fd)) == _fp(os.stat(
                path.name, dir_fd=parent, follow_symlinks=False)), "RECOVERY_FILE_CHANGED")
        finally:
            os.close(fd)
    return {"path": str(path), "sha256": digest.hexdigest(), "fp": _fp(before)}


def _verify_refs(refs):
    for ref in refs:
        need(reference(ref["path"]) == ref, "RECOVERY_HISTORY_OR_ACCEPTANCE_CHANGED")


def _read(path):
    ref = reference(path)
    value = w._read(path, limit=4 * 1024**2)
    need(reference(path) == ref, "RECOVERY_CONTROL_CHANGED")
    return value, ref


def _fresh_python_command(argv, python, directory):
    need(isinstance(argv, (list, tuple)) and argv and Path(argv[0]).absolute() == Path(python).absolute(),
         "RECOVERY_FOREIGN_CHILD_INTERPRETER")
    absent = Path(directory) / ("absent-bytecode-" + uuid.uuid4().hex)
    need(not absent.exists(), "RECOVERY_BYTECODE_PATH_MUST_BE_ABSENT")
    return [str(argv[0]), "-X", "pycache_prefix=" + str(absent), *map(str, argv[1:])], absent


def _environment_child(script, report, label, arguments):
    """Give the frozen doctor's/smoke's own Python children fresh -X paths too."""
    script = Path(script).absolute()
    need(script.name in ("runtime_doctor.py", "runtime_environment_smoke.py"),
         "RECOVERY_VERIFICATION_CHILD_REQUIRED")
    namespace = runpy.run_path(str(script))
    original_run = subprocess.run
    proxy = types.SimpleNamespace(**vars(subprocess))
    def run(argv, *args, **kwargs):
        command, absent = _fresh_python_command(argv, sys.executable, report)
        result = original_run(command, *args, **kwargs)
        need(not absent.exists(), "RECOVERY_CHILD_WROTE_BYTECODE")
        w._write(Path(report) / ("nested-" + label + "-" + uuid.uuid4().hex + ".json"),
                 {"argv": command, "exit_code": result.returncode, "status": "COMPLETED"})
        return result
    proxy.run = run
    namespace["main"].__globals__["subprocess"] = proxy
    return namespace["main"](arguments)


def verify_environment(python, destination, report_dir, source_root=None, *,
                       _fixture_verifier=None, allow_test_fixture=False):
    """Re-run the original verifier with fresh bytecode paths for every child.

    Only a new report directory and isolated TEST_FIXTURE smoke artifacts are
    written. No package is installed and no runtime registry is changed.
    """
    source = Path(source_root or ROOT).resolve(strict=True)
    destination = Path(destination).resolve(strict=True)
    python = Path(python).expanduser().absolute()
    need(python == destination / "bin/python", "RECOVERY_EXACT_VENV_INVOCATION_REQUIRED")
    need((destination / "pyvenv.cfg").is_file(), "RECOVERY_EXISTING_VENV_REQUIRED")
    report = Path(report_dir).expanduser().absolute()
    need(not report.exists() and not report.is_symlink(), "RECOVERY_REPORT_EXISTS_PRESERVED")
    w._directory(report.parent)
    sources = [reference(source / "scripts" / name) for name in (
        "bootstrap_daily_runtime.py", "runtime_doctor.py", "runtime_environment_smoke.py")]
    sources.append(reference(Path(__file__).resolve()))
    executable = reference(python.resolve(strict=True))
    report.mkdir(mode=0o700)
    fixture = _fixture_verifier is not None
    need(not fixture or (allow_test_fixture and "TEST_FIXTURE" in str(report)),
         "RECOVERY_EXPLICIT_TEST_FIXTURE_REQUIRED")
    result = {"schema_version": ENV_VERSION, "status": "STARTED", "started_at": w._stamp(),
              "python": str(python), "destination": str(destination), "source_root": str(source),
              "executable": executable, "source_refs": sources, "test_fixture": fixture,
              "registry_changed": False, "packages_installed": False,
              "source_authority_restored": False, "steps": [], "nested_steps": []}
    try:
        if fixture:
            identity = _fixture_verifier(result, report)
        else:
            namespace = runpy.run_path(str(source / "scripts/bootstrap_daily_runtime.py"))
            manifest = namespace["validate_lock"]()
            result["manifest"] = reference(namespace["MANIFEST"])
            result["lock_refs"] = [reference(source / name) for name in manifest["source_requirements"]]
            result["lock_refs"] += [reference(Path(namespace["MANIFEST"]).parent / name)
                                    for name in manifest["locks"]]
            original_execute = namespace["execute"]
            def execute(argv, directory, reports, label, **kwargs):
                argv = list(argv)
                if len(argv) > 3 and Path(str(argv[3])).name in ("runtime_doctor.py", "runtime_environment_smoke.py"):
                    argv = [*argv[:3], str(Path(__file__).resolve()), "--environment-child",
                            str(argv[3]), str(report), label, *argv[4:]]
                command, absent = _fresh_python_command(argv, python, report)
                value = original_execute(command, directory, reports, label, **kwargs)
                need(not absent.exists(), "RECOVERY_CHILD_WROTE_BYTECODE")
                result["steps"].append(reference(report / (label + ".json")))
                result["nested_steps"].extend(reference(p) for p in sorted(report.glob("nested-" + label + "-*.json")))
                return value
            private = dict(namespace, execute=execute)
            verify = types.FunctionType(namespace["verify"].__code__, private)
            probe = "import runpy,json; n=runpy.run_path(" + repr(str(source / "scripts/bootstrap_daily_runtime.py")) + "); print(json.dumps(n['native_identity'](n['validate_lock']())))"
            result["native_identity"] = json.loads(execute([python, "-I", "-B", "-c", probe],
                                                           report, report, "native-identity"))
            identity = verify(destination, report, manifest)
            need(len(result["steps"]) == 7, "RECOVERY_ENVIRONMENT_STEPS_INCOMPLETE")
        result["runtime_identity"] = identity
        _verify_refs([executable, *sources, *result.get("lock_refs", []),
                      *([result["manifest"]] if "manifest" in result else []), *result["steps"], *result["nested_steps"]])
        result.update(status="ACCEPTED", completed_at=w._stamp())
    except BaseException:
        result.update(status="FAILED", completed_at=w._stamp())
        raise
    finally:
        w._write(report / "environment-reacceptance.json", result)
    return reference(report / "environment-reacceptance.json")


def _environment(ref, profile, *, allow_test_fixture=False):
    _verify_refs([ref])
    value, current = _read(ref["path"])
    need(current == ref and value.get("schema_version") == ENV_VERSION
         and value.get("status") == "ACCEPTED", "RECOVERY_ACCEPTED_ENVIRONMENT_REQUIRED")
    need(not value.get("test_fixture") or allow_test_fixture, "RECOVERY_TEST_FIXTURE_NOT_PRODUCTION")
    need(value["python"] == profile["python"] and value["source_root"] == profile["source_root"],
         "RECOVERY_ENVIRONMENT_LOCATION_MISMATCH")
    _verify_refs([value["executable"], *value["source_refs"], *value.get("lock_refs", []),
                  *([value["manifest"]] if "manifest" in value else []), *value["steps"], *value.get("nested_steps", [])])
    for step in [*value["steps"], *value.get("nested_steps", [])]:
        record, _ = _read(step["path"])
        need(record.get("exit_code") == 0 and record.get("status") == "COMPLETED",
             "RECOVERY_ENVIRONMENT_STEP_FAILED")
    need(value.get("test_fixture") or len(value["steps"]) == 7,
         "RECOVERY_ENVIRONMENT_STEPS_INCOMPLETE")


def _operation_paths(directory):
    directory = Path(directory)
    if not directory.exists():
        need(not directory.is_symlink(), "RECOVERY_OPERATION_DIRECTORY_ALIAS")
        return []
    w._directory(directory)
    paths = sorted(directory.glob("*.json"))
    need(len(paths) <= MAX_FILES, "RECOVERY_OPERATION_COUNT_LIMIT")
    return paths


def _history(registry, old_name):
    saved, registry_ref = _read(registry / "runtimes" / (w._name(old_name) + ".json"))
    need(saved.get("schema_version") == w.VERSION,
         "RECOVERY_REPEATED_EPOCH_REQUIRES_EXPLICIT_CHAIN_PROTOCOL")
    profile = w.validate_profile(saved["profile"])
    need(profile["backend"] == "premarket" and profile["name"] == old_name,
         "RECOVERY_PREMARKET_ORIGINAL_REQUIRED")
    old = w.Runtime(registry, profile, saved["binding"])
    directory = old.directory / "operations"
    operations = []
    for path in _operation_paths(directory):
        record, ref = _read(path)
        need(record.get("schema_version") == w.VERSION and record.get("scope") == old.scope
             and record.get("operation_id") == path.stem and path.stem == w._digest(
                 {"scope": old.scope, "identity": record.get("identity")}),
             "RECOVERY_ANCESTOR_OPERATION_INVALID")
        operations.append({"reference": ref, "identity": record["identity"], "phase": record.get("phase")})
    default = registry / "default.json"
    return {"registry": registry_ref, "profile": profile, "binding": saved["binding"],
            "config": reference(profile["config"]), "scope": old.scope,
            "directory": str(directory), "resources": old.resources, "operations": operations,
            "default": reference(default) if default.exists() or default.is_symlink() else None}


def _check_history(registry, history):
    need(_history(registry, history["profile"]["name"]) == history,
         "RECOVERY_ANCESTOR_HISTORY_CHANGED")


def plan_recovery(registry_root, old_name, new_profile, environment_receipt, *,
                  purpose="EXPLICIT_PREMARKET_ENVIRONMENT_AND_DATA_RECOVERY", allow_test_fixture=False):
    registry = Path(registry_root).expanduser().absolute()
    w._directory(registry)
    history = _history(registry, old_name)
    profile = w.validate_profile(new_profile)
    old = history["profile"]
    need(profile["name"] != old_name and profile["backend"] == "premarket",
         "RECOVERY_NEW_RUNTIME_NAME_REQUIRED")
    for key in ("report_root", "python", "source_root"):
        need(profile[key] == old[key], "RECOVERY_SHARED_LOCATION_MUST_REMAIN")
    need(profile["storage"]["roots"] == old["storage"]["roots"],
         "RECOVERY_STORAGE_ROOTS_MUST_REMAIN")
    state = Path(profile["state_root"])
    need(state.is_relative_to(Path(old["state_root"]) / "epochs")
         and state != Path(old["state_root"]) / "epochs", "RECOVERY_NEW_OWNED_STATE_EPOCH_REQUIRED")
    need(profile["config"] != old["config"], "RECOVERY_OLD_CONFIG_PRESERVED")
    config, config_ref = _read(profile["config"])
    need(config.get("state_root") == profile["state_root"], "RECOVERY_CONFIG_STATE_MISMATCH")
    _environment(environment_receipt, profile, allow_test_fixture=allow_test_fixture)
    destination = registry / "runtimes" / (profile["name"] + ".json")
    need(not destination.exists() and not destination.is_symlink(), "RECOVERY_RUNTIME_EXISTS_PRESERVED")
    value = {"schema_version": VERSION, "registry_root": str(registry), "profile": profile,
             "binding": w._binding(profile), "config": config_ref, "history": history,
             "environment": deepcopy(environment_receipt), "purpose": purpose,
             "policy_change": {"old_storage": old["storage"], "new_storage": profile["storage"],
                               "old_limits": w._read(old["config"]).get("limits"), "new_limits": config.get("limits")},
             "test_fixture": bool(allow_test_fixture), "planned_at": w._stamp(),
             "default_changed": False, "source_authority_restored": False}
    need(isinstance(purpose, str) and bool(purpose) and len(purpose) <= 512, "RECOVERY_PURPOSE_REQUIRED")
    value["epoch_id"] = "runtime_recovery_" + w._digest(value)
    return value


def _check_plan(plan, registry):
    need(plan.get("schema_version") == VERSION and plan.get("registry_root") == str(registry)
         and plan["epoch_id"] == "runtime_recovery_" + w._digest({k: v for k, v in plan.items() if k != "epoch_id"}),
         "RECOVERY_PLAN_IDENTITY_CHANGED")
    _check_history(registry, plan["history"])
    _verify_refs([plan["config"]])
    need(w.validate_profile(plan["profile"]) == plan["profile"]
         and w._binding(plan["profile"]) == plan["binding"], "RECOVERY_NEW_BINDING_CHANGED")
    _environment(plan["environment"], plan["profile"], allow_test_fixture=plan["test_fixture"])


def apply_recovery(plan, registry_root=None, *, allow_test_fixture=False):
    """Append one new epoch and registry entry, leaving the default unchanged."""
    registry = Path(registry_root or plan["registry_root"]).expanduser().absolute()
    need(not plan.get("test_fixture") or allow_test_fixture, "RECOVERY_TEST_FIXTURE_NOT_PRODUCTION")
    with w._lock(registry / "registry.lock"), ExitStack() as stack:
        for resource in sorted(set(plan["history"]["resources"]) | set(w._resources(plan["binding"]))):
            stack.enter_context(w._lock(registry / "resources" / (resource + ".lock")))
        _check_plan(plan, registry)
        profile = plan["profile"]
        destination = registry / "runtimes" / (profile["name"] + ".json")
        need(not destination.exists() and not destination.is_symlink(), "RECOVERY_RUNTIME_EXISTS_PRESERVED")
        policy = {"report_root": profile["report_root"], "storage": profile["storage"]}
        for resource in w._resources(plan["binding"]):
            claim = registry / "resources" / (resource + ".json")
            if claim.exists() or claim.is_symlink():
                need(w._read(claim) == policy, "SHARED_RESOURCE_POLICY_CONFLICT")
        runtime = w.Runtime(registry, profile, plan["binding"])
        need(runtime.preflight()["allowed"], "RECOVERY_CAPACITY_PREFLIGHT_BLOCKED")
        epoch_path = registry / "recovery-epochs" / (plan["epoch_id"] + ".json")
        need(not epoch_path.exists() and not epoch_path.is_symlink(), "RECOVERY_EPOCH_EXISTS_RECONCILE_NO_RETRY")
        w._write(epoch_path, plan)
        for resource in w._resources(plan["binding"]):
            claim = registry / "resources" / (resource + ".json")
            if not claim.exists():
                w._write(claim, policy)
        _check_plan(plan, registry)
        w._write(destination, {"schema_version": VERSION, "profile": profile, "binding": plan["binding"],
                              "epoch": reference(epoch_path), "registered_at": w._stamp(),
                              "authority": "LOCATION_ONLY_NOT_SOURCE_AUTHORITY"})
    return {"status": "REGISTERED_NEW_RECOVERY_EPOCH", "name": profile["name"],
            "epoch_id": plan["epoch_id"], "default_changed": False,
            "runtime": reference(destination), "source_authority_restored": False}


class RecoveryRuntime(w.Runtime):
    def __init__(self, registry, saved, registry_ref, *, allow_test_fixture=False):
        super().__init__(registry, saved["profile"], saved["binding"])
        self._registry_ref = registry_ref
        self._epoch_ref = saved["epoch"]
        plan, ref = _read(self._epoch_ref["path"])
        need(ref == self._epoch_ref, "RECOVERY_EPOCH_CHANGED")
        need(plan["profile"] == self.profile and plan["binding"] == self.binding,
             "RECOVERY_RUNTIME_EPOCH_MISMATCH")
        need(not plan["test_fixture"] or allow_test_fixture, "RECOVERY_TEST_FIXTURE_NOT_PRODUCTION")
        self.recovery_plan = plan
        self._active_identity = None
        self.resources = sorted(set(self.resources) | set(plan["history"]["resources"]))
        self.check_binding()

    def check_binding(self):
        super().check_binding()
        _verify_refs([self._registry_ref, self._epoch_ref])
        _check_plan(self.recovery_plan, self.registry_root)

    def guard_ancestor_request(self, identity):
        _check_history(self.registry_root, self.recovery_plan["history"])
        for item in self.recovery_plan["history"]["operations"]:
            other = item["identity"]
            same_key = bool(identity.get("request_key")) and other.get("request_key") == identity["request_key"]
            same_sessions = (identity.get("backend") == other.get("backend") == "premarket"
                and bool(identity.get("observation_date")) and bool(identity.get("target_session"))
                and (identity["observation_date"], identity["target_session"]) ==
                    (other.get("observation_date"), other.get("target_session")))
            need(other != identity and not same_key and not same_sessions,
                 "RECOVERY_ANCESTOR_REQUEST_RECONCILE_NO_RETRY")

    @contextmanager
    def exclusive(self):
        self.check_binding()
        with ExitStack() as stack:
            for resource in self.resources:
                stack.enter_context(w._lock(self.registry_root / "resources" / (resource + ".lock")))
            self.check_binding()
            if self._active_identity is not None:
                self.guard_ancestor_request(self._active_identity)
            try:
                yield
            finally:
                self.check_binding()

    def run_once(self, identity, action, validate):
        need(self._active_identity is None, "RECOVERY_RUNTIME_REENTRANCY_FORBIDDEN")
        self._active_identity = deepcopy(identity)
        try:
            return super().run_once(identity, action, validate)
        finally:
            self._active_identity = None


def load_runtime(name, registry_root=None, *, allow_test_fixture=False):
    """Explicit name only; original default stays untouched and can remain stale."""
    registry = Path(registry_root or w.default_registry_root()).expanduser().absolute()
    saved, ref = _read(registry / "runtimes" / (w._name(name) + ".json"))
    if saved.get("schema_version") != VERSION:
        return w.load_runtime(name, registry)
    return RecoveryRuntime(registry, saved, ref, allow_test_fixture=allow_test_fixture)


if __name__ == "__main__":
    need(len(sys.argv) >= 5 and sys.argv[1] == "--environment-child", "RECOVERY_USE_EXPLICIT_PYTHON_API")
    raise SystemExit(_environment_child(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]))
