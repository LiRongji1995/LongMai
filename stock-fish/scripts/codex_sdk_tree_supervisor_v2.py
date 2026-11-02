"""Bounded POSIX supervision of an explicitly launched command and descendants.

The root is an explicitly owned direct child. Only an unreaped, kernel-proven
live root permits group signaling; observed descendants retain their identity
after the root is reaped. Cleanup has bounded, separate descendant and root
wait windows and never grants more business execution time.
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

VERSION = "SDK_OWNED_TREE_SUPERVISOR_V2"
IDENTITY_BASIS = "PSUTIL_7_2_2_NATIVE_CREATE_TIME_MONOTONIC_TRUE_PID_UID"
MAX_CLEANUP_SECONDS = 30.
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


def _stable_start(process):
    """Pinned native psutil token; public create_time adjusts for wall-clock changes.

    The registered psutil private platform API is deliberately version gated.
    No public wall-clock fallback can safely preserve this identity contract.
    """
    need(psutil.__version__ == "7.2.2" and sys.platform in ("darwin", "linux"),
         "SUPERVISOR_STABLE_IDENTITY_PLATFORM_UNSUPPORTED")
    try:
        value = process._proc.create_time(monotonic=True)
    except (AttributeError, TypeError) as error:
        raise SupervisorError("SUPERVISOR_STABLE_IDENTITY_API_UNAVAILABLE") from error
    need(type(value) in (int, float) and math.isfinite(value) and value > 0,
         "SUPERVISOR_STABLE_IDENTITY_INVALID")
    return value


def _identity(process):
    return dict(pid=process.pid, create_time=_stable_start(process), uid=process.uids().real)


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
    def __init__(self, root, anchor=None):
        self.root = dict(root)
        self.anchor = anchor
        self.known = {(root["pid"], root["create_time"]): dict(root)}
        self.samples = []
        self.max_processes = 1
        self.last_root_observation = None
        self.last_root_failure = None

    def observe(self, rows, unavailable=()):
        need(self.root["pid"] not in unavailable, "SUPERVISOR_ROOT_OBSERVATION_UNAVAILABLE")
        owned = {}
        for key, identity in self.known.items():
            need(identity["pid"] not in unavailable, "SUPERVISOR_OWNED_OBSERVATION_UNAVAILABLE")
            row = rows.get(identity["pid"])
            if row is not None and {name: row[name] for name in identity} == identity:
                owned[row["pid"]] = row
        root_row = rows.get(self.root["pid"])
        self.last_root_observation = dict(snapshot_root=root_row, fresh_root=None,
                                          fresh_state=None, kernel_terminal_proof=None)
        if self.anchor is not None and self.anchor.reaped:
            root_row = dict(self.root, ppid=None, status=psutil.STATUS_DEAD, rss=0, pgid=None, sid=None)
            owned[self.root["pid"]] = root_row
        if root_row is None:
            # A Darwin process-table enumeration can miss the live -> zombie
            # transition entirely. The direct child is still unreaped, so one
            # fresh kernel observation may establish its matching terminal
            # identity. Never invent a live RSS sample or accept a reused PID.
            fresh_state = "MISSING"
            try:
                terminal = psutil.Process(self.root["pid"])
                identity, state = _identity(terminal), terminal.status()
                self.last_root_observation["fresh_root"] = dict(identity, status=state)
                fresh_state = "IDENTITY_CONFLICT" if identity != self.root else "MATCHING_LIVE"
                if identity == self.root and state in DEAD:
                    root_row = dict(identity, ppid=None, status=state, rss=0, pgid=None, sid=None)
                    owned[self.root["pid"]] = root_row
                    fresh_state = "MATCHING_TERMINAL"
            except (psutil.NoSuchProcess, ProcessLookupError):
                pass
            except (psutil.AccessDenied, PermissionError):
                fresh_state = "UNAVAILABLE"
            self.last_root_observation["fresh_state"] = fresh_state
            # Darwin can hide even the fresh PID row during exit. Only this
            # module's actual direct-child wait status can fill that terminal
            # root; absence/ECHILD/identity conflict/AccessDenied cannot do so.
            if root_row is None and fresh_state in ("MISSING", "MATCHING_LIVE") and self.anchor is not None:
                proof = self.anchor.probe("ROOT_ENUMERATION_TERMINAL")
                self.last_root_observation["kernel_terminal_proof"] = proof
                if proof == "REAPED":
                    root_row = dict(self.root, ppid=None, status=psutil.STATUS_DEAD, rss=0, pgid=None, sid=None)
                    owned[self.root["pid"]] = root_row
        if root_row is None or {name: root_row[name] for name in self.root} != self.root:
            self.last_root_failure = self.last_root_observation
            raise SupervisorError("SUPERVISOR_ROOT_IDENTITY_LOST")
        if self.anchor is not None and self.anchor.reaped:
            # The old root session/group number is no longer an ownership
            # anchor. Neither same-session rows nor new children of known
            # descendants can expand the authenticated set after this point.
            known_pids = {identity["pid"] for identity in self.known.values()}
            unknown = [row for row in rows.values() if row["pid"] not in known_pids
                       and (row["sid"] == self.root["pid"] or row["pgid"] == self.root["pid"]
                            or row["ppid"] in owned)]
            need(not unknown, "SUPERVISOR_UNOBSERVED_PROCESS_AFTER_ROOT_REAP")
            self.max_processes = max(self.max_processes, len(owned))
            return list(owned.values())
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
            try:
                process = psutil.Process(identity["pid"])
                current = _identity(process)
                if current != identity:
                    need(identity != self.root, "SUPERVISOR_ROOT_IDENTITY_CHANGED")
                    continue  # The old descendant is gone; never signal the new PID owner.
                values.append((identity, process.status()))
            except psutil.NoSuchProcess:
                continue
        return values


class RootAnchor:
    """One owner's direct waitpid state; never infer exit status from ECHILD.

    Popen.wait() treats ECHILD as status zero on POSIX. We cannot use that
    compatibility behavior as evidence. A zero WNOHANG PID proves a live,
    unreaped direct child; a returned child PID proves the actual wait status.
    The launch is private and callers must not separately poll/wait/reap it.
    """
    def __init__(self, process, identity=None):
        self.process, self.identity = process, identity
        self.reaped, self.lost = False, False
        self.code = self.wait_status = None
        self.calls, self.last_phase = 0, None
        self.reason = None

    def probe(self, phase):
        self.last_phase = phase
        if self.reaped:
            return "REAPED"
        if self.lost:
            return "UNKNOWN"
        if self.process.returncode is not None:
            self.lost, self.reason = True, "SUPERVISOR_ROOT_EXTERNAL_WAIT_UNVERIFIED"
            return "UNKNOWN"
        try:
            self.calls += 1
            pid, status = os.waitpid(self.process.pid, os.WNOHANG)
        except ChildProcessError:
            self.lost, self.reason = True, "SUPERVISOR_ROOT_ECHILD_EXIT_UNKNOWN"
            return "UNKNOWN"
        except InterruptedError:
            return "INTERRUPTED"
        except OSError:
            self.reason = "SUPERVISOR_ROOT_WAITPID_UNAVAILABLE"
            return "UNKNOWN"
        if pid == 0:
            return "LIVE_DIRECT_CHILD"
        if pid != self.process.pid or not (os.WIFEXITED(status) or os.WIFSIGNALED(status)):
            self.lost, self.reason = True, "SUPERVISOR_ROOT_WAIT_STATUS_INVALID"
            return "UNKNOWN"
        self.reaped, self.wait_status = True, status
        self.code = os.waitstatus_to_exitcode(status)
        self.process.returncode = self.code
        return "REAPED"

    def evidence(self):
        return dict(method="OS_WAITPID_WNOHANG", state=("WAITPID_EXIT_STATUS_CONFIRMED" if self.reaped
            else "EXIT_STATUS_UNKNOWN"), wait_status=self.wait_status, waitpid_calls=self.calls,
            last_phase=self.last_phase, exit_code_proven=self.reaped, error_reason=self.reason)


def _signal_owned(ownership, signum, signals, errors, *, anchor):
    # A successful direct-child WNOHANG probe pins the live PID until this
    # owner reaps it. If that probe reaps a zombie, group signaling is forbidden.
    # Explicit identity mismatch is never overridden by the child relationship.
    identity_matches = True
    if anchor.identity is not None:
        try:
            current = _identity(psutil.Process(anchor.process.pid))
            if current != anchor.identity:
                identity_matches = False
                errors.append("SUPERVISOR_ROOT_IDENTITY_CHANGED")
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError, PermissionError):
            errors.append("SUPERVISOR_ROOT_SIGNAL_OBSERVATION_UNAVAILABLE")
        except Exception as error:
            errors.append(_safe(error))
            identity_matches = False
    proof = anchor.probe("BEFORE_SIGNAL")
    if anchor.reason:
        errors.append(anchor.reason)
    default_reaper = signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
    if not default_reaper:
        errors.append("SUPERVISOR_EXCLUSIVE_DEFAULT_SIGCHLD_REQUIRED")
    if proof == "LIVE_DIRECT_CHILD" and identity_matches and default_reaper:
        try:
            # start_new_session=True succeeded before Popen returned. No root
            # wait/poll/reaper can run in this module between proof and killpg.
            os.killpg(anchor.process.pid, signum)
            signals.append(dict(kind="KERNEL_OWNED_UNREAPED_ROOT_GROUP", pid=anchor.process.pid,
                                signal=signum, ownership_proof=proof))
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(_safe(error))
    if ownership is None:
        return
    for identity in list(ownership.known.values()):
        if identity == ownership.root:
            continue
        try:
            process = _same(identity)
            if process is None or process.status() in DEAD:
                continue
            # psutil's own send_signal guard uses the same stable native token.
            process.send_signal(signum)
            signals.append(dict(kind="OBSERVED_DESCENDANT", **identity, signal=signum))
        except psutil.NoSuchProcess:
            pass
        except Exception as error:
            errors.append(_safe(error))


def _cleanup(process, ownership, term_grace, kill_grace, observe_callback=None, *,
             root_reap_seconds=5., utc_now=None, phase_hook=None, anchor=None):
    """Bounded safety-only cleanup; these windows do not extend business work.

    Limits bound scheduled waits. A suspended OS or a blocked kernel syscall
    can still delay return; actual UTC and monotonic durations are reported.
    """
    need(all(type(x) in (int, float) and math.isfinite(x) and x >= 0
             for x in (term_grace, kill_grace, root_reap_seconds))
         and kill_grace > 0 and root_reap_seconds > 0
         and term_grace + kill_grace + root_reap_seconds <= MAX_CLEANUP_SECONDS,
         "SUPERVISOR_CLEANUP_TOTAL_LIMIT")
    utc_now = utc_now or (lambda: datetime.now(timezone.utc))
    started, utc_started = time.monotonic(), utc_now()
    total_window = term_grace + kill_grace + root_reap_seconds
    anchor = anchor or RootAnchor(process, ownership.root if ownership else None)
    signals, errors = [], []
    observation_failed = False
    def phase(name):
        if phase_hook:
            try:
                phase_hook(name, dict(pid=process.pid, root_reaped=anchor.reaped,
                                     monotonic_elapsed_seconds=time.monotonic()-started))
            except Exception as error:
                errors.append(_safe(error))
    def within(deadline):
        wall = (utc_now()-utc_started).total_seconds()
        if wall < 0:
            errors.append("SUPERVISOR_CLEANUP_UTC_CLOCK_MOVED_BACKWARD")
            return False
        return time.monotonic() < deadline and time.monotonic()-started < MAX_CLEANUP_SECONDS and wall < MAX_CLEANUP_SECONDS
    def observe():
        nonlocal observation_failed
        if ownership is None:
            return []
        try:
            # Once root is reaped, only previously authenticated descendants
            # may be checked; an old group/session number no longer grants ownership.
            if not anchor.reaped and not anchor.lost:
                rows, unavailable = _snapshot()
                if observe_callback is not None:
                    observe_callback(rows, unavailable, None)
                owned = ownership.observe(rows, unavailable)
                if observe_callback is not None:
                    observe_callback(rows, unavailable, owned)
            return ownership.present()
        except Exception as error:
            observation_failed = True
            errors.append(_safe(error))
            try:
                return ownership.present()
            except Exception as second:
                errors.append(_safe(second))
                return [(ownership.root, "OBSERVATION_UNAVAILABLE")]
    def descendants(values):
        return [(ident, state) for ident, state in values
                if ownership is not None and ident != ownership.root]
    def live(values):
        return any(state not in DEAD for _, state in values)
    phase("before_termination")
    present = observe()
    root_state = anchor.probe("CLEANUP_INITIAL")
    if (not anchor.reaped and not anchor.lost) or live(descendants(present)):
        _signal_owned(ownership, signal.SIGTERM, signals, errors, anchor=anchor)
        deadline = time.monotonic() + term_grace
        while within(deadline):
            present = observe()
            if anchor.probe("TERM_WAIT") != "LIVE_DIRECT_CHILD" and not live(descendants(present)):
                break
            time.sleep(min(.02, max(0., deadline-time.monotonic())))
        present = observe()
        if anchor.probe("AFTER_TERM_WAIT") not in ("REAPED", "UNKNOWN") or live(descendants(present)):
            _signal_owned(ownership, signal.SIGKILL, signals, errors, anchor=anchor)
    # Descendant disappearance gets its own bounded window; it cannot consume
    # the later root wait window. We keep checking identities across reparenting.
    deadline = time.monotonic() + kill_grace
    present = observe()
    while within(deadline) and descendants(present):
        if live(descendants(present)):
            _signal_owned(ownership, signal.SIGKILL, signals, errors, anchor=anchor)
        time.sleep(min(.02, max(0., deadline-time.monotonic())))
        present = observe()
    phase("after_descendant_wait")
    if descendants(present):
        errors.append("SUPERVISOR_DESCENDANT_DISAPPEARANCE_TIMEOUT")
    phase("before_root_reap")
    root_started = time.monotonic()
    root_deadline = root_started + root_reap_seconds
    root_state = anchor.probe("ROOT_WINDOW_INITIAL")
    while root_state in ("LIVE_DIRECT_CHILD", "INTERRUPTED") and within(root_deadline):
        time.sleep(min(.02, max(0., root_deadline-time.monotonic())))
        root_state = anchor.probe("ROOT_REAP_WINDOW")
    root_window_elapsed = time.monotonic()-root_started
    if not anchor.reaped:
        errors.append("SUPERVISOR_ROOT_REAP_TIMEOUT" if not anchor.lost else anchor.reason)
    phase("after_root_reap_window")
    # Even if the bounded window just expired, take the final nonblocking
    # kernel exit status. Its earlier timeout remains a failure if it succeeds.
    phase("before_final_waitpid")
    final_waitpid_attempted = not anchor.reaped and not anchor.lost
    anchor.probe("FINAL_WNOHANG")
    if anchor.reason:
        errors.append(anchor.reason)
    remaining = []
    if ownership is not None:
        try:
            remaining = [dict(identity, status=state) for identity, state in ownership.present()
                         if identity != ownership.root or not anchor.reaped]
        except Exception as error:
            errors.append(_safe(error))
            remaining.append(dict(ownership.root, status="OBSERVATION_UNAVAILABLE"))
    if not anchor.reaped and not any(row["pid"] == process.pid for row in remaining):
        remaining.append(dict(ownership.root if ownership else {"pid": process.pid},
                              status="UNREAPED_EXIT_UNKNOWN"))
    if observation_failed:
        errors.append("SUPERVISOR_CLEANUP_OBSERVATION_INCOMPLETE")
    ended_utc = utc_now()
    return dict(root_reaped=anchor.reaped, child_exit_code=anchor.code, remaining=remaining,
        all_observed_owned_exited=anchor.reaped and not any(row["status"] not in DEAD for row in remaining),
        all_observed_descendants_gone=anchor.reaped and not remaining, signals=signals,
        errors=sorted(set(x for x in errors if x)),
        cleanup_scope="DIRECT_KERNEL_WAITPID_AND_STABLE_IDENTITY_OBSERVED_DESCENDANTS",
        root_reap_evidence=anchor.evidence(), final_waitpid_attempted=final_waitpid_attempted,
        root_reap_window_elapsed_seconds=root_window_elapsed,
        actual_utc_elapsed_seconds=(ended_utc-utc_started).total_seconds(),
        monotonic_elapsed_seconds=time.monotonic()-started,
        started_at=utc_started.isoformat(), completed_at=ended_utc.isoformat(),
        window_limits=dict(term_seconds=term_grace, descendant_seconds=kill_grace,
                           root_reap_seconds=root_reap_seconds, total_scheduled_seconds=total_window,
                           aggregate_wait_cap_seconds=MAX_CLEANUP_SECONDS),
        cleanup_limit_scope="BOUNDED_SCHEDULED_WAITS_NOT_OS_SUSPEND_OR_BLOCKED_SYSCALL_GUARANTEE",
        business_budget_extension_seconds=0)


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
    need(claim.get("identity_basis") == IDENTITY_BASIS and own == ticket["child"] and os.getppid() == claim["parent"]["pid"]
         and parent is not None and parent.status() not in DEAD
         and os.getsid(0) == own["pid"] and os.getpgrp() == own["pid"],
         "SUPERVISOR_WORKER_PROCESS_IDENTITY_CHANGED")
    need(digest(sys.orig_argv) == claim["argv_sha256"] and str(Path.cwd()) == claim["cwd"],
         "SUPERVISOR_WORKER_ARGV_OR_CWD_CHANGED")
    returned_read_bytes = len(raw) + len(claim_raw)
    for ref in claim["sources"]:
        source_raw, _ = reference(ref["path"], ref)
        returned_read_bytes += len(source_raw)
    return dict(schema_version=VERSION, identity_basis=IDENTITY_BASIS, claim=claim_ref, child=own, parent=claim["parent"],
        limits=claim["limits"], control_dir=str(Path(control_dir).absolute()),
        context="LIVE_OWNED_PROCESS_CONTEXT_NOT_SOURCE_AUTHORITY", source_capability_issued=False,
        worker_context_returned_read_bytes=returned_read_bytes)


def run_supervised(command, *, cwd, env, limits, control_dir, source_references=(),
                   accepted_exit_codes=(0,), test_fixture=False, _test_hooks=None):
    need(os.name == "posix", "SUPERVISOR_POSIX_PROCESS_OWNERSHIP_REQUIRED")
    need(signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL,
         "SUPERVISOR_EXCLUSIVE_DEFAULT_SIGCHLD_REQUIRED")
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
    root_reap_seconds = limits.get("root_reap_seconds", 5.)
    need(all(type(x) in (int, float) and math.isfinite(x) for x in (interval, term_grace, kill_grace, root_reap_seconds))
         and .01 <= interval <= 5 and term_grace >= 0 and kill_grace > 0 and root_reap_seconds > 0
         and term_grace + kill_grace + root_reap_seconds <= MAX_CLEANUP_SECONDS, "SUPERVISOR_POLL_OR_CLEANUP_LIMIT")
    need(limits["output_bytes"] >= 2*FAILURE_RESERVE_BYTES, "SUPERVISOR_FAILURE_OUTPUT_RESERVE_REQUIRED")
    need(all(type(value) is int for value in accepted_exit_codes) and accepted_exit_codes, "SUPERVISOR_ACCEPTED_EXIT_CODES_REQUIRED")
    control, cwd = Path(control_dir).absolute(), Path(cwd).absolute()
    hooks = _test_hooks or {}
    need(not hooks or test_fixture and any("TEST_FIXTURE" in part for part in control.parts),
         "SUPERVISOR_HOOKS_REQUIRE_EXPLICIT_TEST_FIXTURE")
    need(set(hooks) <= {"before_sample", "after_sample", "after_launch", "utc_now", "cleanup_phase"}, "SUPERVISOR_UNKNOWN_TEST_HOOK")
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
    claim = dict(schema_version=VERSION, identity_basis=IDENTITY_BASIS,
        reaper_contract="DEFAULT_SIGCHLD_MODULE_EXCLUSIVE_DIRECT_CHILD_WAITER_NO_EXTERNAL_WAITPID_THREAD",
        parent=_identity(psutil.Process()), argv_sha256=digest(list(command)),
        cwd=str(cwd), limits=dict(limits), consumed_limit_fields=list(CONSUMED_LIMITS),
        limits_not_enforced_here=sorted(set(limits)-set(CONSUMED_LIMITS)-{
            "sample_interval_seconds", "term_grace_seconds", "kill_grace_seconds", "root_reap_seconds"}),
        sources=list(sources.values()), nonce_sha256=hashlib.sha256(nonce.encode()).hexdigest(),
        started_at=utc_started.isoformat(), source_capability_issued=False, retry_authorized=False)
    claim_ref = _write(control / "claim.json", claim)
    read_bytes += claim_ref["bytes"]
    process = ownership = anchor = None
    console = None
    read_fd = write_fd = None
    failure, monitor_error = None, None
    peaks = dict(tree=0, root=0, supervisor=0)
    samples = []
    sample_count, last_offset, max_gap = 0, None, 0.
    last_sample_utc, max_utc_gap = None, 0.
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
        nonlocal last_sample_utc, max_utc_gap
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
        sample_utc = utc_now()
        item = dict(monotonic_offset_seconds=before-mono_started, sampled_at=sample_utc.isoformat(),
                    sample_seconds=time.monotonic()-before, tree_rss_bytes=tree_rss,
                    root_rss_bytes=root_rss, supervisor_rss_bytes=self_rss,
                    owned_count=len(owned), live_count=len(live))
        sample_count += 1
        item["sample_number"] = sample_count
        if last_offset is not None:
            max_gap = max(max_gap, item["monotonic_offset_seconds"]-last_offset)
        last_offset = item["monotonic_offset_seconds"]
        if last_sample_utc is not None:
            utc_gap = (sample_utc-last_sample_utc).total_seconds()
            need(utc_gap >= 0, "SUPERVISOR_UTC_CLOCK_MOVED_BACKWARD")
            max_utc_gap = max(max_utc_gap, utc_gap)
        last_sample_utc = sample_utc
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
        anchor = RootAnchor(process)
        root = _identity(psutil.Process(process.pid))
        anchor.identity = root
        need(os.getpgid(process.pid) == process.pid and os.getsid(process.pid) == process.pid,
             "SUPERVISOR_NEW_SESSION_REQUIRED")
        ownership = Ownership(root, anchor)
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
        if process is not None:
            cleanup = _cleanup(process, ownership, term_grace, kill_grace, cleanup_observation,
                               root_reap_seconds=root_reap_seconds, utc_now=utc_now,
                               phase_hook=hooks.get("cleanup_phase"), anchor=anchor)
            child_code = cleanup["child_exit_code"]
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
        root_identity=ownership.root if ownership else None, identity_basis=IDENTITY_BASIS, root_reaped=cleanup["root_reaped"],
        all_observed_owned_exited=cleanup["all_observed_owned_exited"],
        all_observed_descendants_gone=cleanup["all_observed_descendants_gone"], cleanup=cleanup,
        observed_peak_tree_rss_bytes=peaks["tree"], observed_peak_root_rss_bytes=peaks["root"],
        supervisor_peak_self_rss_bytes=int(raw_self if sys.platform == "darwin" else raw_self*1024),
        observed_peak_supervisor_rss_bytes=peaks["supervisor"], sample_count=sample_count,
        max_sample_gap_seconds=max_gap, max_sample_gap_clock="MONOTONIC",
        max_utc_sample_gap_seconds=max_utc_gap, sample_interval_seconds=interval,
        samples=samples, samples_scope="FIRST_8_LAST_56_SELECTION_NOT_FULL_SAMPLE_LOG", peak_sample=peak_sample,
        process_snapshot_bytes_total=snapshot_bytes_total, process_snapshot_bytes_min=snapshot_bytes_min,
        process_snapshot_bytes_max=snapshot_bytes_max, total_sample_seconds=total_sample_seconds,
        cleanup_process_snapshot_bytes=cleanup_snapshot_bytes,
        max_observed_processes=ownership.max_processes if ownership else 0,
        observed_identities=list(ownership.known.values()) if ownership else [],
        last_root_observation=ownership.last_root_observation if ownership else None,
        last_root_failure=ownership.last_root_failure if ownership else None,
        started_at=utc_started.isoformat(), completed_at=utc_now().isoformat(),
        actual_utc_elapsed_seconds=elapsed, monotonic_elapsed_seconds=time.monotonic()-mono_started,
        supervisor_returned_read_bytes=read_bytes,
        accounting_scope="FULL_SOURCE_CONTROL_READS_AND_SERIALIZED_NUMERIC_PROCESS_SNAPSHOTS_NOT_ALL_KERNEL_IO",
        consumed_limit_fields=list(CONSUMED_LIMITS), limits_not_enforced_here=claim["limits_not_enforced_here"],
        observation_scope="OBSERVED_DESCENDANTS_AND_PINNED_ROOT_SESSION; NOT_OS_HARD_QUOTA",
        unobserved_short_lived_detached_descendants="NOT_RETROSPECTIVELY_DISCOVERABLE",
        signal_identity_scope="DIRECT_CHILD_WNOHANG_UNREAPED_ROOT_AND_STABLE_NATIVE_START_UID_DESCENDANTS",
        reaper_contract=claim["reaper_contract"],
        identity_platform_scope="PINNED_PSUTIL_PRIVATE_PLATFORM_API_DARWIN_LINUX_ONLY_NO_WALL_FALLBACK",
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
            failure_reason=failure or _safe(error), terminal_validation_error=_safe(error),
            candidate_completion=completion, claim=claim_ref))
        read_bytes += failure_ref["bytes"]
        report.update(status="FAILED", failure_reason=failure or _safe(error),
                      terminal_validation_error=_safe(error), failure=failure_ref)
    terminal_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {**report, "completion": completion, "terminal_supervisor_returned_read_bytes": read_bytes,
        "terminal_actual_utc_elapsed_seconds": (utc_now()-utc_started).total_seconds(),
        "terminal_monotonic_elapsed_seconds": time.monotonic()-mono_started,
        "terminal_supervisor_peak_self_rss_bytes": int(terminal_peak if sys.platform == "darwin" else terminal_peak*1024)}
