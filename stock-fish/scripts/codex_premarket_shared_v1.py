"""Bounded original premarket workflow and its owned worker process.

Only the original research engine is executable. Native source authentication,
per-phase budgets, process cleanup and terminal checks remain authoritative.
The historical schema name is retained for saved result readers.
"""
from __future__ import annotations

import argparse
import ast
import builtins
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time as wall_time, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import FunctionType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hashlib
from stockfish_core.file_integrity import (
    content_equal, content_reference_equal, content_fingerprint, verify_content_reference,
)

from stockfish_core.yaogu import premarket_workflow_v1 as legacy
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from scripts.codex_sdk_tree_supervisor_v3 import NativeProcessSession

VERSION = "CODEX_PREMARKET_SHARED_WORKFLOW_V1"
EXIT_CODES = {"COMPLETE": 0, "COMPLETED_WITH_UNKNOWNS": 2, "FAILED": 3, "BUDGET_EXCEEDED": 4}
SUCCESS = frozenset(("COMPLETE", "COMPLETED_WITH_UNKNOWNS"))
MAX_SCRIPT_FILES = 4096


def _engine(value):
    if value != "original":
        raise ValueError("SHARED_PREMARKET_EXPLICIT_ENGINE_REQUIRED")
    return value


def _expected_sessions(value):
    if value is None:
        return None
    if type(value) not in (tuple, list) or len(value) != 3 or any(type(part) is not str for part in value):
        raise ValueError("SHARED_PREMARKET_EXPECTED_SESSION_FORMAT")
    try:
        parsed = tuple(date.fromisoformat(part) for part in value)
    except ValueError:
        raise ValueError("SHARED_PREMARKET_EXPECTED_SESSION_FORMAT") from None
    if tuple(day.isoformat() for day in parsed) != tuple(value) or parsed[0] >= parsed[1]:
        raise ValueError("SHARED_PREMARKET_EXPECTED_SESSION_FORMAT")
    return tuple(value)


def _expected_gate(expected, now=None):
    expected = _expected_sessions(expected)
    if expected is None:
        return None
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("SHARED_PREMARKET_AWARE_CURRENT_CLOCK_REQUIRED")
    local = current.astimezone(legacy.SH)
    observation, target, planned_local_day = (date.fromisoformat(part) for part in expected)
    if local.date() != planned_local_day:
        raise ValueError("SHARED_PREMARKET_PLANNING_LOCAL_DAY_CHANGED")
    if not (datetime.combine(observation, wall_time(18), legacy.SH) <= local
            < datetime.combine(target, wall_time(18), legacy.SH)):
        raise ValueError("SHARED_PREMARKET_EXPECTED_SESSION_WINDOW_CHANGED")
    return current


def _clone(function, bindings):
    scope = dict(function.__globals__)
    scope.update(bindings)
    result = FunctionType(function.__code__, scope, function.__name__,
        function.__defaults__, function.__closure__)
    result.__kwdefaults__ = function.__kwdefaults__
    return result


def _script_refs(read_meter=None):
    # Start from explicit entrypoints and follow only statically declared
    # script imports, including function-local imports. Unrelated new scripts
    # therefore do not invalidate an archived execution's source identity.
    pending = {ROOT / "scripts" / (name + ".py") for name in (
        "codex_premarket_shared_v1", "codex_daily_v2", "codex_premarket_preflight_v1")}
    references = {}
    while pending:
        path = min(pending)
        pending.remove(path)
        if path in references:
            continue
        if len(references) >= MAX_SCRIPT_FILES:
            raise ValueError("SHARED_PREMARKET_SCRIPT_CLOSURE_LIMIT")
        reference = legacy._ref(path, read_meter)
        raw = path.read_bytes()
        if read_meter is not None:
            read_meter(len(raw))
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("SHARED_PREMARKET_SCRIPT_CHANGED_DURING_DISCOVERY")
        tree = ast.parse(raw, filename=str(path))
        references[path] = reference
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names if alias.name.startswith("scripts."))
            elif isinstance(node, ast.ImportFrom):
                if node.module == "scripts":
                    names.update("scripts." + alias.name for alias in node.names)
                elif node.module and node.module.startswith("scripts."):
                    names.add(node.module)
                elif node.level and path.parent == ROOT / "scripts":
                    raise ValueError("SHARED_PREMARKET_RELATIVE_SCRIPT_IMPORT_UNSUPPORTED")
        for name in names:
            if not all(part.isidentifier() for part in name.split(".")):
                raise ValueError("SHARED_PREMARKET_SCRIPT_IMPORT_UNSUPPORTED")
            candidate = ROOT.joinpath(*name.split(".")).with_suffix(".py")
            pending.add(candidate)
    return [references[path] for path in sorted(references)]


def _code_refs(read_meter=None):
    refs = {ref["path"]: ref for ref in legacy._code_refs(read_meter)}
    for reference in _script_refs(read_meter):
        prior = refs.setdefault(reference["path"], reference)
        if not content_reference_equal(prior, reference):
            raise ValueError("SHARED_PREMARKET_SOURCE_REFERENCE_CONFLICT")
    return [refs[path] for path in sorted(refs)]


code_refs = _code_refs


def _workflow_budget(*args, **kwargs):
    """Keep the exact original Budget type and its genuine counters."""
    budget = Budget(*args, **kwargs)
    budget.shared_source_refs = None
    budget.phase_timings = []
    original_phase = budget.phase
    @contextmanager
    def phase(name):
        started = time.monotonic()
        status = "FAILED"
        try:
            with original_phase(name):
                if budget.shared_source_refs is None:
                    if name != "capture":
                        raise ValueError("SHARED_PREMARKET_CAPTURE_MUST_START_FIRST")
                    budget.shared_source_refs = _script_refs(budget.charge_read)
                yield budget
            status = "COMPLETE"
        finally:
            budget.phase_timings.append(dict(phase=name, status=status,
                elapsed_seconds=round(time.monotonic() - started, 6)))
    budget.phase = phase
    return budget


def _shared_completion(job, *, status, phases, budget, **fields):
    if status in SUCCESS:
        # These are control/source reads, charged to the original capture
        # allocation. No phase borrows another allocation and exhaustion stays
        # sticky. This runs inside the original workflow's exception boundary.
        with budget.phase("capture"):
            for reference in budget.shared_source_refs:
                legacy._verify(reference, budget.charge_read)
        references = {ref["path"]: ref for ref in fields["validation_references"]}
        for reference in budget.shared_source_refs:
            prior = references.setdefault(reference["path"], reference)
            if not content_reference_equal(prior, reference):
                raise ValueError("SHARED_PREMARKET_SOURCE_REFERENCE_CONFLICT")
        fields["validation_references"] = list(references.values())
    fields.update(execution_version=VERSION,
        native_core_algorithm="UNCHANGED_ORIGINAL_NATIVE_CORE",
        theme_execution="UNCHANGED_ORIGINAL_NATIVE_THEME",
        execution_engine=budget.execution_engine,
        phase_timings=deepcopy(budget.phase_timings),
        source_capability_restored=False,
        expected_sessions=list(budget.expected_sessions) if budget.expected_sessions is not None else None)
    return _clone(legacy._completion, {"VERSION": VERSION})(job,
        status=status, phases=phases, budget=budget, **fields)


def execute_workflow_shared(config, job_dir, *, now=None, adapters=None, expected_sessions=None, engine="original"):
    """Run capture through display and terminal checks in the owned worker.

    ``adapters`` retains the exact original explicit TEST_FIXTURE restriction.
    The production path has no arbitrary collaborator/owner/reader injection.
    """
    engine = _engine(engine)
    expected = _expected_sessions(expected_sessions)
    fixture_clock = now if config.get("capture", {}).get("allow_test_fixture") is True else None
    if expected is not None and now is not None and fixture_clock is None:
        raise ValueError("SHARED_PREMARKET_PRODUCTION_CLOCK_OVERRIDE_FORBIDDEN")
    capture_now = _expected_gate(expected, fixture_clock) if expected is not None else now
    from stockfish_core.yaogu import premarket_capture_cache_v1 as capture
    original_import = builtins.__import__

    def collect(*args, **kwargs):
        if expected is not None:
            kwargs["now"] = _expected_gate(expected, fixture_clock)
        return capture.capture_collect(*args, **kwargs)

    def collect_themes(*args, **kwargs):
        _expected_gate(expected, fixture_clock)
        return capture.capture_theme_sources(*args, **kwargs)

    def selected_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (level == 1 and name == "premarket_capture_cache_v1"
                and tuple(fromlist) == ("CaptureCache", "capture_collect", "capture_theme_sources")
                and globals is not None
                and globals.get("__package__") == legacy.__package__):
            return SimpleNamespace(CaptureCache=capture.CaptureCache,
                capture_collect=collect, capture_theme_sources=collect_themes)
        return original_import(name, globals, locals, fromlist, level)

    local_builtins = dict(vars(builtins))
    local_builtins["__import__"] = selected_import

    def budget_factory(*args, **kwargs):
        budget = _workflow_budget(*args, **kwargs)
        budget.expected_sessions = expected
        budget.execution_engine = engine
        return budget

    def latest_gate(calendar_rows, observation, target, clock=None):
        if expected is None:
            return legacy._latest_gate(calendar_rows, observation, target, clock)
        current = _expected_gate(expected, fixture_clock)
        if (str(observation), str(target)) != expected[:2]:
            raise ValueError("SHARED_PREMARKET_CAPTURE_SESSION_DIFFERS_FROM_REQUEST")
        return legacy._latest_gate(calendar_rows, observation, target, current)

    execute = _clone(legacy.execute_workflow, {"__builtins__": local_builtins,
        "Budget": budget_factory, "_completion": _shared_completion,
        "_latest_gate": latest_gate})
    return execute(config, job_dir, now=capture_now, adapters=adapters)


class _SupervisorGuard:
    def __init__(self, limits, started_at=None, expected_sessions=None, engine="original"):
        now = time.monotonic()
        if started_at is not None and (type(started_at) not in (int, float)
                or not math.isfinite(started_at) or not 0 <= started_at <= now):
            raise ValueError("SHARED_PREMARKET_PARENT_CLOCK_INVALID")
        self.started = now if started_at is None else started_at
        self.limits = limits
        self.job = None
        self.failed = False
        self.last_output_check = 0.0
        self.expected_sessions = _expected_sessions(expected_sessions)
        self.engine = _engine(engine)

    def check(self, *, force=False):
        if self.failed:
            raise BudgetExceeded("SHARED_PREMARKET_SUPERVISOR_BUDGET_EXHAUSTED")
        reason = None
        if self.limits["max_seconds"] is not None and time.monotonic() - self.started > self.limits["max_seconds"]:
            reason = "TOTAL_TIME_LIMIT_EXCEEDED"
        if self.job is not None and (force or time.monotonic() - self.last_output_check >= 0.5):
            if legacy.directory_bytes(self.job) > self.limits["output_bytes"]:
                reason = "OUTPUT_LIMIT_EXCEEDED"
            self.last_output_check = time.monotonic()
        if reason:
            self.failed = True
            raise BudgetExceeded(reason)

    def seal(self, path, value):
        self.job = Path(path).parent
        if value.get("status") in SUCCESS:
            _expected_gate(self.expected_sessions)
            self.check(force=True)
            value["execution_version"] = VERSION
            value["elapsed_seconds"] = round(time.monotonic() - self.started, 6)
            value["whole_workflow_wall_enforced"] = True
            value["expected_sessions"] = list(self.expected_sessions) if self.expected_sessions is not None else None
            value["execution_engine"] = self.engine
        legacy._seal(path, value)
        if value.get("status") in SUCCESS:
            _expected_gate(self.expected_sessions)
            self.check(force=True)

    def reference(self, path, read_meter=None):
        self.check()
        result = legacy._ref(path, read_meter)
        self.check()
        return result

    def verify(self, reference, read_meter=None):
        self.check()
        legacy._verify(reference, read_meter)
        self.check()



class _WorkerSubprocess:
    """Only rewrite the original owned worker invocation, never its globals."""
    def __init__(self, absent, guard, fixture_command=None):
        self.absent, self.guard, self.fixture_command = absent, guard, fixture_command

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def Popen(self, command, **kwargs):
        self.guard.check()
        _expected_gate(self.guard.expected_sessions)
        if self.fixture_command is None:
            if (len(command) != 7 or command[:4] != [sys.executable, "-B", "-m", legacy.__spec__.name]
                    or command[4:6] != ["_worker", "--job"]):
                raise ValueError("SHARED_PREMARKET_UNEXPECTED_WORKER_COMMAND")
            command = [sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(self.absent),
                str(Path(__file__).resolve()), "_worker", "--job", command[6], "--engine", self.guard.engine]
            if self.guard.expected_sessions is not None:
                for option, value in zip(("--observation-date", "--target-session", "--planning-local-date"),
                                        self.guard.expected_sessions):
                    command += [option, value]
        elif command != self.fixture_command:
            raise ValueError("SHARED_PREMARKET_FIXTURE_COMMAND_CHANGED")
        if Path(self.absent).exists():
            raise ValueError("SHARED_PREMARKET_BYTECODE_PATH_MUST_BE_ABSENT")
        environment = dict(kwargs["env"])
        for name in ("PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        kwargs["env"] = environment
        return subprocess.Popen(command, **kwargs)


def supervise(config, *, progress=True, started_at=None, expected_sessions=None, engine="original",
              recovery_context=None, continuation_context=None, _fixture_worker_command=None):
    """Return ``(exit_code, final)``; caller coordinates its stable request key.

    Registered configuration retains its original schema and every limit.
    Optional existing recovery locators are copied into this job's control
    configuration only; the capture reader must authenticate their full chain.
    The parent additionally enforces wall/output during terminal hashing.
    ``_fixture_worker_command`` is restricted to explicit isolated fixture state.
    """
    if not isinstance(config, dict) or set(config) - {
            "schema_version", "state_root", "minimum_amount_cny", "limits",
            "capture", "news", "seed_sources"} or config.get("schema_version", legacy.VERSION) != legacy.VERSION:
        raise ValueError("UNSUPPORTED_PREMARKET_CONFIG")
    config = legacy.load_config(overrides=deepcopy(config))
    if recovery_context is not None:
        if (not isinstance(recovery_context, dict) or set(recovery_context) not in (
                {"scope", "scope_directory", "completions"},
                {"scope", "scope_directory", "completions", "factor_reconciliations"})):
            raise ValueError("SHARED_PREMARKET_RECOVERY_CONTEXT_FORMAT")
        config["recovery_context"] = deepcopy(recovery_context)
    if continuation_context is not None:
        if (not isinstance(continuation_context, dict) or set(continuation_context) !=
                {"schema_version", "scope", "scope_directory", "operation_id", "claim", "proposal"}
                or continuation_context.get("schema_version") != "PREMARKET_CONTINUATION_AUTHORIZATION_V1"):
            raise ValueError("SHARED_PREMARKET_CONTINUATION_CONTEXT_FORMAT")
        config["continuation_context"] = deepcopy(continuation_context)
    if _fixture_worker_command is not None and config.get("capture", {}).get("allow_test_fixture") is not True:
        raise ValueError("ADAPTERS_REQUIRE_EXPLICIT_TEST_FIXTURE")
    class Guard(_SupervisorGuard):
        def check(self, **options):
            super().check(**options)
            try:
                session.check()
            except ValueError as error:
                if str(error) in {"SUPERVISOR_UTC_DEADLINE_EXCEEDED", "SUPERVISOR_MONOTONIC_DEADLINE_EXCEEDED"}:
                    raise BudgetExceeded(str(error)) from error
                raise

        def tree_sample(self, pid, read_meter=None):
            self.check()
            value = session.sample(pid, read_meter)
            self.check()
            return value

        def seal(self, path, value):
            if value.get("status") in SUCCESS:
                session.require_complete()
            if value.get("status") != "UNSEALED" and session.process is not None:
                value["external_worker_reaped"] = session.anchor.reaped
                value["external_worker_exit_code"] = session.returncode
            value["native_inner_supervision"] = session.report()
            value["supervisor_accounting_scope"] = (
                "EXPLICIT_SOURCE_ARTIFACT_CONTROL_READS_AND_NATIVE_PROCESS_SNAPSHOT_BYTES; "
                "FINAL_SELF_REFERENCE_EXCLUDED; NOT_ALL_KERNEL_IO")
            return super().seal(path, value)

    class Worker(_WorkerSubprocess):
        def Popen(self, command, **options):
            return session.bind(super().Popen(command, **options))

    def references(read_meter=None):
        session.meter = read_meter
        return _code_refs(read_meter)

    guard = Guard(config["limits"], started_at, expected_sessions, engine)
    session = NativeProcessSession(config["limits"], started_at=started_at)
    _expected_gate(guard.expected_sessions)
    with tempfile.TemporaryDirectory(prefix="stockfish-premarket-bytecode-") as directory:
        worker = Worker(Path(directory) / "absent", guard, _fixture_worker_command)
        run = _clone(legacy.supervise, {"VERSION": VERSION, "_code_refs": references,
            "_ref": guard.reference, "_verify": guard.verify, "_seal": guard.seal,
            "subprocess": worker})
        return run(config, progress=progress, worker_command=_fixture_worker_command,
                   sample_tree=guard.tree_sample, remaining_owned=session.remaining, stop_owned=session.stop)


def verify_job(path):
    """Verify owned derivatives too; external RAW gains no fresh authority."""
    job = Path(path).expanduser().resolve(strict=True)
    final_ref = legacy._ref(job / "final-control.json")
    result = _clone(legacy.verify_job, {"VERSION": VERSION})(job)
    final = legacy._read_control(job / "final-control.json")
    owned, seen, already_verified = {}, {}, set()
    if final.get("completion"):
        completion = legacy._read_control(final["completion"]["path"])
        known = []
        for role in ("core_artifacts", "theme_artifacts"):
            known.extend((completion.get(role) or {}).values())
        known.extend((final.get("display") or {}).get("artifacts", {}).values())
        if completion.get("news") is not None:
            known.append(completion["news"]["metadata"])
        for reference in known:
            prior = seen.setdefault(reference["path"], reference)
            if not content_reference_equal(prior, reference):
                raise ValueError("SHARED_PREMARKET_ARCHIVED_REFERENCE_CONFLICT")
            already_verified.add(reference["path"])
        for reference in completion.get("validation_references", ()):
            if (not isinstance(reference, dict) or set(reference) != {"path", "sha256", "bytes", "fp"}
                    or not isinstance(reference["path"], str)):
                raise ValueError("SHARED_PREMARKET_ARCHIVED_REFERENCE_SCHEMA")
            prior = seen.setdefault(reference["path"], reference)
            if not content_reference_equal(prior, reference):
                raise ValueError("SHARED_PREMARKET_ARCHIVED_REFERENCE_CONFLICT")
            source = Path(reference["path"])
            if not source.is_absolute():
                raise ValueError("SHARED_PREMARKET_ARCHIVED_REFERENCE_NOT_ABSOLUTE")
            if source.is_relative_to(job):
                if source.resolve(strict=True) != source or source == job:
                    raise ValueError("SHARED_PREMARKET_ARCHIVED_DERIVATIVE_ALIAS")
                owned[reference["path"]] = reference
        for name, reference in owned.items():
            if name not in already_verified:
                legacy._verify(reference)
        for reference in owned.values():
            verify_content_reference(reference)
    legacy._verify(final_ref)
    result.update(owned_derivative_reference_count=len(owned),
        owned_derivatives_verified=True, external_sources_reauthenticated=False)
    return result


def parser():
    result = argparse.ArgumentParser(description="内部原盘前worker；用户使用codex_daily_v2.py")
    commands = result.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--job", required=True)
    worker.add_argument("--engine", choices=("original",), default="original")
    for option in ("--observation-date", "--target-session", "--planning-local-date"):
        worker.add_argument(option)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        expected = None
        if args.command in ("premarket", "_worker"):
            parts = tuple(getattr(args, name) for name in ("observation_date", "target_session", "planning_local_date"))
            expected = _expected_sessions(parts) if any(part is not None for part in parts) else None
        if args.command == "_worker":
            if (not sys.flags.isolated or not sys.dont_write_bytecode
                    or sys.pycache_prefix is None or Path(sys.pycache_prefix).exists()
                    or os.name == "posix" and os.getpgrp() != os.getpid()):
                raise ValueError("SHARED_PREMARKET_ISOLATED_OWNED_WORKER_REQUIRED")
            job = Path(args.job).resolve(strict=True)
            value = execute_workflow_shared(legacy._read_control(job / "configuration.json"), job,
                expected_sessions=expected, engine=args.engine)
            return EXIT_CODES[value["status"]]
    except Exception as error:
        # Outer errors contain controlled codes only; raw provider exceptions
        # remain in the private original worker completion.
        print(json.dumps(dict(schema_version=VERSION, status="FAILED_BEFORE_EXECUTION",
            error=type(error).__name__), ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
