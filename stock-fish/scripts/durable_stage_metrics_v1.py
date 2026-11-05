"""Durable process-local stage evidence for a newly authorized execution.

This journal does not grant execution or source authority. A supervisor must
bind its identity to the business authority receipt before launching a writer.
Each event is an exclusive, fsynced file. An interrupted last write, absent
terminal, failed sample or open stage is UNKNOWN/FAILED, never success.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_workspace as workspace
from scripts import codex_delivery_audit_v1 as delivery

VERSION = "DURABLE_STAGE_METRICS_V1"
MAX_EVENTS = 8192
MAX_BYTES = 64 * 1024**2
IDENTITY_KEYS = ("workspace_id", "business_id", "attempt_id")


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def identity(receipt):
    need(type(receipt) is dict, "METRICS_AUTHORITY_RECEIPT_REQUIRED")
    result = {key: receipt.get(key) for key in IDENTITY_KEYS}
    need(all(type(v) is str and v and len(v) <= 256 for v in result.values()),
         "METRICS_AUTHORITY_IDENTITY_REQUIRED")
    need(re.fullmatch("business_[0-9a-f]{64}", result["business_id"]) is not None,
         "METRICS_BUSINESS_ID_REQUIRED")
    return result


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def native_sample():
    """Actual current process values. Peak RSS is not instantaneous tree RSS."""
    from scripts.codex_sdk_tree_supervisor_v3 import _identity, psutil
    native_identity = _identity(psutil.Process())
    value = resource.getrusage(resource.RUSAGE_SELF)
    return {"monotonic_seconds": time.monotonic(),
        "utc": datetime.now(timezone.utc).isoformat(),
        "process_cpu_seconds": value.ru_utime + value.ru_stime,
        "process_peak_rss_bytes": int(value.ru_maxrss if sys.platform == "darwin" else value.ru_maxrss * 1024),
        "process_id": os.getpid(), "native_process_identity": native_identity,
        "process_scope": "CURRENT_WRITER_PROCESS_ONLY"}


def _valid_sample(value):
    need(type(value) is dict and all(_number(value.get(k)) for k in
         ("monotonic_seconds", "process_cpu_seconds", "process_peak_rss_bytes"))
         and type(value.get("process_id")) is int and value["process_id"] > 0,
         "METRICS_NATIVE_SAMPLE_INVALID")
    delivery._clock(value.get("utc"))
    native = value.get("native_process_identity")
    need(type(native) is dict and set(native) == {"pid", "create_time", "uid"} and
         native["pid"] == value["process_id"] and _number(native["create_time"]) and
         native["create_time"] > 0 and type(native["uid"]) is int and native["uid"] >= 0,
         "METRICS_NATIVE_IDENTITY_REQUIRED")


def _counts(value):
    if value is None:
        return None
    need(type(value) is dict and all(type(k) is str and k and type(v) is int and v >= 0
         for k, v in value.items()), "METRICS_READ_COUNTERS_INVALID")
    return dict(value)


class StageJournal:
    """One owned writer, one new directory; never resume or overwrite a journal.

    ``stage(name, counters=...)`` accepts a callable returning the genuine
    Budget.metrics mapping. ``metrics_snapshot`` is the heartbeat integration
    point at the original Budget responsibility boundary. Tests may inject a
    sampler only in an explicitly named TEST_FIXTURE directory.
    """
    def __init__(self, directory, authority_receipt, *, role="WORKER", test_fixture=False,
                 sampler=None, max_events=MAX_EVENTS, max_bytes=MAX_BYTES):
        initialization_started, initialization_cpu = time.monotonic(), time.process_time()
        self.root = Path(directory).absolute()
        need(self.root.parent.resolve(strict=True) == self.root.parent and
             ".." not in self.root.parts, "METRICS_CANONICAL_NEW_DIRECTORY_REQUIRED")
        need(role in ("WORKER", "SUPERVISOR"), "METRICS_WRITER_ROLE_INVALID")
        need(type(test_fixture) is bool, "METRICS_FIXTURE_FLAG_INVALID")
        need(not test_fixture or any(p.startswith("TEST_FIXTURE") for p in self.root.parts),
             "METRICS_EXPLICIT_TEST_FIXTURE_REQUIRED")
        need(sampler is None or test_fixture, "METRICS_NATIVE_SAMPLER_REQUIRED")
        need(type(max_events) is int and 1 <= max_events <= MAX_EVENTS and
             type(max_bytes) is int and 1 <= max_bytes <= MAX_BYTES, "METRICS_LIMITS_INVALID")
        self.identity = identity(authority_receipt)
        self.role, self.test_fixture = role, test_fixture
        self.sampler = sampler or native_sample
        self.limit_events, self.limit_bytes = max_events, max_bytes
        self.seq, self.previous, self.bytes = 0, None, 0
        self.cost = dict(wall_seconds=0.0, cpu_seconds=0.0, written_bytes=0, fsync_calls=0,
                         sampling_wall_seconds=0.0, sampling_cpu_seconds=0.0)
        self.active, self.last_reads, self.stages = [], {}, 0
        self.closed, self.broken = False, False
        self.writer = os.getpid()
        self.created_monotonic = time.monotonic()
        self.last_sample = None
        with workspace._directory_fd(self.root.parent) as parent:
            os.mkdir(self.root.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        with workspace._directory_fd(self.root) as directory_fd:
            s = os.fstat(directory_fd)
            self.directory_identity = (s.st_dev, s.st_ino)
        self.cost.update(initialization_wall_seconds=time.monotonic()-initialization_started,
                         initialization_cpu_seconds=time.process_time()-initialization_cpu,
                         initialization_directory_fsync_calls=1)
        self.record("JOURNAL_START", details={"role": role})

    def _publish(self, value):
        started, cpu = time.monotonic(), time.process_time()
        need(not self.closed and not self.broken and os.getpid() == self.writer,
             "METRICS_WRITER_CLOSED_BROKEN_OR_FORKED")
        need(self.seq < self.limit_events, "METRICS_EVENT_LIMIT")
        value = {"schema_version": VERSION, "identity": self.identity, "sequence": self.seq,
            "previous_event_hash": self.previous, "writer_pid": self.writer,
            "writer_role": self.role, "test_fixture": self.test_fixture, **value,
            "completed_publication_cost": deepcopy(self.cost),
            "publication_cost_scope": "PRIOR_COMPLETED_EVENT_WRITES; CURRENT_WRITE_TAIL_NOT_YET_OBSERVED"}
        value["event_hash"] = digest(value)
        raw = encoded(value) + b"\n"
        need(self.bytes + len(raw) <= self.limit_bytes, "METRICS_BYTE_LIMIT")
        fd = None
        try:
            with workspace._directory_fd(self.root) as parent:
                info = os.fstat(parent)
                need((info.st_dev, info.st_ino) == self.directory_identity,
                     "METRICS_DIRECTORY_REPLACED")
                fd = os.open(f"{self.seq:06d}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW, 0o600, dir_fd=parent)
                offset = 0
                while offset < len(raw):
                    count = os.write(fd, raw[offset:])
                    need(count > 0, "METRICS_SHORT_WRITE")
                    offset += count
                    self.cost["written_bytes"] += count
                os.fsync(fd)
                self.cost["fsync_calls"] += 1
                os.fsync(parent)
                self.cost["fsync_calls"] += 1
                need(os.fstat(fd).st_nlink == 1, "METRICS_SINGLE_LINK_REQUIRED")
                with workspace._directory_fd(self.root) as current:
                    s = os.fstat(current)
                    need((s.st_dev, s.st_ino) == self.directory_identity,
                         "METRICS_DIRECTORY_REPLACED")
        except BaseException:
            self.broken = True  # A partial event must not be retried or replaced.
            raise
        finally:
            if fd is not None:
                os.close(fd)
            self.cost["wall_seconds"] += time.monotonic() - started
            self.cost["cpu_seconds"] += time.process_time() - cpu
        self.previous, self.seq = value["event_hash"], self.seq + 1
        self.bytes += len(raw)
        return value

    def record(self, event, *, stage_id=None, stage=None, counters=None, details=None):
        need(event in {"JOURNAL_START", "STAGE_START", "STAGE_END", "STAGE_FAILED",
             "METRICS_SNAPSHOT", "CLEANUP_START", "CLEANUP_END", "CLEANUP_FAILED", "JOURNAL_END"},
             "METRICS_EVENT_KIND_INVALID")
        sample, error, reads = None, None, None
        sampling_started, sampling_cpu = time.monotonic(), time.process_time()
        try:
            sample = self.sampler()
            _valid_sample(sample)
            need(sample["process_id"] == self.writer, "METRICS_SAMPLE_PROCESS_MISMATCH")
            if self.last_sample:
                need(sample["monotonic_seconds"] >= self.last_sample["monotonic_seconds"] and
                     sample["process_cpu_seconds"] >= self.last_sample["process_cpu_seconds"],
                     "METRICS_NATIVE_COUNTER_MOVED_BACKWARD")
            value = counters() if callable(counters) else counters
            reads = _counts(value.get("returned_read_bytes") if value is not None else None)
            if reads is not None:
                need(all(reads.get(k, -1) >= v for k, v in self.last_reads.items()),
                     "METRICS_READ_COUNTER_MOVED_BACKWARD")
                self.last_reads = reads
        except Exception as failure:
            error = {"type": type(failure).__name__, "reason": "NATIVE_SAMPLE_OR_COUNTER_OBSERVATION_FAILED"}
            sample, reads = None, None
        self.cost["sampling_wall_seconds"] += time.monotonic() - sampling_started
        self.cost["sampling_cpu_seconds"] += time.process_time() - sampling_cpu
        value = self._publish({"event": event, "stage_id": stage_id, "stage": stage,
            "sample": sample, "returned_read_bytes": reads,
            "read_scope": "EXPLICIT_BUDGET_RETURNED_BYTES_NOT_KERNEL_IO",
            "sampling_error": error, "details": deepcopy(details or {})})
        if error:
            raise ValueError("METRICS_SAMPLE_FAILED_EVENT_PRESERVED")
        self.last_sample = sample
        return value

    @contextmanager
    def stage(self, name, *, counters=None, cleanup=False):
        need(type(name) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", name),
             "METRICS_STAGE_NAME_INVALID")
        self.stages += 1
        stage_id = f"stage-{self.stages}"
        prefix = "CLEANUP" if cleanup else "STAGE"
        self.record(prefix + "_START", stage_id=stage_id, stage=name, counters=counters,
                    details={"parent_stage_id": self.active[-1] if self.active else None})
        self.active.append(stage_id)
        try:
            yield self
        except BaseException as failure:
            try:
                self.record(prefix + "_FAILED", stage_id=stage_id, stage=name, counters=counters,
                            details={"exception_type": type(failure).__name__})
            except BaseException as telemetry_failure:
                if hasattr(failure, "add_note"):
                    failure.add_note("Stage metrics failure retained: " + type(telemetry_failure).__name__)
            raise
        else:
            self.record(prefix + "_END", stage_id=stage_id, stage=name, counters=counters)
        finally:
            self.active.pop()

    def metrics_snapshot(self, metrics):
        return self.record("METRICS_SNAPSHOT", stage_id=self.active[-1] if self.active else None,
                           counters=metrics)

    def finish(self, status, *, counters=None):
        need(status in ("COMPLETE", "FAILED", "UNKNOWN") and not self.active,
             "METRICS_TERMINAL_STATUS_OR_OPEN_STAGE")
        event = self.record("JOURNAL_END", counters=counters, details={"status": status})
        self.closed = True
        return {"schema_version": VERSION + "_WRITER_RECEIPT", "identity": self.identity,
            "terminal_event_hash": event["event_hash"], "publication_cost": deepcopy(self.cost),
            "elapsed_monotonic_seconds": time.monotonic() - self.created_monotonic,
            "receipt_role": "RETURNED_TO_PARENT; NOT_NATIVE_EXIT_OR_SOURCE_AUTHORITY"}

    def wrap_budget(self, base_class):
        """Actual Budget.phase integration without modifying frozen core bytes."""
        journal = self
        class DurableBudget(base_class):
            @contextmanager
            def phase(self, name):
                with journal.stage(name, counters=lambda: self.metrics):
                    with super().phase(name):
                        yield self
        return DurableBudget


def audit_journal(directory, *, expected_identity=None, max_bytes=MAX_BYTES, max_seconds=60):
    need(type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES and _number(max_seconds)
         and 0 < max_seconds <= 180, "METRICS_AUDIT_LIMITS_INVALID")
    root = Path(directory).absolute()
    need(root.resolve(strict=True) == root, "METRICS_CANONICAL_DIRECTORY_REQUIRED")
    paths = sorted(root.iterdir())
    need(0 < len(paths) <= MAX_EVENTS and all(re.fullmatch(r"\d{6}\.json", p.name) for p in paths),
         "METRICS_EVENT_SET_INVALID")
    reader = delivery._Reader(max_bytes, max_seconds)
    started, readers = reader.started, [reader]
    records, previous, open_stages, completed = [], None, {}, []
    partial, errors, binding, native_writer = None, [], None, None
    for index, path in enumerate(paths):
        need(path.name == f"{index:06d}.json", "METRICS_EVENT_SEQUENCE_GAP")
        if len(reader.refs) >= 2048:
            reader = delivery._Reader(max_bytes, max_seconds)
            reader.started = started
            readers.append(reader)
        reader.max_bytes = max_bytes - sum(r.returned_bytes for r in readers if r is not reader)
        try:
            row, _ = reader.read(path, control=True)
        except (ValueError, UnicodeError) as failure:
            # Only a syntactically incomplete tail can be classified UNKNOWN.
            # Physical changes, aliases and budgets must remain hard failures.
            if index != len(paths) - 1 or str(failure) != "PREMARKET_INVALID_CONTROL_JSON":
                raise
            partial = path.name
            break
        need(row.get("schema_version") == VERSION and row.get("sequence") == index and
             row.get("previous_event_hash") == previous and
             row.get("event_hash") == digest({k: v for k, v in row.items() if k != "event_hash"}),
             "METRICS_EVENT_CHAIN_INVALID")
        observed = identity(row.get("identity"))
        binding = observed if binding is None else binding
        need(binding == observed and (expected_identity is None or binding == identity(expected_identity)),
             "METRICS_BUSINESS_BINDING_MISMATCH")
        need(index != 0 or row["event"] == "JOURNAL_START", "METRICS_START_REQUIRED")
        need(not records or records[-1]["event"] != "JOURNAL_END", "METRICS_AFTER_TERMINAL")
        if row.get("sampling_error"):
            errors.append({"sequence": index, "reason": "NATIVE_SAMPLE_OR_COUNTER_OBSERVATION_FAILED"})
        else:
            _valid_sample(row.get("sample"))
            need(row["sample"]["process_id"] == row["writer_pid"], "METRICS_SAMPLE_WRITER_CONFLICT")
            if native_writer is None:
                native_writer = row["sample"]["native_process_identity"]
            need(native_writer == row["sample"]["native_process_identity"],
                 "METRICS_NATIVE_WRITER_IDENTITY_CHANGED")
        if records:
            need(row["writer_pid"] == records[0]["writer_pid"] and
                 row["writer_role"] == records[0]["writer_role"] and
                 row["test_fixture"] is records[0]["test_fixture"], "METRICS_WRITER_IDENTITY_CHANGED")
        event, sid = row["event"], row.get("stage_id")
        if event in ("STAGE_START", "CLEANUP_START"):
            need(type(sid) is str and sid not in open_stages, "METRICS_STAGE_ID_CONFLICT")
            open_stages[sid] = row
        elif event in ("STAGE_END", "STAGE_FAILED", "CLEANUP_END", "CLEANUP_FAILED"):
            start = open_stages.pop(sid, None)
            need(start is not None and start["stage"] == row["stage"], "METRICS_STAGE_START_MISSING")
            first, last = start.get("sample"), row.get("sample")
            seconds = last["monotonic_seconds"] - first["monotonic_seconds"] if first and last else None
            cpu = last["process_cpu_seconds"] - first["process_cpu_seconds"] if first and last else None
            need(seconds is None or seconds >= 0, "METRICS_STAGE_CLOCK_ORDER")
            reads = None
            if start.get("returned_read_bytes") is not None and row.get("returned_read_bytes") is not None:
                reads = {k: v - start["returned_read_bytes"].get(k, 0)
                         for k, v in row["returned_read_bytes"].items()}
                need(all(v >= 0 for v in reads.values()), "METRICS_STAGE_READ_COUNTER_ORDER")
            completed.append({"stage_id": sid, "stage": row["stage"], "event": event,
                "wall_seconds": seconds, "process_cpu_seconds": cpu, "returned_read_bytes": reads,
                "process_peak_rss_bytes": last["process_peak_rss_bytes"] if last else None,
                "inclusive_nested_costs_do_not_sum": True})
        records.append(row)
        previous = row["event_hash"]
    for reader in readers:
        reader.max_bytes = max_bytes - sum(r.returned_bytes for r in readers if r is not reader)
        reader.terminal()
    need(paths == sorted(root.iterdir()), "METRICS_EVENT_SET_CHANGED")
    terminal = records[-1] if records and records[-1]["event"] == "JOURNAL_END" else None
    status = terminal["details"].get("status") if terminal else "UNKNOWN"
    if partial or open_stages:
        status = "UNKNOWN"
    elif errors or any(r["event"].endswith("_FAILED") for r in records):
        status = "FAILED"
    need(status in ("COMPLETE", "FAILED", "UNKNOWN"), "METRICS_TERMINAL_INVALID")
    return {"schema_version": VERSION + "_AUDIT", "identity": binding, "status": status,
        "event_count": len(records), "terminal_event_hash": previous if terminal else None,
        "test_fixture": any(r["test_fixture"] for r in records), "open_stages": sorted(open_stages),
        "writer_identity": native_writer,
        "partial_tail": partial, "sampling_errors": errors, "stages": completed,
        "publication_cost_lower_bound": records[-1]["completed_publication_cost"] if records else None,
        "last_publication_cost": "UNKNOWN_UNLESS_PARENT_BINDS_WRITER_RECEIPT",
        "process_exit_status": "NOT_OBSERVED_BY_JOURNAL", "read_bytes": sum(r.returned_bytes for r in readers),
        "evidence": [ref for r in readers for ref in r.refs.values()],
        "source_authority": False, "retry_authorized": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit_journal(args.directory)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "COMPLETE" else 2
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "REJECTED", "error_type": type(error).__name__}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
