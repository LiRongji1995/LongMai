"""Single local entry for bounded research, saved results and maintenance.

The existing registration, operation journal and source budgets remain in place.
Saved results keep their original dates and never gain fresh source authority.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.file_integrity import (
    content_equal, content_reference_equal, content_fingerprint, verify_content_reference,
)

from scripts import codex_premarket as original
from scripts import codex_workspace as workspace

VERSION = "CODEX_DAILY_EXECUTION_PIPELINE_V2"
need = original._need


def _registered(runtime, argv):
    prepared = original._prepared(runtime.profile)
    script = prepared["source"] / "scripts/codex_daily_v2.py"
    original._fp(script)
    if (ROOT == prepared["source"] and workspace.interpreter_path(sys.executable) == prepared["python"]
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


def _plan(runtime, *, collect_missing=False, allow_test_fixture=False):
    from scripts import codex_premarket_preflight_v1
    return codex_premarket_preflight_v1.plan(runtime, collect_missing=collect_missing,
                                            allow_test_fixture=allow_test_fixture)


def _snapshot(prepared):
    value = original._snapshot(prepared)
    # Same explicit closure that the actual worker and supervisor authenticate.
    value["shared_execution_sources"] = _workflow().code_refs()
    return value


def _same(prepared, expected):
    need(content_equal(_snapshot(prepared), expected), "DAILY_SHARED_IMPLEMENTATION_OR_CONFIG_CHANGED")


def _prior_request(runtime, identity):
    """Called again inside run_once's OS lock, before starting a new worker.

    Different source/configuration identities must not make a failed, uncertain,
    or already completed request disappear. Explicit reconciliation is separate.
    """
    if getattr(runtime, "ancestor_ledgers", None):
        runtime.guard_ancestor_request(identity)
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
        if other.get("backend") == "premarket":
            same_sessions = (other.get("observation_date"), other.get("target_session")) == (
                identity["observation_date"], identity["target_session"])
            same_key = other.get("request_key") == identity["request_key"]
            need(not same_key or same_sessions, "DAILY_SHARED_REQUEST_KEY_REUSED_ACROSS_SESSIONS")
            if same_key or same_sessions:
                raise original.PremarketError("DAILY_SHARED_EXISTING_REQUEST_RECONCILE_NO_RETRY")


def _validate(prepared, snapshot, receipt, observation, target, expected_engine, *, recovery_context=None,
              effective_limits=None, continuation_context=None, premarket_deadline=None):
    workflow = _workflow()
    _same(prepared, snapshot)
    need(receipt.get("schema_version") == VERSION, "DAILY_SHARED_RECEIPT_VERSION")
    need(receipt.get("observation_date") == observation and receipt.get("target_session") == target,
         "DAILY_SHARED_RECEIPT_SESSIONS")
    job = original._job_path(prepared, receipt["job"])
    final, final_ref = original._control(job / "final-control.json")
    need(content_reference_equal(final_ref, receipt["final"]), "DAILY_SHARED_FINAL_CHANGED")
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
    need(content_reference_equal(completion_ref, final["completion"])
         and content_reference_equal(completion_ref, receipt["completion"])
         and completion.get("schema_version") == workflow.VERSION
         and completion.get("status") == final["status"] and completion.get("core_verified") is True
         and completion.get("observation_date") == observation and completion.get("target_session") == target,
         "DAILY_SHARED_COMPLETION_BINDING")
    if premarket_deadline is not None:
        need(receipt.get("premarket_deadline") == final.get("premarket_deadline") ==
             completion.get("premarket_deadline") == premarket_deadline,
             "REFRESH_PREMARKET_DEADLINE_BINDING")
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
    need(content_equal(display, final.get("display")) and isinstance(display.get("artifacts"), dict)
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
    expected_config = frozen.load_config(str(prepared["config"]))
    if effective_limits is not None:
        _bounded_continuation_limits(expected_config["limits"], effective_limits)
        expected_config["limits"] = effective_limits
    if recovery_context is not None:
        expected_config["recovery_context"] = recovery_context
    if continuation_context is not None:
        expected_config["continuation_context"] = continuation_context
    need(actual_config == expected_config, "DAILY_SHARED_EXECUTED_CONFIG_CHANGED")
    need(content_equal(frozen._read_control(final["code_registry"]["path"]), workflow.code_refs()),
         "DAILY_SHARED_EXECUTED_SOURCE_CHANGED")
    verified = workflow.verify_job(job)
    need(verified.get("archived_bytes_verified") is True
         and verified.get("source_authority_restored") is False
         and verified.get("status") == final["status"] and verified.get("job") == str(job),
         "DAILY_SHARED_ARCHIVE_VERIFICATION_FAILED")
    gate = frozen._latest_gate(completion["calendar_rows"], observation, target)
    _same(prepared, snapshot)
    for reference in refs:
        need(verify_content_reference(reference), "DAILY_SHARED_FINAL_CONTENT_CHANGE")
    need(content_reference_equal(original._reference(job / "final-control.json"), final_ref), "DAILY_SHARED_FINAL_CHANGED")
    return dict(verified=True, archived_bytes_verified=True, currentness=gate,
                source_authority_restored=False, complete_daily_delivery=True)


def _premarket(runtime, args):
    observation, target = str(args.observation_date), str(args.target_session)
    need(args.engine == "original", "REMOVED_EXPERIMENTAL_ENGINE")
    need(date.fromisoformat(observation).isoformat() == observation
         and date.fromisoformat(target).isoformat() == target and observation < target,
         "DAILY_SHARED_ORDERED_SESSIONS_REQUIRED")
    need(isinstance(args.request_key, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", args.request_key),
         "DAILY_SHARED_REQUEST_KEY_REQUIRED")
    prepared = original._prepared(runtime.profile)
    need(prepared["source"] == ROOT and workspace.interpreter_path(sys.executable) == prepared["python"],
         "DAILY_SHARED_REGISTERED_SOURCE_AND_PYTHON_REQUIRED")
    snapshot = _snapshot(prepared)
    recovery_context = runtime.capture_recovery_context()
    identity = dict(schema_version=VERSION, backend="premarket", state_root=str(prepared["state"]),
        source_root=str(prepared["source"]), python_path=str(prepared["python"]),
        configuration=snapshot["configuration"], python=snapshot["python"],
        execution_sources_sha256=hashlib.sha256(workspace._canonical(snapshot)).hexdigest(),
        observation_date=observation, target_session=target, request_key=args.request_key,
        execution_engine=args.engine)
    collect_missing = getattr(args, "collect_missing", False)
    need(type(collect_missing) is bool, "DAILY_EXPLICIT_COLLECTION_MODE_REQUIRED")
    if collect_missing:
        identity["explicit_missing_collection"] = True

    def admission_plan():
        options = {}
        if collect_missing:
            options["collect_missing"] = True
        if getattr(args, "allow_test_fixture", False):
            options["allow_test_fixture"] = True
        return _plan(runtime, **options)

    def admitted(plan):
        return plan.get("collection_admitted" if collect_missing else "execution_admitted") is True

    # Preflight is read-only and deliberately outside run_once: an inadmissible
    # request must not leave SUBMITTING/FAILED journal records or start collection.
    with runtime.exclusive():
        _prior_request(runtime, identity)
        key = workspace._digest({"scope": runtime.scope, "identity": identity})
        existing = runtime.directory / "operations" / (key + ".json")
        already_claimed = existing.exists() or existing.is_symlink()
    if not already_claimed:
        plan = admission_plan()
        if not admitted(plan):
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
        fresh_plan = admission_plan()
        need(admitted(fresh_plan),
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
        recovery_options = {} if recovery_context is None else dict(recovery_context=recovery_context)
        code, final = _workflow().supervise(config, progress=False, started_at=execution_budget.started,
                                           expected_sessions=(observation, target, local_date), engine=args.engine,
                                           **recovery_options)
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
            need(content_equal(runtime.capture_recovery_context(), recovery_context),
                 "DAILY_SHARED_RECOVERY_CONTEXT_CHANGED")
            validation_options = {} if recovery_context is None else dict(recovery_context=recovery_context)
            checked = _validate(prepared, snapshot, receipt, observation, target, args.engine,
                                **validation_options)
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


def _bounded_continuation_limits(original_limits, remaining_limits):
    """A continuation consumes an existing finite envelope, never a new one."""
    need(isinstance(remaining_limits, dict) and set(remaining_limits) == set(original_limits),
         "CONTINUATION_LIMIT_KEYS_CHANGED")
    for name, maximum in original_limits.items():
        value = remaining_limits[name]
        need(type(maximum) in (int, float) and type(value) in (int, float)
             and 0 < value <= maximum, "CONTINUATION_LIMIT_NOT_WITHIN_ORIGINAL:" + name)


def _continue_premarket(runtime, args):
    """One explicitly authorized continuation attached to the original failure."""
    from scripts import codex_premarket_continuation as continuation
    from stockfish_core.yaogu import premarket_workflow_v1 as frozen
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget

    planning = args.command == "premarket-continue-plan"
    need(planning or args.authorize_one_source_retry, "CONTINUATION_EXPLICIT_ONE_SOURCE_AUTHORIZATION_REQUIRED")
    reconciliation_path = getattr(args, "factor_reconciliation", None)
    reconciliation_sha = getattr(args, "expected_reconciliation_sha256", None)
    reconcile_authorized = getattr(args, "authorize_existing_factor_reconciliation", False)
    need((reconciliation_path is None) == (reconciliation_sha is None),
         "CONTINUATION_RECONCILIATION_PATH_AND_SHA_REQUIRED")
    need(not reconcile_authorized or reconciliation_path is not None,
         "CONTINUATION_RECONCILIATION_EVIDENCE_REQUIRED")
    need(planning or reconciliation_path is None or reconcile_authorized,
         "CONTINUATION_EXPLICIT_FACTOR_RECONCILIATION_AUTHORIZATION_REQUIRED")
    reconciliation_options = {}
    if reconciliation_path is not None:
        need(re.fullmatch(r"[0-9a-f]{64}", reconciliation_sha) is not None,
             "CONTINUATION_RECONCILIATION_SHA256_REQUIRED")
        reconciliation_options["factor_reconciliation"] = dict(path=str(reconciliation_path), sha256=reconciliation_sha)
    prepared = original._prepared(runtime.profile)
    need(prepared["source"] == ROOT and workspace.interpreter_path(sys.executable) == prepared["python"],
         "DAILY_SHARED_REGISTERED_SOURCE_AND_PYTHON_REQUIRED")
    proposal = continuation.prepare(runtime, operation_path=args.operation, source_job=args.source_job,
        snapshot_proof_path=args.snapshot_proof, expected_snapshot_proof_sha256=args.expected_proof_sha256,
        allowed_source_key=args.source_key, allowed_receipt_id=args.source_receipt,
        allow_test_fixture=getattr(args, "allow_test_fixture", False), **reconciliation_options)
    if planning:
        return 0, proposal
    identity = proposal["identity"]
    observation, target = identity["observation_date"], identity["target_session"]
    local_day = proposal["source_retry"]["request_spec"]["business_session"]
    expected_sessions = (observation, target, local_day)
    wrapper_budget = Budget({**proposal["remaining_limits"],
        "max_seconds": continuation.WRAPPER_PREPARE_SECONDS,
        "capture_read_bytes": continuation.WRAPPER_PREPARE_READ_BYTES})
    with wrapper_budget.phase("capture"), wrapper_budget.meter_files():
        _workflow()._expected_gate(expected_sessions)
        snapshot = _snapshot(prepared)
        recovery_context = runtime.capture_recovery_context()
        original_config = frozen.load_config(str(prepared["config"]))
        need(not original_config["capture"].get("retry_failed", False)
             and not original_config["capture"].get("offline", False)
             and not original_config["seed_sources"], "CONTINUATION_ORIGINAL_CAPTURE_POLICY_REQUIRED")
    wrapper_preparation_metrics = wrapper_budget.metrics
    execution_budget = None
    effective_limits, execution_authorization = None, None

    def action(remaining_limits, authorization):
        nonlocal execution_budget, effective_limits, execution_authorization
        _bounded_continuation_limits(original_config["limits"], remaining_limits)
        effective_limits, execution_authorization = dict(remaining_limits), authorization
        execution_budget = Budget({**effective_limits,
            "capture_read_bytes": min(effective_limits["capture_read_bytes"], effective_limits["supervisor_read_bytes"])})
        final = None
        try:
            with execution_budget.phase("capture"), execution_budget.meter_files():
                _same(prepared, snapshot)
                need(content_equal(runtime.capture_recovery_context(), recovery_context),
                     "DAILY_SHARED_RECOVERY_CONTEXT_CHANGED")
                _workflow()._expected_gate(expected_sessions)
                config = frozen.load_config(str(prepared["config"]))
                config["limits"] = effective_limits
            options = {} if recovery_context is None else dict(recovery_context=recovery_context)
            code, final = _workflow().supervise(config, progress=False, started_at=execution_budget.started,
                expected_sessions=expected_sessions, engine="original", continuation_context=authorization, **options)
            execution_budget.charge_read(final.get("supervisor_returned_read_bytes", 0))
            execution_budget.check("continuation_supervisor_return", force=True)
            with execution_budget.phase("capture"), execution_budget.meter_files():
                _same(prepared, snapshot)
                need(code in (0, 2) and final.get("status") in original.SUCCESS
                     and code == original.SUCCESS[final["status"]],
                     "CONTINUATION_EXECUTION_FAILED_NO_FURTHER_ATTEMPT")
                job = original._job_path(prepared, final["job"])
                saved, final_ref = original._control(job / "final-control.json")
                need(saved == final, "DAILY_SHARED_RETURNED_FINAL_CHANGED")
                completion, completion_ref = original._control(Path(final["completion"]["path"]))
                return dict(schema_version=VERSION, status=final["status"], job=str(job),
                    execution_engine="original", expected_sessions=list(expected_sessions),
                    observation_date=observation, target_session=target, run_id=completion["run_id"],
                    strategy_id=completion.get("strategy_id"), theme_status=completion.get("theme_status"),
                    final=final_ref, completion=completion_ref, execution_exit_code=code,
                    execution_seconds=time.monotonic()-execution_budget.started,
                    display_artifacts={key: ref["path"] for key, ref in (final.get("display") or {}).get("artifacts", {}).items()},
                    source_authority_restored=False, result_role="ARCHIVED_REUSE_NOT_NEW_CAPTURE")
        except BaseException as error:
            information = dict(parent_verification_metrics=execution_budget.metrics,
                               worker_final=None, job=None)
            if final is not None and final.get("job"):
                try:
                    job = original._job_path(prepared, final["job"])
                    information.update(worker_final=original._reference(job / "final-control.json"), job=str(job))
                except Exception:
                    # A broken failure artifact must not replace the original error.
                    pass
            error.continuation_failure = information
            raise

    def validate(receipt, checked_proposal):
        nonlocal execution_budget, effective_limits, execution_authorization
        fresh = execution_budget is not None
        if not fresh:
            # Reading a successful continuation must not create another worker.
            effective_limits = checked_proposal["remaining_limits"]
            execution_authorization = continuation.authorization(runtime, checked_proposal)
            _bounded_continuation_limits(original_config["limits"], effective_limits)
            execution_budget = Budget({**effective_limits,
                "capture_read_bytes": min(effective_limits["capture_read_bytes"], effective_limits["supervisor_read_bytes"])})
        try:
            with execution_budget.phase("capture"), execution_budget.meter_files():
                _workflow()._expected_gate(expected_sessions)
                need(content_equal(runtime.capture_recovery_context(), recovery_context),
                     "DAILY_SHARED_RECOVERY_CONTEXT_CHANGED")
                checked = _validate(prepared, snapshot, receipt, observation, target, "original",
                    recovery_context=recovery_context, effective_limits=effective_limits,
                    continuation_context=execution_authorization)
        except BaseException as error:
            error.continuation_failure = dict(parent_verification_metrics=execution_budget.metrics,
                worker_final=receipt.get("final"), job=receipt.get("job"))
            raise
        checked["parent_verification_metrics"] = execution_budget.metrics
        checked["wrapper_preparation_metrics"] = wrapper_preparation_metrics
        checked["parent_budget_scope"] = "CONTINUATION_ACTION_AND_VALIDATION" if fresh else "READONLY_CONTINUATION_REUSE"
        return checked

    execute_options = ({"authorize_existing_factor_reconciliation": True} if reconcile_authorized else {})
    result = continuation.execute(runtime, proposal, action=action, validate=validate, **execute_options)
    result.update(complete_daily_delivery=True, execution_version=VERSION,
        daily_v1_readiness="UNKNOWN_NOT_EXECUTED", market_edge="NOT_ESTABLISHED")
    return result["receipt"]["execution_exit_code"], result


def _refresh_premarket(runtime, args):
    """One same-session refresh under a sealed continuation's original budget."""
    from scripts import codex_premarket_refresh as refresh
    from scripts import codex_premarket_continuation as continuation
    from stockfish_core.yaogu import premarket_workflow_v1 as frozen
    from stockfish_core.yaogu.premarket_runtime_v1 import Budget

    planning = args.command == "premarket-refresh-plan"
    need(planning or args.authorize_missing_refresh, "REFRESH_EXPLICIT_MISSING_COLLECTION_AUTHORIZATION_REQUIRED")
    prepared = original._prepared(runtime.profile)
    need(prepared["source"] == ROOT and workspace.interpreter_path(sys.executable) == prepared["python"],
         "DAILY_SHARED_REGISTERED_SOURCE_AND_PYTHON_REQUIRED")
    proposal = refresh.prepare(runtime, operation_path=args.operation,
        expected_terminal_sha256=args.expected_terminal_sha256,
        allow_test_fixture=getattr(args, "allow_test_fixture", False))
    if planning:
        return 0, proposal
    observation, target, local_day = proposal["expected_sessions"]
    expected_sessions = (observation, target, local_day)
    wrapper_budget = Budget({**proposal["remaining_limits"],
        "max_seconds": continuation.WRAPPER_PREPARE_SECONDS,
        "capture_read_bytes": continuation.WRAPPER_PREPARE_READ_BYTES})
    with wrapper_budget.phase("capture"), wrapper_budget.meter_files():
        _workflow()._expected_gate(expected_sessions)
        snapshot = _snapshot(prepared)
        recovery_context = runtime.capture_recovery_context()
        original_config = frozen.load_config(str(prepared["config"]))
        need(not original_config["capture"].get("retry_failed", False)
             and not original_config["capture"].get("offline", False)
             and not original_config["seed_sources"], "REFRESH_ORIGINAL_CAPTURE_POLICY_REQUIRED")
    preparation_metrics = wrapper_budget.metrics
    execution_budget = None
    effective_limits = None

    def action(remaining_limits, authorization):
        nonlocal execution_budget, effective_limits
        _bounded_continuation_limits(original_config["limits"], remaining_limits)
        effective_limits = dict(remaining_limits)
        execution_budget = Budget({**effective_limits,
            "capture_read_bytes": min(effective_limits["capture_read_bytes"], effective_limits["supervisor_read_bytes"])})
        final = None
        try:
            with execution_budget.phase("capture"), execution_budget.meter_files():
                _same(prepared, snapshot)
                need(content_equal(runtime.capture_recovery_context(), recovery_context),
                     "DAILY_SHARED_RECOVERY_CONTEXT_CHANGED")
                _workflow()._expected_gate(expected_sessions)
                config = frozen.load_config(str(prepared["config"]))
                config["limits"] = effective_limits
            options = {} if recovery_context is None else dict(recovery_context=recovery_context)
            code, final = _workflow().supervise(config, progress=False, started_at=execution_budget.started,
                expected_sessions=expected_sessions, engine="original", require_premarket=True, **options)
            execution_budget.charge_read(final.get("supervisor_returned_read_bytes", 0))
            execution_budget.check("refresh_supervisor_return", force=True)
            with execution_budget.phase("capture"), execution_budget.meter_files():
                _same(prepared, snapshot)
                need(code in (0, 2) and final.get("status") in original.SUCCESS
                     and code == original.SUCCESS[final["status"]], "REFRESH_EXECUTION_FAILED_NO_RETRY")
                job = original._job_path(prepared, final["job"])
                saved, final_ref = original._control(job / "final-control.json")
                need(saved == final, "DAILY_SHARED_RETURNED_FINAL_CHANGED")
                completion, completion_ref = original._control(Path(final["completion"]["path"]))
                return dict(schema_version=VERSION, status=final["status"], job=str(job),
                    execution_engine="original", expected_sessions=list(expected_sessions),
                    observation_date=observation, target_session=target, run_id=completion["run_id"],
                    strategy_id=completion.get("strategy_id"), theme_status=completion.get("theme_status"),
                    final=final_ref, completion=completion_ref, execution_exit_code=code,
                    execution_seconds=time.monotonic()-execution_budget.started,
                    display_artifacts={key: ref["path"] for key, ref in (final.get("display") or {}).get("artifacts", {}).items()},
                    refresh_authorization=authorization, premarket_deadline=proposal["premarket_deadline"],
                    source_authority_restored=False, result_role="ARCHIVED_REUSE_NOT_NEW_CAPTURE")
        except BaseException as error:
            information = dict(parent_verification_metrics=execution_budget.metrics, worker_final=None, job=None)
            if final is not None and final.get("job"):
                try:
                    job = original._job_path(prepared, final["job"])
                    information.update(worker_final=original._reference(job / "final-control.json"), job=str(job))
                except Exception:
                    pass
            error.refresh_failure = information
            raise

    def validate(receipt, checked_proposal):
        nonlocal execution_budget, effective_limits
        fresh = execution_budget is not None
        if not fresh:
            effective_limits = checked_proposal["remaining_limits"]
            _bounded_continuation_limits(original_config["limits"], effective_limits)
            execution_budget = Budget({**effective_limits,
                "capture_read_bytes": min(effective_limits["capture_read_bytes"], effective_limits["supervisor_read_bytes"])})
        try:
            with execution_budget.phase("capture"), execution_budget.meter_files():
                _workflow()._expected_gate(expected_sessions)
                need(content_equal(runtime.capture_recovery_context(), recovery_context),
                     "DAILY_SHARED_RECOVERY_CONTEXT_CHANGED")
                checked = _validate(prepared, snapshot, receipt, observation, target, "original",
                    recovery_context=recovery_context, effective_limits=effective_limits,
                    premarket_deadline=checked_proposal["premarket_deadline"])
        except BaseException as error:
            error.refresh_failure = dict(parent_verification_metrics=execution_budget.metrics,
                worker_final=receipt.get("final"), job=receipt.get("job"))
            raise
        checked["parent_verification_metrics"] = execution_budget.metrics
        checked["wrapper_preparation_metrics"] = preparation_metrics
        checked["parent_budget_scope"] = "REFRESH_ACTION_AND_VALIDATION" if fresh else "READONLY_REFRESH_REUSE"
        return checked

    result = refresh.execute(runtime, proposal, action=action, validate=validate)
    result.update(complete_daily_delivery=True, execution_version=VERSION,
        daily_v1_readiness="UNKNOWN_NOT_EXECUTED", market_edge="NOT_ESTABLISHED")
    return result["receipt"]["execution_exit_code"], result


def _safe_reason(error):
    reason = str(error)
    return reason if re.fullmatch(r"[A-Z][A-Z0-9_:.-]{1,180}", reason) else "LOCAL_DEPENDENCY_OR_RESPONSE_SCHEMA_ERROR"


def _views():
    from scripts import codex_result_view_v1
    return codex_result_view_v1


def _prescreen_view():
    from scripts import codex_prescreen_view
    return codex_prescreen_view


def _select_view(runtime, view_id, fixture=False, *, observation_date=None, target_session=None):
    if view_id is not None:
        return view_id
    rows = _views().list_views(runtime, allow_test_fixture=fixture)["views"]
    requested = {key: str(value) for key, value in (
        ("observation_date", observation_date), ("target_session", target_session)) if value is not None}
    if requested:
        rows = [row for row in rows if all(row[key] == value for key, value in requested.items())]
        need(bool(rows), "DAILY_SEALED_RESULT_NOT_FOUND_FOR_REQUESTED_SESSION")
    need(bool(rows), "DAILY_NO_SEALED_RESULT_PUBLISH_ONCE_REQUIRED")
    return max(rows, key=lambda row: (row["observation_date"], row["decision_cutoff"], row["view_id"]))["view_id"]


def _export_new(path, content):
    """Publish complete UTF-8 bytes once; never replace a report or source."""
    path = Path(path).expanduser().absolute()
    need(path.parent.resolve(strict=True) == path.parent, "EXPORT_PARENT_ALIAS_FORBIDDEN")
    raw = content.encode("utf-8")
    _views()._write_new(path, raw)
    return dict(output=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def _operations(runtime):
    runtime.check_binding()
    rows = []
    ledgers = [(runtime.directory / "operations", runtime.scope, False)] + [
        (directory, scope, True) for directory, scope in getattr(runtime, "ancestor_ledgers", ())]
    for directory, scope, inherited in ledgers:
        need(not directory.is_symlink(), "DAILY_OPERATION_DIRECTORY_ALIAS")
        paths = sorted(directory.glob("*.json")) if directory.exists() else []
        need(len(paths) <= 10000, "DAILY_OPERATION_SCAN_LIMIT")
        for path in paths:
            record = workspace._read(path)
            identity = record.get("identity", {})
            need(record.get("scope") == scope and record.get("operation_id") == path.stem
                 and path.stem == workspace._digest(dict(scope=scope, identity=identity)),
                 "DAILY_OPERATION_IDENTITY_INVALID")
            rows.append({key: record.get(key) for key in ("operation_id", "phase", "started_at", "completed_at", "reason")}
                        | {key: identity.get(key) for key in ("request_key", "observation_date", "target_session", "backend")}
                        | dict(scope=scope, inherited=inherited))
    runtime.check_binding()
    return dict(scope=runtime.scope, operations=rows, read_only=True, retry_authorized=False)


def _database_command(runtime, args):
    """Use existing local storage and original strategy workers; no HTTP service."""
    from stockfish_core.yaogu import r2_closure_cli as local
    local._fixture_boundary(args)
    writing = args.command == "research" or (args.command == "research-outcomes" and args.update)
    if writing and args.command == "research-outcomes":
        # R1/R2 outcomes require authenticated original archives and the daily
        # closure's cutoff and prior-request checks. The generic PV/V2 worker
        # must never provide a second execution route for those saved runs.
        with local.service_context(args) as service:
            run = service.strategy_experiment(args.run_id)
            need(run.get("execution_profile") != "SHORT_HORIZON_RESEARCH_SINGLE",
                 "SHORT_HORIZON_UPDATE_REQUIRES_OUTCOMES_R2_DAILY_RUN")
    if not writing:
        with local.service_context(args) as service:
            if args.command == "history":
                if args.run_id:
                    if args.format == "csv":
                        content = service.export_strategy_experiment_csv(args.run_id, strategy_id=args.strategy)
                    else:
                        content = service.export_strategy_experiment_json(args.run_id, strategy_id=args.strategy)
                    if args.output:
                        return 0, dict(read_only=True, run_id=args.run_id, **_export_new(args.output, content))
                    return 0, dict(read_only=True, run_id=args.run_id, content=content)
                return 0, dict(read_only=True, runs=service.list_strategy_experiments(limit=args.limit))
            if args.command == "research-outcomes":
                return 0, dict(read_only=True, run_id=args.run_id, outcomes=service.strategy_outcomes(args.run_id))
            if args.command == "jobs":
                return 0, dict(read_only=True, jobs=service.jobs.get(args.job_id) if args.job_id else service.jobs.list(limit=args.limit))
    need(runtime is not None and runtime.profile.get("backend") == "premarket", "DAILY_REGISTERED_RUNTIME_REQUIRED")
    need(bool(args.input_id) and bool(args.request_key), "RESEARCH_EXPLICIT_INPUT_AND_REQUEST_KEY_REQUIRED")
    need(re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", args.request_key), "RESEARCH_REQUEST_KEY_INVALID")
    limits = workspace._read(runtime.profile["config"], limit=65536).get("limits", {})
    configured = limits.get("max_seconds")
    seconds = min(args.wait_seconds, configured) if configured is not None else args.wait_seconds
    need(1 <= seconds <= 3600, "WAIT_SECONDS_MUST_BE_1_TO_3600")
    kind = "strategy_experiments" if args.command == "research" else "strategy_outcomes"
    database = str(args.db.resolve(strict=True))
    for destination in (args.db.parent, args.state):
        need(runtime.preflight(destination=destination)["allowed"], "RESEARCH_REGISTERED_STORAGE_BUDGET_BLOCKED")
    identity = dict(schema_version="LOCAL_RESEARCH_REQUEST_V1", backend="local-research", database=database,
        command=kind, input_id=args.input_id, run_id=getattr(args, "run_id", None), request_key=args.request_key)
    # Original request records are authoritative across key/source changes.
    def prior():
        for path in sorted((runtime.directory / "operations").glob("*.json")):
            other = workspace._read(path).get("identity", {})
            if other == identity:
                continue
            same_key = other.get("request_key") == args.request_key
            same_work = (other.get("backend") == "local-research" and
                all(other.get(k) == identity[k] for k in ("database", "command", "input_id", "run_id")))
            need(not (same_key or same_work), "RESEARCH_EXISTING_REQUEST_RECONCILE_NO_RETRY")
    def action():
        prior()
        with local.service_context(args, writable=True) as service:
            service.jobs.register(kind, service._strategy_experiment_job if kind == "strategy_experiments" else service._strategy_outcome_job)
            # No startup migration/config activation and no input guessing.
            service.input(args.input_id)
            job_id = "local_" + workspace._digest(identity)
            existing = service.jobs.get(job_id)
            need(existing is None, "RESEARCH_EXISTING_JOB_RECONCILE_NO_RETRY")
            # Old public entrypoints used other job IDs. Source/config/key
            # changes cannot hide an earlier attempt for these business inputs.
            previous_kinds = (kind, "strategy_tracking") if kind == "strategy_outcomes" else (kind,)
            for previous_kind in previous_kinds:
                offset = 0
                while True:
                    jobs = service.jobs.list(kind=previous_kind, limit=1000, offset=offset)
                    for previous in jobs:
                        payload = previous.get("payload", {})
                        same = payload.get("input_id") == args.input_id
                        if kind == "strategy_outcomes":
                            same = same and payload.get("run_id") == args.run_id
                        need(not same, "RESEARCH_EXISTING_JOB_USE_HISTORY_NO_RETRY")
                    if len(jobs) < 1000:
                        break
                    offset += len(jobs)
                    need(offset < 10000, "RESEARCH_JOB_RECONCILIATION_LIMIT")
            if kind == "strategy_experiments":
                job = service.submit_strategy_experiments(input_id=args.input_id, job_id=job_id)
            else:
                job = service.submit_strategy_outcomes(args.run_id, input_id=args.input_id, job_id=job_id)
            adapter = SimpleNamespace(status=service.jobs.get, cancel=service.jobs.cancel)
            code, terminal = local._wait(adapter, job, seconds, cancel_owned=True)
            need(code == 0 and terminal.get("state") == "SUCCEEDED", "RESEARCH_JOB_FAILED_OR_UNCERTAIN_NO_RETRY")
            return dict(job_id=job_id, run_id=terminal["result"].get("run_id"), input_id=args.input_id,
                        result=terminal["result"], state=terminal["state"])
    def validate(receipt):
        with local.service_context(args) as service:
            job = service.jobs.get(receipt["job_id"])
            need(job is not None and job["state"] == "SUCCEEDED" and job["result"] == receipt["result"],
                 "RESEARCH_SAVED_JOB_CHANGED")
            # This reads and checks the saved canonical result, never recomputes it.
            service.strategy_experiment(receipt["run_id"])
            return dict(verified=True, source_reauthenticated_now=False)
    with runtime.exclusive():
        prior()
    return 0, runtime.run_once(identity, action, validate)


def _outcomes(runtime, args):
    from stockfish_core.yaogu import r2_closure_cli as local
    need(bool(args.arguments), "DAILY_EXPLICIT_OUTCOME_COMMAND_REQUIRED")
    selected = local.parser().parse_args(args.arguments)
    if args.allow_test_fixture:
        selected.allow_test_fixture = True
    if selected.command not in {"r2-archive-import", "r2-daily-run"}:
        return local.execute(selected)
    need(runtime is not None, "DAILY_REGISTERED_RUNTIME_REQUIRED")
    need(bool(args.request_key) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", args.request_key),
         "OUTCOME_STABLE_REQUEST_KEY_REQUIRED")
    for destination in (selected.db.parent, selected.state):
        need(runtime.preflight(destination=destination)["allowed"], "OUTCOME_REGISTERED_STORAGE_BUDGET_BLOCKED")
    business = dict(command=selected.command, database=str(selected.db.resolve(strict=True)))
    if selected.command == "r2-daily-run":
        business.update(run_ids=sorted(selected.run_id), input_id=selected.input_id)
        limits = workspace._read(runtime.profile["config"], limit=65536).get("limits", {})
        if limits.get("max_seconds") is not None:
            selected.wait_seconds = min(selected.wait_seconds, limits["max_seconds"])
    else:
        business.update(archive_final_sha256=selected.expected_final_sha256)
    identity = dict(schema_version="LOCAL_OUTCOME_REQUEST_V1", backend="local-outcomes",
        request_key=args.request_key, business=business)
    def prior():
        paths = sorted((runtime.directory / "operations").glob("*.json"))
        need(len(paths) <= 10000, "OUTCOME_OPERATION_SCAN_LIMIT")
        for path in paths:
            other = workspace._read(path).get("identity", {})
            if other == identity:
                continue
            need(not (other.get("request_key") == args.request_key or
                      other.get("backend") == "local-outcomes" and other.get("business") == business),
                 "OUTCOME_EXISTING_REQUEST_RECONCILE_NO_RETRY")
    def stored(receipt):
        with local.service_context(selected) as service:
            if receipt.get("job_id"):
                value = service.jobs.get(receipt["job_id"])
                need(value is not None and value["state"] == "SUCCEEDED", "OUTCOME_SAVED_JOB_NOT_SUCCESSFUL")
                return value["result"]
            return service.store.get_document(receipt["namespace"], receipt["document_id"])
    def action():
        prior()
        code, result = local.execute(selected)
        need(code == 0, "OUTCOME_FAILED_OR_UNCERTAIN_NO_RETRY")
        if selected.command == "r2-daily-run":
            job = result["job"]
            receipt = dict(job_id=job["job_id"])
        else:
            from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import CERTIFICATES
            receipt = dict(namespace=CERTIFICATES, document_id=result["bridge_id"])
        value = stored(receipt)
        need(value is not None, "OUTCOME_COMMITTED_RESULT_MISSING")
        return dict(receipt, result_sha256=workspace._digest(value), result_summary={
            key: value.get(key) for key in ("status", "run_id", "input_id", "run_count", "ledger_id")})
    def validate(receipt):
        value = stored(receipt)
        need(value is not None and workspace._digest(value) == receipt["result_sha256"], "OUTCOME_COMMITTED_RESULT_CHANGED")
        return dict(verified=True, source_reauthenticated_now=False)
    with runtime.exclusive():
        prior()
    return 0, runtime.run_once(identity, action, validate)


def parser():
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument("--runtime")
    result.add_argument("--registry-root", type=Path)
    result.add_argument("--allow-test-fixture", action="store_true", help="Only isolated TEST_FIXTURE data")
    commands = result.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("register", help="登记已有本机路径；保留旧账本", allow_abbrev=False)
    sub.add_argument("--config", type=Path, required=True)
    sub.add_argument("--default", action="store_true")
    for name in ("status", "operations", "views", "doctor"):
        commands.add_parser(name, allow_abbrev=False)
    sub = commands.add_parser("premarket-plan", allow_abbrev=False)
    sub.add_argument("--collect-missing", action="store_true",
                     help="只读认证已有依赖并规划原预算内缺失来源；不采集")
    for name in ("publish", "audit"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--archive-id", required=True)
        sub.add_argument("--profile", choices=("FULL_CORE_ARCHIVE_READONLY_V1", "FULL_CORE_ARCHIVE_READONLY_V2"), required=True)
    for name in ("show", "export"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--view-id")
        sub.add_argument("--job", type=Path, help="已保存完整盘前任务目录")
        roles = ("summary", "observation.json", "observation.csv", "all.csv", "focus.csv", "unselected.csv")
        sub.add_argument("--role", choices=roles + (("prescreen",) if name == "show" else ()), default="summary" if name == "show" else "all.csv")
        sub.add_argument("--observation-date", type=date.fromisoformat)
        sub.add_argument("--target-session", type=date.fromisoformat)
        if name == "export":
            sub.add_argument("--output", type=Path, required=True)
    sub = commands.add_parser("premarket", help="准入后原盘前完整执行", allow_abbrev=False)
    sub.add_argument("--observation-date", type=date.fromisoformat, required=True)
    sub.add_argument("--target-session", type=date.fromisoformat, required=True)
    sub.add_argument("--request-key", required=True)
    sub.add_argument("--engine", choices=("original",), default="original")
    sub.add_argument("--collect-missing", action="store_true",
                     help="显式允许原采集器在登记预算内补齐缺失来源；不重试旧失败")
    for name in ("premarket-refresh-plan", "premarket-refresh"):
        sub = commands.add_parser(name, allow_abbrev=False,
            help="原已成功续跑任务下的一次同交易日补缺；累计扣减预算，不重试旧失败")
        sub.add_argument("--operation", type=Path, required=True)
        sub.add_argument("--expected-terminal-sha256", required=True)
        if name == "premarket-refresh":
            sub.add_argument("--authorize-missing-refresh", action="store_true",
                             help="明确授权在原剩余额度内补齐必要的新缺失请求")
    for name in ("premarket-continue-plan", "premarket-continue"):
        sub = commands.add_parser(name, allow_abbrev=False,
            help="原失败记录下的一次限定恢复；保留原key、旧失败和累计预算")
        sub.add_argument("--operation", type=Path, required=True)
        sub.add_argument("--source-job", type=Path, required=True)
        sub.add_argument("--snapshot-proof", type=Path, required=True)
        sub.add_argument("--expected-proof-sha256", required=True)
        sub.add_argument("--source-key", required=True)
        sub.add_argument("--source-receipt", required=True)
        sub.add_argument("--factor-reconciliation", type=Path,
                         help="已审阅的唯一祖先复权UNKNOWN与现有完整结果清单；不重采旧请求")
        sub.add_argument("--expected-reconciliation-sha256",
                         help="上述清单的精确SHA-256；只读计划也须绑定")
        if name == "premarket-continue":
            sub.add_argument("--authorize-one-source-retry", action="store_true",
                             help="明确授权所列原来源最多追加一次；失败或不明后不再尝试")
            sub.add_argument("--authorize-existing-factor-reconciliation", action="store_true",
                             help="明确登记已有完整结果与唯一旧UNKNOWN关系，允许原余额内本观察日新复权覆盖其范围")
    for name in ("premarket-factor-plan", "premarket-factor-authorize"):
        sub = commands.add_parser(name, allow_abbrev=False,
            help="将已成功续跑的精确复权核对关联到未来合法观察日；不重采旧请求、不增加预算")
        sub.add_argument("--operation", type=Path, required=True)
        sub.add_argument("--expected-terminal-sha256", required=True)
        if name == "premarket-factor-authorize":
            sub.add_argument("--authorize-future-observations", action="store_true")
    sub = commands.add_parser("outcomes", help="已有R2归档的显式后验", allow_abbrev=False)
    sub.add_argument("--request-key", help="写操作必须提供稳定请求标识")
    sub.add_argument("arguments", nargs=argparse.REMAINDER)
    for name in ("history", "research", "research-outcomes", "jobs"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("--db", type=Path, required=True)
        sub.add_argument("--state", type=Path, required=True)
        if name == "history":
            sub.add_argument("--run-id")
            sub.add_argument("--limit", type=int, default=20)
            sub.add_argument("--strategy", choices=("PV_OBSERVATION_V1", "STAGE_AWARE_MOMENTUM_V2", "DAILY_V1"))
            sub.add_argument("--format", choices=("json", "csv"), default="json")
            sub.add_argument("--output", type=Path)
        elif name == "jobs":
            sub.add_argument("--job-id")
            sub.add_argument("--limit", type=int, default=20)
        else:
            sub.add_argument("--input-id", required=name == "research")
            sub.add_argument("--request-key", required=name == "research")
            sub.add_argument("--wait-seconds", type=float, default=300)
            if name == "research-outcomes":
                sub.add_argument("--run-id", required=True)
                sub.add_argument("--update", action="store_true")
    sub = commands.add_parser("backup", allow_abbrev=False)
    sub.add_argument("--database", type=Path)
    sub.add_argument("--artifact-root", type=Path, action="append", default=[])
    sub.add_argument("--destination", type=Path, required=True)
    sub = commands.add_parser("restore", allow_abbrev=False)
    sub.add_argument("--backup", type=Path, required=True)
    sub.add_argument("--destination", type=Path, required=True)
    sub = commands.add_parser("verify-backup", allow_abbrev=False)
    sub.add_argument("--backup", type=Path, required=True)
    return result


def execute(runtime, args):
    fixture = getattr(args, "allow_test_fixture", False)
    if args.command == "doctor":
        import importlib
        import importlib.metadata
        modules = {"pydantic": "pydantic", "PyYAML": "yaml", "requests": "requests",
                   "psutil": "psutil", "akshare": "akshare", "baostock": "baostock"}
        packages = {}
        for distribution, module in modules.items():
            importlib.import_module(module)
            packages[distribution] = importlib.metadata.version(distribution)
        return 0, dict(status="IMPORTS_VERIFIED", python=sys.executable, packages=packages,
                       source_readiness="NOT_INFERRED", database_opened=False)
    if args.command in {"history", "research", "research-outcomes", "jobs"}:
        return _database_command(runtime, args)
    if args.command in {"backup", "restore", "verify-backup"}:
        from stockfish_core.snapshot_backup import backup_state, restore_state, verify_backup
        if args.command == "backup":
            with runtime.exclusive() if runtime is not None else nullcontext():
                return 0, backup_state(args.database, args.destination, artifact_roots=args.artifact_root)
        if args.command == "restore":
            return 0, restore_state(args.backup, args.destination)
        return 0, verify_backup(args.backup)
    if args.command == "register":
        return 0, workspace.register_profile(args.config, args.registry_root, make_default=args.default)
    if args.command == "outcomes":
        return _outcomes(runtime, args)
    need(runtime is not None and runtime.profile.get("backend") == "premarket", "DAILY_REGISTERED_RUNTIME_REQUIRED")
    if args.command == "premarket-plan":
        options = {}
        if args.collect_missing:
            options["collect_missing"] = True
        if fixture:
            options["allow_test_fixture"] = True
        return 0, _plan(runtime, **options)
    if args.command == "premarket":
        return _premarket(runtime, args)
    if args.command in {"premarket-continue-plan", "premarket-continue"}:
        return _continue_premarket(runtime, args)
    if args.command in {"premarket-refresh-plan", "premarket-refresh"}:
        return _refresh_premarket(runtime, args)
    if args.command in {"premarket-factor-plan", "premarket-factor-authorize"}:
        from scripts import codex_premarket_continuation as continuation
        if args.command == "premarket-factor-authorize":
            need(args.authorize_future_observations, "EXPLICIT_FUTURE_FACTOR_AUTHORIZATION_REQUIRED")
        proposal = continuation.prepare_future_factor_authorization(runtime,
            operation_path=args.operation, expected_terminal_sha256=args.expected_terminal_sha256,
            allow_test_fixture=fixture)
        if args.command == "premarket-factor-plan":
            return 0, proposal
        return 0, continuation.register_future_factor_authorization(runtime, proposal,
            authorize_future_observations=args.authorize_future_observations)
    if args.command == "operations":
        return 0, _operations(runtime)
    if args.command == "status":
        return 0, dict(runtime=workspace.runtime_status(runtime.profile["name"], runtime.registry_root, allow_test_fixture=fixture),
            capacity=runtime.storage_report(),
            business_source_readiness="NOT_INFERRED_FROM_RUNTIME_OR_STORAGE")
    if args.command == "views":
        return 0, _views().list_views(runtime, allow_test_fixture=fixture)
    if args.command == "publish":
        return 0, _views().publish_view(runtime, args.archive_id, profile=args.profile, allow_test_fixture=fixture)
    if args.command in {"show", "export"}:
        if args.role == "prescreen":
            need(args.command == "show" and args.job is not None and args.view_id is None,
                 "PRESCREEN_RECHECK_REQUIRES_SAVED_JOB")
            return 0, _prescreen_view().read_prescreen(runtime, args.job,
                allow_test_fixture=fixture,
                observation_date=str(args.observation_date) if args.observation_date else None,
                target_session=str(args.target_session) if args.target_session else None)
        if args.job is not None:
            need(args.view_id is None, "CHOOSE_SAVED_JOB_OR_VIEW")
            job = original._job_path(original._prepared(runtime.profile), str(args.job.absolute()))
            final, reference = original._control(job / "final-control.json")
            from stockfish_core.yaogu import premarket_workflow_v1
            verifier = _workflow() if final.get("schema_version") == _workflow().VERSION else premarket_workflow_v1
            verified = verifier.verify_job(job)
            need(verified.get("archived_bytes_verified") is True, "SAVED_JOB_CONTENT_VERIFICATION_REQUIRED")
            viewed = dict(status="VERIFIED_SAVED_JOB", job=str(job), final_status=final["status"],
                          source_reauthenticated_now=False, source_authority_restored=False,
                          full_premarket_ready=False, content_verification=verified)
            if final.get("completion") is not None:
                completion, _ = original._control(Path(final["completion"]["path"]))
                for attr in ("observation_date", "target_session"):
                    requested = getattr(args, attr)
                    need(requested is None or str(requested) == completion.get(attr), "SAVED_JOB_SESSION_MISMATCH")
                viewed["summary"] = {key: completion.get(key) for key in (
                    "run_id", "strategy_id", "observation_date", "target_session", "status", "theme_status")}
                if args.role != "summary":
                    artifact = (completion.get("core_artifacts") or {}).get(args.role)
                    need(artifact is not None, "SAVED_JOB_EXPORT_UNAVAILABLE")
                    original._inside_job(artifact, job)
                    viewed["content"] = Path(artifact["path"]).read_text(encoding="utf-8")
                    need(verify_content_reference(artifact), "SAVED_JOB_EXPORT_CHANGED")
            else:
                viewed["summary"] = {key: final.get(key) for key in ("status", "error", "cleanup_error")}
                need(args.role == "summary", "SAVED_FAILED_JOB_HAS_NO_EXPORT")
            need(verify_content_reference(reference), "SAVED_JOB_FINAL_CHANGED")
        else:
            view_id = _select_view(runtime, args.view_id, fixture,
                observation_date=args.observation_date, target_session=args.target_session)
            viewed = _views().read_view(runtime, view_id, role=args.role,
                allow_test_fixture=fixture,
                observation_date=str(args.observation_date) if args.observation_date else None,
                target_session=str(args.target_session) if args.target_session else None)
        if args.command == "export":
            content = viewed.get("content", json.dumps(viewed["summary"], ensure_ascii=False, allow_nan=False))
            viewed = {key: value for key, value in viewed.items() if key != "content"}
            viewed.update(_export_new(args.output, content))
        return 0, viewed
    if args.command == "audit":
        from scripts import codex_core_archive, codex_core_archive_v2
        module = codex_core_archive if args.profile.endswith("_V1") else codex_core_archive_v2
        return 0, module.reuse_archive(runtime, args.archive_id, profile=args.profile, allow_test_fixture=fixture)
    raise workspace.WorkspaceError("DAILY_UNKNOWN_COMMAND")


def main(argv=None):
    selected = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(selected)
    try:
        runtime = workspace.load_runtime(args.runtime, args.registry_root, allow_test_fixture=args.allow_test_fixture)
        if runtime is not None:
            dispatched = _registered(runtime, selected)
            if dispatched is not None:
                return dispatched
        code, result = execute(runtime, args)
    except KeyboardInterrupt:
        code, result = 130, dict(status="INTERRUPTED", reason="USER_INTERRUPTED")
    except Exception as error:
        code, result = 2, dict(status="BLOCKED_OR_FAILED", reason=_safe_reason(error))
    print(json.dumps({**result, "entry_version": VERSION, "command_exit_code": code}, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
