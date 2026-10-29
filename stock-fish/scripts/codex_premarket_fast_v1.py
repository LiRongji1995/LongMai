"""Explicit original-engine execution with full canonical hashing in C.

This version does not cache digests, skip source validation, shorten windows or
change the daily default. The existing exact typed encoder is used in a single
owned process and all module bindings are restored before accepting completion.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_first_core as first
from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu import premarket_workflow_v1 as original
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded

VERSION = "CODEX_ORIGINAL_CANONICAL_EXECUTION_V1"
PROFILES = ("reference", "canonical")
_ACTIVE = False


def _need(value, reason):
    if not value:
        raise ValueError(VERSION + ":" + reason)


def _single_thread():
    _need(threading.current_thread() is threading.main_thread()
          and threading.active_count() == 1, "SINGLE_THREAD_PROCESS_REQUIRED")


def _scope_sources(budget):
    from stockfish_core.yaogu import history_artifacts, short_horizon_archive_bridge_v1
    paths = (Path(__file__), Path(first.__file__), Path(history_artifacts.__file__),
             Path(short_horizon_archive_bridge_v1.__file__))
    return [budget.read(path.resolve())[1] for path in paths]


@contextmanager
def execution_scope(profile, budget):
    """Measure both arms identically; recompute every complete original hash.

    No SQL/source capability or decoded value is retained by this scope. Exact
    MarketInput/tuple/model types use the original 128-row typed implementation;
    unsupported objects retain its full reference fallback. Caller supplies the
    genuine cumulative stage Budget and owns process isolation/supervision.
    """
    global _ACTIVE
    _need(profile in PROFILES, "EXPLICIT_ENCODING_PROFILE_REQUIRED")
    _need(type(budget) is Budget, "ORIGINAL_BUDGET_REQUIRED")
    _need(not _ACTIVE, "NESTED_ENCODING_SCOPE_FORBIDDEN")
    _single_thread()
    modules = first._load()
    history = modules["history_artifacts"]
    canonical = modules["short_horizon_archive_bridge_v1"]
    names = ("digest_json_stream", "digest_market_input")
    originals = {name: getattr(history, name) for name in names}
    _need(canonical._reference_digest_market_input is originals["digest_market_input"],
          "REFERENCE_FALLBACK_BINDING_CHANGED")
    for module_name in ("premarket_native_core_v1", "short_horizon_participation", "theme_evolution"):
        _need(all(getattr(modules[module_name], name) is originals[name] for name in names),
              "NATIVE_ENCODER_BINDING_CHANGED")
    _need(modules["research_context_artifacts"].digest_market_input is originals["digest_market_input"],
          "CONTEXT_ENCODER_BINDING_CHANGED")
    sources = _scope_sources(budget)
    metrics = dict(version=VERSION, profile=profile,
        canonical_profile="ORIGINAL" if profile == "reference" else canonical.DEFAULT_ENCODING_PROFILE,
        hash_policy="FULL_CANONICAL_CONTENT_EVERY_CALL_NO_MEMOIZATION",
        timing_role="INCLUSIVE_ENCODING_CALLS_MAY_OVERLAP_DO_NOT_SUM",
        source_references=sources, bindings_restored=False,
        calls={name: dict(count=0, wall_seconds=0.0, cpu_seconds=0.0, failures=0) for name in names})

    def check():
        budget.check("full_canonical_hash")
        _single_thread()

    def wrap(name):
        def digest(value):
            check()
            started, cpu = time.perf_counter(), time.process_time()
            primary = None
            row = metrics["calls"][name]
            row["count"] += 1
            try:
                if profile == "reference":
                    return originals[name](value)
                if name == "digest_market_input":
                    return canonical.digest_market_input(value, checkpoint=check,
                        encoding_profile=canonical.DEFAULT_ENCODING_PROFILE)
                return canonical.digest_json_stream(value, checkpoint=check)
            except BaseException as error:
                primary = error
                row["failures"] += 1
                raise
            finally:
                row["wall_seconds"] += time.perf_counter() - started
                row["cpu_seconds"] += time.process_time() - cpu
                try:
                    check()
                except BaseException as error:
                    if primary is None:
                        raise
                    primary.add_note("ENCODER_EXIT_CHECK_FAILED:" + repr(error))
        return digest

    wrappers = {name: wrap(name) for name in names}
    bindings = []
    _ACTIVE = True
    try:
        for module_name, module in tuple(sys.modules.items()):
            if module_name.startswith(("stockfish_core.", "scripts.")):
                for name in names:
                    if getattr(module, name, None) is originals[name]:
                        bindings.append((module, name, originals[name]))
                        setattr(module, name, wrappers[name])
        _need(bindings, "ENCODER_BINDINGS_UNAVAILABLE")
        metrics["binding_count"] = len(bindings)
        yield metrics
        _single_thread()
        _need(all(getattr(module, name, None) is wrappers[name] for module, name, _ in bindings),
              "ENCODER_BINDING_CHANGED_DURING_EXECUTION")
        for reference in sources:
            budget.read(reference["path"], expected=reference)
        budget.check("encoding_scope_terminal", force=True)
    finally:
        for module_name, module in tuple(sys.modules.items()):
            # Project scripts may import these aliases lazily inside the scope.
            # They must not retain a wrapper bound to this invocation's budget.
            if module_name.startswith(("stockfish_core.", "scripts.")):
                for name in names:
                    if getattr(module, name, None) is wrappers[name]:
                        setattr(module, name, originals[name])
        for module, name, value in bindings:
            setattr(module, name, value)
        _ACTIVE = False
        metrics["bindings_restored"] = True


def _script_refs(read_meter=None):
    refs = {row["path"]: row for row in shared._script_refs(read_meter)}
    pending = {Path(__file__).resolve(), ROOT / "scripts/codex_daily_fast_v1.py"}
    visited = set()
    while pending:
        path = min(pending)
        pending.remove(path)
        if path in visited:
            continue
        visited.add(path)
        _need(len(visited) <= shared.MAX_SCRIPT_FILES, "SCRIPT_CLOSURE_LIMIT")
        reference = original._ref(path, read_meter)
        prior = refs.setdefault(str(path), reference)
        _need(prior == reference, "SOURCE_REFERENCE_CONFLICT")
        raw = path.read_bytes()
        if read_meter is not None:
            read_meter(len(raw))
        _need(original.fingerprint(path) == reference["fp"], "SOURCE_CHANGED_DURING_DISCOVERY")
        for node in ast.walk(ast.parse(raw, filename=str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names if alias.name.startswith("scripts.")]
            elif isinstance(node, ast.ImportFrom):
                if node.module == "scripts":
                    names = ["scripts." + alias.name for alias in node.names]
                elif node.module and node.module.startswith("scripts."):
                    names = [node.module]
            for name in names:
                pending.add(ROOT.joinpath(*name.split(".")).with_suffix(".py"))
    return [refs[name] for name in sorted(refs)]


def code_refs(read_meter=None):
    refs = {row["path"]: row for row in original._code_refs(read_meter)}
    for row in _script_refs(read_meter):
        _need(refs.setdefault(row["path"], row) == row, "SOURCE_REFERENCE_CONFLICT")
    return [refs[name] for name in sorted(refs)]


def execute_workflow_fast(config, job_dir, *, now=None, adapters=None,
                          expected_sessions=None, engine="original", profile="canonical"):
    """Complete original workflow; new identity and exact original limits."""
    _need(engine == "original", "ORIGINAL_ENGINE_REQUIRED")
    fixture = config.get("capture", {}).get("allow_test_fixture") is True
    _need(fixture or (sys.flags.isolated and sys.dont_write_bytecode
          and sys.pycache_prefix is not None and not Path(sys.pycache_prefix).exists()
          and os.name == "posix" and os.getpgrp() == os.getpid()),
          "ISOLATED_OWNED_WORKER_REQUIRED")
    factory = shared._clone(shared._workflow_budget, {"_script_refs": _script_refs})
    budget = factory(config["limits"], job_dir=Path(job_dir),
                     cache_dir=Path(config["state_root"]) / "cache")
    completion = shared._clone(shared._shared_completion, {"VERSION": VERSION})
    saved = {}

    def finish(job, *, status, phases, budget, **fields):
        # Keep successful publication until the encoding scope's own terminal
        # has completed and all imported bindings have been restored.
        saved.update(job=job, status=status, phases=phases, budget=budget, fields=fields)
        return {"status": status}

    execute = shared._clone(shared.execute_workflow_shared,
        {"_workflow_budget": lambda *a, **k: budget, "_shared_completion": finish})
    metrics = None
    try:
        with execution_scope(profile, budget) as metrics:
            execute(config, job_dir, now=now, adapters=adapters,
                    expected_sessions=expected_sessions, engine=engine)
        _need(saved, "WORKFLOW_COMPLETION_REQUIRED")
        fields = saved["fields"]
        fields["encoding_execution"] = deepcopy(metrics)
        return completion(saved["job"], status=saved["status"], phases=saved["phases"],
                          budget=budget, **fields)
    except Exception as error:
        if not saved:
            saved.update(job=Path(job_dir), status="BUDGET_EXCEEDED" if isinstance(error, BudgetExceeded) else "FAILED",
                phases={name: "NOT_RUN" for name in ("capture", "core", "news", "theme_sources", "theme", "presentation")},
                fields=dict(core_verified=False, core_artifacts=None, display=None))
        # Preserve an existing primary failure; a later budget or source error
        # cannot erase the independently verified core and original phase log.
        fields = saved["fields"]
        detail = dict(type=type(error).__name__, reason=str(error)[:2000])
        if "error" in fields:
            fields["encoding_terminal_failure"] = detail
        else:
            fields["error"] = detail
        fields.update(display=None, encoding_execution=deepcopy(metrics), execution_version=VERSION,
                      execution_engine=engine, expected_sessions=expected_sessions)
        status = saved["status"] if saved["status"] not in shared.SUCCESS else (
            "BUDGET_EXCEEDED" if isinstance(error, BudgetExceeded) else "FAILED")
        return shared._clone(original._completion, {"VERSION": VERSION})(Path(job_dir),
            status=status, phases=saved["phases"], budget=budget, **fields)


class _WorkerSubprocess(shared._WorkerSubprocess):
    Popen = shared._clone(shared._WorkerSubprocess.Popen, {"__file__": __file__})


class _SupervisorGuard(shared._SupervisorGuard):
    seal = shared._clone(shared._SupervisorGuard.seal, {"VERSION": VERSION})


def supervise(config, **kwargs):
    _need(kwargs.get("engine", "original") == "original", "ORIGINAL_ENGINE_REQUIRED")
    execute = shared._clone(shared.supervise, {"VERSION": VERSION, "_code_refs": code_refs,
        "_WorkerSubprocess": _WorkerSubprocess, "_SupervisorGuard": _SupervisorGuard})
    return execute(config, **kwargs)


def verify_job(path):
    return shared._clone(shared.verify_job, {"VERSION": VERSION})(path)


def main(argv=None):
    # This low-level CLI cannot submit daily jobs without the registered gateway.
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS, allow_abbrev=False)
    worker.add_argument("--job", required=True)
    worker.add_argument("--engine", choices=("original",), default="original")
    for name in ("observation-date", "target-session", "planning-local-date"):
        worker.add_argument("--" + name)
    verify = commands.add_parser("premarket-verify", allow_abbrev=False)
    verify.add_argument("job")
    args = parser.parse_args(argv)
    if args.command == "premarket-verify":
        print(json.dumps(verify_job(args.job), ensure_ascii=False))
        return 0
    parts = tuple(getattr(args, name) for name in
                  ("observation_date", "target_session", "planning_local_date"))
    expected = shared._expected_sessions(parts) if any(part is not None for part in parts) else None
    job = Path(args.job).resolve(strict=True)
    value = execute_workflow_fast(original._read_control(job / "configuration.json"), job,
                                  expected_sessions=expected, engine=args.engine)
    return shared.EXIT_CODES[value["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
