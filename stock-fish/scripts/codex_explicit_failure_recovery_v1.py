"""One explicit append-only recovery of a failed historical source request.

The original operation, request key, job, configuration and existing cache
files are immutable. Only the exact authorized keys may append new captures.
A new claim binds the original failure to one predetermined source-only job.
Any existing slot, including an interrupted claim, requires reconciliation and
is never resumed by this entry. This is not a current premarket delivery.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_premarket_source_bundle_v3 as bundle
from scripts import codex_runtime_recovery_v1 as recovery
from scripts import codex_workspace as workspace

VERSION = "EXPLICIT_HISTORICAL_FAILURE_RECOVERY_V1"
ROLE = "HISTORICAL_LATE_SOURCE_RECOVERY"
SUCCESS = "COMPLETE_SOURCE_RECOVERY_LATE"
PARTIAL = "INCOMPLETE_SOURCE_RECOVERY_LATE"
FAILED = "FAILED_RETAINED_NO_RETRY"
MAX_EVENTS = 512
FAILURE_RESERVE_BYTES = 256 * 1024
need = bundle.need


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _instant(value):
    parsed = datetime.fromisoformat(value)
    need(parsed.tzinfo is not None, "RECOVERY_AWARE_CLOCK_REQUIRED")
    return parsed


def _digest(value):
    return hashlib.sha256(bundle.previous.canonical(value)).hexdigest()


def _read(path):
    raw, ref = bundle.read(path)
    return bundle.previous.strict_json(raw), ref


def _reference(expected):
    actual = bundle.read(expected["path"])[1]
    need(actual == expected, "RECOVERY_PHYSICAL_REFERENCE_CHANGED")
    return actual


def _references(values):
    seen = {}
    for value in values:
        need(value["path"] not in seen or seen[value["path"]] == value, "RECOVERY_REFERENCE_CONFLICT")
        seen[value["path"]] = value
        _reference(value)
    return [seen[path] for path in sorted(seen)]


def _write(path, value):
    raw = bundle.previous.canonical(value)
    need(len(raw) <= bundle.MAX_FILE_BYTES, "RECOVERY_CONTROL_TOO_LARGE")
    return bundle._write_new(path, raw)


def _collector(runtime, override, allow_test_fixture):
    need(isinstance(runtime, recovery.RecoveryRuntime), "RECOVERY_ATTESTED_RUNTIME_REQUIRED")
    fixture = runtime.recovery_plan.get("test_fixture") is True
    need(not fixture or allow_test_fixture, "RECOVERY_TEST_FIXTURE_NOT_PRODUCTION")
    if override is not None:
        need(allow_test_fixture and fixture, "RECOVERY_COLLABORATOR_REQUIRES_TEST_FIXTURE")
        return override
    from scripts import codex_full_factor_recovery_v1
    need(Path(codex_full_factor_recovery_v1.__file__).absolute() == ROOT / "scripts/codex_full_factor_recovery_v1.py",
         "RECOVERY_COLLECTOR_SOURCE_ALIAS")
    return codex_full_factor_recovery_v1


def _new_budget(runtime):
    from scripts import codex_recovered_premarket_v1 as bounded
    config = bounded.configuration(runtime, bounded.PROFILE)
    limits = dict(config["limits"])
    limits["capture_read_bytes"] = min(limits["capture_read_bytes"], limits["supervisor_read_bytes"])
    budget = bounded.Budget(limits)
    budget.utc_started_at = datetime.now(timezone.utc)
    original_check = budget.check

    def utc_elapsed():
        elapsed = (datetime.now(timezone.utc) - budget.utc_started_at).total_seconds()
        if elapsed < 0:
            budget._fail("RECOVERY_UTC_CLOCK_MOVED_BACKWARD")
        return elapsed

    def checked(stage, *, force=False):
        if utc_elapsed() > budget.limits["max_seconds"]:
            budget._fail("RECOVERY_UTC_WALL_TIME_LIMIT_EXCEEDED")
        original_check(stage, force=force)

    def remaining_seconds():
        checked("explicit_recovery_remaining_time", force=True)
        return max(0, min(budget.limits["max_seconds"] - (time.monotonic() - budget.started),
                          budget.limits["max_seconds"] - utc_elapsed()))

    budget.check = checked
    budget.remaining_seconds = remaining_seconds
    # load_config returned this one text file before Budget could be constructed.
    budget.charge_read(Path(runtime.profile["config"]).stat().st_size)
    return budget, config


def _lower_budget(budget, limits):
    limits = dict(limits)
    limits["capture_read_bytes"] = min(limits["capture_read_bytes"], limits["supervisor_read_bytes"])
    need(set(limits) == set(budget.limits), "RECOVERY_EXACT_BUDGET_FIELDS_REQUIRED")
    for key, value in limits.items():
        need(type(value) in (int, float) and math.isfinite(value) and 0 < value <= budget.limits[key],
             "RECOVERY_REMAINING_BUDGET_INVALID")
    budget.limits = dict(limits)
    budget.limits["capture_read_bytes"] = min(limits["capture_read_bytes"], limits["supervisor_read_bytes"])
    budget.check("explicit_recovery_remaining_budget", force=True)


def _failure(runtime, operation_path, job_path):
    operation_path, job = Path(operation_path).absolute(), Path(job_path).absolute()
    need(operation_path.parent == runtime.directory / "operations", "RECOVERY_OPERATION_OUTSIDE_ORIGINAL_SCOPE")
    operation, op_ref = _read(operation_path)
    identity = operation.get("identity", {})
    operation_id = operation_path.stem
    need(operation.get("schema_version") == workspace.VERSION and operation.get("scope") == runtime.scope
         and operation.get("operation_id") == operation_id
         and operation_id == workspace._digest({"scope": runtime.scope, "identity": identity}),
         "RECOVERY_OPERATION_BINDING_INVALID")
    need(operation.get("phase") == "FAILED_OR_UNKNOWN" and "receipt" not in operation,
         "RECOVERY_REQUIRES_ORIGINAL_FAILED_OPERATION")
    need(identity.get("schema_version") == "CODEX_RECOVERED_PREMARKET_V1"
         and identity.get("backend") == "premarket" and identity.get("execution_engine") == "original"
         and identity.get("explicit_collection_profile") == "REAL_PREMARKET_RECOVERY_3H_V1"
         and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", identity.get("request_key", "")),
         "RECOVERY_ORIGINAL_REQUEST_IDENTITY_REQUIRED")
    need(_instant(operation["started_at"]) <= _instant(operation["finished_at"]), "RECOVERY_OPERATION_CLOCK_ORDER")
    need(_instant(operation["finished_at"]) <= datetime.now(timezone.utc), "RECOVERY_FUTURE_FAILURE_FORBIDDEN")
    need(job.parent == Path(runtime.profile["state_root"]) / "jobs", "RECOVERY_JOB_OUTSIDE_ORIGINAL_STATE")
    final, final_ref = _read(job / "final-control.json")
    need(final.get("schema_version") == "CODEX_PREMARKET_SHARED_WORKFLOW_V1"
         and final.get("status") == "FAILED" and final.get("job") == str(job)
         and final.get("external_worker_reaped") is True and type(final.get("external_worker_exit_code")) is int
         and final["external_worker_exit_code"] != 0
         and final.get("implementation_epoch_verified") is True,
         "RECOVERY_REQUIRES_REAPED_FAILED_JOB")
    need(_instant(operation["started_at"]) <= _instant(final["final_sealed_at"])
         <= _instant(operation["finished_at"]), "RECOVERY_SELECTED_JOB_OUTSIDE_OPERATION_INTERVAL")
    refs = [op_ref, final_ref, _reference(identity["configuration"])]
    controls = {}
    for role, filename in (("completion", "worker-completion.json"), ("configuration", "configuration.json"),
                           ("code_registry", "code-registry.json")):
        expected = final[role]
        need(expected["path"] == str(job / filename), "RECOVERY_JOB_CONTROL_PATH_CHANGED")
        value, ref = _read(expected["path"])
        need(ref == expected, "RECOVERY_JOB_CONTROL_REFERENCE_CHANGED")
        controls[role] = value
        refs.append(ref)
    completion = controls["completion"]
    need(completion.get("schema_version") == final["schema_version"] and completion.get("status") == "FAILED"
         and completion.get("core_verified") is False and completion.get("execution_engine") == "original"
         and completion.get("expected_sessions", [])[:2] == [identity["observation_date"], identity["target_session"]]
         and completion.get("phases", {}).get("capture") == "FAILED"
         and all(completion.get("phases", {}).get(name) == "NOT_RUN" for name in
                 ("core", "news", "theme_sources", "theme", "presentation")),
         "RECOVERY_REQUIRES_HISTORICAL_CAPTURE_FAILURE")
    need(type(controls["code_registry"]) is list and 0 < len(controls["code_registry"]) <= 512,
         "RECOVERY_CODE_REGISTRY_REQUIRED")
    refs.extend(_references(controls["code_registry"]))
    return dict(operation_id=operation_id, request_key=identity["request_key"],
        observation_date=identity["observation_date"], target_session=identity["target_session"],
        operation=op_ref, job=str(job), final=final_ref, references=_references(refs),
        job_binding="EXPLICIT_CURRENT_RECONCILIATION_NOT_ORIGINAL_RECEIPT",
        original_operation_has_job_receipt=False, original_status="FAILED_OR_UNKNOWN",
        job_status="FAILED", worker_reaped=True, configuration=controls["configuration"])


def _source_guard(runtime, directory, manifest_sha256, collector, allow_test_fixture):
    directory = Path(directory).absolute()
    verified = bundle.verify(directory, manifest_sha256)
    document, manifest = _read(directory / bundle.MANIFEST)
    need(manifest["sha256"] == manifest_sha256 and document["source_root"] == runtime.profile["source_root"],
         "RECOVERY_BUNDLE_REGISTERED_SOURCE_ORIGIN_CHANGED")
    if not allow_test_fixture:
        need(ROOT == Path(runtime.profile["source_root"]), "RECOVERY_REGISTERED_EXTERNAL_SOURCE_REQUIRED")
    external = [bundle.read(Path(__file__).absolute())[1]]
    if hasattr(collector, "__file__"):
        external.append(bundle.read(Path(collector.__file__).absolute())[1])
    else:
        need(allow_test_fixture and runtime.recovery_plan.get("test_fixture") is True,
             "RECOVERY_COLLECTOR_SOURCE_REQUIRED")
    return dict(bundle_root=str(directory), manifest=manifest, verification=verified,
                external_sources=_references(external), source_capability_issued=False)


def _paths(runtime, operation_id):
    need(re.fullmatch(r"[0-9a-f]{64}", operation_id) is not None, "RECOVERY_OPERATION_ID_REQUIRED")
    slot = runtime.directory / "explicit-failure-recoveries" / operation_id
    job = Path(runtime.profile["state_root"]) / "jobs" / ("explicit-recovery-" + operation_id)
    return slot, job


def _request_conflicts(runtime, failure):
    paths = sorted((runtime.directory / "operations").glob("*.json"))
    need(len(paths) <= 10000, "RECOVERY_OPERATION_SCAN_LIMIT")
    identities = []
    for path in paths:
        record, _ = _read(path)
        identity = record.get("identity", {})
        need(record.get("schema_version") == workspace.VERSION and record.get("scope") == runtime.scope
             and record.get("operation_id") == path.stem
             and path.stem == workspace._digest({"scope": runtime.scope, "identity": identity}),
             "RECOVERY_OTHER_OPERATION_BINDING_INVALID")
        if path.stem != failure["operation_id"]:
            identities.append(identity)
    history = runtime.recovery_plan.get("history", {}).get("operations", [])
    for old in history:
        recovery._verify_refs([old["reference"]])
        identities.append(old["identity"])
    for identity in identities:
        if identity.get("backend") == "premarket":
            same_key = identity.get("request_key") == failure["request_key"]
            same_dates = (identity.get("observation_date"), identity.get("target_session")) == (
                failure["observation_date"], failure["target_session"])
            need(not same_key and not same_dates, "RECOVERY_OTHER_BUSINESS_REQUEST_CONFLICT")


def _empty_slot(slot, job):
    need(not os.path.lexists(slot) and not os.path.lexists(job), "RECOVERY_EXISTING_SLOT_RECONCILE_NO_RETRY")


def _plan_key(value):
    return _digest({key: item for key, item in value.items() if key != "plan_id"})


def prepare(runtime, *, operation_path, job_path, bundle_directory, manifest_sha256,
            allow_test_fixture=False, collector=None):
    producer = _collector(runtime, collector, allow_test_fixture)
    budget, config = _new_budget(runtime)
    started = _stamp()
    with budget.phase("capture"), budget.meter_files(), runtime.exclusive():
        runtime.check_binding()
        failure = _failure(runtime, operation_path, job_path)
        _request_conflicts(runtime, failure)
        slot, job = _paths(runtime, failure["operation_id"])
        _empty_slot(slot, job)
        need(failure["configuration"] == config, "RECOVERY_ORIGINAL_CONFIGURATION_CHANGED")
        guard = _source_guard(runtime, bundle_directory, manifest_sha256, producer, allow_test_fixture)
        baseline = producer.budget_baseline(runtime, Path(job_path))
        need(baseline["original_limits"] == config["limits"], "RECOVERY_BUDGET_BASELINE_CONFIG_CHANGED")
        _references(baseline["references"])
        _lower_budget(budget, baseline["remaining_limits"])
        source_plan = producer.plan(runtime, Path(bundle_directory), manifest_sha256, Path(job_path), budget=budget)
        need(type(source_plan) is dict and isinstance(source_plan.get("plan_hash"), str), "RECOVERY_SOURCE_PLAN_ID_REQUIRED")
        _references(failure["references"] + guard["external_sources"])
        need(_source_guard(runtime, bundle_directory, manifest_sha256, producer, allow_test_fixture) == guard,
             "RECOVERY_SOURCE_CHANGED_DURING_PLAN")
        budget.check("explicit_recovery_plan_terminal", force=True)
        runtime.check_binding()
    finished = _stamp()
    elapsed = max(time.monotonic() - budget.started, (_instant(finished) - _instant(started)).total_seconds())
    metrics = budget.metrics
    need(all(metrics["calls"][name] == 0 for name in ("requests", "sdk_reserved", "http_reserved")),
         "RECOVERY_PLAN_MUST_NOT_COLLECT")
    value = dict(schema_version=VERSION, source_role=ROLE, original_scope=runtime.scope,
        failure=failure, source_guard=guard, baseline=baseline, source_plan=source_plan,
        request_key=failure["request_key"], operation_id=failure["operation_id"], slot=str(slot), source_job=str(job),
        original_configuration=config, storage_policy=runtime.profile["storage"],
        planning=dict(started_at=started, finished_at=finished,
            conservative_elapsed_seconds=math.ceil(max(elapsed, metrics["elapsed_seconds"])), metrics=metrics),
        source_capability_issued=False, retry_scope="ONE_EXPLICIT_ATTEMPT_FOR_EXACT_SOURCE_PLAN",
        old_operation_rewritten=False, historical_pit_restored=False, complete_daily_delivery=False,
        test_fixture=bool(allow_test_fixture))
    value["plan_id"] = _plan_key(value)
    # Reserve the one private plan artifact as output even for an API caller
    # that keeps the plan in memory; the execution slot copy is counted later.
    value["planning"]["artifact_bytes"] = 0
    for _ in range(8):
        value["plan_id"] = _plan_key(value)
        size = len(bundle.previous.canonical(value))
        if size == value["planning"]["artifact_bytes"]:
            break
        value["planning"]["artifact_bytes"] = size
    value["plan_id"] = _plan_key(value)
    need(value["planning"]["artifact_bytes"] == len(bundle.previous.canonical(value))
         and size + FAILURE_RESERVE_BYTES < budget.limits["output_bytes"], "RECOVERY_PLAN_OUTPUT_LIMIT")
    return value


def _execution_limits(plan):
    limits = dict(plan["baseline"]["remaining_limits"])
    planning = plan["planning"]
    metrics = planning["metrics"]
    actual_wall = (_instant(planning["finished_at"]) - _instant(planning["started_at"])).total_seconds()
    need(actual_wall >= 0 and type(planning["conservative_elapsed_seconds"]) is int
         and planning["conservative_elapsed_seconds"] >= math.ceil(max(actual_wall, metrics["elapsed_seconds"]))
         and not metrics["failures"] and all(metrics["calls"][name] == 0 for name in
             ("requests", "sdk_reserved", "http_reserved")), "RECOVERY_PLANNING_BUDGET_EVIDENCE_INVALID")
    limits["max_seconds"] -= planning["conservative_elapsed_seconds"]
    returned = metrics["returned_read_bytes"]
    need(set(returned) == {"capture", "core", "theme", "presentation"}
         and all(type(value) is int and value >= 0 for value in returned.values()),
         "RECOVERY_PLANNING_READ_ACCOUNTING_INVALID")
    for phase in ("capture", "core", "theme", "presentation"):
        limits[phase + "_read_bytes"] -= returned[phase]
    limits["supervisor_read_bytes"] -= sum(returned.values())
    need(type(planning["artifact_bytes"]) is int and planning["artifact_bytes"] > 0,
         "RECOVERY_PLANNING_OUTPUT_ACCOUNTING_INVALID")
    limits["output_bytes"] -= planning["artifact_bytes"]
    limits["capture_read_bytes"] = min(limits["capture_read_bytes"], limits["supervisor_read_bytes"])
    need(all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in limits.values()),
         "RECOVERY_PLANNING_EXHAUSTED_REMAINING_BUDGET")
    return limits


def _validate_plan(runtime, plan, producer, allow_test_fixture, budget):
    need(plan.get("schema_version") == VERSION and plan.get("source_role") == ROLE
         and plan.get("plan_id") == _plan_key(plan) and plan.get("original_scope") == runtime.scope,
         "RECOVERY_PLAN_BINDING_CHANGED")
    need(plan.get("test_fixture") is bool(allow_test_fixture)
         and plan.get("old_operation_rewritten") is False and plan.get("historical_pit_restored") is False
         and plan.get("source_capability_issued") is False and plan.get("complete_daily_delivery") is False,
         "RECOVERY_PLAN_AUTHORITY_CHANGED")
    slot, job = _paths(runtime, plan["operation_id"])
    need(plan["slot"] == str(slot) and plan["source_job"] == str(job)
         and plan["operation_id"] == plan["failure"]["operation_id"]
         and plan["request_key"] == plan["failure"]["request_key"]
         and plan["storage_policy"] == runtime.profile["storage"], "RECOVERY_PLAN_SCOPE_CHANGED")
    failure = _failure(runtime, plan["failure"]["operation"]["path"], plan["failure"]["job"])
    _request_conflicts(runtime, failure)
    need(failure == plan["failure"], "RECOVERY_ORIGINAL_FAILURE_CHANGED")
    guard = plan["source_guard"]
    need(_source_guard(runtime, guard["bundle_root"], guard["manifest"]["sha256"], producer, allow_test_fixture) == guard,
         "RECOVERY_SOURCE_EPOCH_CHANGED")
    need(producer.budget_baseline(runtime, Path(failure["job"])) == plan["baseline"], "RECOVERY_BUDGET_BASELINE_CHANGED")
    refreshed = producer.plan(runtime, Path(guard["bundle_root"]), guard["manifest"]["sha256"], Path(failure["job"]), budget=budget)
    need(refreshed["plan_hash"] == plan["source_plan"]["plan_hash"], "RECOVERY_SOURCE_SCOPE_CHANGED")
    runtime.check_binding()
    return slot, job


def _revoke_completion(slot, reference):
    source, rejected = slot / "completion.json", slot / "rejected-completion.json"
    with bundle._directory_fd(slot) as descriptor:
        try:
            info = os.stat(source.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return dict(status="COMPLETION_MISSING_NOT_REMOVED")
        if (info.st_dev, info.st_ino) != (reference["fp"]["st_dev"], reference["fp"]["st_ino"]):
            return dict(status="UNOWNED_REPLACEMENT_PRESERVED")
        os.link(source.name, rejected.name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        os.unlink(source.name, dir_fd=descriptor)
        os.fsync(descriptor)
    return bundle.read(rejected)[1]


def _output_bytes(slot, job):
    from stockfish_core.yaogu.premarket_runtime_v1 import directory_bytes
    return sum(directory_bytes(path) for path in (slot, job) if path.exists())


def _persist(path, value, budget, slot, job):
    need(_output_bytes(slot, job) + len(bundle.previous.canonical(value)) + FAILURE_RESERVE_BYTES
         <= budget.limits["output_bytes"], "RECOVERY_CONTROL_AND_JOB_OUTPUT_LIMIT")
    return _write(path, value)


def execute(runtime, plan, *, plan_reference=None, allow_test_fixture=False, collector=None):
    producer = _collector(runtime, collector, allow_test_fixture)
    budget, config = _new_budget(runtime)
    need(config == plan["original_configuration"], "RECOVERY_ORIGINAL_CONFIGURATION_CHANGED")
    _lower_budget(budget, _execution_limits(plan))
    slot, job = _paths(runtime, plan["operation_id"])
    claim_ref = completion_ref = None
    events = []
    started = _stamp()
    try:
        with budget.phase("capture"), budget.meter_files(), runtime.exclusive():
            _empty_slot(slot, job)
            if plan_reference is not None:
                _reference(plan_reference)
                need(_read(plan_reference["path"])[0] == plan, "RECOVERY_EXPLICIT_PLAN_CONTENT_CHANGED")
            _validate_plan(runtime, plan, producer, allow_test_fixture, budget)
            capacity = runtime.preflight(expected_growth_bytes=max(
                runtime.profile["storage"]["expected_growth_bytes"], budget.limits["output_bytes"]))
            need(capacity["allowed"], "RECOVERY_ORIGINAL_CAPACITY_BLOCKED")
            with bundle._directory_fd(slot.parent, create=True) as parent:
                os.mkdir(slot.name, mode=0o700, dir_fd=parent)
                os.fsync(parent)
            claim = dict(schema_version=VERSION, source_role=ROLE, plan_id=plan["plan_id"],
                original_scope=runtime.scope, original_operation=plan["failure"]["operation"],
                explicit_selected_old_job=plan["failure"]["job"], request_key=plan["request_key"],
                source_plan_hash=plan["source_plan"]["plan_hash"], source_job=str(job), claimed_at=_stamp(),
                execution_limits=budget.limits, complete_daily_delivery=False, retry_authorized=False)
            claim["explicit_plan_file"] = plan_reference
            claim_ref = _persist(slot / "claim.json", claim, budget, slot, job)
            plan_ref = _persist(slot / "plan.json", plan, budget, slot, job)
            with bundle._directory_fd(job.parent) as parent:
                os.mkdir(job.name, mode=0o700, dir_fd=parent)
                os.fsync(parent)
            budget.job_dir = job
            budget.cache_dir = Path(runtime.profile["state_root"]) / "cache"

            def checkpoint(event):
                need(type(event) is dict and len(events) < MAX_EVENTS, "RECOVERY_BOUNDED_REQUEST_EVENT_REQUIRED")
                _reference(claim_ref)
                budget.check("explicit_recovery_request_checkpoint", force=True)
                reference = _persist(slot / "request-events" / (f"{len(events):04d}.json"),
                    dict(schema_version=VERSION, source_role=ROLE, plan_id=plan["plan_id"],
                         request_key=plan["request_key"], source_job=str(job), event=event, recorded_at=_stamp()),
                    budget, slot, job)
                events.append(reference)
                return reference

            result = producer.collect(plan["source_plan"], job, budget, checkpoint=checkpoint)
            need(type(result) is dict and type(result.get("source_complete")) is bool,
                 "RECOVERY_SOURCE_COMPLETION_CONTRACT_REQUIRED")
            producer.verify_result(result, plan["source_plan"], job, budget)
            _references(plan["failure"]["references"] + plan["source_guard"]["external_sources"] + events)
            _reference(claim_ref)
            _reference(plan_ref)
            if plan_reference is not None:
                _reference(plan_reference)
            need(_source_guard(runtime, plan["source_guard"]["bundle_root"],
                plan["source_guard"]["manifest"]["sha256"], producer, allow_test_fixture) == plan["source_guard"],
                "RECOVERY_TERMINAL_SOURCE_CHANGED")
            elapsed = max(time.monotonic() - budget.started, (_instant(_stamp()) - _instant(started)).total_seconds())
            need(elapsed <= budget.limits["max_seconds"], "RECOVERY_ACTUAL_UTC_WAIT_LIMIT_EXCEEDED")
            budget.check("explicit_recovery_before_completion", force=True)
            completion = dict(schema_version=VERSION, source_role=ROLE,
                status=SUCCESS if result["source_complete"] else PARTIAL,
                plan_id=plan["plan_id"], request_key=plan["request_key"], source_job=str(job),
                claim=claim_ref, plan=plan_ref, events=events, result=result,
                started_at=started, completed_at=_stamp(), metrics=budget.metrics,
                metrics_scope="PRE_PUBLICATION_SNAPSHOT_TERMINAL_CHECKS_FOLLOW",
                actual_elapsed_seconds=elapsed, complete_daily_delivery=False,
                old_operation_rewritten=False, historical_pit_restored=False, source_capability_issued=False)
            completion_ref = _persist(slot / "completion.json", completion, budget, slot, job)
            producer.verify_result(result, plan["source_plan"], job, budget)
            _references(plan["failure"]["references"] + plan["source_guard"]["external_sources"] + events)
            _reference(completion_ref)
            need(_source_guard(runtime, plan["source_guard"]["bundle_root"],
                plan["source_guard"]["manifest"]["sha256"], producer, allow_test_fixture) == plan["source_guard"],
                "RECOVERY_POST_PUBLICATION_SOURCE_CHANGED")
            if plan_reference is not None:
                _reference(plan_reference)
            runtime.check_binding()
            budget.check("explicit_recovery_after_completion", force=True)
        need((_instant(_stamp()) - _instant(started)).total_seconds() <= budget.limits["max_seconds"],
             "RECOVERY_ACTUAL_UTC_WAIT_LIMIT_EXCEEDED")
        need(_output_bytes(slot, job) + FAILURE_RESERVE_BYTES <= budget.limits["output_bytes"],
             "RECOVERY_CONTROL_AND_JOB_OUTPUT_LIMIT")
        with budget.meter_files():
            _references([claim_ref, plan_ref, *events])
            if plan_reference is not None:
                _reference(plan_reference)
            budget.check("explicit_recovery_terminal_output_identity", force=True)
            _reference(completion_ref)
        return {**completion, "completion": completion_ref, "terminal_metrics": budget.metrics,
            "terminal_actual_elapsed_seconds": max(time.monotonic()-budget.started,
                (_instant(_stamp())-_instant(started)).total_seconds())}
    except BaseException as error:
        if claim_ref is None:
            raise
        from scripts.codex_daily import _safe_reason
        rejected, revoke_error = None, None
        if completion_ref is not None:
            try:
                rejected = _revoke_completion(slot, completion_ref)
            except Exception as revoke:
                revoke_error = dict(error_type=type(revoke).__name__, error_reason=_safe_reason(revoke))
        failure = dict(schema_version=VERSION, source_role=ROLE, status=FAILED, plan_id=plan["plan_id"],
            request_key=plan["request_key"], source_job=str(job), claim=claim_ref,
            events=events, started_at=started, failed_at=_stamp(),
            reason="EXPLICIT_SOURCE_RECOVERY_DID_NOT_SEAL", error_type=type(error).__name__,
            error_reason=_safe_reason(error), terminal_metrics=budget.metrics,
            rejected_completion=rejected, revoke_error=revoke_error, complete_daily_delivery=False,
            old_operation_rewritten=False, historical_pit_restored=False, source_capability_issued=False,
            retry_authorized=False)
        failure_ref = _write(slot / "failure.json", failure)
        return {**failure, "failure": failure_ref,
            "terminal_actual_elapsed_seconds": max(time.monotonic()-budget.started,
                (_instant(_stamp())-_instant(started)).total_seconds())}


def status(runtime, operation_id):
    runtime.check_binding()
    slot, job = _paths(runtime, operation_id)
    base = dict(schema_version=VERSION, source_role=ROLE, operation_id=operation_id,
        source_job=str(job), complete_daily_delivery=False, source_capability_issued=False,
        status_scope="ARCHIVED_CONTROL_CONSISTENCY_NOT_SOURCE_REAUTHENTICATION", retry_authorized=False)
    if not os.path.lexists(slot):
        return {**base, "status": "NO_RECOVERY_SLOT_OBSERVED"}
    if not os.path.lexists(slot / "claim.json"):
        return {**base, "status": "INTERRUPTED_SLOT_RECONCILE_NO_RETRY"}
    claim, claim_ref = _read(slot / "claim.json")
    need(claim.get("schema_version") == VERSION and claim.get("original_scope") == runtime.scope
         and claim.get("source_job") == str(job)
         and Path(claim["original_operation"]["path"]).stem == operation_id,
         "RECOVERY_SAVED_CLAIM_BINDING_CHANGED")
    for filename in ("failure.json", "completion.json"):
        if os.path.lexists(slot / filename):
            value, reference = _read(slot / filename)
            need(value.get("schema_version") == VERSION and value.get("claim") == claim_ref
                 and value.get("plan_id") == claim["plan_id"] and value.get("source_job") == str(job)
                 and value.get("request_key") == claim["request_key"], "RECOVERY_SAVED_TERMINAL_BINDING_CHANGED")
            for ref in value.get("events", []):
                _reference(ref)
            if filename == "completion.json":
                need(value.get("status") in (SUCCESS, PARTIAL)
                     and value.get("result", {}).get("source_complete") is (value["status"] == SUCCESS)
                     and all(value.get(name) is False for name in ("complete_daily_delivery",
                         "old_operation_rewritten", "historical_pit_restored", "source_capability_issued")),
                     "RECOVERY_SAVED_COMPLETION_STATUS_CHANGED")
                _reference(value["plan"])
            else:
                need(value.get("status") == FAILED, "RECOVERY_SAVED_FAILURE_STATUS_CHANGED")
            runtime.check_binding()
            return {**base, "status": value["status"], "terminal": reference, "claim": claim_ref}
    return {**base, "status": "CLAIMED_OUTCOME_UNKNOWN_RECONCILE_NO_RETRY", "claim": claim_ref}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--registry-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("prepare", allow_abbrev=False)
    planning.add_argument("--operation", type=Path, required=True)
    planning.add_argument("--job", type=Path, required=True)
    planning.add_argument("--bundle", type=Path, required=True)
    planning.add_argument("--manifest-sha256", required=True)
    planning.add_argument("--output", type=Path, required=True)
    execution = commands.add_parser("execute", allow_abbrev=False)
    execution.add_argument("--plan", type=Path, required=True)
    execution.add_argument("--plan-sha256", required=True)
    query = commands.add_parser("status", allow_abbrev=False)
    query.add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    try:
        runtime = recovery.load_runtime(args.runtime, args.registry_root)
        need(isinstance(runtime, recovery.RecoveryRuntime), "RECOVERY_ATTESTED_RUNTIME_REQUIRED")
        need(Path(sys.executable).absolute() == Path(runtime.profile["python"]).absolute()
             and sys.pycache_prefix is not None and not Path(sys.pycache_prefix).exists(),
             "RECOVERY_REGISTERED_FRESH_INTERPRETER_REQUIRED")
        if args.command == "prepare":
            value = prepare(runtime, operation_path=args.operation, job_path=args.job,
                bundle_directory=args.bundle, manifest_sha256=args.manifest_sha256)
            ref = _write(args.output, value)
            result = dict(status="PREPARED_NOT_EXECUTED", plan=ref, plan_id=value["plan_id"],
                source_role=ROLE, complete_daily_delivery=False)
        elif args.command == "execute":
            value, ref = _read(args.plan)
            need(ref["sha256"] == args.plan_sha256, "RECOVERY_EXPLICIT_PLAN_SHA256_MISMATCH")
            result = execute(runtime, value, plan_reference=ref)
        else:
            result = status(runtime, args.operation_id)
        code = 0 if result["status"] in (SUCCESS, "PREPARED_NOT_EXECUTED") or args.command == "status" else 4
    except Exception as error:
        from scripts.codex_daily import _safe_reason
        code, result = 4, dict(status="BLOCKED_OR_FAILED", reason="EXPLICIT_SOURCE_RECOVERY_VALIDATION_FAILED",
            error_reason=_safe_reason(error), error_type=type(error).__name__,
            source_role=ROLE, complete_daily_delivery=False, retry_authorized=False)
    if args.command == "execute":
        result = {key: result[key] for key in ("status", "source_role", "request_key", "source_job", "plan_id",
            "completion", "failure", "reason", "error_reason", "error_type", "terminal_metrics",
            "terminal_actual_elapsed_seconds", "complete_daily_delivery", "retry_authorized") if key in result} | {
            "coverage": {key: result.get("result", {}).get(key) for key in (
                "source_complete", "requested_codes_count", "complete_codes_count", "original_complete_reused", "new_attempts")}}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
