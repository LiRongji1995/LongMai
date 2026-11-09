"""One budgeted same-session refresh attached to a sealed continuation.

This coordinator grants no source retry and never rewrites the original
operation, continuation, provider evidence or report. The daily entry supplies
the ordinary collection and validation callbacks under the existing scope lock.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, time as wall_time
import math
from pathlib import Path
import time

from scripts import codex_workspace as workspace
from scripts import codex_premarket_continuation as parent
from stockfish_core.file_integrity import content_equal, content_reference_equal, _content
from stockfish_core.yaogu.premarket_runtime_v1 import Budget
from stockfish_core.yaogu.collection_factor_reconciliation import future_factor_authorization_basis

VERSION = "PREMARKET_SAME_SESSION_REFRESH_PROPOSAL_V1"
CLAIM_VERSION = "PREMARKET_SAME_SESSION_REFRESH_CLAIM_V1"
TERMINAL_VERSION = "PREMARKET_SAME_SESSION_REFRESH_TERMINAL_V1"
AUTHORIZATION_VERSION = "PREMARKET_SAME_SESSION_REFRESH_AUTHORIZATION_V1"
PREPARE_READ_BYTES = 1024**3
PREPARE_SECONDS = 120
CONTROL_READ_BYTES = 256 * 1024**2
CONTROL_SECONDS = 60
WRAPPER_PREPARE_READ_BYTES = 64 * 1024**2
WRAPPER_PREPARE_SECONDS = 60
JOURNAL_OUTPUT_BYTES = 1024**2
MAX_FILES = 8192


def _need(condition, reason):
    if not condition:
        raise ValueError("REFRESH_" + reason)


def _now(proposal=None, *, override=None, fixture=False):
    if proposal is not None:
        fixture = proposal["preparation_arguments"]["allow_test_fixture"]
        override = proposal["preparation_arguments"].get("_now_value")
    return parent._now(override, fixture)


def ensure_before_deadline(proposal):
    now = _now(proposal)
    deadline = parent._clock(proposal["premarket_deadline"])
    _need(str(now.astimezone(parent.SH).date()) == proposal["expected_sessions"][2]
          and now < deadline, "PREMARKET_DEADLINE_PASSED")
    return now


def _slot(runtime, operation_id):
    return parent._slot(runtime, operation_id).parent / "refresh"


def _digest(value):
    return workspace._digest(_content({k: v for k, v in value.items()
        if k not in ("proposal_id", "preparation_metrics")}))


def _verify_refs(reader, proposal):
    for reference in proposal["validation_references"]:
        actual = reader.reference(reference["path"], limit=max(reference["bytes"], 1))
        _need(content_reference_equal(actual, reference), "PARENT_REFERENCE_CHANGED")


def _inventory(job, reader, maximum):
    paths = sorted(job.rglob("*"))
    _need(len(paths) <= MAX_FILES and all(not p.is_symlink() for p in paths), "PARENT_JOB_INVENTORY_BOUND")
    files = [p for p in paths if p.is_file()]
    _need(all(p.is_file() or p.is_dir() for p in paths), "PARENT_JOB_UNKNOWN_OBJECT")
    total = sum(p.stat().st_size for p in files)
    _need(total <= maximum, "PARENT_OUTPUT_BUDGET_EXCEEDED")
    references = [reader.reference(p, limit=max(p.stat().st_size, 1)) for p in files]
    _need(sum(ref["bytes"] for ref in references) == total, "PARENT_OUTPUT_CHANGED")
    return total, [dict(path=str(p), type="DIRECTORY" if p.is_dir() else "FILE") for p in paths]


def _number(value, *, integer=False):
    _need(type(value) is int if integer else type(value) in (int, float), "ACCOUNTING_NUMBER_TYPE")
    _need(math.isfinite(value) and value >= 0, "ACCOUNTING_NUMBER_RANGE")
    return value


def _remaining(prior, terminal, output_bytes, now, deadline, frozen_seconds):
    original = prior["original_limits"]
    limits = deepcopy(prior["remaining_limits"])
    _need(set(limits) == set(original), "PARENT_BUDGET_KEYS_CHANGED")
    for key, value in limits.items():
        _need(0 < _number(value) <= _number(original[key]), "PARENT_BUDGET_NOT_FINITE")
    measured = terminal["new_accounting"]
    worker, coordinator = measured["worker_metrics"], measured["coordinator_metrics"]
    for metrics in (worker, coordinator, measured["wrapper_preparation_metrics"]):
        _need(metrics.get("pending_request_results") == 0 and metrics.get("failures") == []
              and metrics.get("calls", {}).get("actual_unknown") == 0, "PARENT_CONSUMPTION_UNKNOWN")
    _need(worker["limits"] == limits, "PARENT_WORKER_LIMITS_CHANGED")
    calls = measured["cumulative_calls"]
    for counter in ("requests", "sdk_reserved", "http_reserved", "sdk_actual", "http_actual"):
        _need(_number(calls[counter], integer=True) ==
              _number(prior["parent_provider_counts"][counter], integer=True)
              + _number(worker["calls"][counter], integer=True), "PARENT_CUMULATIVE_CALLS_CHANGED")
    # The whole old controller interval includes worker, locked preparation and
    # final validation. Debiting it again is deliberately conservative: old
    # reserved but unused control room is never refunded.
    elapsed = math.ceil(max(_number(terminal["parent_metrics"]["elapsed_seconds"]),
        _number(worker["elapsed_seconds"]), _number(coordinator["elapsed_seconds"])))
    debit = dict(max_seconds=elapsed + 2 * PREPARE_SECONDS + CONTROL_SECONDS + WRAPPER_PREPARE_SECONDS,
        supervisor_read_bytes=_number(coordinator["returned_read_bytes"]["capture"], integer=True)
            + 2 * PREPARE_READ_BYTES + CONTROL_READ_BYTES + WRAPPER_PREPARE_READ_BYTES,
        output_bytes=output_bytes + JOURNAL_OUTPUT_BYTES)
    for counter, cap in (("requests", "max_requests"), ("sdk_reserved", "max_sdk_calls"),
                         ("http_reserved", "max_http_attempts")):
        debit[cap] = _number(worker["calls"][counter], integer=True)
    for phase in ("capture", "core", "theme", "presentation"):
        debit[phase + "_read_bytes"] = _number(worker["returned_read_bytes"][phase], integer=True)
    for key, value in debit.items():
        limits[key] -= value
        _need(limits[key] > 0, "CUMULATIVE_BUDGET_EXHAUSTED:" + key)
    available = math.floor((deadline - now).total_seconds())
    if frozen_seconds is None:
        limits["max_seconds"] = min(limits["max_seconds"], available -
            (2 * PREPARE_SECONDS + CONTROL_SECONDS + WRAPPER_PREPARE_SECONDS))
    else:
        _need(type(frozen_seconds) in (int, float) and 0 < frozen_seconds <= limits["max_seconds"],
              "FROZEN_EXECUTION_LIMIT_CHANGED")
        _need(available >= frozen_seconds + PREPARE_SECONDS + CONTROL_SECONDS + WRAPPER_PREPARE_SECONDS,
              "PREMARKET_WINDOW_TOO_SHORT_BEFORE_CLAIM")
        limits["max_seconds"] = frozen_seconds
    _need(limits["max_seconds"] > 0, "PREMARKET_WINDOW_EXHAUSTED")
    return limits, dict(debits=debit, prior_cumulative_calls=deepcopy(calls),
        old_controller_seconds_debit=elapsed, old_job_output_bytes=output_bytes,
        new_prepare_read_reserve_bytes=2 * PREPARE_READ_BYTES,
        new_prepare_seconds_reserve=2 * PREPARE_SECONDS,
        new_control_read_reserve_bytes=CONTROL_READ_BYTES, new_control_seconds_reserve=CONTROL_SECONDS,
        new_wrapper_read_reserve_bytes=WRAPPER_PREPARE_READ_BYTES,
        new_wrapper_seconds_reserve=WRAPPER_PREPARE_SECONDS,
        journal_output_reserve_bytes=JOURNAL_OUTPUT_BYTES,
        budget_credit=0, peak_limits_policy="UNCHANGED_RSS_AND_GLOBAL_CACHE_CAPS")


def prepare(runtime, *, operation_path, expected_terminal_sha256, allow_test_fixture=False, _now_value=None):
    return _prepare(runtime, operation_path=operation_path, expected_terminal_sha256=expected_terminal_sha256,
                    allow_test_fixture=allow_test_fixture, _now_value=_now_value)


def _prepare(runtime, *, operation_path, expected_terminal_sha256, allow_test_fixture=False,
             _now_value=None, frozen_seconds=None):
    _need(type(allow_test_fixture) is bool and (not allow_test_fixture or "TEST_FIXTURE" in str(runtime.directory)),
          "EXPLICIT_TEST_FIXTURE_REQUIRED")
    now = _now(override=_now_value, fixture=allow_test_fixture)
    budget = Budget(parent._reader_limits(seconds=PREPARE_SECONDS, byte_limit=PREPARE_READ_BYTES))
    with budget.meter_files():
        runtime.check_binding()
        reader = parent._Reader(budget)
        path = parent._path(operation_path)
        operation, operation_ref = reader.read(path)
        identity = operation.get("identity", {})
        opid = operation.get("operation_id")
        _need(path.parent == Path(runtime.directory) / "operations" and path.stem == opid
              and operation.get("scope") == runtime.scope and operation.get("phase") == "FAILED_OR_UNKNOWN"
              and opid == workspace._digest(dict(scope=runtime.scope, identity=identity))
              and identity.get("backend") == runtime.profile.get("backend") == "premarket"
              and identity.get("state_root") == runtime.profile["state_root"], "ORIGINAL_OPERATION_SCOPE_CHANGED")
        slot = _slot(runtime, opid)
        if slot.exists():
            _need(not slot.is_symlink() and set(p.name for p in slot.iterdir()) <=
                  {"proposal.json", "claim.json", "terminal.json"}, "FIXED_SLOT_CHANGED")
        if (slot / "claim.json").exists():
            stored, proposal_ref = reader.read(slot / "proposal.json")
            claim, claim_ref = reader.read(slot / "claim.json")
            _need((slot / "terminal.json").is_file(), "CLAIM_EXISTS_NO_RETRY")
            terminal, _ = reader.read(slot / "terminal.json")
            _need(stored.get("proposal_id") == _digest(stored) == claim.get("proposal_id") == terminal.get("proposal_id")
                  and terminal.get("schema_version") == TERMINAL_VERSION and terminal.get("status") == "SUCCEEDED"
                  and content_reference_equal(claim["proposal_ref"], proposal_ref)
                  and content_reference_equal(terminal["claim_ref"], claim_ref)
                  and stored["base_terminal_ref"]["sha256"] == expected_terminal_sha256,
                  "CLAIM_EXISTS_NO_RETRY")
            _claim_matches(claim, stored)
            _need(parent._clock(claim["claimed_at"]) <= parent._clock(terminal["completed_at"])
                  < parent._clock(stored["premarket_deadline"]), "SEALED_PREMARKET_CLOCK_CHANGED")
            _parent_unchanged(runtime, stored, reader)
            reader.verify_all()
            return stored
        base_slot = parent._slot(runtime, opid)
        terminal, terminal_ref = reader.read(base_slot / "terminal.json")
        _need(terminal_ref["sha256"] == expected_terminal_sha256, "EXPECTED_PARENT_TERMINAL_CHANGED")
        prior, _ = reader.read(base_slot / "proposal.json")
        cache = parent._future_readonly_cache(Path(identity["state_root"]) / "cache", budget, allow_test_fixture)
        basis = future_factor_authorization_basis(cache, scope=runtime.scope,
            scope_directory=str(runtime.directory), operation_id=opid, terminal_reference=terminal_ref)
        _need(content_reference_equal(basis["original_operation_ref"], operation_ref), "PARENT_OPERATION_CHANGED")
        observation, target = identity["observation_date"], identity["target_session"]
        local = now.astimezone(parent.SH)
        _need(str(local.date()) == target and observation < target, "REFRESH_REQUIRES_TARGET_SESSION_MORNING")
        deadline = datetime.combine(local.date(), wall_time(9, 30), parent.SH)
        _need(now < deadline, "PREMARKET_DEADLINE_PASSED")
        calendar = cache.calendar_reference(now)
        _need(calendar is not None, "AUTHENTICATED_CURRENT_CALENDAR_REQUIRED")
        from stockfish_core.yaogu.premarket_workflow_v1 import _latest_gate
        _latest_gate(cache.verify(calendar)["rows"], observation, target, now)
        job = parent._path(basis["completed_job"], directory=True)
        output, inventory = _inventory(job, reader, prior["remaining_limits"]["output_bytes"])
        limits, accounting = _remaining(prior, terminal, output, now, deadline, frozen_seconds)
        for reference in cache.terminal():
            previous = reader.refs.setdefault(reference["path"], reference)
            _need(content_reference_equal(previous, reference), "PARENT_SOURCE_REFERENCE_CHANGED")
        proposal = dict(schema_version=VERSION, status="ADMITTED_ONE_SAME_SESSION_REFRESH",
            scope=deepcopy(runtime.scope), scope_directory=str(runtime.directory), operation_id=opid,
            original_operation_id=opid, identity=deepcopy(identity), original_operation_ref=operation_ref,
            base_terminal_ref=terminal_ref, source_job=str(job), expected_sessions=[observation, target, str(local.date())],
            premarket_deadline=deadline.isoformat(), remaining_limits=limits, budget_debits=accounting,
            parent_provider_counts=terminal["new_accounting"]["cumulative_calls"],
            parent_job_inventory=inventory, validation_references=[reader.refs[k] for k in sorted(reader.refs)],
            retry_authorized=False, continuation_authorization_reused=False,
            original_records_rewritten=False, budget_credit=0,
            preparation_arguments=dict(operation_path=str(path), expected_terminal_sha256=expected_terminal_sha256,
                allow_test_fixture=allow_test_fixture, _now_value=_now_value))
        reader.verify_all()
        runtime.check_binding()
        proposal["proposal_id"] = _digest(proposal)
    proposal["preparation_metrics"] = budget.metrics
    return proposal


def _authorization(runtime, proposal, claim_ref, proposal_ref):
    return dict(schema_version=AUTHORIZATION_VERSION, scope=deepcopy(runtime.scope),
        scope_directory=str(runtime.directory), operation_id=proposal["operation_id"],
        claim=claim_ref, proposal=proposal_ref)


def _inventory_unchanged(proposal):
    job = Path(proposal["source_job"])
    paths = sorted(job.rglob("*"))
    _need(all(not p.is_symlink() for p in paths), "PARENT_JOB_ALIAS")
    actual = [dict(path=str(p), type="DIRECTORY" if p.is_dir() else "FILE") for p in paths]
    _need(actual == proposal["parent_job_inventory"], "PARENT_JOB_INVENTORY_CHANGED")


def _parent_unchanged(runtime, proposal, reader):
    _inventory_unchanged(proposal)
    _verify_refs(reader, proposal)
    cache = parent._future_readonly_cache(Path(proposal["identity"]["state_root"]) / "cache",
        reader.budget, proposal["preparation_arguments"]["allow_test_fixture"])
    basis = future_factor_authorization_basis(cache, scope=runtime.scope, scope_directory=str(runtime.directory),
        operation_id=proposal["operation_id"], terminal_reference=proposal["base_terminal_ref"])
    _need(basis["completed_job"] == proposal["source_job"]
          and content_reference_equal(basis["original_operation_ref"], proposal["original_operation_ref"]),
          "PARENT_BINDING_CHANGED")
    for reference in cache.terminal():
        previous = reader.refs.setdefault(reference["path"], reference)
        _need(content_reference_equal(previous, reference), "PARENT_REFERENCE_CHANGED")


def _claim_matches(claim, proposal):
    _need(claim.get("schema_version") == CLAIM_VERSION and claim.get("scope") == proposal["scope"]
          and claim.get("operation_id") == proposal["operation_id"]
          and claim.get("remaining_limits") == proposal["remaining_limits"]
          and claim.get("expected_sessions") == proposal["expected_sessions"]
          and claim.get("premarket_deadline") == proposal["premarket_deadline"]
          and content_reference_equal(claim["base_terminal_ref"], proposal["base_terminal_ref"])
          and claim.get("retry_authorized") is False and claim.get("original_records_rewritten") is False
          and parent._clock(claim["claimed_at"]) < parent._clock(proposal["premarket_deadline"]),
          "CLAIM_BINDING_CHANGED")


def execute(runtime, proposal, *, action, validate):
    _need(callable(action) and callable(validate) and proposal.get("schema_version") == VERSION
          and proposal.get("proposal_id") == _digest(proposal) and proposal.get("scope") == runtime.scope
          and proposal.get("scope_directory") == str(runtime.directory), "PROPOSAL_CHANGED")
    slot = _slot(runtime, proposal["operation_id"])
    started = time.monotonic()
    started_clock = _now(proposal)
    with runtime.exclusive():
        control = Budget(parent._reader_limits(seconds=proposal["remaining_limits"]["max_seconds"]
            + PREPARE_SECONDS + CONTROL_SECONDS + WRAPPER_PREPARE_SECONDS, byte_limit=CONTROL_READ_BYTES))
        reader = parent._Reader(control)
        # Large old artifacts were authenticated in prepare; execute's locked
        # prepare accounts their second full read before any durable claim.
        if (slot / "claim.json").exists():
            with control.meter_files():
                stored, proposal_ref = reader.read(slot / "proposal.json")
                claim, claim_ref = reader.read(slot / "claim.json")
                _need((slot / "terminal.json").is_file(), "CLAIM_EXISTS_NO_RETRY")
                terminal, terminal_ref = reader.read(slot / "terminal.json")
                _need(stored.get("proposal_id") == proposal["proposal_id"] == claim.get("proposal_id")
                      and terminal.get("schema_version") == TERMINAL_VERSION
                      and terminal.get("status") == "SUCCEEDED" and terminal.get("proposal_id") == proposal["proposal_id"]
                      and content_reference_equal(claim["proposal_ref"], proposal_ref)
                      and content_reference_equal(terminal["claim_ref"], claim_ref), "CLAIM_EXISTS_NO_RETRY")
                _claim_matches(claim, proposal)
                _need(parent._clock(claim["claimed_at"]) <= parent._clock(terminal["completed_at"])
                      < parent._clock(proposal["premarket_deadline"]), "SEALED_PREMARKET_CLOCK_CHANGED")
                authorization = _authorization(runtime, proposal, claim_ref, proposal_ref)
                _need(content_equal(terminal["receipt"].get("refresh_authorization"), authorization),
                      "RECEIPT_AUTHORIZATION_CHANGED")
            checked = validate(deepcopy(terminal["receipt"]), deepcopy(stored))
            with control.meter_files():
                _need(isinstance(checked, dict) and checked.get("verified") is True, "RECEIPT_NOT_VERIFIED")
                _parent_unchanged(runtime, stored, reader)
                reader.verify_all()
                runtime.check_binding()
            return dict(status="REUSED", operation_id=proposal["operation_id"], receipt=terminal["receipt"],
                validation=checked, authorization=authorization, terminal_ref=terminal_ref,
                new_action_calls=0, read_only_archived_reuse=True, parent_metrics=control.metrics)
        ensure_before_deadline(proposal)
        fresh = _prepare(runtime, **proposal["preparation_arguments"], frozen_seconds=proposal["remaining_limits"]["max_seconds"])
        _need(fresh["proposal_id"] == proposal["proposal_id"], "ADMISSION_CHANGED_BEFORE_CLAIM")
        _need(time.monotonic() - started <= PREPARE_SECONDS, "LOCKED_PREPARATION_TIME_EXCEEDED")
        claim_ref = None
        try:
            with control.meter_files():
                now = ensure_before_deadline(proposal)
                _need((parent._clock(proposal["premarket_deadline"]) - now).total_seconds()
                      >= proposal["remaining_limits"]["max_seconds"] + CONTROL_SECONDS,
                      "PREMARKET_WINDOW_TOO_SHORT_BEFORE_CLAIM")
                if (slot / "proposal.json").exists():
                    stored, proposal_ref = reader.read(slot / "proposal.json")
                    _need(stored.get("proposal_id") == proposal["proposal_id"], "EXISTING_PROPOSAL_CHANGED")
                else:
                    workspace._write(slot / "proposal.json", proposal)
                    _, proposal_ref = reader.read(slot / "proposal.json")
                claim = dict(schema_version=CLAIM_VERSION, proposal_id=proposal["proposal_id"],
                    scope=deepcopy(runtime.scope), operation_id=proposal["operation_id"],
                    proposal_ref=proposal_ref, base_terminal_ref=proposal["base_terminal_ref"],
                    remaining_limits=proposal["remaining_limits"], expected_sessions=proposal["expected_sessions"],
                    premarket_deadline=proposal["premarket_deadline"], claimed_at=_now(proposal).isoformat(),
                    retry_authorized=False, original_records_rewritten=False)
                _need(len(workspace._canonical(proposal)) + len(workspace._canonical(claim))
                      < JOURNAL_OUTPUT_BYTES - workspace.MAX_RECEIPT, "JOURNAL_OUTPUT_RESERVE_INSUFFICIENT")
                workspace._write(slot / "claim.json", claim)
                _, claim_ref = reader.read(slot / "claim.json")
            authorization = _authorization(runtime, proposal, claim_ref, proposal_ref)
            ensure_before_deadline(proposal)
            receipt = action(deepcopy(proposal["remaining_limits"]), deepcopy(authorization))
            ensure_before_deadline(proposal)
            _need(isinstance(receipt, dict) and content_equal(receipt.get("refresh_authorization"), authorization)
                  and len(workspace._canonical(receipt)) <= workspace.MAX_RECEIPT, "RECEIPT_AUTHORIZATION_CHANGED")
            validation_started = time.monotonic()
            checked = validate(deepcopy(receipt), deepcopy(proposal))
            with control.meter_files():
                _need(isinstance(checked, dict) and checked.get("verified") is True, "RECEIPT_NOT_VERIFIED")
                measured = parent._terminal_metrics(reader, receipt, checked, proposal)
                _parent_unchanged(runtime, proposal, reader)
                reader.verify_all()
                finished_clock = ensure_before_deadline(proposal)
                _need(0 <= (finished_clock - started_clock).total_seconds()
                      <= control.limits["max_seconds"], "EXECUTION_CLOCK_CHANGED")
                _need(time.monotonic() - validation_started <= CONTROL_SECONDS, "TERMINAL_VALIDATION_TIME_EXCEEDED")
                runtime.check_binding()
                control.check("refresh_terminal", force=True)
                terminal = dict(schema_version=TERMINAL_VERSION, status="SUCCEEDED", proposal_id=proposal["proposal_id"],
                    operation_id=proposal["operation_id"], claim_ref=claim_ref, receipt=receipt, validation=checked,
                    completed_at=_now(proposal).isoformat(), original_records_rewritten=False,
                    retry_authorized=False, budget_credit=0, new_accounting=measured,
                    parent_metrics=dict(control=control.metrics, locked_preparation=fresh["preparation_metrics"],
                        elapsed_seconds=time.monotonic() - started))
                _need(sum(p.stat().st_size for p in slot.iterdir() if p.is_file())
                      + len(workspace._canonical(terminal)) <= JOURNAL_OUTPUT_BYTES, "JOURNAL_OUTPUT_LIMIT")
                workspace._write(slot / "terminal.json", terminal)
                _, terminal_ref = reader.read(slot / "terminal.json")
            return dict(status="RECORDED", operation_id=proposal["operation_id"], receipt=receipt,
                validation=checked, authorization=authorization, terminal_ref=terminal_ref,
                new_action_calls=1, parent_metrics=terminal["parent_metrics"])
        except BaseException as error:
            if claim_ref is not None and not (slot / "terminal.json").exists():
                try:
                    failure = getattr(error, "refresh_failure", None)
                    if failure is not None:
                        _need(isinstance(failure, dict) and set(failure) <=
                              {"parent_verification_metrics", "worker_final", "job"}
                              and len(workspace._canonical(failure)) <= workspace.MAX_RECEIPT,
                              "BOUNDED_FAILURE_ACCOUNTING_REQUIRED")
                        if failure.get("worker_final") is not None:
                            value, ref = reader.read(failure["worker_final"])
                            job = parent._path(failure["job"], directory=True)
                            _need(job.parent == Path(proposal["identity"]["state_root"]) / "jobs"
                                  and ref["path"] == str(job / "final-control.json")
                                  and value.get("job") == str(job), "FAILURE_WORKER_BINDING")
                    workspace._write(slot / "terminal.json", dict(schema_version=TERMINAL_VERSION,
                        status="FAILED_OR_UNKNOWN", operation_id=proposal["operation_id"],
                        proposal_id=proposal["proposal_id"], claim_ref=claim_ref,
                        reason="ACTION_OR_VALIDATION_FAILED_NO_RETRY", error_type=type(error).__name__,
                        completed_at=_now(proposal).isoformat(), refresh_failure=failure,
                        parent_metrics=control.metrics, original_records_rewritten=False))
                except BaseException:
                    pass
            raise
