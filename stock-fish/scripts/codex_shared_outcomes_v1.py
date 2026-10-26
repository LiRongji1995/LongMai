"""Explicit synchronous outcome execution with one genuine verification session.

Original plan, evaluator, DB-parent checks, cancellation, JobService state
machine and all three terminal checks remain in use. Only this invocation's
cloned plan/worker globals receive a private session factory. No old module,
request, strategy, service deployment or source capability is rewritten.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timezone
import hashlib
from pathlib import Path
import sys
import time
from threading import get_ident
from types import FunctionType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from stockfish_core.common import canonical_json
from stockfish_core.jobs import JobCancelled, JobService
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as original
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.visibility import stamp

VERSION = "SHARED_OUTCOME_EXECUTION_V1"
REQUESTS = "shared_outcome_requests_v1"
LEDGERS = "shared_outcome_ledgers_v1"
DEFAULT_LIMITS = {"max_seconds": 300, "max_rss_bytes": 2 * 1024 ** 3,
                  "max_total_read_bytes": 8 * 1024 ** 3, "max_unique_bytes": 4 * 1024 ** 3,
                  "max_files": 4096}


class SharedExecutionError(ValueError):
    pass


def _need(condition, code):
    if not condition:
        raise SharedExecutionError("SHARED_OUTCOME_" + code)


def source_identity():
    return {"version": VERSION, "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _select_plan(closure, mode):
    from stockfish_core.yaogu.dated_identity_outcome_closure_v1 import DatedIdentityLateClosure
    allowed = {
        "daily": {original.ShortHorizonDailyClosure, original.RestoredResultDailyClosure},
        "replay": {original.ClosedSessionClosure},
        "late": {original.LateCompletedSessionClosure, original.LocalCopyLateCompletedSessionClosure,
                 DatedIdentityLateClosure},
    }
    _need(mode in allowed and type(closure) in allowed[mode], "EXPLICIT_SUPPORTED_CLOSURE_AND_MODE_REQUIRED")
    # These are the original wrapper preconditions before their original plan
    # body. Only the body creates a session, so only that function is cloned.
    if isinstance(closure, original.RestoredResultDailyClosure):
        closure._require_actual_clock()
    if isinstance(closure, original.LocalCopyLateCompletedSessionClosure):
        closure._require_source_identity()
    return {"daily": original.ShortHorizonDailyClosure.plan,
            "replay": original.ClosedSessionClosure.plan_replay,
            "late": original.LateCompletedSessionClosure.plan_late}[mode]


def _clone(function, proxy):
    namespace = dict(function.__globals__)
    namespace["_bridge_module"] = lambda: proxy
    value = FunctionType(function.__code__, namespace, function.__name__,
                         function.__defaults__, function.__closure__)
    value.__kwdefaults__ = deepcopy(function.__kwdefaults__)
    return value


def _require_submission_admission(closure, plan, mode):
    """Preserve original submit wrappers immediately before durable admission."""
    if isinstance(closure, original.LocalCopyLateCompletedSessionClosure):
        closure._require_source_identity()
    if mode == "late":
        request = plan["request"]
        reasons = closure._late_reasons(closure._business_session(request["business_session"]),
            stamp(request["evaluated_at"]), stamp(closure.clock()), admission=True)
        if reasons:
            raise ValueError("SHORT_HORIZON_CLOSURE_" + reasons[0])


class _BridgeProxy:
    def __init__(self, scope):
        self.ArchiveVerificationSession = scope.factory

    def __getattr__(self, name):
        return getattr(bridge, name)


class _Scope:
    """Private, single-use, thread/store/request bound scope; never serialized."""
    def __init__(self, closure, run_ids, input_id, mode):
        self.closure, self.service = closure, closure.service
        self.store, self.jobs = self.service.store, self.service.jobs
        self.thread = get_ident()
        self.store_identity = bridge._view_store_identity(self.service)
        self.run_ids, self.input_id, self.mode = tuple(run_ids), input_id, mode
        self.active, self.phase, self.consumed = True, "PLAN", False
        self.session, self.context, self.request_json = None, None, None
        self.adapter = None
        self.factory_calls, self.session_count = 0, 0
        self.plan_started = None
        self.last_audit = None
        self.original_policy = deepcopy(closure._policy_identity())
        self.adapter_identity = source_identity()
        self.options = deepcopy(closure._session_options())
        for key, maximum in DEFAULT_LIMITS.items():
            value = self.options.get(key, maximum)
            _need(type(value) in ((int, float) if key == "max_seconds" else (int,))
                  and 0 < value <= maximum, "ORIGINAL_DEFAULT_BUDGET_MUST_NOT_EXPAND")
            self.options[key] = value
        self.options["encoding_profile"] = self.original_policy.get(
            "worker_encoding_profile", bridge.DEFAULT_ENCODING_PROFILE)

    def guard(self, *, physical=False, request=False):
        _need(self.active and get_ident() == self.thread, "SCOPE_THREAD_OR_LIFETIME_CHANGED")
        _need(self.closure.service is self.service and self.service.store is self.store
              and self.service.jobs is self.jobs and self.jobs.store is self.store,
              "SCOPE_SERVICE_OR_STORE_CHANGED")
        _need(not self.jobs._closed and not self.jobs._storage_failure
              and self.jobs.owner_id and not self.jobs._service_lock.file.closed,
              "ACTIVE_ORIGINAL_JOB_LOCK_REQUIRED")
        if physical:
            _need(bridge._view_store_identity(self.service) == self.store_identity,
                  "SCOPE_PHYSICAL_DATABASE_CHANGED")
        if request:
            _need(self.context is not None and canonical_json(self.context.payload) == self.request_json,
                  "SCOPE_COMPLETE_REQUEST_CHANGED")

    def checkpoint(self):
        self.guard()
        if self.context is not None:
            self.context.check_cancelled()

    def policy(self):
        self.guard(physical=True)
        current = self.closure._policy_identity()
        _need(current == self.original_policy and source_identity() == self.adapter_identity,
              "SOURCE_OR_POLICY_CHANGED")
        return {**deepcopy(current), "version": VERSION,
                "shared_execution": {"mode": self.mode, "adapter_source": deepcopy(self.adapter_identity),
                    "base_policy_hash": digest(current), "scope": "SAME_THREAD_SERVICE_STORE_REQUEST_ONCE",
                    "budget_scope": "PLAN_THROUGH_TRANSACTION_TERMINAL",
                    "typed_input_retention": "FRESH_NORMALIZER_PRIVATE_PROOF",
                    "default_limits_not_expanded": True}}

    def factory(self, **options):
        self.guard(physical=True, request=self.phase == "WORKER")
        _need(self.phase in {"PLAN", "WORKER"}, "SESSION_FACTORY_PHASE")
        expected_call = 0 if self.phase == "PLAN" else 1
        _need(self.factory_calls == expected_call, "SESSION_FACTORY_SINGLE_PLAN_AND_WORKER_ONLY")
        supplied = {key: value for key, value in options.items() if key != "checkpoint"}
        supplied.setdefault("encoding_profile", self.options["encoding_profile"])
        for key, default in DEFAULT_LIMITS.items():
            supplied.setdefault(key, default)
        _need(supplied == self.options, "SESSION_ARGUMENTS_CHANGED")
        if self.phase == "WORKER":
            _need(options.get("checkpoint") == self.context.check_cancelled,
                  "WORKER_CANCELLATION_CALLBACK_REQUIRED")
        else:
            _need("checkpoint" not in options, "UNEXPECTED_PLAN_CALLBACK")
        self.factory_calls += 1
        if self.session is None:
            self.session = bridge.ArchiveVerificationSession(checkpoint=self.checkpoint, **self.options)
            if self.plan_started is not None:
                # Include the original plan's admission/calendar work, too.
                # Moving this clock earlier can only reduce available time.
                self.session.started = min(self.session.started, self.plan_started)
            self.session_count += 1
            # The new adapter joins the genuine SHA + seven-field physical
            # file closure, so every original terminal rereads it as well.
            self.session.read(Path(__file__), expected=self.adapter_identity["sha256"],
                              limit=1024 ** 2, retain=False)
        _need(type(self.session) is bridge.ArchiveVerificationSession, "GENUINE_SESSION_REQUIRED")
        self.session.check()
        return self.session

    def authenticate(self, adapter, run_ids, input_id, session, checkpoint=lambda: None, **kwargs):
        self.guard(physical=True, request=self.phase == "WORKER")
        _need(session is self.session and tuple(run_ids) == self.run_ids and input_id == self.input_id,
              "AUTHENTICATION_TASK_CHANGED")
        _need(adapter is self.adapter and adapter.service is self.service, "ADAPTER_SERVICE_CHANGED")
        kwargs["retain_input"] = True
        # In particular, the dated-input override is not replaced by a cached
        # descriptor. It retains its original reconstruction and lineage checks.
        return type(self.closure)._authenticated(adapter, run_ids, input_id, session,
                                                checkpoint=checkpoint, **kwargs)

    def enter_worker(self, context):
        self.guard(physical=True)
        _need(self.phase == "PLANNED" and not self.consumed and context.service is self.jobs,
              "SINGLE_SYNCHRONOUS_WORKER_REQUIRED")
        _need(canonical_json(context.payload) == self.request_json
              and context.job_id == context.payload["root_job_id"], "SCOPE_COMPLETE_REQUEST_CHANGED")
        self.consumed, self.context, self.phase = True, context, "WORKER"
        self.guard(request=True)

    def terminal(self, adapter, session):
        self.guard(physical=True, request=self.phase == "WORKER")
        _need(session is self.session and adapter is self.adapter and adapter.service is self.service,
              "TERMINAL_SESSION_OR_SERVICE_CHANGED")
        self.policy()
        result = type(self.closure)._terminal(adapter, session)
        self.policy()
        self.guard(physical=True, request=self.phase == "WORKER")
        _need(adapter is self.adapter and adapter.service is self.service, "TERMINAL_SESSION_OR_SERVICE_CHANGED")
        self.last_audit = deepcopy(result)
        return {**result, "shared_execution_version": VERSION,
                "session_objects_created": self.session_count,
                "budget_includes_plan": True}

    def audit(self):
        try:
            value = self.session.audit() if self.session is not None else None
        except (ValueError, OSError):
            value = None
        return {"schema_version": VERSION, "session_objects_created": self.session_count,
                "session_factory_calls": self.factory_calls, "single_thread": True,
                "budget_includes_plan": True, "actual_session_audit": value,
                "last_completed_terminal_audit": deepcopy(self.last_audit),
                "source_capability_serialized": False}

    def close(self):
        self.active, self.phase = False, "CLOSED"
        self.context = self.session = None


def _adapter(closure, scope, mode):
    def policy(self):
        return scope.policy()

    def authenticated(self, *args, **kwargs):
        return scope.authenticate(self, *args, **kwargs)

    def terminal(self, session):
        return scope.terminal(self, session)

    cls = type("_SharedInvocationClosure", (type(closure),), {
        "version": VERSION, "job_kind": "shared_outcome_" + mode + "_v1",
        "ledgers": LEDGERS, "job_prefix": "shared_outcome_" + mode + "_",
        "_policy_identity": policy, "_authenticated": authenticated, "_terminal": terminal})
    adapter = object.__new__(cls)
    adapter.__dict__.update(closure.__dict__)
    _need(scope.adapter is None, "SINGLE_ADAPTER_REQUIRED")
    scope.adapter = adapter
    return adapter


def _saved(service, adapter, record, selectors):
    _need(record.get("schema_version") == VERSION and record.get("selectors") == selectors,
          "SAVED_REQUEST_IDENTITY_CHANGED")
    job = service.jobs.get(record["job_id"])
    _need(job is not None, "UNKNOWN_PREVIOUS_SUBMISSION_NO_RETRY")
    _need(job["kind"] == adapter.job_kind and job["payload"] == record["plan"]["request"],
          "SAVED_JOB_REQUEST_CHANGED")
    _need(job["state"] == "SUCCEEDED", "EXISTING_JOB_NOT_SUCCEEDED_NO_AUTOMATIC_RETRY")
    ledger = service.store.get_document(LEDGERS, job["job_id"])
    _need(type(ledger) is dict and ledger.get("job_id") == job["job_id"]
          and all(ledger.get(key) == job["payload"][key] for key in
                  ("input_id", "run_ids", "policy", "bindings", "evaluated_at")),
          "SAVED_LEDGER_IDENTITY_CHANGED")
    return {"schema_version": VERSION, "status": "REUSED_SAVED_LEDGER", "plan": record["plan"],
            "job": job, "ledger": ledger, "session_audit": None,
            "source_reauthenticated_now": False, "new_worker_calls": 0,
            "verification_scope": "CURRENT_SAVED_BYTES_ONLY_NOT_FRESH_ARCHIVE_AUTHENTICATION"}


def execute_shared(closure, run_ids, input_id, *, mode="daily", business_session=None, evaluated_at=None):
    """Execute one exact request synchronously under the existing JobService lock.

    Only a successful saved request may be reused. Unknown/queued/failed work
    is preserved for explicit reconciliation; no recovery or retry occurs here.
    For deterministic cross-call reuse, supply the same explicit evaluated_at.
    """
    plan_function = _select_plan(closure, mode)
    run_ids = closure._run_ids(run_ids)
    _need(type(input_id) is str and input_id, "EXPLICIT_INPUT_REQUIRED")
    jobs = closure._write_jobs()
    _need(isinstance(jobs, JobService) and jobs.store is closure.service.store,
          "MATCHING_ORIGINAL_JOB_SERVICE_REQUIRED")
    evaluated = stamp(evaluated_at if evaluated_at is not None else closure.clock()).astimezone(timezone.utc)
    if mode == "daily":
        _need(business_session is None, "DAILY_BUSINESS_SESSION_IS_DERIVED")
        planning = {"evaluated_at": evaluated}
    else:
        _need(evaluated_at is not None and business_session is not None, "EXPLICIT_BUSINESS_SESSION_AND_CUTOFF_REQUIRED")
        business = closure._business_session(business_session)
        planning = {"business_session": business, "evaluated_at": evaluated}
    scope = _Scope(closure, run_ids, input_id, mode)
    adapter = _adapter(closure, scope, mode)
    proxy = _BridgeProxy(scope)
    plan_call = _clone(plan_function, proxy)
    worker_call = _clone(original.ShortHorizonDailyClosure._worker, proxy)
    selectors = {"mode": mode, "run_ids": run_ids, "input_id": input_id,
                 "evaluated_at": evaluated.isoformat(),
                 "business_session": str(planning.get("business_session")) if mode != "daily" else None,
                 "policy": scope.policy()}
    operation_id = digest(selectors)
    try:
        # The existing OS lock remains owned by JobService. Its RLock prevents
        # another submit/start while this synchronous worker is active; cancel
        # remains available because JobService.cancel does not take this lock.
        with jobs._lock:
            scope.guard(physical=True)
            previous = closure.service.store.get_document(REQUESTS, operation_id)
            if previous is not None:
                return _saved(closure.service, adapter, previous, selectors)
            with jobs.store.transaction(write=False) as connection:
                active = connection.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('QUEUED','RUNNING','CANCEL_REQUESTED')").fetchone()[0]
            _need(not jobs._futures and active == 0, "EXISTING_ACTIVE_JOBS_NO_AUTOMATIC_RECOVERY")
            scope.plan_started = time.monotonic()
            plan = plan_call(adapter, run_ids, input_id, **planning)
            if not plan["can_submit"]:
                return {"schema_version": VERSION, "status": "BLOCKED", "plan": plan, "job": None,
                        "ledger": None, "session_audit": scope.audit(), "new_worker_calls": 0,
                        "source_reauthenticated_now": bool(scope.session_count)}
            _need(plan.get("request") is not None and scope.factory_calls == 1,
                  "COMPLETE_AUTHENTICATED_PLAN_REQUIRED")
            scope.request_json = canonical_json(plan["request"])
            scope.phase = "PLANNED"
            _need(jobs.get(plan["job_id"]) is None, "EXISTING_JOB_REQUIRES_EXACT_SAVED_REQUEST_NO_RETRY")
            _require_submission_admission(closure, plan, mode)
            # A crash after this record but before create is UNKNOWN, never an
            # implicit second attempt. Only small plan/request metadata is added.
            closure.service.store.put_document(REQUESTS, operation_id,
                {"schema_version": VERSION, "selectors": selectors, "job_id": plan["job_id"], "plan": plan})
            created = jobs.create(adapter.job_kind, plan["request"], job_id=plan["job_id"])
            _need(created["state"] == "QUEUED" and created["attempt"] == 1 and created["retry_of"] is None,
                  "NEW_FIRST_ATTEMPT_REQUIRED")

            def run(context):
                try:
                    scope.enter_worker(context)
                    return worker_call(adapter, context)
                except KeyboardInterrupt as exc:
                    # Synchronous main-thread interruption must retain the
                    # original cooperative CANCELLED state and rollback path.
                    raise JobCancelled("shared synchronous execution interrupted") from exc

            jobs._run(plan["job_id"], run)
            job = jobs.get(plan["job_id"])
            ledger = closure.service.store.get_document(LEDGERS, plan["job_id"])
            if job["state"] == "SUCCEEDED":
                _need(ledger is not None and scope.factory_calls == 2 and scope.session_count == 1,
                      "SUCCESS_REQUIRES_ONE_COMPLETE_SHARED_SESSION")
            return {"schema_version": VERSION, "status": job["state"], "plan": plan,
                    "job": job, "ledger": ledger, "session_audit": scope.audit(),
                    "source_reauthenticated_now": True, "new_worker_calls": 1,
                    "verification_scope": "ONE_INVOCATION_PLAN_AND_WORKER_COMPLETE_ORIGINAL_CHECKS"}
    finally:
        scope.close()


def execute_cli(arguments):
    """Return (exit_code, JSON result), reusing original explicit CLI boundaries.

    Only the six existing run commands are accepted; no import, retry, recovery,
    collection or deployment command is dispatched. Synchronous execution uses
    the shared original 300-second budget, not the old background wait option.
    """
    from stockfish_core.yaogu import r2_closure_cli as cli
    commands = {"r2-daily-run": ("daily", original.ShortHorizonDailyClosure, None),
                "r2-restored-daily-run": ("daily", original.RestoredResultDailyClosure, "restoration"),
                "r2-closed-session-run": ("replay", original.ClosedSessionClosure, None),
                "r2-late-completed-run": ("late", original.LateCompletedSessionClosure, None),
                "r2-local-copy-late-run": ("late", original.LocalCopyLateCompletedSessionClosure, "local_copy"),
                "r2-dated-late-run": ("late", None, "local_copy")}
    try:
        _need(type(arguments) in (list, tuple) and bool(arguments), "EXPLICIT_RUN_COMMAND_REQUIRED")
        if list(arguments) == ["--help"]:
            return 0, {"schema_version": VERSION, "commands": sorted(commands),
                       "execution": "SYNCHRONOUS_SHARED_300_SECONDS", "automatic_retry": False,
                       "daily_explicit_cutoff_option": "--knowledge-cutoff"}
        _need(arguments[0] in commands, "ONLY_EXPLICIT_RUN_COMMANDS_ALLOWED")
        _need(not any(value == "--wait-seconds" or value.startswith("--wait-seconds=") for value in arguments),
              "BACKGROUND_WAIT_OPTION_NOT_APPLICABLE_TO_SYNCHRONOUS_EXECUTION")
        parser = cli.parser()
        # This parser instance belongs only to the new entry point. The old
        # parser/function remains untouched; explicit cutoffs support exact reuse.
        choices = parser._subparsers._group_actions[0].choices
        for command in ("r2-daily-run", "r2-restored-daily-run"):
            choices[command].add_argument("--knowledge-cutoff", help="Exact aware cutoff for shared-request identity")
        args = parser.parse_args(list(arguments))
        cli._fixture_boundary(args)
        mode, closure_type, protocol_kind = commands[args.command]
        if closure_type is None:
            from stockfish_core.yaogu.dated_identity_outcome_closure_v1 import DatedIdentityLateClosure
            closure_type = DatedIdentityLateClosure
        options = {}
        requested = getattr(args, "source_protocol", None)
        restoration = [getattr(args, key, None) for key in ("restoration_manifest", "restoration_manifest_sha256")]
        local_copy = [getattr(args, key, None) for key in ("local_copy_manifest", "local_copy_manifest_sha256")]
        if protocol_kind == "restoration":
            _need(requested == "SEALED_RESULT_BYTES_RESTORED_V1" and all(value is not None for value in restoration)
                  and all(value is None for value in local_copy), "EXPLICIT_PINNED_RESTORATION_REQUIRED")
            from stockfish_core.yaogu.sealed_result_restoration_v1 import manifest_reference
            options["restoration_manifest_ref"] = manifest_reference(*restoration)
        elif protocol_kind == "local_copy":
            _need(requested == "SEALED_RESULT_LOCAL_COPY_V1" and all(value is not None for value in local_copy)
                  and all(value is None for value in restoration), "EXPLICIT_PINNED_LOCAL_COPY_REQUIRED")
            from stockfish_core.yaogu.sealed_result_local_copy_v1 import manifest_reference
            options["local_copy_manifest_ref"] = manifest_reference(*local_copy)
        else:
            _need(requested is None and all(value is None for value in restoration + local_copy),
                  "SOURCE_PROTOCOL_COMMAND_MISMATCH")
        profile = getattr(args, "verification_profile", None)
        if profile is not None:
            selected = cli.session_options(profile)
            _need(all(selected.get(key, maximum) <= maximum for key, maximum in DEFAULT_LIMITS.items()),
                  "ORIGINAL_DEFAULT_BUDGET_MUST_NOT_EXPAND")
            options["verification_profile"] = profile
        with cli.service_context(args, writable=True) as service:
            closure = closure_type(service, max_runs=args.max_runs, **options)
            value = execute_shared(closure, args.run_id, args.input_id, mode=mode,
                business_session=getattr(args, "business_session", None),
                evaluated_at=getattr(args, "knowledge_cutoff", None))
            return (0 if value["status"] in {"SUCCEEDED", "REUSED_SAVED_LEDGER"} else 3), value
    except (ValueError, OSError) as exc:
        return 2, {"schema_version": VERSION, "status": "FAILED", **cli._safe_error(exc)}


if __name__ == "__main__":
    import json
    code, value = execute_cli(sys.argv[1:])
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    raise SystemExit(code)
