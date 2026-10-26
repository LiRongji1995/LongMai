"""Registered daily entry with a complete, bounded shared premarket workflow.

V1 remains byte-for-byte compatible for saved results. New runs have their own
execution identity; a new identity never authorizes retrying an existing request.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_daily as legacy
from scripts import codex_premarket as original
from scripts import codex_workspace as workspace

VERSION = "CODEX_DAILY_EXECUTION_PIPELINE_V2"
need = original._need


def _registered(runtime, argv):
    prepared = original._prepared(runtime.profile)
    script = prepared["source"] / "scripts/codex_daily_v2.py"
    original._fp(script)
    if (ROOT == prepared["source"] and Path(sys.executable).absolute() == prepared["python"]
            and sys.pycache_prefix is not None and not Path(sys.pycache_prefix).exists()):
        return None
    with tempfile.TemporaryDirectory(prefix="stockfish-daily-v2-") as directory:
        command = [str(prepared["python"]), "-I", "-B", "-X",
            "pycache_prefix=" + str(Path(directory) / "absent"), str(script), *argv]
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.call(command, cwd=prepared["source"], env=env)


def _workflow():
    from scripts import codex_premarket_shared_v1
    return codex_premarket_shared_v1


def _plan(runtime):
    from scripts import codex_premarket_preflight_v1
    return codex_premarket_preflight_v1.plan(runtime)


def _snapshot(prepared):
    value = original._snapshot(prepared)
    # Same explicit closure that the actual worker and supervisor authenticate.
    value["shared_execution_sources"] = _workflow().code_refs()
    return value


def _same(prepared, expected):
    need(_snapshot(prepared) == expected, "DAILY_SHARED_IMPLEMENTATION_OR_CONFIG_CHANGED")


def _prior_request(runtime, identity):
    """Called again inside run_once's OS lock, before starting a new worker.

    Different source/configuration identities must not make a failed, uncertain,
    or already completed request disappear. Explicit reconciliation is separate.
    """
    directory = runtime.directory / "operations"
    if not directory.exists():
        return
    need(not directory.is_symlink(), "DAILY_SHARED_OPERATION_DIRECTORY_ALIAS")
    paths = sorted(directory.glob("*.json"))
    need(len(paths) <= 10000, "DAILY_SHARED_OPERATION_SCAN_LIMIT")
    for path in paths:
        record = workspace._read(path)
        other = record.get("identity", {})
        if other == identity:
            continue
        if (other.get("backend") == "premarket"
                and other.get("request_key") == identity["request_key"]):
            need((other.get("observation_date"), other.get("target_session")) ==
                 (identity["observation_date"], identity["target_session"]),
                 "DAILY_SHARED_REQUEST_KEY_REUSED_ACROSS_SESSIONS")
            raise original.PremarketError("DAILY_SHARED_EXISTING_REQUEST_RECONCILE_NO_RETRY")


def _validate(prepared, snapshot, receipt, observation, target, expected_engine):
    workflow = _workflow()
    _same(prepared, snapshot)
    need(receipt.get("schema_version") == VERSION, "DAILY_SHARED_RECEIPT_VERSION")
    need(receipt.get("observation_date") == observation and receipt.get("target_session") == target,
         "DAILY_SHARED_RECEIPT_SESSIONS")
    job = original._job_path(prepared, receipt["job"])
    final, final_ref = original._control(job / "final-control.json")
    need(final_ref == receipt["final"], "DAILY_SHARED_FINAL_CHANGED")
    need(final.get("schema_version") == workflow.VERSION and final.get("status") in original.SUCCESS
         and receipt.get("status") == final["status"]
         and final.get("job") == str(job)
         and final.get("external_worker_exit_code") == original.SUCCESS[final["status"]]
         and receipt.get("execution_exit_code") == final["external_worker_exit_code"]
         and final.get("external_worker_reaped") is True
         and final.get("core_verified") is True
         and final.get("whole_workflow_wall_enforced") is True
         and final.get("implementation_epoch_verified") is True
         and final.get("source_terminal_binding_verified") is True,
         "DAILY_SHARED_SUCCESSFUL_SEALED_CORE_REQUIRED")
    need(receipt.get("source_authority_restored") is False
         and receipt.get("result_role") == "ARCHIVED_REUSE_NOT_NEW_CAPTURE",
         "DAILY_SHARED_ARCHIVE_AUTHORITY_CLAIM")
    refs = []
    for name in ("completion", "configuration", "code_registry"):
        original._inside_job(final[name], job)
        refs.append(final[name])
    completion, completion_ref = original._control(Path(final["completion"]["path"]))
    need(completion_ref == final["completion"] == receipt["completion"]
         and completion.get("schema_version") == workflow.VERSION
         and completion.get("status") == final["status"] and completion.get("core_verified") is True
         and completion.get("observation_date") == observation and completion.get("target_session") == target,
         "DAILY_SHARED_COMPLETION_BINDING")
    need(bool(completion.get("run_id")) and completion["run_id"] == receipt["run_id"]
         and completion.get("strategy_id") == "SHORT_HORIZON_STRENGTH_R2"
         and receipt.get("strategy_id") == completion["strategy_id"]
         and receipt.get("theme_status") == completion.get("theme_status"),
         "DAILY_SHARED_RUN_BINDING")
    need(receipt.get("execution_engine") in ("original", "shared")
         and final.get("execution_engine") == completion.get("execution_engine") ==
             receipt["execution_engine"] == expected_engine,
         "DAILY_SHARED_EXECUTION_ENGINE_BINDING")
    expected_sessions = receipt.get("expected_sessions")
    need(isinstance(expected_sessions, list) and len(expected_sessions) == 3
         and expected_sessions[:2] == [observation, target]
         and final.get("expected_sessions") == completion.get("expected_sessions") == expected_sessions,
         "DAILY_SHARED_EXECUTION_SESSION_BINDING")
    need(set(completion.get("core_artifacts") or {}) == original.CORE_FILES,
         "DAILY_SHARED_COMPLETE_CORE_REQUIRED")
    display = completion.get("display") or {}
    need(display == final.get("display") and isinstance(display.get("artifacts"), dict)
         and len(display["artifacts"]) == 3
         and receipt.get("display_artifacts") == {key: value["path"] for key, value in display["artifacts"].items()},
         "DAILY_SHARED_COMPLETE_DISPLAY_REQUIRED")
    for role in ("core_artifacts", "theme_artifacts"):
        refs.extend((completion.get(role) or {}).values())
    refs.extend(display["artifacts"].values())
    if completion.get("news") is not None:
        refs.append(completion["news"]["metadata"])
    for reference in refs:
        original._inside_job(reference, job)
    from stockfish_core.yaogu import premarket_workflow_v1 as frozen
    actual_config = frozen._read_control(final["configuration"]["path"])
    need(actual_config == frozen.load_config(str(prepared["config"])), "DAILY_SHARED_EXECUTED_CONFIG_CHANGED")
    need(frozen._read_control(final["code_registry"]["path"]) == workflow.code_refs(),
         "DAILY_SHARED_EXECUTED_SOURCE_CHANGED")
    verified = workflow.verify_job(job)
    need(verified.get("archived_bytes_verified") is True
         and verified.get("source_authority_restored") is False
         and verified.get("status") == final["status"] and verified.get("job") == str(job),
         "DAILY_SHARED_ARCHIVE_VERIFICATION_FAILED")
    gate = frozen._latest_gate(completion["calendar_rows"], observation, target)
    _same(prepared, snapshot)
    for reference in refs:
        need(original._fp(Path(reference["path"])) == reference["fp"], "DAILY_SHARED_FINAL_PHYSICAL_CHANGE")
    need(original._reference(job / "final-control.json") == final_ref, "DAILY_SHARED_FINAL_CHANGED")
    return dict(verified=True, archived_bytes_verified=True, currentness=gate,
                source_authority_restored=False, complete_daily_delivery=True)


def _premarket(runtime, args):
    observation, target = str(args.observation_date), str(args.target_session)
    need(date.fromisoformat(observation).isoformat() == observation
         and date.fromisoformat(target).isoformat() == target and observation < target,
         "DAILY_SHARED_ORDERED_SESSIONS_REQUIRED")
    need(isinstance(args.request_key, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", args.request_key),
         "DAILY_SHARED_REQUEST_KEY_REQUIRED")
    prepared = original._prepared(runtime.profile)
    need(prepared["source"] == ROOT and Path(sys.executable).absolute() == prepared["python"],
         "DAILY_SHARED_REGISTERED_SOURCE_AND_PYTHON_REQUIRED")
    snapshot = _snapshot(prepared)
    identity = dict(schema_version=VERSION, backend="premarket", state_root=str(prepared["state"]),
        source_root=str(prepared["source"]), python_path=str(prepared["python"]),
        configuration=snapshot["configuration"], python=snapshot["python"],
        execution_sources_sha256=hashlib.sha256(workspace._canonical(snapshot)).hexdigest(),
        observation_date=observation, target_session=target, request_key=args.request_key,
        execution_engine=args.engine)
    # Preflight is read-only and deliberately outside run_once: an inadmissible
    # request must not leave SUBMITTING/FAILED journal records or start collection.
    with runtime.exclusive():
        _prior_request(runtime, identity)
        key = workspace._digest({"scope": runtime.scope, "identity": identity})
        existing = runtime.directory / "operations" / (key + ".json")
        already_claimed = existing.exists() or existing.is_symlink()
    if not already_claimed:
        plan = _plan(runtime)
        if not plan.get("execution_admitted"):
            return 4, dict(schema_version=VERSION, status="BLOCKED_BEFORE_COLLECTION", preflight=plan,
                network_calls=0, worker_started=False, request_claimed=False, retry_authorized=False,
                complete_daily_delivery=False)
        need(plan.get("observation_date") == observation and plan.get("target_session") == target,
             "DAILY_SHARED_REQUEST_NOT_LATEST_SESSION")
        _same(prepared, snapshot)

    from stockfish_core.yaogu import premarket_workflow_v1 as frozen
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget
    limits = frozen.load_config(str(prepared["config"]))["limits"]
    execution_budget = None

    def new_wrapper_budget():
        # Budget's file meter has four worker phase names. This separate parent
        # meter uses its capture counter for the stricter supervisor allocation;
        # it is never credited as worker capture allowance. Child supervision
        # reads are charged into the same parent envelope after it returns.
        return Budget({**limits, "capture_read_bytes": min(limits["capture_read_bytes"],
                                                         limits["supervisor_read_bytes"])})

    def action():
        nonlocal execution_budget
        execution_budget = new_wrapper_budget()
        # The shared coordinator's lock is now held. Re-evaluate the full
        # source/date/capacity admission under that lock; its read-only diagnostic
        # allocation remains distinct, while elapsed time consumes this action.
        fresh_plan = _plan(runtime)
        need(fresh_plan.get("execution_admitted") is True,
             "DAILY_SHARED_ADMISSION_CHANGED_BEFORE_EXECUTION")
        need(fresh_plan.get("observation_date") == observation and fresh_plan.get("target_session") == target,
             "DAILY_SHARED_REQUEST_NOT_LATEST_SESSION")
        planning_clock = datetime.fromisoformat(fresh_plan["planned_at"].replace("Z", "+00:00"))
        need(planning_clock.utcoffset() is not None, "DAILY_SHARED_AWARE_PLAN_CLOCK_REQUIRED")
        local_date = str(planning_clock.astimezone(timezone(timedelta(hours=8))).date())
        execution_budget.check("locked_admission_complete", force=True)
        with execution_budget.phase("capture"), execution_budget.meter_files():
            _prior_request(runtime, identity)
            _same(prepared, snapshot)
            config = frozen.load_config(str(prepared["config"]))
        started = execution_budget.started
        code, final = _workflow().supervise(config, progress=False, started_at=execution_budget.started,
                                           expected_sessions=(observation, target, local_date), engine=args.engine)
        execution_budget.charge_read(final.get("supervisor_returned_read_bytes", 0))
        execution_budget.check("supervisor_return", force=True)
        with execution_budget.phase("capture"), execution_budget.meter_files():
            _same(prepared, snapshot)
            need(code in (0, 2) and final.get("status") in original.SUCCESS
                 and code == original.SUCCESS[final["status"]],
                 "DAILY_SHARED_EXECUTION_FAILED_NO_AUTOMATIC_RETRY")
            job = original._job_path(prepared, final["job"])
            saved, final_ref = original._control(job / "final-control.json")
            need(saved == final, "DAILY_SHARED_RETURNED_FINAL_CHANGED")
            completion, completion_ref = original._control(Path(final["completion"]["path"]))
            return dict(schema_version=VERSION, status=final["status"], job=str(job),
                execution_engine=args.engine,
                expected_sessions=[observation, target, local_date],
                observation_date=observation, target_session=target, run_id=completion["run_id"],
                strategy_id=completion.get("strategy_id"), theme_status=completion.get("theme_status"),
                final=final_ref, completion=completion_ref, execution_exit_code=code,
                execution_seconds=time.monotonic()-started,
                display_artifacts={key: ref["path"] for key, ref in (final.get("display") or {}).get("artifacts", {}).items()},
                source_authority_restored=False, result_role="ARCHIVED_REUSE_NOT_NEW_CAPTURE")

    def validate(receipt):
        nonlocal execution_budget
        fresh = execution_budget is not None
        if execution_budget is None:
            execution_budget = new_wrapper_budget()
        with execution_budget.phase("capture"), execution_budget.meter_files():
            checked = _validate(prepared, snapshot, receipt, observation, target, args.engine)
        checked["parent_verification_metrics"] = execution_budget.metrics
        checked["parent_budget_scope"] = ("FRESH_ACTION_SUPERVISION_AND_RECEIPT_VALIDATION" if fresh
                                          else "CURRENT_READONLY_REUSE_VALIDATION")
        checked["parent_read_limit_source"] = "MIN_ORIGINAL_SUPERVISOR_AND_CAPTURE_READ_LIMITS"
        return checked

    result = runtime.run_once(identity, action, validate)
    # Do not erase a valid saved run if the final coordinator bookkeeping takes
    # too long; refuse this delivery while retaining the original durable record.
    execution_budget.check("coordinator_return", force=True)
    result["complete_daily_delivery"] = True
    result["execution_version"] = VERSION
    result["daily_v1_readiness"] = "UNKNOWN_NOT_EXECUTED"
    result["market_edge"] = "NOT_ESTABLISHED"
    return result["receipt"]["execution_exit_code"], result


def parser():
    result = legacy.parser()
    # Extend V1's parser without modifying its implementation or defaults.
    commands = next(action for action in result._actions if isinstance(action, argparse._SubParsersAction))
    commands.add_parser("premarket-plan", help="完整盘前只读准入；不采集", allow_abbrev=False)
    sub = commands.add_parser("premarket", help="准入后完整共享盘前执行", allow_abbrev=False)
    sub.add_argument("--observation-date", type=date.fromisoformat, required=True)
    sub.add_argument("--target-session", type=date.fromisoformat, required=True)
    sub.add_argument("--request-key", required=True)
    sub.add_argument("--engine", choices=("original", "shared"), default="original",
                     help="默认原成熟题材路径；shared为显式性能实验，未证明真实市场提速")
    return result


def execute(runtime, args):
    need(runtime is not None and runtime.profile.get("backend") == "premarket", "DAILY_REGISTERED_RUNTIME_REQUIRED")
    if args.command == "premarket-plan":
        return 0, _plan(runtime)
    if args.command == "premarket":
        return _premarket(runtime, args)
    return legacy.execute(runtime, args)


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(selected)
    try:
        runtime = workspace.load_runtime(args.runtime, args.registry_root)
        need(runtime is not None, "DAILY_REGISTERED_RUNTIME_REQUIRED")
        dispatched = _registered(runtime, selected)
        if dispatched is not None:
            return dispatched
        code, result = execute(runtime, args)
    except KeyboardInterrupt:
        code, result = 130, dict(status="INTERRUPTED", reason="USER_INTERRUPTED")
    except Exception as error:
        code, result = 2, dict(status="BLOCKED_OR_FAILED", reason=legacy._safe_reason(error))
    print(json.dumps({**result, "entry_version": VERSION, "command_exit_code": code}, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
