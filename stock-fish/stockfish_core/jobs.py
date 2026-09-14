"""Durable, bounded, cooperative background jobs with explicit restart semantics.

One JobService owns a database at a time (OS advisory lock). Worker callables are
not serialized: interrupted work requires an explicit retry using the registered
handler. Workers must honor check_cancelled and have bounded external I/O.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import re
from pathlib import Path
import threading
from typing import Any, Callable
from uuid import uuid4

from .common import UnavailableError, canonical_json, utc_now
from .storage import SQLiteStore, StorageError, _validate_page


TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"})


class JobCancelled(RuntimeError):
    pass


class JobConflict(ValueError):
    pass


class _ServiceLock:
    """Crash-released OS lock; no PID guessing and no process termination."""
    def __init__(self, path: Path):
        self.file = None
        try:
            self.file = path.open("a+b")
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            import os
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError) as exc:
            if self.file:
                self.file.close()
            raise StorageError("another job worker service owns this database") from exc

    def close(self) -> None:
        if not self.file.closed:
            self.file.close()


class JobContext:
    def __init__(self, service: "JobService", job_id: str):
        self.service = service
        self.job_id = job_id

    @property
    def payload(self) -> dict:
        return self.service.get(self.job_id)["payload"]

    @property
    def cancelled(self) -> bool:
        job = self.service.get(self.job_id)
        return bool(job["cancel_requested"] or job["state"] in {"CANCELLED", "INTERRUPTED"})

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled("job cancellation requested")

    def complete(self, result: Any, write_fn: Callable) -> Any:
        """Commit domain artifacts and successful job outcome in one transaction.

        ``write_fn(connection)`` must use the supplied connection, perform no
        network I/O, and return promptly. Cancellation and publication serialize
        on the same DB write lock, so exactly one can win.
        """
        return self.service.complete_atomically(self.job_id, result, write_fn)

    def fail(self, result: Any, error: dict, write_fn: Callable) -> Any:
        """Persist failure diagnostics and FAILED job outcome in one transaction."""
        return self.service.fail_atomically(self.job_id, result, error, write_fn)

    def update(self, progress: float | None = None, message: str | None = None, **data: Any) -> dict:
        self.check_cancelled()
        return self.service.update(self.job_id, progress=progress, message=message, **data)


class JobService:
    def __init__(self, store: SQLiteStore | None = None, *, max_workers: int = 2,
                 max_pending: int = 100, recover: bool = True):
        if max_workers < 1 or max_pending < max_workers:
            raise ValueError("invalid worker/pending capacity")
        self.store = store or SQLiteStore()
        self.owner_id = f"worker_{uuid4().hex}"
        self._lock = threading.RLock()
        self._handlers: dict[str, Callable[[JobContext], Any]] = {}
        self._terminal_observers: dict[str, Callable[[dict], Any]] = {}
        self._futures: dict[str, Future] = {}
        self._closed = False
        self._storage_failure = False
        self.max_pending = max_pending
        self._service_lock = _ServiceLock(self.store.path.with_suffix(self.store.path.suffix + ".jobs.lock"))
        try:
            if recover:
                self.recover_interrupted()
            self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="stockfish-job")
        except BaseException:
            self._service_lock.close()
            raise

    @staticmethod
    def _decode(row) -> dict:
        result = dict(row)
        for key in ("payload", "data", "result", "error"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    @staticmethod
    def _event(connection, job_id: str, state: str, progress: float, detail: dict) -> None:
        connection.execute("INSERT INTO job_events(job_id,state,progress,recorded_at,detail) VALUES(?,?,?,?,?)",
                           (job_id, state, progress, utc_now().isoformat(), canonical_json(detail)))

    def register(self, kind: str, worker: Callable[[JobContext], Any], *, on_terminal=None) -> None:
        if not isinstance(kind, str) or not kind or not callable(worker):
            raise ValueError("kind and callable worker are required")
        with self._lock:
            self._handlers[kind] = worker
            if on_terminal is not None:
                if not callable(on_terminal):
                    raise ValueError("terminal observer must be callable")
                self._terminal_observers[kind] = on_terminal

    def _notify_terminal(self, job_id):
        # Observability runs after the domain transaction. It must never turn a
        # committed result into a failed job or mask a storage failure.
        try:
            job = self.get(job_id)
            observer = self._terminal_observers.get(job['kind']) if job else None
            if observer is not None and job['state'] in TERMINAL_STATES:
                observer(job)
        except Exception:
            pass

    def create(self, kind: str, payload: dict, *, job_id: str | None = None,
               retry_of: str | None = None) -> dict:
        if not isinstance(kind, str) or not kind or not isinstance(payload, dict):
            raise ValueError("job kind and object payload are required")
        raw_payload = canonical_json(payload)
        job_id = job_id or f"job_{uuid4().hex}"
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("job_id must be a non-empty string")
        with self._lock:
            if self._closed or self._storage_failure:
                raise StorageError("job service is not accepting work")
            with self.store.transaction() as connection:
                existing = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if existing:
                    if existing["kind"] != kind or existing["payload"] != raw_payload:
                        raise JobConflict("job id already has different input")
                    return self._decode(existing)
                queued = connection.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('QUEUED','RUNNING','CANCEL_REQUESTED')").fetchone()[0]
                if queued >= self.max_pending:
                    raise JobConflict("job capacity exceeded")
                attempt = 1
                if retry_of:
                    previous = connection.execute("SELECT * FROM jobs WHERE job_id=?", (retry_of,)).fetchone()
                    if not previous or previous["state"] not in {"FAILED", "INTERRUPTED", "CANCELLED"}:
                        raise JobConflict("only failed, interrupted or cancelled jobs may be retried")
                    if previous["kind"] != kind or previous["payload"] != raw_payload:
                        raise JobConflict("retry input differs from original job")
                    attempt = previous["attempt"] + 1
                now = utc_now().isoformat()
                connection.execute("""INSERT INTO jobs(job_id,kind,state,payload,data,created_at,updated_at,attempt,retry_of)
                                      VALUES(?,?,'QUEUED',?,'{}',?,?,?,?)""",
                                   (job_id, kind, raw_payload, now, now, attempt, retry_of))
                self._event(connection, job_id, "QUEUED", 0, {"retry_of": retry_of})
        return self.get(job_id)

    def submit(self, kind: str, payload: dict, worker: Callable[[JobContext], Any] | None = None,
               *, job_id: str | None = None) -> dict:
        if worker is not None:
            self.register(kind, worker)
        with self._lock:
            handler = worker or self._handlers.get(kind)
            if handler is None:
                raise ValueError("no handler registered for this job kind")
            job = self.create(kind, payload, job_id=job_id)
            if job["state"] == "QUEUED":
                self.start(job["job_id"], handler)
        return self.get(job["job_id"])

    def start(self, job_id: str, worker: Callable[[JobContext], Any] | None = None) -> dict:
        with self._lock:
            if self._closed or self._storage_failure:
                raise StorageError("job service is not accepting work")
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job_id in self._futures:
                return job
            if job["state"] != "QUEUED":
                raise JobConflict("job is not queued")
            handler = worker or self._handlers.get(job["kind"])
            if handler is None:
                raise ValueError("no handler registered for this job kind")
            future = self._executor.submit(self._run, job_id, handler)
            self._futures[job_id] = future
            future.add_done_callback(lambda _future: self._forget(job_id))
            return job

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _run(self, job_id: str, handler: Callable[[JobContext], Any]) -> None:
        try:
            with self.store.transaction() as connection:
                row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if row["state"] != "QUEUED":
                    return
                now = utc_now().isoformat()
                connection.execute("UPDATE jobs SET state='RUNNING',started_at=?,updated_at=?,owner_id=?,revision=revision+1 WHERE job_id=?",
                                   (now, now, self.owner_id, job_id))
                self._event(connection, job_id, "RUNNING", row["progress"], {})
            context = JobContext(self, job_id)
            context.check_cancelled()
            result = handler(context)
            if self.get(job_id)["state"] in TERMINAL_STATES:
                return  # ctx.complete/ctx.fail already committed the outcome.
            # A returned failure object cannot accidentally become SUCCEEDED.
            if isinstance(result, dict) and (result.get("success") is False or
                    str(result.get("status", "")).lower() in {"failed", "error", "unavailable"}):
                raise RuntimeError("worker returned a failure result")
            canonical_json(result)  # reject NaN / invalid serialization before success
            self._finish(job_id, "SUCCEEDED", result=result)
        except JobCancelled:
            self._finish_safely(job_id, "CANCELLED", error={"code": "CANCELLED", "message": "cancellation requested"})
        except BaseException as exc:
            # Do not persist arbitrary exception strings: SDK errors may contain keys.
            error = {"code": "WORKER_FAILED", "type": type(exc).__name__, "message": "worker failed; no successful result published"}
            if isinstance(exc, UnavailableError):
                error["code"] = "DEPENDENCY_UNAVAILABLE"
                code = exc.reason.split(":", 1)[0] if isinstance(exc.reason, str) else ""
                if re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", code):
                    error["reason_codes"] = [code]
                allowed = {"core", "data", "market_data", "quant", "model_registry", "research", "llm",
                           "risk", "recommendation", "tracker", "jobs", "storage"}
                error["component"] = exc.component if isinstance(exc.component, str) and exc.component in allowed else "core"
            self._finish_safely(job_id, "FAILED", error=error)
        finally:
            self._notify_terminal(job_id)

    def _finish_safely(self, job_id: str, state: str, **kwargs) -> None:
        try:
            self._finish(job_id, state, **kwargs)
        except StorageError:
            # Durable failure cannot be written while disk is unavailable. Never
            # advertise success; health blocks new work and restart recovers it.
            self._storage_failure = True

    def _finish(self, job_id: str, state: str, *, result: Any = None, error: dict | None = None) -> None:
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["state"] in TERMINAL_STATES:
                return
            if row["cancel_requested"]:
                state, result, error = "CANCELLED", None, {"code": "CANCELLED", "message": "cancellation requested"}
            progress = 1.0 if state == "SUCCEEDED" else row["progress"]
            now = utc_now().isoformat()
            connection.execute("""UPDATE jobs SET state=?,progress=?,result=?,error=?,finished_at=?,updated_at=?,revision=revision+1 WHERE job_id=?""",
                               (state, progress, canonical_json(result) if result is not None else None,
                                canonical_json(error) if error is not None else None, now, now, job_id))
            self._event(connection, job_id, state, progress, {"error": error})

    def complete_atomically(self, job_id: str, result: Any, write_fn: Callable) -> Any:
        if isinstance(result, dict) and (result.get("success") is False or
                str(result.get("status", "")).lower() in {"failed", "error", "unavailable"}):
            raise ValueError("cannot atomically publish a failure as success")
        return self._terminal_atomically(job_id, "SUCCEEDED", result, None, write_fn)

    def fail_atomically(self, job_id: str, result: Any, error: dict, write_fn: Callable) -> Any:
        if not isinstance(error, dict) or not isinstance(error.get("code"), str) or not error["code"]:
            raise ValueError("failure requires a safe machine-readable error code")
        if error.keys() - {"code", "message", "component", "reason_codes", "type"}:
            raise ValueError("unsupported error fields")
        return self._terminal_atomically(job_id, "FAILED", result, error, write_fn)

    def _terminal_atomically(self, job_id: str, state: str, result: Any, error: dict | None, write_fn: Callable) -> Any:
        if not callable(write_fn):
            raise ValueError("transaction writer must be callable")
        serialized = canonical_json(result)
        serialized_error = canonical_json(error) if error is not None else None
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] == state:
                if row["result"] != serialized or row["error"] != serialized_error:
                    raise JobConflict("job already completed with a different result")
                return result
            if row["cancel_requested"]:
                raise JobCancelled("job cancellation requested before publication")
            if row["state"] != "RUNNING" or row["owner_id"] != self.owner_id:
                raise JobConflict("only the active owning worker can publish")
            write_fn(connection)
            now = utc_now().isoformat()
            progress = 1.0 if state == "SUCCEEDED" else row["progress"]
            connection.execute("""UPDATE jobs SET state=?,progress=?,result=?,error=?,
                                  finished_at=?,updated_at=?,revision=revision+1 WHERE job_id=?""",
                               (state, progress, serialized, serialized_error, now, now, job_id))
            self._event(connection, job_id, state, progress, {"atomic_artifacts": True, "error": error})
        return result

    def get(self, job_id: str) -> dict | None:
        with self.store.transaction(write=False) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return self._decode(row) if row else None

    def list(self, *, kind: str | None = None, state: str | None = None,
             limit: int = 100, offset: int = 0) -> list[dict]:
        _validate_page(limit, offset)
        filters, params = [], []
        if kind is not None:
            filters.append("kind=?")
            params.append(kind)
        if state is not None:
            filters.append("state=?")
            params.append(state)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.store.transaction(write=False) as connection:
            rows = connection.execute("SELECT * FROM jobs" + where + " ORDER BY created_at DESC,job_id LIMIT ? OFFSET ?",
                                      (*params, limit, offset)).fetchall()
            return [self._decode(row) for row in rows]

    def update(self, job_id: str, *, progress: float | None = None,
               message: str | None = None, **data: Any) -> dict:
        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, (int, float)) or not 0 <= progress <= 1):
            raise ValueError("progress must be a finite number between zero and one")
        if message is not None and not isinstance(message, str):
            raise ValueError("message must be text")
        canonical_json(data)
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] in TERMINAL_STATES:
                raise JobConflict("terminal job state cannot be mutated")
            if row["cancel_requested"]:
                raise JobCancelled("job cancellation requested")
            if progress is not None and progress < row["progress"]:
                raise JobConflict("job progress cannot move backwards")
            merged = json.loads(row["data"])
            merged.update(data)
            next_progress = row["progress"] if progress is None else progress
            next_message = row["message"] if message is None else message
            connection.execute("UPDATE jobs SET progress=?,message=?,data=?,updated_at=?,revision=revision+1 WHERE job_id=?",
                               (next_progress, next_message, canonical_json(merged), utc_now().isoformat(), job_id))
            self._event(connection, job_id, row["state"], next_progress, {"message": next_message, "data_keys": sorted(data)})
        return self.get(job_id)

    def cancel(self, job_id: str) -> dict:
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] in TERMINAL_STATES:
                return self._decode(row)
            state = "CANCELLED" if row["state"] == "QUEUED" else "CANCEL_REQUESTED"
            now = utc_now().isoformat()
            connection.execute("UPDATE jobs SET state=?,cancel_requested=1,updated_at=?,finished_at=?,revision=revision+1 WHERE job_id=?",
                               (state, now, now if state == "CANCELLED" else None, job_id))
            self._event(connection, job_id, state, row["progress"], {})
        self._notify_terminal(job_id)
        return self.get(job_id)

    def retry(self, job_id: str, worker: Callable[[JobContext], Any] | None = None) -> dict:
        previous = self.get(job_id)
        if previous is None:
            raise KeyError(job_id)
        handler = worker or self._handlers.get(previous["kind"])
        if handler is None:
            raise ValueError("retry requires a registered handler")
        with self._lock:
            job = self.create(previous["kind"], previous["payload"], retry_of=job_id)
            self.start(job["job_id"], handler)
        return self.get(job["job_id"])

    def events(self, job_id: str) -> list[dict]:
        with self.store.transaction(write=False) as connection:
            rows = connection.execute("SELECT * FROM job_events WHERE job_id=? ORDER BY event_id", (job_id,)).fetchall()
            return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def recover_interrupted(self) -> int:
        if self._futures:
            raise JobConflict("cannot recover while this service has active workers")
        with self.store.transaction() as connection:
            rows = connection.execute("SELECT * FROM jobs WHERE state IN ('QUEUED','RUNNING','CANCEL_REQUESTED')").fetchall()
            for row in rows:
                state = "CANCELLED" if row["cancel_requested"] else "INTERRUPTED"
                now = utc_now().isoformat()
                error = {"code": "PROCESS_RESTART", "message": "previous execution ended before durable completion; explicit retry required"}
                connection.execute("UPDATE jobs SET state=?,error=?,finished_at=?,updated_at=?,revision=revision+1 WHERE job_id=?",
                                   (state, canonical_json(error), now, now, row["job_id"]))
                self._event(connection, row["job_id"], state, row["progress"], {"error": error})
            return len(rows)

    def health(self) -> dict:
        storage = self.store.health()
        return {"component": "jobs", "status": "healthy" if storage["status"] == "healthy" and not self._storage_failure and not self._closed else "unavailable",
                "active_workers": len(self._futures), "accepting_work": not self._closed and not self._storage_failure,
                "persistent": True, "recovery": "INTERRUPTED_REQUIRES_EXPLICIT_RETRY"}

    def shutdown(self, wait: bool = True, *, cancel_pending: bool = True) -> None:
        if not wait:
            raise ValueError("reliable shutdown must join workers; use bounded cancellable worker I/O")
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if cancel_pending:
            try:
                for state in ("QUEUED", "RUNNING", "CANCEL_REQUESTED"):
                    for job in self.list(state=state, limit=10000):
                        self.cancel(job["job_id"])
            except StorageError:
                self._storage_failure = True
        try:
            self._executor.shutdown(wait=True, cancel_futures=False)
        finally:
            self._service_lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.shutdown()
