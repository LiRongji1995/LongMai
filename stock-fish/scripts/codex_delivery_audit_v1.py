"""Bounded, read-only accounting of existing daily delivery evidence.

This reader never starts jobs, creates claims, retries, opens a business DB or
reissues source authority. A consistent archived workflow is not a statement
about present data readiness, fully known eligibility, or user receipt.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_premarket as original
from scripts import codex_workspace as workspace

VERSION = "CODEX_DELIVERY_AUDIT_V1"
WORKFLOWS = frozenset(("STANDALONE_PREMARKET_WORKFLOW_V1", "CODEX_PREMARKET_SHARED_WORKFLOW_V1",
                       "CODEX_ORIGINAL_CANONICAL_EXECUTION_V1"))
OPERATIONS = frozenset(("CODEX_PREMARKET_ADAPTER_V1", "CODEX_DAILY_EXECUTION_PIPELINE_V2",
                       "CODEX_DAILY_ORIGINAL_CANONICAL_V1", "CODEX_RECOVERED_PREMARKET_V1"))
PHASES = ("capture", "core", "news", "theme_sources", "theme", "presentation")
SUCCESS = {"COMPLETE": 0, "COMPLETED_WITH_UNKNOWNS": 2}
FAILED = frozenset(("FAILED", "BUDGET_EXCEEDED"))
CONTROL_LIMIT = 16 * 1024**2


class AuditError(ValueError):
    pass


def _need(ok, reason):
    if not ok:
        raise AuditError(reason)


def _clock(value):
    _need(isinstance(value, str), "AUDIT_AWARE_CLOCK_REQUIRED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AuditError("AUDIT_AWARE_CLOCK_REQUIRED") from None
    _need(result.utcoffset() is not None, "AUDIT_AWARE_CLOCK_REQUIRED")
    return result.astimezone(timezone.utc)


def _seconds(value):
    _need(type(value) in (int, float) and math.isfinite(value) and value >= 0,
          "AUDIT_FINITE_NONNEGATIVE_SECONDS_REQUIRED")
    return value


def _path(value, *, directory=False):
    path = Path(value).expanduser().absolute()
    _need(".." not in path.parts and path.resolve(strict=True) == path,
          "AUDIT_CANONICAL_PATH_REQUIRED")
    if directory:
        _need(path.is_dir(), "AUDIT_DIRECTORY_REQUIRED")
    return path


def _fingerprint(info):
    _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
          "AUDIT_REGULAR_SINGLE_LINK_REQUIRED")
    _need(not getattr(info, "st_flags", 0) & 0x40000000 and
          (not info.st_size or getattr(info, "st_blocks", 1) > 0), "AUDIT_RESIDENT_FILE_REQUIRED")
    return {key: int(getattr(info, key, 0)) for key in original.FP_KEYS}


class _Reader:
    def __init__(self, max_bytes, max_seconds):
        _need(type(max_bytes) is int and max_bytes > 0, "AUDIT_POSITIVE_BYTE_LIMIT_REQUIRED")
        _need(_seconds(max_seconds) > 0, "AUDIT_POSITIVE_TIME_LIMIT_REQUIRED")
        self.max_bytes, self.max_seconds = max_bytes, max_seconds
        self.started, self.returned_bytes, self.refs, self.absent = time.monotonic(), 0, {}, set()

    def check(self):
        _need(time.monotonic() - self.started <= self.max_seconds, "AUDIT_TIME_LIMIT")
        _need(self.returned_bytes <= self.max_bytes, "AUDIT_READ_LIMIT")

    def exists(self, path):
        self.check()
        path = Path(path)
        if path.exists() or path.is_symlink():
            return True
        self.absent.add(path)
        return False

    def read(self, path, *, expected=None, control=False):
        self.check()
        path = _path(path)
        _need(str(path) in self.refs or len(self.refs) < 4096, "AUDIT_FILE_COUNT_LIMIT")
        digest, chunks = hashlib.sha256(), []
        # Walk every ancestor with O_NOFOLLOW, then use only the pinned parent
        # descriptor for stat/open. A preliminary resolve alone cannot prevent
        # an ancestor from being redirected between validation and file open.
        with workspace._directory_fd(path.parent) as parent:
            parent_stat = os.fstat(parent)
            before = _fingerprint(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
            _need(not control or before["st_size"] <= CONTROL_LIMIT, "AUDIT_CONTROL_SIZE_LIMIT")
            _need(before["st_size"] <= self.max_bytes - self.returned_bytes, "AUDIT_READ_LIMIT")
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                _need(_fingerprint(os.fstat(fd)) == before, "AUDIT_EVIDENCE_CHANGED")
                while True:
                    raw = os.read(fd, 65536)
                    if not raw:
                        break
                    self.returned_bytes += len(raw)
                    self.check()
                    digest.update(raw)
                    if control:
                        chunks.append(raw)
                current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                _need(_fingerprint(os.fstat(fd)) == before == _fingerprint(current),
                      "AUDIT_EVIDENCE_CHANGED")
            finally:
                os.close(fd)
            # Reopening the canonical ancestry detects a moved/replaced parent
            # even when the old descriptor and its original file remain valid.
            with workspace._directory_fd(path.parent) as current_parent:
                current_parent_stat = os.fstat(current_parent)
                _need((parent_stat.st_dev, parent_stat.st_ino) ==
                      (current_parent_stat.st_dev, current_parent_stat.st_ino),
                      "AUDIT_PARENT_DIRECTORY_CHANGED")
        ref = dict(path=str(path), bytes=before["st_size"], sha256=digest.hexdigest(), fp=before)
        if expected is not None:
            _need(type(expected) is dict and set(expected) == {"path", "bytes", "sha256", "fp"}
                  and ref == expected, "AUDIT_REFERENCE_MISMATCH")
        _need(self.refs.get(str(path), ref) == ref, "AUDIT_EVIDENCE_CHANGED")
        self.refs[str(path)] = ref
        if control:
            value = original._json(b"".join(chunks))
            _need(type(value) is dict, "AUDIT_CONTROL_OBJECT_REQUIRED")
            return value, ref
        return ref

    def terminal(self):
        # Every consumed file is reread, not an mtime cache. No external RAW is
        # implicitly added to this derivative/control audit.
        for reference in list(self.refs.values()):
            self.read(reference["path"], expected=reference)
        for path in self.absent:
            _need(not path.exists() and not path.is_symlink(), "AUDIT_EVIDENCE_APPEARED_DURING_READ")
        self.check()


def _base():
    return dict(schema_version=VERSION, audited_at=datetime.now(timezone.utc).isoformat(),
        evidence_role="LOCAL_ARCHIVE_AND_CONTROL_CONSISTENCY_NOT_SOURCE_AUTHORITY",
        source_reauthenticated_now=False, source_authority_restored=False,
        user_delivery_status="UNKNOWN_NOT_OBSERVED", daily_v1_readiness="UNKNOWN_NOT_EVALUATED",
        market_edge="NOT_ESTABLISHED", retry_authorized=False, worker_started=False,
        network_calls=0, business_database_opened=False)


def _finish(reader, result):
    reader.terminal()
    result.update(evidence=[reader.refs[key] for key in sorted(reader.refs)],
        audit_returned_read_bytes=reader.returned_bytes,
        audit_seconds=time.monotonic()-reader.started,
        absent_controls=[str(path) for path in sorted(reader.absent)])
    return result


def _timing(record=None, final=None, deadline=None):
    record, final = record or {}, final or {}
    start = record.get("started_at")
    finish = record.get("completed_at") or record.get("finished_at")
    end = final.get("final_sealed_at")
    result = dict(operation_started_at=start, operation_finished_at=finish,
        final_sealed_at=final.get("final_sealed_at"), utc_operation_seconds=None,
        operation_action_monotonic_seconds=None, supervisor_monotonic_seconds=None,
        deadline=deadline, workflow_sealed_by_deadline="UNKNOWN", user_received_by_deadline="UNKNOWN",
        deadline_role="CALLER_SUPPLIED_NOT_VERIFIED_PRECOMMITTED",
        clocks_are_separate=True, clock_difference_cause="NOT_INFERRED")
    for name in (start, finish, final.get("final_sealed_at")):
        if name is not None:
            _clock(name)
    if start is not None and finish is not None:
        elapsed = (_clock(finish)-_clock(start)).total_seconds()
        _need(elapsed >= 0, "AUDIT_CLOCK_ORDER")
        result["utc_operation_seconds"] = elapsed
    for key, value in (("operation_action_monotonic_seconds", record.get("action_seconds")),
                       ("supervisor_monotonic_seconds", final.get("elapsed_seconds"))):
        if value is not None:
            result[key] = _seconds(value)
    if deadline is not None:
        cutoff = _clock(deadline)
        if final.get("status") in SUCCESS and end is not None:
            result["workflow_sealed_by_deadline"] = _clock(end) <= cutoff
    return result


def _owned(reader, job, reference, *, control=False):
    _need(type(reference) is dict and type(reference.get("path")) is str,
          "AUDIT_REFERENCE_REQUIRED")
    path = _path(reference["path"])
    _need(path.is_relative_to(job), "AUDIT_ARTIFACT_OUTSIDE_JOB")
    return reader.read(path, expected=reference, control=control)


def _job(reader, job, *, expected_final=None, receipt=None, deadline=None):
    job = _path(job, directory=True)
    result = dict(job=str(job), workflow_status="IN_PROGRESS_OR_UNKNOWN",
        archived_workflow_complete=False, data_completeness="UNKNOWN",
        archived_core_status="UNKNOWN", core_usable_for_new_delivery=False,
        phases={name: "UNKNOWN" for name in PHASES}, run_id=None,
        request_binding="NOT_PROVIDED", timing=_timing(deadline=deadline))
    final_path = job / "final-control.json"
    if not reader.exists(final_path):
        _need(expected_final is None, "AUDIT_RECEIPT_FINAL_MISSING")
        return result
    final, _ = reader.read(final_path, expected=expected_final, control=True)
    _need(final.get("schema_version") in WORKFLOWS, "AUDIT_FINAL_IDENTITY")
    status = final.get("status")
    if status == "UNSEALED":
        _need(expected_final is None and receipt is None and final.get("job", str(job)) == str(job),
              "AUDIT_RECORDED_JOB_UNSEALED")
        if final.get("created_at") is not None:
            _clock(final["created_at"])
        return result
    _need(final.get("job") == str(job), "AUDIT_FINAL_IDENTITY")
    _need(status in SUCCESS or status in FAILED, "AUDIT_UNSUPPORTED_FINAL_STATUS")
    result.update(workflow_status=status, timing=_timing(final=final, deadline=deadline),
                  worker_reaped=final.get("external_worker_reaped") is True)
    completion = None
    if final.get("completion") is not None:
        completion, _ = _owned(reader, job, final["completion"], control=True)
    elif status in FAILED and reader.exists(job / "worker-completion.json"):
        completion, _ = reader.read(job / "worker-completion.json", control=True)
        result["completion_binding"] = "UNBOUND_WORKER_RECORD_AFTER_FAILED_SUPERVISOR"
    if completion is not None:
        _need(completion.get("schema_version") == final["schema_version"], "AUDIT_COMPLETION_SCHEMA")
        phases = completion.get("phases")
        _need(type(phases) is dict and set(phases) == set(PHASES)
              and all(type(value) is str for value in phases.values()), "AUDIT_COMPLETE_PHASE_MAP_REQUIRED")
        result.update(phases=phases, run_id=completion.get("run_id"),
            observation_date=completion.get("observation_date"), target_session=completion.get("target_session"),
            strategy_id=completion.get("strategy_id"), theme_status=completion.get("theme_status"))
        for role in ("core_artifacts", "theme_artifacts"):
            refs = completion.get(role) or {}
            _need(type(refs) is dict, "AUDIT_ARTIFACT_MAP_REQUIRED")
            for ref in refs.values():
                _owned(reader, job, ref)
        core = completion.get("core_artifacts") or {}
        if completion.get("core_verified") is True and set(core) == original.CORE_FILES and phases["core"] == "COMPLETE":
            result["archived_core_status"] = "BYTES_VERIFIED_FRESH_SOURCE_AUDIT_REQUIRED"
        display = completion.get("display")
        if display:
            _need(type(display.get("artifacts")) is dict and len(display["artifacts"]) == 3,
                  "AUDIT_THREE_DISPLAY_ARTIFACTS_REQUIRED")
            for reference in display["artifacts"].values():
                _owned(reader, job, reference)
        if completion.get("news") is not None:
            _owned(reader, job, completion["news"]["metadata"])
        # Shared execution can bind extra owned journal/facts outside its
        # top-level artifact maps; those are still part of local completeness.
        for reference in completion.get("validation_references", []):
            _need(type(reference) is dict and type(reference.get("path")) is str,
                  "AUDIT_REFERENCE_REQUIRED")
            if Path(reference["path"]).is_relative_to(job):
                _owned(reader, job, reference)
        result["data_completeness"] = ("KNOWN_STAGE_GAPS" if any(value != "COMPLETE" for value in phases.values())
                                       else "UNKNOWN_REQUIRES_FULL_POOL_COVERAGE_AUDIT")
    for role in ("configuration", "code_registry"):
        if final.get(role) is not None:
            _owned(reader, job, final[role])
    if status in SUCCESS:
        _need(completion is not None and completion.get("status") == status,
              "AUDIT_SUCCESS_COMPLETION_CONFLICT")
        _need(final.get("external_worker_exit_code") == SUCCESS[status]
              and final.get("external_worker_reaped") is True
              and final.get("core_verified") is True
              and final.get("implementation_epoch_verified") is True
              and final.get("source_terminal_binding_verified") is True,
              "AUDIT_SUCCESS_TERMINAL_GUARDS_REQUIRED")
        _need(result["archived_core_status"] == "BYTES_VERIFIED_FRESH_SOURCE_AUDIT_REQUIRED"
              and completion.get("display") and final.get("display") == completion["display"]
              and completion["phases"]["presentation"] == "COMPLETE"
              and final.get("configuration") and final.get("code_registry"),
              "AUDIT_SUCCESS_FULL_OUTPUT_REQUIRED")
        _need((status == "COMPLETE") == all(value == "COMPLETE" for value in completion["phases"].values()),
              "AUDIT_STATUS_PHASE_CONFLICT")
        result["archived_workflow_complete"] = True
    else:
        result["data_completeness"] = "WORKFLOW_FAILED"
        result["reason"] = "FINAL_FAILURE_NOT_OVERRIDDEN_BY_INTERMEDIATE_CORE_OR_WORKER_SUCCESS"
    if receipt is not None:
        _need(status in SUCCESS and receipt.get("status") == status
              and receipt.get("execution_exit_code") == SUCCESS[status]
              and receipt.get("completion") == final.get("completion"), "AUDIT_RECEIPT_FINAL_CONFLICT")
        for name in ("run_id", "observation_date", "target_session", "strategy_id"):
            _need(receipt.get(name) == completion.get(name), "AUDIT_RECEIPT_RUN_IDENTITY_CONFLICT")
        if receipt.get("execution_engine") is not None:
            _need(receipt["execution_engine"] == final.get("execution_engine") == completion.get("execution_engine"),
                  "AUDIT_RECEIPT_ENGINE_CONFLICT")
        result["request_binding"] = "RECORDED_RECEIPT_REFERENCES_MATCH"
    return result


def audit_job(job, *, deadline=None, max_bytes=256*1024**2, max_seconds=120):
    """Inspect one explicitly selected job; never infer a request association."""
    reader = _Reader(max_bytes, max_seconds)
    return _finish(reader, {**_base(), **_job(reader, job, deadline=deadline)})


def _operation(reader, path, *, state_root, deadline=None, expected_scope=None):
    path, state_root = _path(path), _path(state_root, directory=True)
    record, _ = reader.read(path, control=True)
    identity = record.get("identity")
    _need(record.get("schema_version") == workspace.VERSION and type(identity) is dict
          and identity.get("schema_version") in OPERATIONS
          and identity.get("backend") == "premarket",
          "AUDIT_OPERATION_IDENTITY")
    state_stat = state_root.stat()
    physical_scope = dict(backend="premarket", device=state_stat.st_dev, inode=state_stat.st_ino)
    _need(record.get("scope") == physical_scope, "AUDIT_OPERATION_PHYSICAL_SCOPE")
    if identity["schema_version"] == "CODEX_RECOVERED_PREMARKET_V1":
        _need("state_root" not in identity and identity.get("execution_engine") == "original"
              and identity.get("explicit_collection_profile") == "REAL_PREMARKET_RECOVERY_3H_V1",
              "AUDIT_RECOVERED_OPERATION_IDENTITY")
    else:
        _need(identity.get("state_root") == str(state_root), "AUDIT_OPERATION_STATE_ROOT")
    if expected_scope is not None:
        _need(record.get("scope") == expected_scope, "AUDIT_OPERATION_SCOPE")
    key = workspace._digest({"scope": record.get("scope"), "identity": identity})
    _need(record.get("operation_id") == key and path.name == key + ".json", "AUDIT_OPERATION_KEY")
    request_key = identity.get("request_key")
    _need(isinstance(request_key, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", request_key),
          "AUDIT_REQUEST_KEY_REQUIRED")
    observed, target = identity.get("observation_date"), identity.get("target_session")
    _need(type(observed) is str and type(target) is str and
          date.fromisoformat(observed).isoformat() == observed and date.fromisoformat(target).isoformat() == target
          and observed < target, "AUDIT_ORDERED_SESSIONS_REQUIRED")
    phase = record.get("phase")
    _need(phase in ("SUBMITTING", "FAILED_OR_UNKNOWN", "RECORDED"), "AUDIT_OPERATION_PHASE")
    result = {**_base(), "operation_id": key, "request_key": request_key,
        "observation_date": observed, "target_session": target, "operation_phase": phase,
        "workflow_status": "IN_PROGRESS_OR_UNKNOWN" if phase == "SUBMITTING" else "FAILED_OR_UNKNOWN",
        "archived_workflow_complete": False, "data_completeness": "UNKNOWN",
        "archived_core_status": "UNKNOWN", "core_usable_for_new_delivery": False,
        "request_binding": "NO_VALIDATED_JOB_RECEIPT", "timing": _timing(record, deadline=deadline)}
    if phase == "RECORDED":
        receipt = record.get("receipt")
        _need(type(receipt) is dict and receipt.get("final"), "AUDIT_RECORDED_RECEIPT_REQUIRED")
        job = _path(receipt["job"], directory=True)
        _need(job.parent == state_root / "jobs", "AUDIT_OPERATION_JOB_OUTSIDE_STATE")
        _need(receipt.get("observation_date") == observed and receipt.get("target_session") == target,
              "AUDIT_OPERATION_RECEIPT_SESSIONS")
        job_report = _job(reader, job, expected_final=receipt["final"], receipt=receipt, deadline=deadline)
        final, _ = reader.read(job / "final-control.json", expected=receipt["final"], control=True)
        result.update(job_report, timing=_timing(record, final, deadline))
    return result


def audit_operation(path, *, state_root, deadline=None, max_bytes=256*1024**2, max_seconds=120):
    """Inspect one unchanged run_once record; failed records never retry."""
    reader = _Reader(max_bytes, max_seconds)
    return _finish(reader, _operation(reader, path, state_root=state_root, deadline=deadline))


def audit_runtime(runtime, *, deadline=None, max_bytes=256*1024**2, max_seconds=120):
    """Read the original bound scope without taking its writing OS lock.

    A writer changing any consumed record or the directory membership makes the
    audit fail. Older/unrelated operations are counted, never silently promoted
    to supported delivery requests or guessed as today's work.
    """
    _need(runtime is not None and runtime.profile.get("backend") == "premarket",
          "AUDIT_PREMARKET_RUNTIME_REQUIRED")
    runtime.check_binding()
    reader = _Reader(max_bytes, max_seconds)
    directory = Path(runtime.directory) / "operations"
    paths = []
    if reader.exists(directory):
        directory = _path(directory, directory=True)
        paths = sorted(directory.glob("*.json"))
        _need(len(paths) <= 10000, "AUDIT_OPERATION_SCAN_LIMIT")
    rows, unsupported = [], 0
    for path in paths:
        record, _ = reader.read(path, control=True)
        identity = record.get("identity") or {}
        _need(type(identity) is dict, "AUDIT_OPERATION_IDENTITY")
        if identity.get("schema_version") in OPERATIONS:
            rows.append(_operation(reader, path, state_root=runtime.profile["state_root"],
                                   deadline=deadline, expected_scope=runtime.scope))
        else:
            unsupported += 1
    result = {**summarize(rows), "runtime_name": runtime.profile.get("name"),
        "scope": runtime.scope, "reports": rows, "unsupported_or_other_operation_count": unsupported,
        "request_inventory_role": "CURRENT_BOUND_SCOPE_ONLY_NOT_ALL_JOBS_OR_RECOVERY_ANCESTORS"}
    _finish(reader, result)
    _need((sorted(directory.glob("*.json")) if directory.exists() else []) == paths,
          "AUDIT_OPERATION_DIRECTORY_CHANGED")
    runtime.check_binding()
    return result


def audit_preflight(path, *, max_bytes=256*1024**2, max_seconds=120):
    """Inspect a saved preflight only; an admitted plan is never execution."""
    reader = _Reader(max_bytes, max_seconds)
    value, _ = reader.read(path, control=True)
    plan = value.get("preflight", value)
    _need(type(plan) is dict and plan.get("schema_version") == "CODEX_PREMARKET_PREFLIGHT_V1"
          and type(plan.get("execution_admitted")) is bool, "AUDIT_PREFLIGHT_SCHEMA")
    _clock(plan.get("planned_at"))
    result = {**_base(), "workflow_status": "ADMITTED_NOT_EXECUTED" if plan["execution_admitted"] else "BLOCKED_BEFORE_COLLECTION",
        "archived_workflow_complete": False, "data_completeness": "UNKNOWN",
        "request_claimed": False, "planned_at": plan["planned_at"],
        "observation_date": plan.get("observation_date"), "target_session": plan.get("target_session"),
        "reason_codes": plan.get("reasons", []), "stage_requirements": plan.get("stages", []),
        "plan_currentness": "SAVED_PLAN_NOT_RECOMPUTED"}
    return _finish(reader, result)


def summarize(reports):
    """Summarize exact request reports; no trading-day denominator is invented."""
    rows = list(reports)
    _need(all(type(row) is dict and row.get("schema_version") == VERSION and row.get("operation_id") for row in rows),
          "AUDIT_OPERATION_REPORTS_REQUIRED")
    ids = [row["operation_id"] for row in rows]
    _need(len(ids) == len(set(ids)), "AUDIT_DUPLICATE_OPERATION")
    sessions = [(row["observation_date"], row["target_session"]) for row in rows]
    return {**_base(), "scope": "EXPLICIT_OPERATION_REPORTS_NOT_EXPECTED_TRADING_SESSIONS",
        "operation_count": len(rows), "distinct_session_pairs": len(set(sessions)),
        "multiple_operations_same_session": len(rows) != len(set(sessions)),
        "workflow_counts": dict(sorted(Counter(row["workflow_status"] for row in rows).items())),
        "data_completeness_counts": dict(sorted(Counter(row["data_completeness"] for row in rows).items())),
        "user_delivered_count": None, "missing_trading_days": None, "continuous_delivery_accepted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读审阅原盘前交付证据；不采集、不重试、不恢复来源能力", allow_abbrev=False)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job")
    group.add_argument("--operation")
    group.add_argument("--preflight")
    parser.add_argument("--state-root")
    parser.add_argument("--deadline", help="有时区的事前交付目标；不代表实际用户收到")
    args = parser.parse_args(argv)
    if args.operation and not args.state_root:
        parser.error("--operation requires --state-root")
    try:
        if args.job:
            result = audit_job(args.job, deadline=args.deadline)
        elif args.operation:
            result = audit_operation(args.operation, state_root=args.state_root, deadline=args.deadline)
        else:
            result = audit_preflight(args.preflight)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        reason = str(error) if isinstance(error, AuditError) else "AUDIT_INPUT_UNAVAILABLE_OR_INVALID"
        print(json.dumps(dict(schema_version=VERSION, status="AUDIT_FAILED", reason=reason), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
