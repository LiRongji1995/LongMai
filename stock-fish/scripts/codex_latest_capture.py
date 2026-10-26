"""Explicit bounded latest-session acquisition through the original provider.

The two opt-in profiles do not edit a registered configuration. Market mode
requests only the latest completed session's two original market endpoints;
scoped history coalesces identical missing code sets across adjacent exchange
sessions. Neither mode executes a strategy or supplies missing theme/factor
evidence. Existing failures and unfinished attempts are never retried.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as day_time, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_incremental as legacy

VERSION = "CODEX_LATEST_CAPTURE_V1"
MIB = 1024**2
SH = timezone(timedelta(hours=8))
PROFILES = {
    "LATEST_SCOPED_HISTORY_V1": dict(mode="scoped-history", growth_bytes=4*MIB,
        source_bytes=MIB, max_requests=3, max_sdk_calls=12),
    "LATEST_MARKET_SESSION_V1": dict(mode="market-session", growth_bytes=16*MIB,
        source_bytes=8*MIB, max_requests=2, max_sdk_calls=6),
}
BASE_LIMITS = dict(max_http_attempts=1, request_http_attempts=1,
    request_timeout_seconds=90, max_seconds=240, capture_read_bytes=256*MIB,
    core_read_bytes=MIB, theme_read_bytes=MIB, presentation_read_bytes=MIB,
    supervisor_read_bytes=MIB, self_rss_bytes=512*MIB, tree_rss_bytes=768*MIB,
    output_bytes=16*MIB, cache_bytes=256*MIB)


class LatestCaptureError(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise LatestCaptureError(reason)


def _policy(profile, mode, overrides=None):
    _need(profile in PROFILES and PROFILES[profile]["mode"] == mode,
          "EXPLICIT_MATCHING_LATEST_CAPTURE_PROFILE_REQUIRED")
    selected = PROFILES[profile]
    maximum = dict(BASE_LIMITS, max_requests=selected["max_requests"],
                   max_sdk_calls=selected["max_sdk_calls"], output_bytes=selected["growth_bytes"])
    overrides = {} if overrides is None else overrides
    _need(isinstance(overrides, dict) and not set(overrides)-set(maximum), "UNKNOWN_LATEST_CAPTURE_LIMIT")
    result = dict(maximum, **overrides)
    _need(all(type(result[key]) is int and 0 < result[key] <= cap for key, cap in maximum.items()),
          "LATEST_CAPTURE_LIMIT_MUST_NOT_EXCEED_PROFILE")
    return result, selected


def _calendar(cache, metadata, requested):
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec
    _need(isinstance(metadata, dict) and metadata.get("endpoint") == "calendar",
          "ORIGINAL_CALENDAR_METADATA_REQUIRED")
    # The old helper checks original registration, all natural days, clocks,
    # completeness and actual trading-day flags before any network admission.
    legacy._calendar(cache, metadata, requested)
    result = cache.verify(metadata)
    legacy._history_clock(metadata)
    now = datetime.now(timezone.utc)
    local = now.astimezone(SH).date()
    start, end = (date.fromisoformat(result["parameters"][key]) for key in ("start_date", "end_date"))
    _need(start <= local < end, "CALENDAR_MUST_COVER_ACTUAL_TODAY_AND_FUTURE")
    sessions = sorted(row["calendar_date"] for row in result["rows"] if row["is_trading_day"] == "1")
    completed = [day for day in sessions if datetime.combine(date.fromisoformat(day), day_time(18), SH) <= now]
    _need(bool(completed), "NO_COMPLETED_ORIGINAL_EXCHANGE_SESSION")
    latest = completed[-1]
    _need(all(day <= latest for day in requested), "REQUESTED_SESSION_NOT_COMPLETED")
    _need(not requested or max(requested) == latest, "LATEST_COMPLETED_SESSION_MUST_BE_INCLUDED")
    # The original request remains unchanged; this is only a planning view.
    spec = RequestSpec(**metadata["request_spec"])
    return dict(latest_completed_session=latest, trading_sessions=sessions,
                original_calendar_request=spec.to_dict(), original_calendar=metadata)


def _terminal_clock(calendar):
    now = datetime.now(timezone.utc)
    today = str(now.astimezone(SH).date())
    parameters = calendar["original_calendar"]["parameters"]
    _need(parameters["start_date"] <= today < parameters["end_date"],
          "LATEST_CALENDAR_CURRENTNESS_CHANGED_AT_TERMINAL")
    complete = [day for day in calendar["trading_sessions"]
                if datetime.combine(date.fromisoformat(day), day_time(18), SH) <= now]
    _need(complete and complete[-1] == calendar["latest_completed_session"],
          "LATEST_COMPLETED_SESSION_CHANGED_AT_TERMINAL")


def merged_batches(specs, trading_sessions):
    """Only identical missing code sets on adjacent declared trading days join."""
    from stockfish_core.yaogu.premarket_capture_cache_v1 import RequestSpec
    grouped = {}
    for spec in specs:
        grouped.setdefault(spec.business_session, []).append(spec)
    positions = {day: index for index, day in enumerate(trading_sessions)}
    groups = []
    for day, members in sorted(grouped.items()):
        codes = tuple(sorted(spec.parameters["codes"][0] for spec in members))
        _need(day in positions, "MISSING_SCOPE_OUTSIDE_ORIGINAL_CALENDAR")
        if groups and groups[-1]["codes"] == codes and positions[day] == positions[groups[-1]["end"]]+1:
            groups[-1]["end"] = day
            groups[-1]["members"].extend(members)
        else:
            groups.append(dict(start=day, end=day, codes=codes, members=list(members)))
    return [(RequestSpec("history", dict(codes=list(group["codes"]), start_date=group["start"],
                end_date=group["end"]), group["end"], role="LATEST_MISSING_CONTIGUOUS_EXCHANGE_RANGE"),
             group["members"]) for group in groups]


def _history_plan(cache, specs, calendar):
    known, results, blocked = {}, {}, []
    for spec in specs:
        legacy._metadata_paths(cache.root, spec)
        try:
            known[spec.key] = cache.lookup(spec)
        except ValueError as error:
            if str(error) != "UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY":
                raise
            blocked.append(dict(request_keys=[spec.key], reason=str(error)))
            known[spec.key] = None
        if known[spec.key] is not None:
            result = cache.verify(known[spec.key])
            legacy._history_clock(known[spec.key].metadata)
            legacy._row_status(result, spec.parameters["codes"][0], spec.business_session)
            results[spec.key] = result
    views, originals, attempts = legacy._range_views(cache,
        [spec for spec in specs if known[spec.key] is None], specs,
        {key: (record.metadata, results[key]) for key, record in known.items() if record is not None})
    for metadata in originals.values():
        # A rejected fresh response cannot become usable on the next command.
        # Validate all parent rows inside this original calendar's coverage,
        # while preserving older rows outside its declared calendar window.
        parent = cache.verify(metadata)
        calendar_parameters = calendar["original_calendar"]["parameters"]
        for row in parent["rows"]:
            if calendar_parameters["start_date"] <= row["date"] <= calendar_parameters["end_date"]:
                _need(row["date"] in calendar["trading_sessions"], "ORIGINAL_HISTORY_ROW_ON_NONTRADING_DAY")
    blocked.extend(attempts)
    missing = [spec for spec in specs if known[spec.key] is None and spec.key not in views]
    batches = merged_batches(missing, calendar["trading_sessions"])
    return known, results, views, originals, blocked, batches, len(legacy._missing_batches(missing))


def _scope_rows(result, batch, members, calendar):
    grouped = legacy._history_rows(result, batch.parameters)
    expected = {(spec.parameters["codes"][0], spec.business_session) for spec in members}
    _need(set(grouped) <= expected, "FRESH_ROWS_OUTSIDE_DECLARED_MISSING_EXCHANGE_SESSIONS")
    _need(all(day in calendar["trading_sessions"] for _, day in grouped), "FRESH_ROW_ON_NONTRADING_DAY")
    return grouped


def run_scope(*, cache_root, mode, profile, calendar_record, operation="plan", codes=None,
              sessions=None, limits=None, execute_network=False, executor=None,
              allow_test_fixture=False, admit=None):
    """Internal component. The registered coordinator owns the total resource lock.

    Real network requires its admission callback. CLI network always executes
    under the bounded process supervisor. Fixture injection is explicit.
    """
    _need(operation in ("plan", "sync") and type(execute_network) is bool
          and type(allow_test_fixture) is bool, "EXPLICIT_LATEST_OPERATION_REQUIRED")
    _need(executor is None or allow_test_fixture, "EXECUTOR_REQUIRES_EXPLICIT_TEST_FIXTURE")
    _need(not execute_network or operation == "sync", "PLAN_CANNOT_EXECUTE_NETWORK")
    _need(not execute_network or callable(admit), "REGISTERED_NETWORK_ADMISSION_REQUIRED")
    policy, selected = _policy(profile, mode, limits)
    root = legacy._directory(cache_root)
    from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    from stockfish_core.yaogu.provider_requests import execute_request
    from scripts.codex_factor_incremental import _inventory
    budget = Budget(policy, cache_dir=root)
    budget.phase_name = "capture"
    before = _inventory(root, lambda: budget.check("LATEST_INVENTORY"))
    _need(before <= policy["cache_bytes"], "LATEST_CACHE_CAPACITY_EXCEEDED")
    growth_cap = min(selected["growth_bytes"], policy["output_bytes"])
    chosen_executor = executor or execute_request

    def inventory():
        count = _inventory(root, lambda: budget.check("LATEST_INVENTORY"))
        _need(count <= policy["cache_bytes"] and count-before <= growth_cap,
              "LATEST_CACHE_GROWTH_LIMIT_EXCEEDED")
        return count

    def guarded(endpoint, parameters, output, **kwargs):
        # Invoked inside the original writer lock after its durable claim;
        # never nest the lock or bypass original provider attempt accounting.
        inventory()
        approved = admit(growth_cap)
        _need(isinstance(approved, dict) and approved.get("allowed") is True,
              "LATEST_NETWORK_CAPACITY_BLOCKED")
        answer = chosen_executor(endpoint, parameters, output, **kwargs)
        inventory()
        return answer

    cache = CaptureCache(root, budget, config=dict(offline=not execute_network, retry_failed=False,
        allow_test_fixture=allow_test_fixture, request_timeout_seconds=policy["request_timeout_seconds"],
        max_http_requests_per_request=1, source_file_bytes=selected["source_bytes"],
        index_file_bytes=MIB, max_cache_entries=512), executor=guarded)
    with budget.phase("capture"), budget.meter_files():
        if mode == "scoped-history":
            codes, sessions, specs = legacy._scope(codes, sessions)
        else:
            _need(not codes and not sessions, "MARKET_SESSION_USES_ORIGINAL_CALENDAR_LATEST_ONLY")
            specs, codes, sessions = [], [], []
        calendar = _calendar(cache, calendar_record, sessions)
        latest = calendar["latest_completed_session"]
        if mode == "market-session":
            sessions = [latest]
            specs = [RequestSpec(endpoint, {"date": latest}, latest, role="FULL_MARKET_CONTEXT")
                     for endpoint in ("market_daily", "master_all")]
            known, results, views, originals, blocked, batches = {}, {}, {}, {}, [], []
            for spec in specs:
                legacy._metadata_paths(root, spec)
                try:
                    record = cache.lookup(spec)
                except ValueError as error:
                    if str(error) != "UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY":
                        raise
                    blocked.append(dict(request_keys=[spec.key], reason=str(error)))
                    record = None
                known[spec.key] = record
                if record is None:
                    batches.append((spec, [spec]))
                else:
                    results[spec.key] = cache.verify(record)
                    legacy._history_clock(record.metadata)
            previous_requests = len(batches)
        else:
            known, results, views, originals, blocked, batches, previous_requests = _history_plan(cache, specs, calendar)
        sdk = sum(len(batch.parameters["codes"])+2 if batch.endpoint == "history" else 3 for batch, _ in batches)
        _need(len(batches) <= policy["max_requests"] and sdk <= policy["max_sdk_calls"],
              "COMPLETE_LATEST_REQUEST_BUDGET_REQUIRED")
        base = dict(schema_version=VERSION, profile=profile, mode=mode, operation=operation,
            latest_completed_session=latest, codes=codes, sessions=sessions,
            old_daily_group_requests=previous_requests, merged_missing_requests=len(batches),
            planned_sdk_reservations=sdk, planned_requests=[batch.to_dict() for batch, _ in batches],
            blocking_attempts=blocked, original_calendar=calendar_record,
            cache_logical_bytes_before=before, growth_cap_bytes=growth_cap, limits=policy,
            network_transfer_bytes=None, network_transfer_measurement="NOT_MEASURED_BY_ORIGINAL_SDK",
            strategy_executed=False, full_premarket_ready=False, database_created=False,
            historical_67_session_backfill=False, factor_tail_promoted=False,
            coverage_type="TEST_FIXTURE" if allow_test_fixture else "RECONSTRUCTED_HISTORICAL")
        base["existing_scope_states"] = [dict(request_key=spec.key,
            status=views[spec.key]["status"] if spec.key in views else
                   (legacy._row_status(results[spec.key], spec.parameters["codes"][0], spec.business_session)
                    if mode == "scoped-history" else "COMPLETE" if results[spec.key]["status"] == "COMPLETE"
                    and results[spec.key]["pagination_complete"] is True else "UNKNOWN_PARENT_INCOMPLETE")
                   if known.get(spec.key) is not None else "MISSING_OR_BLOCKED") for spec in specs]
        if operation == "plan" or blocked:
            cache.terminal()
            after = inventory()
            _terminal_clock(calendar)
            return dict(base, status="BLOCKED_UNFINISHED_CAPTURE_NO_RETRY" if blocked else "PLAN_VERIFIED_ORIGINAL_SOURCES",
                new_requests=0, cache_logical_bytes_after=after, added_cache_logical_bytes=after-before,
                metrics=budget.metrics, elapsed_seconds=budget.metrics["elapsed_seconds"])
        _need(execute_network or not batches, "OFFLINE_LATEST_HAS_MISSING_SCOPES")
        fresh, batch_receipts = {}, []
        for batch, members in batches:
            metadata = cache.capture_one(batch).metadata
            result = cache.verify(metadata)
            legacy._history_clock(metadata)
            batch_receipts.append(metadata)
            if mode == "scoped-history":
                grouped = _scope_rows(result, batch, members, calendar)
                for spec in members:
                    rows = grouped.get((spec.parameters["codes"][0], spec.business_session), [])
                    complete = result["status"] == "COMPLETE" and result.get("pagination_complete") is True
                    fresh[spec.key] = legacy._range_descriptor(spec,
                        [(metadata["receipt_id"], sorted(legacy._canonical(row) for row in rows), rows, complete)],
                        {metadata["receipt_id"]: metadata}, cache_hit=False)
            else:
                fresh[batch.key] = dict(request_key=batch.key, request_spec=batch.to_dict(),
                    status="COMPLETE" if result["status"] == "COMPLETE" and result["pagination_complete"] is True
                           else "UNKNOWN_PARENT_INCOMPLETE",
                    parent_status=result["status"], cache_hit=False, receipt=metadata, actual_raw_rows=len(result["rows"]))
        shards = []
        for spec in specs:
            if spec.key in fresh or spec.key in views:
                shards.append(fresh.get(spec.key, views.get(spec.key)))
                continue
            result, metadata = results[spec.key], known[spec.key].metadata
            status = (legacy._row_status(result, spec.parameters["codes"][0], spec.business_session)
                      if mode == "scoped-history" else "COMPLETE" if result["status"] == "COMPLETE"
                      and result["pagination_complete"] is True else "UNKNOWN_PARENT_INCOMPLETE")
            shards.append(dict(request_key=spec.key, request_spec=spec.to_dict(), cache_hit=True,
                status=status, receipt=metadata, actual_raw_rows=len(result["rows"])))
        cache.terminal()
        after = inventory()
        _terminal_clock(calendar)
        complete = all(row["status"] in ("ACTUAL_RAW_ROW", "SUSPENDED_SOURCE_ROW", "COMPLETE") for row in shards)
        return dict(base, status="COMPLETE_EXPLICIT_SOURCE_SCOPE" if complete else "PARTIAL_EXPLICIT_SOURCE_SCOPE",
            shards=shards, original_range_receipts=list(originals.values()), new_batch_receipts=batch_receipts,
            new_requests=budget.metrics["calls"]["requests"], cache_hits=sum(row["cache_hit"] for row in shards),
            actual_raw_rows=sum(row["actual_raw_rows"] for row in shards), cache_logical_bytes_after=after,
            added_cache_logical_bytes=after-before, metrics=budget.metrics,
            elapsed_seconds=budget.metrics["elapsed_seconds"])


def run_registered(runtime, *, operation, mode, profile, calendar_record, codes=None, sessions=None,
                   execute_network=False, limits=None, _supervised=False, _control_directory=None):
    from scripts.codex_premarket import _prepared, _control
    _need(runtime is not None and runtime.profile["backend"] == "premarket", "REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    prepared = _prepared(runtime.profile)
    _need(prepared["source"] == ROOT and Path(sys.executable).absolute() == prepared["python"],
          "REGISTERED_LATEST_SOURCE_AND_PYTHON_REQUIRED")
    _need(not execute_network or _supervised and _control_directory is not None,
          "LATEST_NETWORK_REQUIRES_PROCESS_SUPERVISOR")
    if execute_network:
        _validate_worker_ticket(runtime, Path(_control_directory).absolute())
    policy, selected = _policy(profile, mode, limits)
    root = prepared["state"] / "cache"
    with runtime.exclusive():
        config, config_ref = _control(prepared["config"])
        _need(type(config.get("capture", {}).get("offline", False)) is bool, "REGISTERED_OFFLINE_POLICY_REQUIRED")
        _need(not execute_network or not config.get("capture", {}).get("offline", False), "REGISTERED_CAPTURE_IS_OFFLINE")
        admission = runtime.preflight(expected_growth_bytes=selected["growth_bytes"] if execute_network else 0, destination=root)
        _need(admission["allowed"] is True, "LATEST_REGISTERED_CAPACITY_BLOCKED")
        if _control_directory is not None:
            from scripts import codex_workspace as workspace
            from scripts.codex_factor_incremental import _inventory
            control = Path(_control_directory).absolute()
            _need(_supervised and control.resolve(strict=True) == control
                  and control.parent == Path(runtime.profile["report_root"])
                  and control.name.startswith("latest-capture-"), "LATEST_LOCKED_CONTROL_SCOPE")
            workspace._write(control / "locked-cache-baseline.json",
                             dict(cache_root=str(root), bytes=_inventory(root)))
        result = run_scope(cache_root=root, operation=operation, mode=mode, profile=profile,
            calendar_record=calendar_record, codes=codes, sessions=sessions, execute_network=execute_network,
            limits=policy, admit=lambda growth: runtime.preflight(expected_growth_bytes=growth, destination=root))
        runtime.check_binding()
        _need(_control(prepared["config"])[1] == config_ref, "REGISTERED_CONFIGURATION_CHANGED")
        result["capacity_after_allowed"] = runtime.preflight(expected_growth_bytes=0, destination=root)["allowed"]
        _need(result["capacity_after_allowed"], "LATEST_REGISTERED_CAPACITY_EXCEEDED_AFTER")
        result["registered_config_unchanged"] = True
        result["explicit_profile_overrides_old_operation_limits"] = True
        return result


def _failure_reason(error):
    from scripts.codex_workspace import WorkspaceError
    from stockfish_core.yaogu.premarket_runtime_v1 import BudgetExceeded
    known = (LatestCaptureError, legacy.IncrementalError, WorkspaceError, BudgetExceeded)
    return str(error)[:2048] if isinstance(error, known) else "LATEST_CAPTURE_FAILED:"+type(error).__name__


def _process_identity(pid):
    value = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5).stdout.strip()
    _need(bool(value) and len(value) < 128, "LATEST_SUPERVISOR_PARENT_IDENTITY_UNAVAILABLE")
    return dict(pid=pid, start=value)


def _validate_worker_ticket(runtime, control):
    from scripts import codex_workspace as workspace
    _need(os.name == "posix" and os.getpgrp() == os.getpid() and os.getsid(0) == os.getpid(),
          "LATEST_WORKER_REQUIRES_OWNED_PROCESS_SESSION")
    _need(control.resolve(strict=True) == control and not control.is_symlink()
          and control.parent == Path(runtime.profile["report_root"])
          and control.name.startswith("latest-capture-"), "LATEST_WORKER_CONTROL_OUTSIDE_RUNTIME")
    _need(set(path.name for path in control.iterdir()) == {"request.json", "console.txt"},
          "LATEST_WORKER_CONTROL_ALREADY_ATTEMPTED_OR_UNRECOGNIZED")
    value = workspace._read(control / "request.json", limit=MIB)
    ticket = value.get("supervisor")
    _need(isinstance(ticket, dict) and set(ticket) == {"parent", "launch_id"}
          and ticket["parent"] == _process_identity(os.getppid())
          and ticket["launch_id"] == control.name.removeprefix("latest-capture-"),
          "LATEST_SUPERVISOR_PARENT_OR_LAUNCH_CHANGED")


def supervised_command(runtime, *, operation, mode, profile, calendar_record, codes=None,
                       sessions=None, execute_network=False):
    """Supervise only this newly owned process group; retain failed attempts."""
    from scripts import codex_workspace as workspace
    from scripts.codex_premarket import _prepared, _reference
    from scripts.codex_factor_incremental import _inventory
    from stockfish_core.yaogu.premarket_workflow_v1 import _tree_sample, _owned_group_members, _stop_owned
    _need(os.name == "posix", "LATEST_PROCESS_SUPERVISOR_REQUIRES_POSIX")
    _need(runtime is not None and runtime.profile["backend"] == "premarket", "REGISTERED_PREMARKET_RUNTIME_REQUIRED")
    prepared = _prepared(runtime.profile)
    _need(prepared["source"] == ROOT and Path(sys.executable).absolute() == prepared["python"],
          "REGISTERED_LATEST_SOURCE_AND_PYTHON_REQUIRED")
    policy, selected = _policy(profile, mode)
    cache_root, report_root = prepared["state"] / "cache", Path(runtime.profile["report_root"])
    source_paths = [Path(__file__).resolve(), Path(legacy.__file__).resolve(),
                    ROOT / "scripts" / "codex_factor_incremental.py", prepared["config"]]
    source_before = [_reference(path, maximum=MIB) for path in source_paths]
    launch_id = uuid.uuid4().hex
    request = dict(schema_version=VERSION, operation=operation, mode=mode, profile=profile,
                   calendar_record=calendar_record, codes=codes, sessions=sessions,
                   execute_network=execute_network,
                   supervisor=dict(parent=_process_identity(os.getpid()), launch_id=launch_id))
    _need(len(legacy._canonical(request)) <= MIB, "LATEST_SUPERVISOR_REQUEST_SIZE_LIMIT")
    with runtime.exclusive():
        _need(runtime.preflight(expected_growth_bytes=2*MIB, destination=report_root)["allowed"] is True,
              "LATEST_SUPERVISOR_CAPACITY_BLOCKED")
        control = report_root / ("latest-capture-" + launch_id)
        workspace._directory(control, create=True)
        workspace._write(control / "request.json", request)
        cache_before = _inventory(cache_root)
    # Worker alone holds the full-operation resource lock. The supervisor must
    # not hold it while waiting for that worker.
    control_identity = (control.stat().st_dev, control.stat().st_ino)
    command = [str(prepared["python"]), "-I", "-B", str(Path(__file__).resolve()), operation,
        "--runtime", runtime.profile["name"], "--registry-root", str(runtime.registry_root),
        "--mode", mode, "--profile", profile, "--worker-control", str(control)]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    process, peak, peak_growth, started = None, 0, 0, time.monotonic()

    def monitor():
        nonlocal peak_growth
        _need(not control.is_symlink() and (control.stat().st_dev, control.stat().st_ino) == control_identity,
              "LATEST_SUPERVISOR_DIRECTORY_CHANGED")
        _need(_inventory(control) <= 2*MIB, "LATEST_SUPERVISOR_OUTPUT_LIMIT")
        # The worker publishes its own cache baseline while holding the total
        # lock. A prior independently authorised task may finish before it.
        ready = control / "locked-cache-baseline.json"
        if ready.exists():
            state = workspace._read(ready, limit=8192)
            _need(set(state) == {"cache_root", "bytes"} and state["cache_root"] == str(cache_root)
                  and type(state["bytes"]) is int and state["bytes"] >= 0, "LATEST_LOCKED_BASELINE_INVALID")
            actual = _inventory(cache_root)
            peak_growth = max(peak_growth, actual-state["bytes"])
            _need(actual <= policy["cache_bytes"] and actual-state["bytes"] <= selected["growth_bytes"],
                  "LATEST_SUPERVISOR_CACHE_GROWTH_LIMIT")

    try:
        with (control / "console.txt").open("xb") as console:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=console,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            while process.poll() is None:
                try:
                    peak = max(peak, _tree_sample(process.pid))
                except Exception:
                    if process.poll() is None:
                        raise
                _need(time.monotonic()-started <= policy["max_seconds"], "LATEST_SUPERVISOR_TIME_LIMIT")
                _need(peak <= policy["tree_rss_bytes"], "LATEST_SUPERVISOR_TREE_RSS_LIMIT")
                monitor()
                time.sleep(.25)
            code = process.wait()
        _need(not _owned_group_members(process.pid), "LATEST_OWNED_DESCENDANTS_REMAIN")
        monitor()
        output = workspace._read(control / "result.json", limit=MIB)
        _need(output.get("schema_version") == VERSION, "LATEST_WORKER_RESULT_SCHEMA")
        if output.get("status") == "FAILED":
            raise LatestCaptureError(output["error"])
        _need(code in (0, 2) and output.get("status") in {
            "COMPLETE_EXPLICIT_SOURCE_SCOPE", "PARTIAL_EXPLICIT_SOURCE_SCOPE",
            "PLAN_VERIFIED_ORIGINAL_SOURCES", "BLOCKED_UNFINISHED_CAPTURE_NO_RETRY"},
            "LATEST_WORKER_FAILED")
        _need(workspace._read(control / "request.json", limit=MIB) == request,
              "LATEST_SUPERVISOR_REQUEST_CHANGED")
        _need([_reference(path, maximum=MIB) for path in source_paths] == source_before,
              "LATEST_IMPLEMENTATION_OR_CONFIGURATION_CHANGED")
        output["external_supervision"] = dict(exit_code=code, reaped=True, peak_tree_rss_bytes=peak,
            elapsed_seconds=time.monotonic()-started, control_directory=str(control),
            peak_added_cache_bytes=peak_growth, initial_cache_bytes=cache_before,
            enforcement="250MS_SAMPLED_PROCESS_TREE_TIME_RSS_AND_CACHE_GROWTH_NOT_FILESYSTEM_QUOTA")
        workspace._write(control / "completion.json", output)
        return output
    except BaseException as error:
        cleanup_error = None
        if process is not None:
            try:
                _stop_owned(process)
            except Exception as cleanup:
                cleanup_error = type(cleanup).__name__
        workspace._write(control / "failure.json", dict(schema_version=VERSION,
            status="FAILED_OR_UNKNOWN", reason=_failure_reason(error), cleanup_error=cleanup_error,
            exit_code=process.returncode if process else None, peak_tree_rss_bytes=peak,
            peak_added_cache_bytes=peak_growth, elapsed_seconds=time.monotonic()-started))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "sync"))
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--registry-root")
    parser.add_argument("--mode", choices=("scoped-history", "market-session"), required=True)
    parser.add_argument("--profile", choices=tuple(PROFILES), required=True)
    parser.add_argument("--calendar-record", type=Path)
    parser.add_argument("--codes", nargs="+")
    parser.add_argument("--sessions", nargs="+")
    parser.add_argument("--execute-network", action="store_true")
    parser.add_argument("--worker-control", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    worker_control = None
    try:
        from scripts import codex_workspace as workspace
        runtime = workspace.load_runtime(args.runtime, args.registry_root)
        if args.worker_control is None:
            _need(args.calendar_record is not None, "ORIGINAL_CALENDAR_CONTROL_REQUIRED")
            record = legacy._calendar_control(args.calendar_record)
            values = dict(operation=args.operation, mode=args.mode, profile=args.profile,
                          calendar_record=record, codes=args.codes, sessions=args.sessions,
                          execute_network=args.execute_network)
            result = supervised_command(runtime, **values) if args.execute_network else run_registered(runtime, **values)
        else:
            candidate = args.worker_control.expanduser().absolute()
            _need(candidate.resolve(strict=True) == candidate and not candidate.is_symlink()
                  and candidate.parent == Path(runtime.profile["report_root"])
                  and candidate.name.startswith("latest-capture-"), "LATEST_WORKER_CONTROL_OUTSIDE_RUNTIME")
            worker_control = candidate
            request = workspace._read(worker_control / "request.json", limit=MIB)
            _need(set(request) == {"schema_version", "operation", "mode", "profile", "calendar_record", "codes", "sessions", "execute_network", "supervisor"}
                  and request.pop("schema_version") == VERSION and request["operation"] == args.operation
                  and request["mode"] == args.mode and request["profile"] == args.profile,
                  "LATEST_WORKER_REQUEST_MISMATCH")
            request.pop("supervisor")
            # This callback records the cache after the worker owns the total
            # resource lock; the supervisor never mistakes another job's writes
            # for this job's allowance.
            result = run_registered(runtime, **request, _supervised=True, _control_directory=worker_control)
            workspace._write(worker_control / "result.json", result)
        if worker_control is None:
            print(legacy._canonical(result).decode())
        return 0 if result["status"] in ("PLAN_VERIFIED_ORIGINAL_SOURCES", "COMPLETE_EXPLICIT_SOURCE_SCOPE") else 2
    except Exception as error:
        result = dict(schema_version=VERSION, status="FAILED", error=_failure_reason(error))
        if worker_control is not None:
            workspace._write(worker_control / "result.json", result)
        else:
            print(json.dumps(result))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
