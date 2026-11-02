"""Bounded POSIX supervision of an explicitly launched command and descendants.

The root PID is not reaped until cleanup, pinning its original session/group.
Observed descendants retain (PID, kernel creation time, UID) across reparenting
and new sessions. Sampling is not an OS memory quota: a short-lived descendant
that detaches before any observation cannot be retrospectively discovered.
This module grants process ownership context, never source or retry authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import time
import uuid

import psutil

VERSION = "SDK_OWNED_TREE_SUPERVISOR_V1"
FP_KEYS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")
MAX_FILE_BYTES = 32 * 1024**2
MAX_PROCESSES = 8192
FAILURE_RESERVE_BYTES = 32768
TICKET_FD = "STOCKFISH_OWNED_SUPERVISOR_FD"
DEAD = {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
CONSUMED_LIMITS = ("tree_rss_bytes", "self_rss_bytes", "max_seconds", "output_bytes", "supervisor_read_bytes")


class SupervisorError(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise SupervisorError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _safe(error):
    value = str(error)
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,200}", value) else type(error).__name__


def _fp(info):
    return {key: getattr(info, key, 0) for key in FP_KEYS}


@contextmanager
def _directory(path, *, create=False):
    path = Path(path).absolute()
    need(".." not in path.parts, "SUPERVISOR_CANONICAL_PATH_REQUIRED")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def reference(path, expected=None):
    path = Path(path).absolute()
    with _directory(path.parent) as parent:
        parent_identity = os.fstat(parent)
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        need(stat.S_ISREG(before.st_mode) and not getattr(before, "st_flags", 0) & 0x40000000,
             "SUPERVISOR_RESIDENT_REGULAR_FILE_REQUIRED")
        need(before.st_size <= MAX_FILE_BYTES, "SUPERVISOR_SOURCE_FILE_LIMIT")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as handle:
            need(_fp(os.fstat(handle.fileno())) == _fp(before), "SUPERVISOR_OPENED_SOURCE_CHANGED")
            raw = handle.read(MAX_FILE_BYTES + 1)
            need(_fp(os.fstat(handle.fileno())) == _fp(before), "SUPERVISOR_SOURCE_CHANGED_DURING_READ")
        need(_fp(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == _fp(before),
             "SUPERVISOR_SOURCE_PATH_CHANGED")
    with _directory(path.parent) as terminal:
        info = os.fstat(terminal)
        need((info.st_dev, info.st_ino) == (parent_identity.st_dev, parent_identity.st_ino)
             and _fp(os.stat(path.name, dir_fd=terminal, follow_symlinks=False)) == _fp(before),
             "SUPERVISOR_SOURCE_ANCESTOR_CHANGED")
    result = dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), fp=_fp(before))
    if expected is not None:
        need(type(expected) is dict and type(expected.get("fp")) is dict
             and set(expected["fp"]) == set(FP_KEYS)
             and {"path", "sha256", "fp"} <= set(expected)
             and set(expected) <= {"path", "bytes", "sha256", "fp"}, "SUPERVISOR_NAMED_PHYSICAL_REFERENCE_REQUIRED")
        need(all(result[name] == value for name, value in expected.items()), "SUPERVISOR_SOURCE_REFERENCE_CHANGED")
    return raw, result


def _write(path, value):
    raw = canonical(value)
    need(len(raw) <= MAX_FILE_BYTES, "SUPERVISOR_CONTROL_FILE_LIMIT")
    path = Path(path)
    with _directory(path.parent) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(parent)
    return reference(path)[1]


def _identity(process):
    return dict(pid=process.pid, create_time=process.create_time(), uid=process.uids().real)


def _same(identity):
    try:
        process = psutil.Process(identity["pid"])
        if _identity(process) != identity:
            return None
        return process
    except psutil.NoSuchProcess:
        return None


def _snapshot():
    """Kernel numeric process facts only; never read command lines/environments."""
    rows, unavailable = {}, []
    for process in psutil.process_iter():
        need(len(rows) + len(unavailable) < MAX_PROCESSES, "SUPERVISOR_PROCESS_TABLE_LIMIT")
        try:
            with process.oneshot():
                identity = _identity(process)
                if identity["uid"] != os.getuid():
                    continue
                status = process.status()
                # Darwin keeps the waitable PID/start/UID but refuses RSS,
                # getsid and getpgid for zombies. It is a known terminal root,
                # not a missing/reused identity and not an RSS observation.
                row = dict(identity, ppid=process.ppid(), status=status,
                           rss=0 if status in DEAD else process.memory_info().rss,
                           pgid=None if status in DEAD else os.getpgid(process.pid),
                           sid=None if status in DEAD else os.getsid(process.pid))
                need(type(row["rss"]) is int and row["rss"] >= 0, "SUPERVISOR_RSS_SAMPLE_INVALID")
                rows[process.pid] = row
        except (psutil.NoSuchProcess, ProcessLookupError):
            # One bounded re-observation closes the live -> zombie race between
            # status and RSS/session reads. A live/unavailable root still fails.
            try:
                current = psutil.Process(process.pid)
                identity = _identity(current)
                status = current.status()
                if identity["uid"] == os.getuid() and status in DEAD:
                    rows[process.pid] = dict(identity, ppid=current.ppid(), status=status, rss=0, pgid=None, sid=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except (psutil.AccessDenied, PermissionError):
            unavailable.append(process.pid)
    return rows, unavailable


class Ownership:
    def __init__(self, root):
        self.root = dict(root)
        self.known = {(root["pid"], root["create_time"]): dict(root)}
        self.samples = []
        self.max_processes = 1

    def observe(self, rows, unavailable=()):
        need(self.root["pid"] not in unavailable, "SUPERVISOR_ROOT_OBSERVATION_UNAVAILABLE")
        owned = {}
        for key, identity in self.known.items():
            need(identity["pid"] not in unavailable, "SUPERVISOR_OWNED_OBSERVATION_UNAVAILABLE")
            row = rows.get(identity["pid"])
            if row is not None and {name: row[name] for name in identity} == identity:
                owned[row["pid"]] = row
        root_row = rows.get(self.root["pid"])
        if root_row is None:
            # A Darwin process-table enumeration can miss the live -> zombie
            # transition entirely. The direct child is still unreaped, so one
            # fresh kernel observation may establish its matching terminal
            # identity. Never invent a live RSS sample or accept a reused PID.
            try:
                terminal = psutil.Process(self.root["pid"])
                identity, state = _identity(terminal), terminal.status()
                if identity == self.root and state in DEAD:
                    root_row = dict(identity, ppid=None, status=state, rss=0, pgid=None, sid=None)
                    owned[self.root["pid"]] = root_row
            except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError, PermissionError):
                pass
        need(root_row is not None and {name: root_row[name] for name in self.root} == self.root,
             "SUPERVISOR_ROOT_IDENTITY_LOST")
        # The unreaped root pins this session identifier. New child groups in
        # the same session remain owned even after their immediate parent exits.
        for row in rows.values():
            if row["sid"] == self.root["pid"] or row["pgid"] == self.root["pid"]:
                need(row["create_time"] >= self.root["create_time"] and row["uid"] == self.root["uid"],
                     "SUPERVISOR_SESSION_OWNERSHIP_CONFLICT")
                owned[row["pid"]] = row
        while True:
            added = {pid: row for pid, row in rows.items() if row["ppid"] in owned and pid not in owned}
            if not added:
                break
            owned.update(added)
        for row in owned.values():
            identity = {name: row[name] for name in self.root}
            self.known[(identity["pid"], identity["create_time"])] = identity
            need(len(self.known) <= MAX_PROCESSES, "SUPERVISOR_OWNED_IDENTITY_LIMIT")
        self.max_processes = max(self.max_processes, len(owned))
        return list(owned.values())

    def present(self):
        values = []
        for identity in self.known.values():
            process = _same(identity)
            if process is not None:
                values.append((identity, process.status()))
        return values


def _signal_owned(ownership, signum, signals, errors):
    # Do not poll()/wait() the direct child before this: an unreaped child PID
    # cannot be reused, so its original group ID cannot become a foreign group.
    try:
        root = _same(ownership.root)
        need(root is not None and (root.status() in DEAD
             or os.getsid(root.pid) == root.pid and os.getpgid(root.pid) == root.pid),
             "SUPERVISOR_GROUP_ANCHOR_LOST")
        os.killpg(root.pid, signum)
        signals.append(dict(kind="OWNED_ROOT_GROUP", pid=root.pid, signal=signum))
    except ProcessLookupError:
        pass
    except Exception as error:
        errors.append(_safe(error))
    for identity in list(ownership.known.values()):
        if identity == ownership.root:
            continue
        try:
            process = _same(identity)
            if process is None or process.status() in DEAD:
                continue
            # psutil's send_signal also checks its cached creation-time identity
            # immediately before signaling; stale observations never kill a PID.
            process.send_signal(signum)
            signals.append(dict(kind="OBSERVED_DESCENDANT", **identity, signal=signum))
        except psutil.NoSuchProcess:
            pass
        except Exception as error:
            errors.append(_safe(error))


def _cleanup(process, ownership, term_grace, kill_grace, observe_callback=None):
    signals, errors = [], []
    def observe():
        try:
            rows, unavailable = _snapshot()
            if observe_callback is not None:
                observe_callback(rows, unavailable, None)
            owned = ownership.observe(rows, unavailable)
            if observe_callback is not None:
                observe_callback(rows, unavailable, owned)
            return ownership.present()
        except Exception as error:
            errors.append(_safe(error))
            try:
                return ownership.present()
            except Exception as second:
                errors.append(_safe(second))
                return [(ownership.root, "UNKNOWN")]
    present = observe()
    if any(state not in DEAD for _, state in present):
        _signal_owned(ownership, signal.SIGTERM, signals, errors)
        until = time.monotonic() + term_grace
        while time.monotonic() < until:
            if not any(state not in DEAD for _, state in observe()):
                break
            time.sleep(.02)
        if any(state not in DEAD for _, state in observe()):
            _signal_owned(ownership, signal.SIGKILL, signals, errors)
    until = time.monotonic() + kill_grace
    present = observe()
    while time.monotonic() < until and any(identity != ownership.root for identity, _ in present):
        if any(state not in DEAD for identity, state in present if identity != ownership.root):
            _signal_owned(ownership, signal.SIGKILL, signals, errors)
        time.sleep(.02)
        present = observe()
    root_reaped, code = False, None
    try:
        code = process.wait(timeout=max(.01, until-time.monotonic()))
        root_reaped = True
    except subprocess.TimeoutExpired:
        errors.append("SUPERVISOR_ROOT_REAP_TIMEOUT")
    remaining = []
    try:
        for identity, state in ownership.present():
            remaining.append(dict(identity, status=state))
    except Exception as error:
        errors.append(_safe(error))
        remaining.append(dict(ownership.root, status="OBSERVATION_UNAVAILABLE"))
    return dict(root_reaped=root_reaped, child_exit_code=code, remaining=remaining,
        all_observed_owned_exited=not any(row["status"] not in DEAD for row in remaining),
        all_observed_descendants_gone=not remaining, signals=signals,
        errors=sorted(set(errors)), cleanup_scope="ROOT_WAITPID_AND_OBSERVED_DESCENDANTS_KERNEL_DISAPPEARANCE")


def validate_worker_context(control_dir):
    """Consume the live inherited pipe; saved JSON alone cannot pass this gate."""
    raw_fd = os.environ.pop(TICKET_FD, None)
    need(isinstance(raw_fd, str) and raw_fd.isascii() and raw_fd.isdigit(), "SUPERVISOR_LIVE_WORKER_PIPE_REQUIRED")
    fd = int(raw_fd)
    need(2 < fd < 65536 and stat.S_ISFIFO(os.fstat(fd).st_mode), "SUPERVISOR_INHERITED_PIPE_REQUIRED")
    try:
        parts, total = [], 0
        while True:
            part = os.read(fd, 4096)
            if not part:
                break
            total += len(part)
            need(total <= 32768, "SUPERVISOR_WORKER_TICKET_LIMIT")
            parts.append(part)
        raw = b"".join(parts)
        need(raw, "SUPERVISOR_WORKER_TICKET_REQUIRED")
    finally:
        os.close(fd)
    ticket = json.loads(raw)
    need(ticket["schema_version"] == VERSION and ticket["control_dir"] == str(Path(control_dir).absolute()),
         "SUPERVISOR_WORKER_CONTEXT_CHANGED")
    claim_raw, claim_ref = reference(Path(control_dir) / "claim.json", ticket["claim"])
    claim = json.loads(claim_raw)
    need(hashlib.sha256(ticket["nonce"].encode()).hexdigest() == claim["nonce_sha256"], "SUPERVISOR_WORKER_NONCE_CHANGED")
    own = _identity(psutil.Process())
    parent = _same(claim["parent"])
    need(own == ticket["child"] and os.getppid() == claim["parent"]["pid"]
         and parent is not None and parent.status() not in DEAD
         and os.getsid(0) == own["pid"] and os.getpgrp() == own["pid"],
         "SUPERVISOR_WORKER_PROCESS_IDENTITY_CHANGED")
    need(digest(sys.orig_argv) == claim["argv_sha256"] and str(Path.cwd()) == claim["cwd"],
         "SUPERVISOR_WORKER_ARGV_OR_CWD_CHANGED")
    returned_read_bytes = len(raw) + len(claim_raw)
    for ref in claim["sources"]:
        source_raw, _ = reference(ref["path"], ref)
        returned_read_bytes += len(source_raw)
    return dict(schema_version=VERSION, claim=claim_ref, child=own, parent=claim["parent"],
        limits=claim["limits"], control_dir=str(Path(control_dir).absolute()),
        context="LIVE_OWNED_PROCESS_CONTEXT_NOT_SOURCE_AUTHORITY", source_capability_issued=False,
        worker_context_returned_read_bytes=returned_read_bytes)


def run_supervised(command, *, cwd, env, limits, control_dir, source_references=(),
                   accepted_exit_codes=(0,), test_fixture=False, _test_hooks=None):
    need(os.name == "posix", "SUPERVISOR_POSIX_PROCESS_OWNERSHIP_REQUIRED")
    need(type(command) in (list, tuple) and 1 <= len(command) <= 256
         and all(type(value) is str and value and "\x00" not in value for value in command)
         and Path(command[0]).is_absolute(), "SUPERVISOR_EXPLICIT_COMMAND_REQUIRED")
    need(type(env) is dict and all(type(k) is str and type(v) is str for k, v in env.items()), "SUPERVISOR_EXPLICIT_ENV_REQUIRED")
    need(type(limits) is dict and all(name in limits for name in CONSUMED_LIMITS), "SUPERVISOR_LIMITS_REQUIRED")
    for name in CONSUMED_LIMITS:
        value = limits[name]
        need(type(value) in (int, float) and math.isfinite(value) and value > 0,
             "SUPERVISOR_FINITE_POSITIVE_LIMIT_REQUIRED")
    interval = limits.get("sample_interval_seconds", .25)
    term_grace, kill_grace = limits.get("term_grace_seconds", .5), limits.get("kill_grace_seconds", 5.)
    need(.01 <= interval <= 5 and 0 <= term_grace <= 30 and 0 < kill_grace <= 30, "SUPERVISOR_POLL_OR_CLEANUP_LIMIT")
    need(limits["output_bytes"] >= 2*FAILURE_RESERVE_BYTES, "SUPERVISOR_FAILURE_OUTPUT_RESERVE_REQUIRED")
    need(all(type(value) is int for value in accepted_exit_codes) and accepted_exit_codes, "SUPERVISOR_ACCEPTED_EXIT_CODES_REQUIRED")
    control, cwd = Path(control_dir).absolute(), Path(cwd).absolute()
    hooks = _test_hooks or {}
    need(not hooks or test_fixture and any("TEST_FIXTURE" in part for part in control.parts),
         "SUPERVISOR_HOOKS_REQUIRE_EXPLICIT_TEST_FIXTURE")
    need(set(hooks) <= {"before_sample", "after_sample", "after_launch", "utc_now"}, "SUPERVISOR_UNKNOWN_TEST_HOOK")
    utc_now = hooks.get("utc_now", lambda: datetime.now(timezone.utc))
    mono_started, utc_started = time.monotonic(), utc_now()
    need(isinstance(utc_started, datetime) and utc_started.utcoffset() is not None, "SUPERVISOR_AWARE_UTC_REQUIRED")
    read_bytes = 0
    def read_ref(path, expected=None):
        nonlocal read_bytes
        raw, ref = reference(path, expected)
        read_bytes += len(raw)
        need(read_bytes <= limits["supervisor_read_bytes"], "SUPERVISOR_READ_LIMIT_EXCEEDED")
        return raw, ref
    sources = {}
    for expected in source_references:
        _, ref = read_ref(expected["path"], expected)
        need(ref["path"] not in sources or sources[ref["path"]] == ref, "SUPERVISOR_SOURCE_CONFLICT")
        sources[ref["path"]] = ref
    own_source = read_ref(Path(__file__).absolute())[1]
    need(own_source["path"] not in sources or sources[own_source["path"]] == own_source,
         "SUPERVISOR_SOURCE_CONFLICT")
    sources[own_source["path"]] = own_source
    with _directory(cwd):
        pass
    with _directory(control.parent) as parent:
        os.mkdir(control.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    nonce = uuid.uuid4().hex + uuid.uuid4().hex
    claim = dict(schema_version=VERSION, parent=_identity(psutil.Process()), argv_sha256=digest(list(command)),
        cwd=str(cwd), limits=dict(limits), consumed_limit_fields=list(CONSUMED_LIMITS),
        limits_not_enforced_here=sorted(set(limits)-set(CONSUMED_LIMITS)-{
            "sample_interval_seconds", "term_grace_seconds", "kill_grace_seconds"}),
        sources=list(sources.values()), nonce_sha256=hashlib.sha256(nonce.encode()).hexdigest(),
        started_at=utc_started.isoformat(), source_capability_issued=False, retry_authorized=False)
    claim_ref = _write(control / "claim.json", claim)
    read_bytes += claim_ref["bytes"]
    process = ownership = None
    console = None
    read_fd = write_fd = None
    failure, monitor_error = None, None
    peaks = dict(tree=0, root=0, supervisor=0)
    samples = []
    sample_count, last_offset, max_gap = 0, None, 0.
    snapshot_bytes_total, snapshot_bytes_min, snapshot_bytes_max = 0, None, 0
    total_sample_seconds, peak_sample = 0., None
    cleanup_snapshot_bytes = 0
    child_code = None
    cleanup = dict(root_reaped=False, all_observed_owned_exited=False, all_observed_descendants_gone=False,
                   remaining=[], signals=[], errors=[])
    def cleanup_observation(rows, unavailable, owned):
        nonlocal read_bytes, cleanup_snapshot_bytes
        if owned is None:
            count = len(canonical(dict(rows=rows, unavailable=unavailable)))
            read_bytes += count
            cleanup_snapshot_bytes += count
            return
        live = [row for row in owned if row["status"] not in DEAD]
        peaks["tree"] = max(peaks["tree"], sum(row["rss"] for row in live))
        peaks["root"] = max(peaks["root"], max((row["rss"] for row in live if row["pid"] == process.pid), default=0))
    def observe():
        nonlocal read_bytes, sample_count, last_offset, max_gap, total_sample_seconds, peak_sample
        nonlocal snapshot_bytes_total, snapshot_bytes_min, snapshot_bytes_max
        if "before_sample" in hooks:
            hooks["before_sample"](dict(sample_count=sample_count, pid=process.pid))
        before = time.monotonic()
        rows, unavailable = _snapshot()
        snapshot_bytes = len(canonical(dict(rows=rows, unavailable=unavailable)))
        snapshot_bytes_total += snapshot_bytes
        snapshot_bytes_min = snapshot_bytes if snapshot_bytes_min is None else min(snapshot_bytes_min, snapshot_bytes)
        snapshot_bytes_max = max(snapshot_bytes_max, snapshot_bytes)
        read_bytes += snapshot_bytes
        need(read_bytes <= limits["supervisor_read_bytes"], "SUPERVISOR_READ_LIMIT_EXCEEDED")
        owned = ownership.observe(rows, unavailable)
        live = [row for row in owned if row["status"] not in DEAD]
        root_row = next(row for row in owned if row["pid"] == process.pid)
        tree_rss = sum(row["rss"] for row in live)
        root_rss = root_row["rss"] if root_row["status"] not in DEAD else 0
        self_rss = psutil.Process().memory_info().rss
        peaks["tree"], peaks["root"], peaks["supervisor"] = (
            max(peaks["tree"], tree_rss), max(peaks["root"], root_rss), max(peaks["supervisor"], self_rss))
        item = dict(monotonic_offset_seconds=before-mono_started, sampled_at=utc_now().isoformat(),
                    sample_seconds=time.monotonic()-before, tree_rss_bytes=tree_rss,
                    root_rss_bytes=root_rss, supervisor_rss_bytes=self_rss,
                    owned_count=len(owned), live_count=len(live))
        sample_count += 1
        item["sample_number"] = sample_count
        if last_offset is not None:
            max_gap = max(max_gap, item["monotonic_offset_seconds"]-last_offset)
        last_offset = item["monotonic_offset_seconds"]
        total_sample_seconds += item["sample_seconds"]
        if peak_sample is None or tree_rss >= peak_sample["tree_rss_bytes"]:
            peak_sample = item
        samples.append(item)
        if len(samples) > 64:
            del samples[8]
        if "after_sample" in hooks:
            hooks["after_sample"](dict(item, sample_count=sample_count, pid=process.pid))
        return root_row, live
    def checks():
        elapsed = (utc_now()-utc_started).total_seconds()
        need(elapsed >= 0, "SUPERVISOR_UTC_CLOCK_MOVED_BACKWARD")
        need(elapsed <= limits["max_seconds"], "SUPERVISOR_UTC_DEADLINE_EXCEEDED")
        need(time.monotonic()-mono_started <= limits["max_seconds"], "SUPERVISOR_MONOTONIC_DEADLINE_EXCEEDED")
        need(read_bytes <= limits["supervisor_read_bytes"], "SUPERVISOR_READ_LIMIT_EXCEEDED")
        need(peaks["tree"] <= limits["tree_rss_bytes"], "SUPERVISOR_TREE_RSS_LIMIT_EXCEEDED")
        peaks["supervisor"] = max(peaks["supervisor"], psutil.Process().memory_info().rss)
        need(peaks["supervisor"] <= limits["self_rss_bytes"], "SUPERVISOR_SELF_RSS_LIMIT_EXCEEDED")
        need((control / "console.log").stat().st_size <= min(limits["output_bytes"], 512*1024),
             "SUPERVISOR_CONSOLE_LIMIT_EXCEEDED")
        need(sum(path.stat().st_size for path in control.iterdir() if path.is_file())
             <= limits["output_bytes"]-FAILURE_RESERVE_BYTES, "SUPERVISOR_OUTPUT_LIMIT_EXCEEDED")
    try:
        with _directory(control) as parent:
            console = os.fdopen(os.open("console.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                      0o600, dir_fd=parent), "wb")
        checks()
        read_fd, write_fd = os.pipe()
        environment = dict(env, **{TICKET_FD: str(read_fd)})
        process = subprocess.Popen(list(command), cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=console, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(read_fd,))
        os.close(read_fd)
        read_fd = None
        root = _identity(psutil.Process(process.pid))
        need(os.getpgid(process.pid) == process.pid and os.getsid(process.pid) == process.pid,
             "SUPERVISOR_NEW_SESSION_REQUIRED")
        ownership = Ownership(root)
        ticket = canonical(dict(schema_version=VERSION, control_dir=str(control), claim=claim_ref, child=root, nonce=nonce))
        need(len(ticket) <= 4096, "SUPERVISOR_ATOMIC_PIPE_TICKET_LIMIT")
        need(os.write(write_fd, ticket) == len(ticket), "SUPERVISOR_WORKER_TICKET_WRITE_INCOMPLETE")
        os.close(write_fd)
        write_fd = None
        if "after_launch" in hooks:
            hooks["after_launch"](process.pid)
        while True:
            root_row, live = observe()
            checks()
            if root_row["status"] in DEAD:
                if any(row["pid"] != process.pid for row in live):
                    failure = "SUPERVISOR_DESCENDANTS_REMAIN_AFTER_ROOT_EXIT"
                break
            time.sleep(interval)
    except BaseException as error:
        failure = _safe(error)
        monitor_error = type(error).__name__
    finally:
        for descriptor in (read_fd, write_fd):
            if descriptor is not None:
                os.close(descriptor)
        if process is not None and ownership is not None:
            cleanup = _cleanup(process, ownership, term_grace, kill_grace, cleanup_observation)
            child_code = cleanup["child_exit_code"]
        elif process is not None:
            # Identity acquisition failed before any command authority was
            # accepted. Popen still owns an unreaped direct child PID.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                cleanup["errors"].append("SUPERVISOR_EARLY_SIGNAL_UNVERIFIED")
            try:
                child_code = process.wait(timeout=kill_grace)
                cleanup.update(root_reaped=True, child_exit_code=child_code)
            except (OSError, subprocess.TimeoutExpired):
                cleanup["errors"].append("SUPERVISOR_EARLY_CLEANUP_UNVERIFIED")
        if console is not None:
            console.close()
    if not failure and (cleanup["errors"] or not cleanup["root_reaped"] or not cleanup["all_observed_descendants_gone"]):
        failure = "SUPERVISOR_OWNED_PROCESS_CLEANUP_UNVERIFIED"
    if not failure and child_code not in accepted_exit_codes:
        failure = "SUPERVISOR_CHILD_EXIT_NOT_ACCEPTED"
    if not failure and (not sample_count or peaks["root"] == 0):
        failure = "SUPERVISOR_LIVE_SAMPLING_COVERAGE_INSUFFICIENT"
    try:
        for ref in sources.values():
            read_ref(ref["path"], ref)
        read_ref(claim_ref["path"], claim_ref)
        if not failure:
            checks()
    except Exception as error:
        failure = failure or _safe(error)
    elapsed = max(0., (utc_now()-utc_started).total_seconds())
    raw_self = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = dict(schema_version=VERSION, status="FAILED" if failure else "COMPLETE", failure_reason=failure,
        monitor_error_type=monitor_error, child_exit_code=child_code, claim=claim_ref,
        root_identity=ownership.root if ownership else None, root_reaped=cleanup["root_reaped"],
        all_observed_owned_exited=cleanup["all_observed_owned_exited"],
        all_observed_descendants_gone=cleanup["all_observed_descendants_gone"], cleanup=cleanup,
        observed_peak_tree_rss_bytes=peaks["tree"], observed_peak_root_rss_bytes=peaks["root"],
        supervisor_peak_self_rss_bytes=int(raw_self if sys.platform == "darwin" else raw_self*1024),
        observed_peak_supervisor_rss_bytes=peaks["supervisor"], sample_count=sample_count,
        max_sample_gap_seconds=max_gap, sample_interval_seconds=interval,
        samples=samples, samples_scope="FIRST_8_LAST_56_SELECTION_NOT_FULL_SAMPLE_LOG", peak_sample=peak_sample,
        process_snapshot_bytes_total=snapshot_bytes_total, process_snapshot_bytes_min=snapshot_bytes_min,
        process_snapshot_bytes_max=snapshot_bytes_max, total_sample_seconds=total_sample_seconds,
        cleanup_process_snapshot_bytes=cleanup_snapshot_bytes,
        max_observed_processes=ownership.max_processes if ownership else 0,
        observed_identities=list(ownership.known.values()) if ownership else [],
        started_at=utc_started.isoformat(), completed_at=utc_now().isoformat(),
        actual_utc_elapsed_seconds=elapsed, monotonic_elapsed_seconds=time.monotonic()-mono_started,
        supervisor_returned_read_bytes=read_bytes,
        accounting_scope="FULL_SOURCE_CONTROL_READS_AND_SERIALIZED_NUMERIC_PROCESS_SNAPSHOTS_NOT_ALL_KERNEL_IO",
        consumed_limit_fields=list(CONSUMED_LIMITS), limits_not_enforced_here=claim["limits_not_enforced_here"],
        observation_scope="OBSERVED_DESCENDANTS_AND_PINNED_ROOT_SESSION; NOT_OS_HARD_QUOTA",
        unobserved_short_lived_detached_descendants="NOT_RETROSPECTIVELY_DISCOVERABLE",
        signal_identity_scope="UNREAPED_ROOT_GROUP_AND_CREATION_TIME_CHECKED_PSUTIL_DESCENDANTS",
        source_capability_issued=False, retry_authorized=False, business_result_verified=False,
        psutil_version=psutil.__version__)
    if sum(path.stat().st_size for path in control.iterdir() if path.is_file()) + len(canonical(report)) \
            > limits["output_bytes"]-FAILURE_RESERVE_BYTES:
        report.update(status="FAILED", failure_reason=failure or "SUPERVISOR_FINAL_OUTPUT_LIMIT_EXCEEDED",
                      samples=[], observed_identities=[], diagnostic_selection_omitted_for_output_budget=True)
        report["cleanup"] = {key: value for key, value in cleanup.items() if key not in ("signals", "remaining")}
    completion = _write(control / "completion.json", report)
    read_bytes += completion["bytes"]
    try:
        for ref in sources.values():
            read_ref(ref["path"], ref)
        read_ref(claim_ref["path"], claim_ref)
        checks()
        read_ref(completion["path"], completion)
        checks()
    except Exception as error:
        # Append a failure authority after a candidate completion; never erase
        # a third-party replacement or mistake the inner exit for success.
        failure_ref = _write(control / "failure.json", dict(schema_version=VERSION, status="FAILED",
            failure_reason=_safe(error), candidate_completion=completion, claim=claim_ref))
        read_bytes += failure_ref["bytes"]
        report.update(status="FAILED", failure_reason=_safe(error), failure=failure_ref)
    terminal_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {**report, "completion": completion, "terminal_supervisor_returned_read_bytes": read_bytes,
        "terminal_actual_utc_elapsed_seconds": (utc_now()-utc_started).total_seconds(),
        "terminal_monotonic_elapsed_seconds": time.monotonic()-mono_started,
        "terminal_supervisor_peak_self_rss_bytes": int(terminal_peak if sys.platform == "darwin" else terminal_peak*1024)}
