"""Explicit current-day bridge with one cumulative original budget.

The original registry, cache, strategies, source bundle and failed requests are
immutable inputs. Calendar acquisition and original-engine delivery share one
durable day claim. A derived invocation configuration only reduces limits.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from types import FunctionType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
VERSION = "CODEX_SUPERVISED_CURRENT_PREMARKET_V1"
SH = timezone(timedelta(hours=8))
CONTROL_LIMIT = 16 * 1024**2
FINAL_RESERVE_SECONDS = 30
TREE_READ_RESERVE = 3 * 1024**3
CONTROL_READ_RESERVE = 256 * 1024**2
CHILD_CONTROL_READ_RESERVE = 512 * 1024**2
TREE_OUTPUT_RESERVE = 8 * 1024**2
CONTROL_OUTPUT_RESERVE = 8 * 1024**2
_CONTROL_BUDGET = ContextVar("supervised_premarket_control_budget", default=None)


def need(condition, reason):
    if not condition:
        raise ValueError("SUPERVISED_PREMARKET_" + reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _modules():
    from scripts import build_premarket_source_bundle_v3 as bundle
    from scripts import codex_runtime_recovery_v1 as recovery
    from scripts import codex_recovered_premarket_v1 as bounded
    from scripts import codex_workspace as workspace
    return bundle, recovery, bounded, workspace


def read(path, expected=None):
    bundle, _, _, _ = _modules()
    raw, ref = bundle.read(Path(path), CONTROL_LIMIT)
    _charge_control(len(raw))
    need(expected is None or ref == expected, "CONTROL_REFERENCE_CHANGED")
    return bundle.previous.strict_json(raw), ref


def write(path, value):
    bundle, _, _, _ = _modules()
    path = Path(path).absolute()
    raw = canonical(value)
    need(len(raw) <= CONTROL_LIMIT, "CONTROL_FILE_LIMIT")
    # The legacy registry's one-MiB small receipt writer is kept unchanged.
    # This explicit plan also binds the full existing cache inventory.
    with bundle._directory_fd(path.parent, create=True) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)
    return read(path)[1]


def _charge_control(count):
    budget = _CONTROL_BUDGET.get()
    if budget is not None and not budget._meter_active:
        budget.charge_read(count)


@contextmanager
def _guard_reads():
    budget = _CONTROL_BUDGET.get()
    if budget is None or budget._meter_active:
        yield
    else:
        with budget.meter_files():
            yield


def check_guard(guard):
    with _guard_reads():
        guard.check()


def number(value, name):
    need(type(value) in (int, float) and math.isfinite(value) and value >= 0,
         "FINITE_NONNEGATIVE_" + name)
    return value


def safe_reason(error):
    from scripts import codex_daily as original_daily
    return original_daily._safe_reason(error)


def remaining_limits(original, *, elapsed_seconds, calls, reads,
                     output_bytes, supervisor_read_bytes=0):
    """Unknown actual counts stay unknown; admission uses reserved counts."""
    result = deepcopy(original)
    elapsed = number(elapsed_seconds, "ELAPSED")
    result["max_seconds"] -= math.ceil(elapsed) + FINAL_RESERVE_SECONDS
    for counter, limit in (("requests", "max_requests"), ("sdk_reserved", "max_sdk_calls"),
                           ("http_reserved", "max_http_attempts")):
        value = number(calls[counter], counter.upper())
        need(type(value) is int, "INTEGER_CALL_COUNTER_REQUIRED")
        result[limit] -= value
    for phase, value in reads.items():
        key = phase + "_read_bytes"
        need(key in original, "UNKNOWN_READ_PHASE")
        result[key] -= math.ceil(number(value, key))
    result["supervisor_read_bytes"] -= math.ceil(number(supervisor_read_bytes, "SUPERVISOR_READ"))
    result["output_bytes"] -= math.ceil(number(output_bytes, "OUTPUT"))
    for key, value in result.items():
        need(type(value) in (int, float) and 0 < value <= original[key],
             "ORIGINAL_BUDGET_EXHAUSTED:" + key)
    return result


def current_plan(runtime):
    """Original planner with a versioned read-only cache ceiling, no writer."""
    from scripts import codex_premarket_preflight_v1 as preflight
    _, _, bounded, _ = _modules()
    config = bounded.configuration(runtime, bounded.PROFILE)
    diagnostic = {key: min(value, config["limits"][key])
                  for key, value in preflight.sources.LIMITS.items()}
    # The old 256 MiB diagnostic profile is retained. This version explicitly
    # inspects the already registered cache up to its unchanged writer ceiling.
    diagnostic["cache_bytes"] = config["limits"]["cache_bytes"]
    facade = SimpleNamespace(**{**vars(preflight.sources), "LIMITS": diagnostic})
    function = FunctionType(preflight.plan.__code__,
        {**preflight.plan.__globals__, "sources": facade}, preflight.plan.__name__,
        preflight.plan.__defaults__, preflight.plan.__closure__)
    function.__kwdefaults__ = preflight.plan.__kwdefaults__
    result = function(runtime)
    result.update(bridge_version=VERSION, original_diagnostic_profile_unchanged=True,
                  diagnostic_cache_ceiling_source="UNCHANGED_REGISTERED_WRITER_LIMIT",
                  diagnostic_limits_authorize_writer=False)
    return result


def registered(runtime):
    _, recovery, bounded, _ = _modules()
    from scripts import codex_premarket as original
    need(isinstance(runtime, recovery.RecoveryRuntime), "ATTESTED_RECOVERY_RUNTIME_REQUIRED")
    prepared = original._prepared(runtime.profile)
    need(prepared["source"] == ROOT and Path(sys.executable).absolute() == prepared["python"]
         and sys.flags.isolated and sys.dont_write_bytecode and sys.pycache_prefix
         and not Path(sys.pycache_prefix).exists(), "REGISTERED_ISOLATED_SOURCE_AND_PYTHON_REQUIRED")
    return bounded.configuration(runtime, bounded.PROFILE)


def source_refs(runtime, bundle_path, manifest_sha):
    bundle, _, _, _ = _modules()
    from scripts import codex_factor_checkpoint_premarket_v1 as gateway
    with _guard_reads():
        guard = gateway.SourceGuard(runtime, bundle_path, manifest_sha)
        refs = [bundle.read(ROOT / "scripts" / name)[1] for name in (
            "codex_supervised_premarket_v1.py", "codex_current_calendar_v1.py",
            "codex_sdk_tree_supervisor_v1.py", "codex_metered_bundle_v1.py")]
        refs.append(bundle.read(Path(runtime.profile["config"]))[1])
    return guard, refs


def verify_refs(refs):
    bundle, _, _, _ = _modules()
    for ref in refs:
        raw, actual = bundle.read(Path(ref["path"]), CONTROL_LIMIT)
        _charge_control(len(raw))
        need(actual == ref, "SOURCE_CHANGED")


def _prepare(runtime, *, bundle_path, manifest_sha):
    from scripts import codex_current_calendar_v1 as calendar
    started, utc_started = time.monotonic(), datetime.now(timezone.utc)
    config = registered(runtime)
    guard, refs = source_refs(runtime, bundle_path, manifest_sha)
    with runtime.exclusive():
        plan = current_plan(runtime)
        check_guard(guard)
        verify_refs(refs)
    calendar_plan = calendar.prepare(runtime, limits=config["limits"])
    return dict(schema_version=VERSION, status="PLAN_ONLY_NO_COLLECTION",
        planning_local_date=str(datetime.now(timezone.utc).astimezone(SH).date()),
        original_limits=config["limits"], full_graph_preflight=plan,
        calendar_plan=calendar_plan, source_references=refs,
        source_bundle=guard.snapshot, runtime_name=runtime.profile["name"],
        preparation_elapsed_seconds=_elapsed(started, utc_started),
        scope=runtime.scope, request_claimed=False, network_calls=0,
        complete_daily_delivery=False, retry_authorized=False)


def _directory_bytes(root):
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    return directory_bytes(root)


def _elapsed(started, utc_started):
    return max(time.monotonic() - started,
               (datetime.now(timezone.utc) - utc_started).total_seconds())


def validate_derived_configuration(original, derived):
    need({key: value for key, value in original.items() if key != "limits"} ==
         {key: value for key, value in derived.items() if key != "limits"},
         "DERIVED_CONFIGURATION_CHANGED_STRATEGY_OR_STATE")
    need(set(derived["limits"]) == set(original["limits"]) and all(
        type(value) in (int, float) and math.isfinite(value)
        and 0 < value <= original["limits"][key]
        for key, value in derived["limits"].items()), "DERIVED_LIMITS_EXCEED_ORIGINAL")


def partition_limits(remaining):
    """Reserve the outer monitor explicitly; never give both layers the cap."""
    worker, monitor = deepcopy(remaining), deepcopy(remaining)
    worker["supervisor_read_bytes"] -= TREE_READ_RESERVE + CONTROL_READ_RESERVE + CHILD_CONTROL_READ_RESERVE
    worker["output_bytes"] -= TREE_OUTPUT_RESERVE + CONTROL_OUTPUT_RESERVE
    monitor["supervisor_read_bytes"] = TREE_READ_RESERVE
    monitor["output_bytes"] = TREE_OUTPUT_RESERVE
    need(worker["supervisor_read_bytes"] > 0 and worker["output_bytes"] > 0,
         "SUPERVISION_RESERVATION_EXCEEDS_REMAINING_BUDGET")
    return worker, monitor


def checked_workflow_result(supervised, value, context_sha):
    need(supervised.get("status") == "COMPLETE" and supervised.get("root_reaped") is True
         and supervised.get("all_observed_descendants_gone") is True,
         "OUTER_SUPERVISION_FAILED")
    need(value.get("schema_version") == VERSION and value.get("context_sha256") == context_sha
         and value.get("execution_exit_code") in (0, 2)
         and value["execution_exit_code"] == supervised.get("child_exit_code"),
         "WORKFLOW_RESULT_BINDING")
    result = value["result"]
    need(result.get("complete_daily_delivery") is True, "COMPLETE_DAILY_DELIVERY_REQUIRED")
    return result


def _worker(args):
    """Fresh interpreter imports the unchanged V3 bundle, not parent modules."""
    need(not any(name == "scripts" or name.startswith("scripts.") for name in sys.modules),
         "WORKER_BUNDLE_IMPORT_MUST_BE_FIRST")
    sys.path.insert(0, str(Path(args.bundle).absolute()))
    from scripts import codex_factor_checkpoint_premarket_v1 as gateway
    from scripts import codex_runtime_recovery_v1 as recovery
    # The supervision module is intentionally outside the frozen bundle. Load
    # exactly the explicitly bound new source, without changing scripts.__path__.
    import importlib.util
    path = ROOT / "scripts/codex_sdk_tree_supervisor_v1.py"
    spec = importlib.util.spec_from_file_location("_owned_supervisor", path)
    supervisor = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = supervisor
    spec.loader.exec_module(supervisor)
    ticket = supervisor.validate_worker_context(Path(args.supervisor_control))
    with _budget_accounting(ticket["limits"], CHILD_CONTROL_READ_RESERVE) as budget:
        _charge_control(ticket["worker_context_returned_read_bytes"])
        return _workflow_body(args, budget)


def _workflow_body(args, budget):
    from scripts import codex_factor_checkpoint_premarket_v1 as gateway
    from scripts import codex_runtime_recovery_v1 as recovery
    context, _ = read(args.context)
    need(digest(context) == args.context_sha256, "WORKER_CONTEXT_CHANGED")
    need(context["schema_version"] == VERSION and context["engine"] == "original",
         "WORKER_CONTEXT_SCHEMA")
    with _guard_reads():
        registered_runtime = recovery.load_runtime(context["runtime"], Path(context["registry_root"]))
        guard = gateway.SourceGuard(registered_runtime, args.bundle, context["manifest_sha256"])
    verify_refs(context["source_references"])
    read(registered_runtime.profile["config"], context["original_configuration"])
    with _guard_reads():
        original = gateway.bounded.configuration(registered_runtime, gateway.bounded.PROFILE)
    derived, _ = read(context["derived_configuration"]["path"], context["derived_configuration"])
    validate_derived_configuration(original, derived)
    import importlib.util
    path = ROOT / "scripts/codex_metered_bundle_v1.py"
    spec = importlib.util.spec_from_file_location("_metered_bundle_adapter", path)
    adapter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = adapter
    spec.loader.exec_module(adapter)
    facade, run = adapter.bind(registered_runtime, guard,
        context["derived_configuration"]["path"], budget)
    options = SimpleNamespace(profile=gateway.bounded.PROFILE,
        observation_date=context["observation_date"], target_session=context["target_session"],
        request_key=context["request_key"], engine="original")
    code, result = run(facade, options)
    check_guard(guard)
    verify_refs(context["source_references"])
    write(context["result_path"], dict(schema_version=VERSION, execution_exit_code=code,
        result=result, context_sha256=args.context_sha256,
        bridge_control_metering=facade.control_read_metering,
        bridge_control_read_accounting_at_publication=budget.metrics,
        bridge_control_read_reserved_bytes=CHILD_CONTROL_READ_RESERVE))
    budget.check("child_bridge_return", force=True)
    return code


def _execute(runtime, plan, *, started, utc_started):
    from scripts import codex_current_calendar_v1 as calendar
    from scripts import codex_factor_checkpoint_premarket_v1 as gateway
    from scripts import codex_sdk_tree_supervisor_v1 as supervisor
    _, _, _, workspace = _modules()
    config = registered(runtime)
    need(plan["schema_version"] == VERSION and plan["scope"] == runtime.scope
         and plan["runtime_name"] == runtime.profile["name"]
         and plan["original_limits"] == config["limits"], "PLAN_BINDING")
    local = str(datetime.now(timezone.utc).astimezone(SH).date())
    need(plan["planning_local_date"] == local, "PLAN_DAY_CHANGED")
    guard, refs = source_refs(runtime, plan["source_bundle"]["bundle_root"],
                              plan["source_bundle"]["manifest"]["sha256"])
    need(refs == plan["source_references"] and guard.snapshot == plan["source_bundle"], "PLAN_SOURCE_CHANGED")
    slot = runtime.directory / "supervised-current-premarket" / local
    preparation_seconds = number(plan["preparation_elapsed_seconds"], "PREPARATION_SECONDS")
    total_elapsed = lambda: preparation_seconds + _elapsed(started, utc_started)
    with runtime.exclusive():
        need(not slot.exists() and not slot.is_symlink(), "EXISTING_DAY_CLAIM_NO_RETRY")
        need(runtime.preflight(expected_growth_bytes=runtime.profile["storage"]["expected_growth_bytes"])["allowed"],
             "ORIGINAL_CAPACITY_BLOCKED")
        workspace._directory(slot, create=True)
        claim = write(slot / "claim.json", dict(schema_version=VERSION,
            plan_sha256=digest(plan), claimed_at=utc_started.isoformat(), runtime_scope=runtime.scope,
            planning_local_date=local, source_references=refs, retry_authorized=False))
        write(slot / "plan.json", plan)
    stage = "CALENDAR"
    completion_ref = None
    try:
        prefix_metrics = [plan["calendar_plan"]["planning"]["metrics"],
                          plan["full_graph_preflight"]["metrics"]]
        prefix_reads = {phase:sum(item["returned_read_bytes"][phase] for item in prefix_metrics)
                        for phase in ("capture", "core", "theme", "presentation")}
        prefix_calls = {counter:sum(item["calls"][counter] for item in prefix_metrics)
                        for counter in ("requests", "sdk_reserved", "http_reserved")}
        calendar_limits = remaining_limits(config["limits"],
            elapsed_seconds=total_elapsed(), calls=prefix_calls, reads=prefix_reads,
            output_bytes=_directory_bytes(slot),
            supervisor_read_bytes=sum(prefix_reads.values()) +
                sum(plan["control_read_accounting"]["returned_read_bytes"].values())+
                sum(_CONTROL_BUDGET.get().metrics["returned_read_bytes"].values()))
        calendar_receipt = calendar.execute(runtime, plan["calendar_plan"], limits=calendar_limits)
        calendar_ref = write(slot / "calendar-receipt.json", calendar_receipt)
        stage = "WHOLE_GRAPH_PLAN"
        with runtime.exclusive():
            graph = current_plan(runtime)
            check_guard(guard)
            verify_refs(refs)
        write(slot / "full-graph-plan.json", graph)
        observed, target = graph["observation_date"], graph["target_session"]
        need(observed is not None and target is not None, "CURRENT_CALENDAR_REQUIRED")
        request_key = "premarket-" + observed + "-target-" + target
        identity = dict(backend="premarket", observation_date=observed,
                        target_session=target, request_key=request_key)
        with runtime.exclusive():
            gateway.prior_request(runtime, identity)
        accounting = calendar_receipt["cumulative_accounting"]
        reads = deepcopy(accounting["returned_read_bytes"])
        for phase, value in graph["metrics"]["returned_read_bytes"].items():
            reads[phase] = reads.get(phase, 0) + value
        # Include this plan's read-only inspection in the same read budget.
        for phase, value in plan["full_graph_preflight"]["metrics"]["returned_read_bytes"].items():
            reads[phase] = reads.get(phase, 0) + value
        limits = remaining_limits(config["limits"], elapsed_seconds=total_elapsed(),
            calls=accounting["calls"], reads=reads,
            output_bytes=accounting["output_bytes"] + _directory_bytes(slot),
            supervisor_read_bytes=accounting["supervisor_returned_read_bytes"] + sum(reads.values()) +
                sum(plan["control_read_accounting"]["returned_read_bytes"].values()))
        slot_bytes_before_workflow = _directory_bytes(slot)
        worker_limits, monitor_limits = partition_limits(limits)
        derived = deepcopy(config)
        derived["limits"] = worker_limits
        derived_ref = write(slot / "remaining-configuration.json", derived)
        context = dict(schema_version=VERSION, engine="original", runtime=runtime.profile["name"],
            registry_root=str(runtime.registry_root), manifest_sha256=guard.sha256,
            source_references=refs, original_configuration=refs[-1],
            derived_configuration=derived_ref, observation_date=observed, target_session=target,
            request_key=request_key, result_path=str(slot / "workflow-result.json"),
            calendar_receipt=calendar_ref)
        context_ref = write(slot / "workflow-context.json", context)
        control = slot / "workflow-supervisor"
        command = [runtime.profile["python"], "-I", "-B", "-X",
            "pycache_prefix=" + str(slot / "absent-worker-pycache"), str(Path(__file__).absolute()),
            "_workflow-worker", "--bundle", str(guard.directory),
            "--context", context_ref["path"], "--context-sha256", digest(context),
            "--supervisor-control", str(control)]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for name in ("PYTHONPATH", "PYTHONHOME"):
            environment.pop(name, None)
        stage = "FULL_ORIGINAL_WORKFLOW"
        supervised = supervisor.run_supervised(command, cwd=guard.directory, env=environment,
            limits=monitor_limits, control_dir=control, source_references=[*refs, context_ref, derived_ref],
            accepted_exit_codes=(0, 2))
        need(supervised["status"] == "COMPLETE", "OUTER_SUPERVISION_FAILED")
        value, result_ref = read(context["result_path"])
        result = checked_workflow_result(supervised, value, digest(context))
        need(total_elapsed() <= config["limits"]["max_seconds"], "OUTER_UTC_TIME_LIMIT")
        with runtime.exclusive():
            check_guard(guard)
            verify_refs(refs)
            read(slot / "claim.json", claim)
            read(context_ref["path"], context_ref)
            read(derived_ref["path"], derived_ref)
            read(context["result_path"], result_ref)
        need(_directory_bytes(slot) <= slot_bytes_before_workflow + TREE_OUTPUT_RESERVE + CONTROL_OUTPUT_RESERVE,
             "OUTER_CONTROL_OUTPUT_LIMIT")
        receipt = dict(schema_version=VERSION, status="COMPLETE_CURRENT_ORIGINAL_DELIVERY",
            planning_local_date=local, observation_date=observed, target_session=target,
            request_key=request_key, execution_engine="original", claim=claim,
            calendar_receipt=calendar_ref, original_limits=config["limits"],
            remaining_configuration=derived_ref, workflow_result=result_ref,
            outer_supervision_read_reserve=TREE_READ_RESERVE,
            outer_control_read_reserve=CONTROL_READ_RESERVE,
            child_control_read_reserve=CHILD_CONTROL_READ_RESERVE,
            outer_output_reserve=TREE_OUTPUT_RESERVE + CONTROL_OUTPUT_RESERVE,
            control_read_accounting=_CONTROL_BUDGET.get().metrics,
            external_supervision=supervised, elapsed_seconds=total_elapsed(),
            completed_at=datetime.now(timezone.utc).isoformat(), complete_daily_delivery=True,
            daily_v1_readiness="UNKNOWN_NOT_EXECUTED", market_edge="NOT_ESTABLISHED",
            retry_authorized=False, registered_configuration_unchanged=True)
        completion_ref = write(slot / "completion.json", receipt)
        with runtime.exclusive():
            check_guard(guard)
            verify_refs(refs)
            read(slot / "claim.json", claim)
            read(context_ref["path"], context_ref)
            read(derived_ref["path"], derived_ref)
            read(context["result_path"], result_ref)
            read(slot / "completion.json", completion_ref)
        need(_directory_bytes(slot) <= slot_bytes_before_workflow + TREE_OUTPUT_RESERVE + CONTROL_OUTPUT_RESERVE,
             "FINAL_CONTROL_OUTPUT_LIMIT")
        need(total_elapsed() <= config["limits"]["max_seconds"], "FINAL_UTC_TIME_LIMIT")
        # The lock's exit binding check and inventory traversal above are
        # callbacks too. The candidate must still be our exact publication.
        read(slot / "completion.json", completion_ref)
        return receipt
    except BaseException as error:
        if completion_ref is not None:
            _revoke_owned_completion(slot, completion_ref)
        write(slot / "failure.json", dict(schema_version=VERSION, status="FAILED_OR_UNKNOWN",
            phase=stage, reason=safe_reason(error),
            elapsed_seconds=total_elapsed(), failed_at=datetime.now(timezone.utc).isoformat(),
            complete_daily_delivery=False, failure_takes_precedence_over_completion=True,
            retry_authorized=False))
        raise


def _revoke_owned_completion(slot, expected):
    """Preserve a failed candidate, remove only the still-owned success name."""
    bundle, _, _, _ = _modules()
    try:
        raw, actual = bundle.read(slot / "completion.json", CONTROL_LIMIT)
        if actual != expected:
            return
        with bundle._directory_fd(slot) as parent:
            fd = os.open("failed-candidate-completion.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if bundle.read(slot / "completion.json", CONTROL_LIMIT)[1] == expected:
                os.unlink("completion.json", dir_fd=parent)
            os.fsync(parent)
    except (OSError, ValueError):
        # A replacement is outside our ownership. The separate failure record
        # is authoritative and the day claim remains non-retryable.
        return


@contextmanager
def _budget_accounting(original_limits, read_limit):
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    limits = dict(original_limits, capture_read_bytes=read_limit)
    budget = Budget(limits)
    token = _CONTROL_BUDGET.set(budget)
    try:
        with budget.phase("capture"):
            yield budget
    finally:
        _CONTROL_BUDGET.reset(token)


def prepare(runtime, *, bundle_path, manifest_sha):
    started, utc_started = time.monotonic(), datetime.now(timezone.utc)
    config = registered(runtime)
    with _budget_accounting(config["limits"], CONTROL_READ_RESERVE) as budget:
        plan = _prepare(runtime, bundle_path=bundle_path, manifest_sha=manifest_sha)
        # The CLI publishes and reads the plan after this function returns.
        # Its maximum read is reserved here, not silently treated as free I/O.
        budget.charge_read(CONTROL_LIMIT)
        plan["planning_publication_read_reserve_bytes"] = CONTROL_LIMIT
        budget.check("bridge_prepare_terminal", force=True)
        plan["preparation_elapsed_seconds"] = _elapsed(started, utc_started)
        plan["control_read_accounting"] = budget.metrics
        return plan


def execute(runtime, plan, *, input_read_bytes=0, _invocation_clock=None):
    started, utc_started = _invocation_clock or (time.monotonic(), datetime.now(timezone.utc))
    config = registered(runtime)
    with _budget_accounting(config["limits"], CONTROL_READ_RESERVE):
        _charge_control(number(input_read_bytes, "PLAN_READ_BYTES"))
        return _execute(runtime, plan, started=started, utc_started=utc_started)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("premarket-plan", "premarket"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--runtime", required=True)
        sub.add_argument("--registry-root", type=Path, required=True)
        if name == "premarket-plan":
            sub.add_argument("--bundle", type=Path, required=True)
            sub.add_argument("--manifest-sha256", required=True)
            sub.add_argument("--output", type=Path, required=True)
        else:
            sub.add_argument("--plan", type=Path, required=True)
            sub.add_argument("--plan-sha256", required=True)
    worker = commands.add_parser("_workflow-worker", allow_abbrev=False)
    worker.add_argument("--bundle", type=Path, required=True)
    worker.add_argument("--context", type=Path, required=True)
    worker.add_argument("--context-sha256", required=True)
    worker.add_argument("--supervisor-control", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "_workflow-worker":
        return _worker(args)
    sys.path.insert(0, str(ROOT))
    invocation_clock = time.monotonic(), datetime.now(timezone.utc)
    try:
        _, recovery, _, _ = _modules()
        runtime = recovery.load_runtime(args.runtime, args.registry_root)
        if args.command == "premarket-plan":
            result = prepare(runtime, bundle_path=args.bundle, manifest_sha=args.manifest_sha256)
            ref = write(args.output, result)
            result = dict(schema_version=VERSION, status=result["status"], plan=ref,
                          plan_sha256=digest(result), network_calls=0, request_claimed=False)
        else:
            plan, plan_ref = read(args.plan)
            need(digest(plan) == args.plan_sha256, "PLAN_SHA_CHANGED")
            result = execute(runtime, plan, input_read_bytes=plan_ref["bytes"],
                             _invocation_clock=invocation_clock)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except Exception as error:
        print(json.dumps(dict(schema_version=VERSION, status="FAILED_OR_BLOCKED",
            reason=safe_reason(error),
            complete_daily_delivery=False, retry_authorized=False), ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
