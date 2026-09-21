"""Measured API/job/export spans. Aggregates without endpoints are NOT_MEASURED."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import os
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4

from ..common import utc_now

TRACE_VERSION = "DAILY_PRIMARY_E2E_TRACE_R16_R1"
_current = ContextVar("daily_primary_trace", default=None)
_parents = ContextVar("daily_primary_span_stack", default=())


def source_identity():
    root = Path(__file__).resolve().parents[3]
    def git(*args):
        try:
            return subprocess.check_output(["git", "-C", str(root), *args],
                stderr=subprocess.DEVNULL, timeout=5, text=True).strip()
        except (OSError, subprocess.SubprocessError):
            return None
    actual_root = git("rev-parse", "--show-toplevel")
    if actual_root is None or Path(actual_root).resolve() != root:
        return {"commit": None, "tree": None, "tracked_dirty": None, "git_root_matches": False}
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "tree": tree, "tracked_dirty": None if status is None else bool(status)}


def _rss_bytes():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss
    except (ImportError, OSError):
        return None


def current_trace():
    return _current.get()


def traced_stage(name, **metadata):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with measured_stage(name, metadata=metadata):
                return function(*args, **kwargs)
        return call
    return decorate


@contextmanager
def measured_stage(name, **kwargs):
    trace = current_trace()
    if trace is None:
        yield {}
    else:
        with trace.stage(name, **kwargs) as row:
            yield row


class DailyPrimaryTrace:
    def __init__(self, *, run_id=None, input_id=None, snapshot_id=None, as_of_session=None,
                 command=None, git_commit=None, benchmark_version=None, boundary="INTERNAL_ONLY",
                 causal_trace_id=None):
        self.started_at, self.started_perf = utc_now(), time.perf_counter()
        self.lock = threading.RLock()
        self.root_span_id = "span_" + uuid4().hex
        self.export_claimed = False
        self.trace = {
            "trace_version": TRACE_VERSION, "trace_id": "trace_" + uuid4().hex,
            "span_id": self.root_span_id, "parent_span_id": None,
            "run_id": run_id, "input_id": input_id, "snapshot_id": snapshot_id,
            "as_of_session": str(as_of_session) if as_of_session is not None else None,
            "command": command, "source_identity": source_identity(), "git_commit": git_commit,
            "benchmark_version": benchmark_version, "boundary": boundary, "causal_trace_id": causal_trace_id,
            "start": self.started_at.isoformat(), "created_at": self.started_at.isoformat(),
            "start_monotonic_seconds": self.started_perf, "end": None, "elapsed_ms": None,
            "process_id": os.getpid(), "stages": [], "status": "RUNNING",
            "rss_start_bytes": _rss_bytes(), "rss_peak_bytes": _rss_bytes(),
            "rss_scope": "CURRENT_PROCESS_ENDPOINT_SAMPLES; supervisor measures process family peak",
            "duration_policy": "ROOT_MONOTONIC; NESTED_SPANS_MUST_NOT_BE_SUMMED",
            "unobservable": {"agent_scheduling": "NOT_MEASURED", "chat_request_to_reply": "NOT_MEASURED",
                            "user_receive": "NOT_MEASURED"},
        }

    @property
    def trace_id(self):
        return self.trace["trace_id"]

    @contextmanager
    def activate(self):
        stack = _parents.get() if _current.get() is self else ()
        token, parents = _current.set(self), _parents.set(stack)
        try:
            yield self
        finally:
            _parents.reset(parents)
            _current.reset(token)

    def update(self, **values):
        with self.lock:
            if self.trace["end"] is not None:
                raise RuntimeError("finished trace is immutable")
            self.trace.update(values)

    @contextmanager
    def stage(self, name, *, item_count=None, snapshot_id=None, run_id=None, metadata=None):
        started_perf, started_at, rss = time.perf_counter(), utc_now(), _rss_bytes()
        stack = _parents.get() if _current.get() is self else ()
        row = {
            "span_id": "span_" + uuid4().hex, "parent_span_id": stack[-1] if stack else self.root_span_id,
            "name": name, "start": started_at.isoformat(), "end": None,
            "start_monotonic_seconds": started_perf, "end_monotonic_seconds": None,
            "elapsed_ms": None, "status": "RUNNING", "success": None,
            "item_count": item_count, "snapshot_id": snapshot_id, "run_id": run_id,
            "rss_start_bytes": rss, "rss_end_bytes": None, "rss_peak_bytes": rss,
            "bytes_read": None, "bytes_written": None, "cache_stats": None,
            "metadata": dict(metadata or {}), "thread_id": threading.get_ident(),
        }
        with self.lock:
            if self.trace["end"] is not None:
                raise RuntimeError("cannot append span to finished trace")
            self.trace["stages"].append(row)
        parent_token = _parents.set((*stack, row["span_id"]))
        outcome = {"status": "SUCCESS", "success": True}
        try:
            yield row
        except BaseException as exc:
            outcome = {"status": "FAILED", "success": False, "error_type": type(exc).__name__}
            raise
        finally:
            _parents.reset(parent_token)
            ended_perf, ended_at, rss_end = time.perf_counter(), utc_now(), _rss_bytes()
            with self.lock:
                row.update(end=ended_at.isoformat(), end_monotonic_seconds=ended_perf,
                           elapsed_ms=(ended_perf - started_perf) * 1000, rss_end_bytes=rss_end, **outcome)
                peaks = [v for v in (rss, rss_end) if v is not None]
                row["rss_peak_bytes"] = max(peaks) if peaks else None
                peaks += [v for v in (self.trace["rss_peak_bytes"],) if v is not None]
                self.trace["rss_peak_bytes"] = max(peaks) if peaks else None

    def mark(self, name, status, reason, **metadata):
        if status not in {"SKIPPED", "NOT_MEASURED"}:
            raise ValueError("unmeasured stages cannot claim success")
        with self.lock:
            if self.trace["end"] is not None:
                raise RuntimeError("finished trace is immutable")
            self.trace["stages"].append({"span_id": "span_" + uuid4().hex,
                "parent_span_id": self.root_span_id, "name": name, "status": status,
                "start": None, "end": None, "elapsed_ms": None, "success": None,
                "metadata": {"reason": reason, **metadata}})

    def record_stage(self, name, *, elapsed_ms=None, success=None, metadata=None, **values):
        self.mark(name, "NOT_MEASURED", "LEGACY_AGGREGATE_WITHOUT_ENDPOINTS",
                  reported_elapsed_ms=elapsed_ms, reported_success=success, **(metadata or {}), **values)

    def annotate_last(self, **values):
        with self.lock:
            if self.trace["end"] is not None:
                raise RuntimeError("finished trace is immutable")
            if self.trace["stages"]:
                self.trace["stages"][-1].update(values)

    def snapshot(self):
        with self.lock:
            return deepcopy(self.trace)

    def fail(self, phase):
        """Record failure without masking its exception or interrupting workers."""
        with self.lock:
            if self.trace['end'] is not None:
                return
            self.trace['terminal_failure'] = phase
            if not any(row['status'] == 'RUNNING' for row in self.trace['stages']):
                self.finish(status='FAILED', metadata={'failure_phase': phase, 'result_ready': False})

    def finish(self, *, status="SUCCESS", run_id=None, result_hash=None, metadata=None):
        with self.lock:
            if self.trace["end"] is not None:
                return self.snapshot()
            if any(row["status"] == "RUNNING" for row in self.trace["stages"]):
                raise RuntimeError("cannot finish trace with active spans")
            present = {row["name"] for row in self.trace["stages"]}
            for name in stage_names():
                if name not in present:
                    self.mark(name, "NOT_MEASURED", "OUTSIDE_OBSERVED_BOUNDARY_OR_NOT_REACHED")
            if status in {"SUCCESS", "SUCCEEDED"}:
                status = "FAILED" if self.trace.get('terminal_failure') or any(row["status"] == "FAILED" for row in self.trace["stages"]) else (
                    "INTERNAL_ONLY" if self.trace["boundary"] == "INTERNAL_ONLY" else (
                    "PARTIAL" if self.trace["boundary"].endswith("_PARTIAL") else "SUCCESS")
                )
            ended_perf, ended = time.perf_counter(), utc_now()
            self.trace.update(status=status, end=ended.isoformat(), finished_at=ended.isoformat(),
                end_monotonic_seconds=ended_perf, elapsed_ms=(ended_perf - self.started_perf) * 1000)
            if run_id:
                self.trace["run_id"] = run_id
            if result_hash:
                self.trace["result_hash"] = result_hash
            if metadata:
                self.trace.setdefault("metadata", {}).update(metadata)
            return self.snapshot()


class TraceRegistry:
    """Bounded continuations; each snapshot has its own immutable document ID."""
    def __init__(self, store, capacity=128):
        self.store, self.capacity = store, capacity
        self.lock = threading.RLock()
        self.traces, self.jobs, self.runs, self.persistence = {}, {}, {}, {}

    def add(self, trace):
        with self.lock:
            if trace.trace_id in self.traces:
                return trace
            if len(self.traces) >= self.capacity:
                removable = next((key for key, item in self.traces.items()
                    if item.trace["end"] is not None or (item.trace["status"] == "READY_FOR_EXPORT"
                        and not item.export_claimed and not any(row['status'] == 'RUNNING' for row in item.snapshot()['stages']))), None)
                if removable is None:
                    raise RuntimeError("trace capacity exceeded")
                self.traces.pop(removable)
                self.persistence.pop(removable, None)
                self.jobs = {key: value for key, value in self.jobs.items() if value != removable}
                self.runs = {key: value for key, value in self.runs.items() if value != removable}
            self.traces[trace.trace_id] = trace
        return trace

    def get(self, trace_id):
        with self.lock:
            return self.traces.get(trace_id)

    def for_job(self, job):
        with self.lock:
            trace = self.get(self.jobs.get(job["job_id"]))
            if trace is not None:
                return trace
            original = self.get(job["payload"].get("trace_id"))
            # Retry inputs stay byte-identical; the new attempt owns a new clock.
            if original is not None and not job.get("retry_of") and original.trace["end"] is None:
                trace = original
            else:
                trace = self.add(DailyPrimaryTrace(input_id=job["payload"].get("input_id"),
                    command="JobService daily primary attempt", boundary="JOB_ATTEMPT_PARTIAL",
                    causal_trace_id=job["payload"].get("trace_id")))
            self.jobs[job["job_id"]] = trace.trace_id
            trace.update(job_id=job["job_id"], retry_of=job.get("retry_of"))
            return trace

    def bind_run(self, trace, run_id):
        with self.lock:
            self.runs[run_id] = trace.trace_id
            trace.update(run_id=run_id)

    def for_export(self, run_id):
        with self.lock:
            trace = self.get(self.runs.get(run_id))
            if (trace is not None and trace.trace["end"] is None and
                trace.trace["status"] == "READY_FOR_EXPORT" and not trace.export_claimed and
                not trace.trace.get('terminal_failure') and
                not any(row["status"] == "RUNNING" for row in trace.snapshot()["stages"])):
                trace.export_claimed = True
                return trace
            return self.add(DailyPrimaryTrace(run_id=run_id, command="GET saved strategy export",
                boundary="EXPORT_ONLY_PARTIAL", causal_trace_id=trace.trace_id if trace else None))

    def persist(self, trace, phase):
        value = trace.snapshot()
        value.update(snapshot_phase=phase, snapshot_recorded_at=utc_now().isoformat())
        document_id = trace.trace_id + "_" + phase + "_" + uuid4().hex
        try:
            self.store.put_document("daily_primary_trace_snapshots", document_id, value)
            result = {"status": "SUCCESS", "document_id": document_id, "phase": phase}
        except Exception as exc:
            # A telemetry failure cannot roll back a completed business job.
            result = {"status": "TRACE_PERSIST_FAILED", "error_type": type(exc).__name__, "phase": phase}
        with self.lock:
            self.persistence.setdefault(trace.trace_id, {})[phase] = result
        return result

    def report(self, trace):
        value = trace.snapshot()
        with self.lock:
            phases = deepcopy(self.persistence.get(trace.trace_id, {}))
        # An earlier phase finishing late cannot overwrite final artifact failure.
        persistence = phases.get("RESULT_READY", {"status": "NOT_MEASURED", "phase": "RESULT_READY"})
        return {"trace": value, "trace_artifact_persistence": persistence,
                "persistence_by_phase": phases,
                "e2e_verified": value["status"] == "SUCCESS" and persistence["status"] == "SUCCESS"}


def stage_names():
    return ("USER_REQUEST_COMMAND_START", "CALENDAR_TRADING_SESSION_RESOLUTION",
        "DATA_FRESHNESS_STATUS_CHECK", "PROVIDER_REFRESH_INCREMENTAL_INGESTION",
        "HISTORY_ARTIFACT_UPDATE", "MARKET_INPUT_CREATION_OR_LOAD", "SOURCE_RESOLUTION",
        "STRATEGY_INPUT_READINESS", "DEPENDENCY_PLANNING", "CACHE_LOOKUP",
        "FEATURE_COMPUTATION", "STRATEGY_COMPUTE", "PERSISTENCE",
        "CANDIDATE_EXPORT", "REPORT_RENDER_API_RESPONSE", "RESULT_READY")
