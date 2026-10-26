"""Explicit first-computation profiles using the unchanged native compact core.

Only canonical encoding changes: every original digest is still recomputed in
full. The optimization lives in an isolated child; it neither edits old source
files nor caches mutable-object hashes or reconstructs a NativeCore owner.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_compact_core as core
from scripts import codex_core as original
from scripts import codex_workspace as workspace

VERSION = "CODEX_FIRST_NATIVE_CORE_ENCODING_V1"
REFERENCE = "FIRST_CORE_REFERENCE_V1"
OPTIMIZED = "FIRST_CORE_CANONICAL_128_V1"
PROFILES = (REFERENCE, OPTIMIZED)
MIB = 1024 ** 2
need = original._need
_ACTIVE = False
_PRELOAD = ("history_artifacts", "premarket_native_core_v1", "short_horizon_participation",
            "research_context_artifacts", "theme_evolution", "strategy_experiments",
            "candidate_service", "short_horizon_archive_bridge_v1")


def _load():
    return {name: importlib.import_module("stockfish_core.yaogu." + name) for name in _PRELOAD}


def _options(profile, first_id):
    need(profile in PROFILES, "FIRST_CORE_EXPLICIT_PROFILE_REQUIRED")
    need(first_id is None or type(first_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", first_id),
         "FIRST_CORE_EXPLICIT_FIRST_ID_INVALID")


def _provenance(runtime, profile, first_id):
    _options(profile, first_id)
    snapshot = core._snapshot(runtime.profile)
    return dict(schema_version=VERSION, execution_profile=profile, first_run_id=first_id,
                coordinator=original._reference(Path(__file__).resolve()),
                native_implementation_digest=original._digest(snapshot),
                canonical_encoding="ORIGINAL" if profile == REFERENCE else "TYPED_ROWS_128_V1",
                hash_policy="FULL_ORIGINAL_CANONICAL_CONTENT_EVERY_CALL_NO_MEMOIZATION")


@contextmanager
def encoding_scope(profile):
    """Invocation-local pure encoder bindings, with complete restoration.

    The bridge retains its original fallback function. Only attributes named
    exactly like the two digest APIs and bound to the original function object
    are replaced; normalizers, selectors and owner checks are never patched.
    """
    global _ACTIVE
    _options(profile, None)
    need(not _ACTIVE, "FIRST_CORE_NESTED_ENCODING_SCOPE_FORBIDDEN")
    modules = _load()
    history, bridge = modules["history_artifacts"], modules["short_horizon_archive_bridge_v1"]
    names = ("digest_json_stream", "digest_market_input")
    originals = {name: getattr(history, name) for name in names}
    need(bridge._reference_digest_market_input is originals["digest_market_input"],
         "FIRST_CORE_ORIGINAL_INPUT_ENCODER_BINDING_CHANGED")
    for module_name in ("premarket_native_core_v1", "short_horizon_participation", "theme_evolution"):
        need(all(getattr(modules[module_name], name) is originals[name] for name in names),
             "FIRST_CORE_NATIVE_ENCODER_BINDING_CHANGED")
    need(modules["research_context_artifacts"].digest_market_input is originals["digest_market_input"],
         "FIRST_CORE_CONTEXT_ENCODER_BINDING_CHANGED")
    metrics = {name: dict(calls=0, elapsed_seconds=0.0, cpu_seconds=0.0) for name in names}

    def check():
        meter = original._ACTIVE_BUDGET.get()
        if meter is not None:
            meter.check()
        need(bridge._rss() <= core.LIMITS["self_rss_bytes"], "FIRST_CORE_ENCODER_RSS_LIMIT")

    def wrapper(name):
        def call(value):
            check()
            started, cpu = time.monotonic(), time.process_time()
            metrics[name]["calls"] += 1
            try:
                if profile == REFERENCE:
                    return originals[name](value)
                function = getattr(bridge, name)
                if name == "digest_market_input":
                    return function(value, checkpoint=check, encoding_profile=bridge.DEFAULT_ENCODING_PROFILE)
                return function(value, checkpoint=check)
            finally:
                metrics[name]["elapsed_seconds"] += time.monotonic() - started
                metrics[name]["cpu_seconds"] += time.process_time() - cpu
                check()
        return call

    wrappers = {name: wrapper(name) for name in names}
    bindings = []
    _ACTIVE = True
    try:
        for module_name, module in tuple(sys.modules.items()):
            if module_name.startswith("stockfish_core."):
                for name in names:
                    if getattr(module, name, None) is originals[name]:
                        bindings.append((module, name, originals[name]))
                        setattr(module, name, wrappers[name])
        need(bindings, "FIRST_CORE_ENCODER_BINDINGS_UNAVAILABLE")
        metrics["binding_count"] = len(bindings)
        yield metrics
        need(all(getattr(module, name, None) is wrappers[name] for module, name, _ in bindings),
             "FIRST_CORE_ENCODER_BINDING_CHANGED_DURING_EXECUTION")
    finally:
        # Dynamic imports may have captured the wrapper while the scope was
        # active. Restore those references as well as all originally owned ones.
        for module_name, module in tuple(sys.modules.items()):
            if module_name.startswith("stockfish_core."):
                for name in names:
                    if getattr(module, name, None) is wrappers[name]:
                        setattr(module, name, originals[name])
        for module, name, value in bindings:
            setattr(module, name, value)
        _ACTIVE = False


class _RuntimeFacade:
    """Keep the original state machine, adding an independent execution key."""
    def __init__(self, runtime, provenance):
        self.runtime, self.provenance = runtime, deepcopy(provenance)

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def verify(self):
        need(_provenance(self.runtime, self.provenance["execution_profile"],
                         self.provenance["first_run_id"]) == self.provenance,
             "FIRST_CORE_IMPLEMENTATION_CHANGED")

    def run_once(self, identity, action, validate):
        enriched = dict(schema_version=VERSION, execution=self.provenance, native_identity=identity)

        def act():
            self.verify()
            receipt = action()
            self.verify()
            receipt["first_core_execution"] = deepcopy(self.provenance)
            return receipt

        def valid(receipt):
            self.verify()
            need(receipt.get("first_core_execution") == self.provenance,
                 "FIRST_CORE_EXECUTION_RECEIPT_CHANGED")
            result = validate(receipt)
            self.verify()
            return result

        return self.runtime.run_once(enriched, act, valid)


def execute_child(runtime, manifest, *, profile, first_run_id=None, allow_test_fixture=False,
                  supervisor_control=None, _fixture_in_process=False):
    _options(profile, first_run_id)
    need(runtime is not None, "FIRST_CORE_REGISTERED_RUNTIME_REQUIRED")
    need((_fixture_in_process and allow_test_fixture) or
         (os.name == "posix" and os.getpgrp() == os.getpid() and supervisor_control is not None),
         "FIRST_CORE_ISOLATED_SUPERVISED_CHILD_REQUIRED")
    _load()
    provenance = _provenance(runtime, profile, first_run_id)
    facade = _RuntimeFacade(runtime, provenance)
    with encoding_scope(profile) as metrics:
        result = core.execute_compact_core(facade, manifest, profile=core.PROFILE,
            allow_test_fixture=allow_test_fixture, _supervisor_control=supervisor_control)
    facade.verify()
    result["first_core_execution"] = provenance
    result["encoding_metrics"] = metrics
    result["encoding_bindings_restored"] = True
    return result


def _monitor_owned_job(control, report_root, prior=None):
    """Retry only a disappearing unpublished staging member during sampling.

    The original compact worker removes its owned staging after validating the
    complete stored archive. A concurrent filesystem inventory may have seen a
    directory entry just before that unlink. Retry a complete inventory, never
    subtract or ignore a member, and retain all original identity/size checks.
    Missing archive, evidence, job/control, or external source files still fail.
    """
    anchor = prior
    for attempt in range(3):
        try:
            return core._monitor_owned_job(control, report_root, anchor)
        except FileNotFoundError as error:
            if attempt == 2 or error.filename is None:
                raise
            value = workspace._read(control / "owned-job.json", limit=8192)
            need(set(value) == {"schema_version", "job", "directory_identity"}
                 and value["schema_version"] == core.VERSION,
                 "FIRST_CORE_MONITOR_JOB_DECLARATION_SCHEMA")
            job = Path(value["job"])
            need(job.is_absolute() and job.parent == report_root and job.name.startswith("compact-core-")
                 and core._owned_job_state(job) == value["directory_identity"]
                 and (anchor is None or value == anchor), "FIRST_CORE_MONITOR_JOB_IDENTITY_CHANGED")
            missing = Path(error.filename)
            if not missing.is_absolute() or not missing.is_relative_to(job):
                raise
            parts = missing.relative_to(job).parts
            allowed = (len(parts) == 1 or len(parts) == 2 and parts[1] in {"owned-token", "core"}
                       or len(parts) == 3 and parts[1] == "core" and parts[2] in core.compact.CORE_FILES)
            if not parts or not re.fullmatch(r"\.codex-compact-stage-[0-9a-f]{32}", parts[0]) or not allowed:
                raise
            anchor = value
            time.sleep(.01)
    raise AssertionError("UNREACHABLE_FIRST_CORE_MONITOR")


def supervised_command(runtime, manifest, *, profile, first_run_id=None, allow_test_fixture=False):
    """Time/RSS/storage supervisor; only the child holds the run_once lock."""
    _options(profile, first_run_id)
    need(runtime is not None and os.name == "posix", "FIRST_CORE_POSIX_REGISTERED_RUNTIME_REQUIRED")
    from stockfish_core.yaogu.premarket_workflow_v1 import _tree_sample, _stop_owned, _owned_group_members
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    prepared = original.premarket._prepared(runtime.profile)
    need(prepared["source"] == ROOT, "FIRST_CORE_REGISTERED_SOURCE_REQUIRED")
    _load()
    provenance = _provenance(runtime, profile, first_run_id)
    manifest = Path(manifest).expanduser().absolute()
    request_ref = original._reference(manifest, original.MAX_MANIFEST)
    report_root = Path(runtime.profile["report_root"])
    with runtime.exclusive():
        need(runtime.preflight(expected_growth_bytes=2 * MIB, destination=report_root)["allowed"],
             "FIRST_CORE_SUPERVISOR_CAPACITY_BLOCKED")
        control = report_root / ("compact-supervisor-first-" + uuid.uuid4().hex)
        workspace._directory(control, create=True)
        workspace._write(control / "launch.json", dict(schema_version=VERSION, execution=provenance,
            manifest=request_ref, allow_test_fixture=allow_test_fixture))
    output = control / "result.json"
    command = [str(prepared["python"]), "-I", "-B", str(Path(__file__).resolve()),
               "--runtime", runtime.profile["name"], "--registry-root", str(runtime.registry_root),
               "--manifest", str(manifest), "--profile", profile, "--worker-result", str(output)]
    if first_run_id is not None:
        command.extend(("--force-first", first_run_id))
    if allow_test_fixture:
        command.append("--allow-test-fixture")
    process, peak, started, owned, peak_bytes = None, 0, time.monotonic(), None, 0
    try:
        with (control / "console.txt").open("xb") as console:
            process = subprocess.Popen(command, cwd=ROOT,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), stdout=console,
                stderr=subprocess.STDOUT, start_new_session=True)
            last = 0
            while process.poll() is None:
                try:
                    peak = max(peak, _tree_sample(process.pid))
                except Exception:
                    if process.poll() is None:
                        raise
                elapsed = time.monotonic() - started
                need(elapsed < core.LIMITS["max_seconds"], "FIRST_CORE_TREE_TIME_LIMIT")
                need(peak <= core.LIMITS["tree_rss_bytes"], "FIRST_CORE_TREE_RSS_LIMIT")
                need(directory_bytes(control) <= 2 * MIB, "FIRST_CORE_SUPERVISOR_OUTPUT_LIMIT")
                owned, size = _monitor_owned_job(control, report_root, owned)
                peak_bytes = max(peak_bytes, size)
                if elapsed - last >= 10:
                    workspace._write(control / "progress.json", dict(status="RUNNING",
                        elapsed_seconds=elapsed, peak_tree_rss_bytes=peak),
                        replace=(control / "progress.json").exists())
                    last = elapsed
                time.sleep(.25)
            code = process.wait()
        need(not _owned_group_members(process.pid), "FIRST_CORE_OWNED_DESCENDANTS_REMAIN")
        owned, size = _monitor_owned_job(control, report_root, owned)
        peak_bytes = max(peak_bytes, size)
        need(directory_bytes(control) <= 2 * MIB, "FIRST_CORE_SUPERVISOR_OUTPUT_LIMIT")
        if code != 0:
            failure_path = control / "worker-error.json"
            need(failure_path.exists() or failure_path.is_symlink(), "FIRST_CORE_WORKER_FAILED_WITHOUT_CONTROL")
            failure = workspace._read(failure_path, limit=8192)
            need(failure.get("schema_version") == VERSION and failure.get("status") == "BLOCKED"
                 and isinstance(failure.get("reason"), str) and len(failure["reason"]) <= 2048,
                 "FIRST_CORE_WORKER_FAILURE_SCHEMA")
            raise original.CoreError(failure["reason"])
        result = workspace._read(output)
        need(result.get("status") in {"RECORDED", "REUSED"} and
             result.get("first_core_execution") == provenance and
             result["receipt"].get("first_core_execution") == provenance and
             result.get("encoding_bindings_restored") is True, "FIRST_CORE_WORKER_RESULT_BINDING")
        need((result["status"] == "REUSED" and owned is None) or
             (result["status"] == "RECORDED" and owned is not None and
              Path(result["receipt"]["evidence"]["path"]).parent == Path(owned["job"])),
             "FIRST_CORE_WORKER_OWNED_JOB_BINDING")
        need(_provenance(runtime, profile, first_run_id) == provenance,
             "FIRST_CORE_SOURCE_CHANGED_DURING_WORKER")
        original._verify(request_ref)
        result["external_supervision"] = dict(exit_code=code, reaped=True, peak_tree_rss_bytes=peak,
            elapsed_seconds=time.monotonic()-started, control_directory=str(control),
            peak_owned_job_bytes=peak_bytes, owned_job=owned,
            scope="PROCESS_TIME_RSS_OWNED_JOB_BYTES_AND_SOURCE_BINDING; ARCHIVE_VERIFIED_BY_WORKER")
        workspace._write(control / "completion.json", result)
        return result
    except BaseException as error:
        cleanup_error = None
        if process is not None:
            try:
                _stop_owned(process)
            except Exception as cleanup:
                cleanup_error = type(cleanup).__name__
        workspace._write(control / "failure.json", dict(status="FAILED_OR_UNKNOWN",
            reason=core._failure_reason(error), error_type=type(error).__name__,
            cleanup_error_type=cleanup_error, exit_code=process.returncode if process else None,
            elapsed_seconds=time.monotonic()-started, peak_tree_rss_bytes=peak,
            peak_owned_job_bytes=peak_bytes, owned_job=owned))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime")
    parser.add_argument("--registry-root")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--force-first", metavar="EXPLICIT_UNIQUE_ID",
        help="Separate measured first computation; reuse or failure remains sticky for this exact ID.")
    parser.add_argument("--allow-test-fixture", action="store_true")
    parser.add_argument("--worker-result", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    control = None
    try:
        runtime = workspace.load_runtime(args.runtime, args.registry_root)
        options = dict(profile=args.profile, first_run_id=args.force_first,
                       allow_test_fixture=args.allow_test_fixture)
        if args.worker_result is None:
            result = supervised_command(runtime, args.manifest, **options)
        else:
            output = Path(args.worker_result).absolute()
            need(runtime is not None and output.name == "result.json" and
                 output.parent.parent == Path(runtime.profile["report_root"]) and
                 output.parent.name.startswith("compact-supervisor-first-"), "FIRST_CORE_WORKER_OUTPUT_SCOPE")
            core._owned_job_state(output.parent)
            control = output.parent
            launch = workspace._read(control / "launch.json")
            _load()
            need(launch == dict(schema_version=VERSION, execution=_provenance(runtime, args.profile, args.force_first),
                 manifest=original._reference(Path(args.manifest).absolute(), original.MAX_MANIFEST),
                 allow_test_fixture=args.allow_test_fixture), "FIRST_CORE_LAUNCH_BINDING_CHANGED")
            result = execute_child(runtime, args.manifest, supervisor_control=control, **options)
            workspace._write(output, result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as error:
        failure = dict(schema_version=VERSION, status="BLOCKED", reason=core._failure_reason(error),
                       error_type=type(error).__name__)
        if control is not None:
            try:
                workspace._write(control / "worker-error.json", failure)
            except (ValueError, OSError):
                pass
        print(json.dumps(failure, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
