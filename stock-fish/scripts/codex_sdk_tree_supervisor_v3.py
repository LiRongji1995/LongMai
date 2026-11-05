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
import json
import math
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.file_integrity import (
    content_equal, content_reference_equal, content_fingerprint, verify_content_reference,
)

VERSION = "SDK_OWNED_TREE_SUPERVISOR_V3"
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


def _safe(error):
    value = str(error)
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,200}", value) else type(error).__name__


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


def _inner_sticky(function):
    """A legacy loop may swallow a sampler error during child exit; remember it."""
    from functools import wraps
    @wraps(function)
    def checked(self, *args, **kwargs):
        try:
            return function(self,*args,**kwargs)
        except BaseException as error:
            if self.failure is None:
                self.failure = _safe(error)
                self._failure_error = error
            raise
    return checked


class NativeProcessSession:
    """One inner worker, the same native sampler, and one actual kernel reaper.

    The frozen workflow receives this small Popen-shaped handle. It never sees
    the underlying Popen and therefore cannot race its poll/wait with our
    RootAnchor. Sampling, ownership, cleanup and wait evidence use the same
    implementation as the outer supervisor. No process-table shell command is
    used. Only observed descendants are covered; sampling is not a hard quota.
    """
    def __init__(self, limits, *, started_at=None):
        need(os.name == "posix" and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL,
             "SUPERVISOR_EXCLUSIVE_DEFAULT_SIGCHLD_REQUIRED")
        self.limits = dict(limits)
        self.started = time.monotonic() if started_at is None else started_at
        need(type(self.started) in (int, float) and math.isfinite(self.started)
             and 0 <= self.started <= time.monotonic(), "SUPERVISOR_INNER_START_INVALID")
        # Earlier monotonic work is charged too; a later suspend is additionally
        # detected by the real UTC clock. The outer bridge retains total UTC.
        from datetime import timedelta
        self.utc_started = datetime.now(timezone.utc) - timedelta(seconds=time.monotonic()-self.started)
        self.process = self.anchor = self.ownership = None
        self.meter = None
        self.samples = self.snapshot_bytes = self.peak_root = self.peak_tree = 0
        self.last_utc = self.last_mono = None
        self.max_utc_gap = self.max_mono_gap = 0.
        self.cleanup = None
        self.failure = None
        self._failure_error = None

    @property
    def pid(self):
        need(self.process is not None, "SUPERVISOR_INNER_WORKER_NOT_BOUND")
        return self.process.pid

    @property
    def returncode(self):
        return self.anchor.code if self.anchor is not None and self.anchor.reaped else None

    def bind(self, process):
        need(self.process is None, "SUPERVISOR_INNER_SECOND_WORKER_FORBIDDEN")
        self.process = process
        self.anchor = RootAnchor(process)
        try:
            root = _identity(psutil.Process(process.pid))
            self.anchor.identity = root
            need(psutil.Process(process.pid).ppid() == os.getpid()
                 and os.getpgid(process.pid) == process.pid and os.getsid(process.pid) == process.pid,
                 "SUPERVISOR_INNER_OWNED_NEW_SESSION_REQUIRED")
            self.ownership = Ownership(root, self.anchor)
        except BaseException:
            try:
                self.stop(self)
            except Exception:
                pass  # The original binding failure remains primary.
            raise
        return self

    def poll(self):
        need(self.anchor is not None, "SUPERVISOR_INNER_WORKER_NOT_BOUND")
        result = self.anchor.probe("INNER_POLL")
        if result == "UNKNOWN" and self.failure is None:
            self.failure = self.anchor.reason or "SUPERVISOR_INNER_WAIT_UNKNOWN"
            self._failure_error = SupervisorError(self.failure)
        return self.returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic()+timeout
        while self.poll() is None:
            need(self.failure is None, self.failure or "SUPERVISOR_INNER_WAIT_UNKNOWN")
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("OWNED_INNER_WORKER", timeout)
            self.check()
            time.sleep(.01)
        return self.returncode

    @_inner_sticky
    def check(self):
        if self._failure_error is not None:
            raise self._failure_error
        need(self.failure is None, self.failure or "SUPERVISOR_INNER_FAILED")
        utc_elapsed = (datetime.now(timezone.utc)-self.utc_started).total_seconds()
        need(utc_elapsed >= 0, "SUPERVISOR_UTC_CLOCK_MOVED_BACKWARD")
        limit = self.limits.get("max_seconds")
        need(type(limit) in (int, float) and math.isfinite(limit) and limit > 0,
             "SUPERVISOR_INNER_FINITE_TIME_LIMIT_REQUIRED")
        need(utc_elapsed <= limit, "SUPERVISOR_UTC_DEADLINE_EXCEEDED")
        need(time.monotonic()-self.started <= limit, "SUPERVISOR_MONOTONIC_DEADLINE_EXCEEDED")

    def _charge_snapshot(self, rows, unavailable, callback):
        count = len(canonical(dict(rows=rows, unavailable=unavailable)))
        self.snapshot_bytes += count
        need(callable(callback), "SUPERVISOR_INNER_READ_METER_REQUIRED")
        callback(count)

    @_inner_sticky
    def sample(self, pid, read_meter):
        need(pid == self.pid, "SUPERVISOR_INNER_PID_CHANGED")
        self.meter = read_meter
        self.check()
        rows, unavailable = _snapshot()
        self._charge_snapshot(rows, unavailable, read_meter)
        owned = self.ownership.observe(rows, unavailable)
        live = [row for row in owned if row["status"] not in DEAD]
        root_rss = max((row["rss"] for row in live if row["pid"] == pid), default=0)
        rss = sum(row["rss"] for row in live)
        self.peak_root, self.peak_tree = max(self.peak_root, root_rss), max(self.peak_tree, rss)
        utc, mono = datetime.now(timezone.utc), time.monotonic()
        if self.last_utc is not None:
            gap = (utc-self.last_utc).total_seconds()
            need(gap >= 0, "SUPERVISOR_UTC_CLOCK_MOVED_BACKWARD")
            self.max_utc_gap = max(self.max_utc_gap, gap)
            self.max_mono_gap = max(self.max_mono_gap, mono-self.last_mono)
        self.last_utc, self.last_mono = utc, mono
        self.samples += 1
        self.check()
        return rss

    @_inner_sticky
    def remaining(self, pid, read_meter):
        need(pid == self.pid and self.anchor.reaped, "SUPERVISOR_INNER_REAP_REQUIRED")
        self.meter = read_meter
        rows, unavailable = _snapshot()
        self._charge_snapshot(rows, unavailable, read_meter)
        owned = self.ownership.observe(rows, unavailable)
        # Even a zombie descendant must disappear before complete delivery.
        return [row["pid"] for row in owned if row["pid"] != pid]

    def stop(self, handle):
        need(handle is self, "SUPERVISOR_INNER_HANDLE_CHANGED")
        if self.process is None:
            return
        if self.cleanup is not None:
            need(self.cleanup["root_reaped"] and self.cleanup["all_observed_descendants_gone"]
                 and not self.cleanup["errors"], "SUPERVISOR_INNER_CLEANUP_UNVERIFIED")
            return
        def observed(rows, unavailable, owned):
            if owned is None:
                self._charge_snapshot(rows, unavailable, self.meter)
        self.cleanup = _cleanup(self.process, self.ownership, .5, 5., observed,
                                root_reap_seconds=5., anchor=self.anchor)
        need(self.cleanup["root_reaped"] and self.cleanup["all_observed_descendants_gone"]
             and not self.cleanup["errors"], "SUPERVISOR_INNER_CLEANUP_UNVERIFIED")

    def report(self):
        return dict(schema_version=VERSION, sampler="NATIVE_KERNEL_SNAPSHOT_V3", shell_sampling=False,
            identity_basis=IDENTITY_BASIS, sample_count=self.samples,
            process_snapshot_bytes=self.snapshot_bytes, observed_peak_root_rss_bytes=self.peak_root,
            observed_peak_tree_rss_bytes=self.peak_tree, max_utc_sample_gap_seconds=self.max_utc_gap,
            max_monotonic_sample_gap_seconds=self.max_mono_gap,
            root_identity=self.ownership.root if self.ownership else None,
            root_reaped=self.anchor.reaped if self.anchor else False,
            root_reap_evidence=self.anchor.evidence() if self.anchor else None,
            cleanup=self.cleanup, failure=self.failure,
            sampler_scope="SAMPLED_OBSERVED_DESCENDANTS_NOT_OS_HARD_QUOTA")

    def require_complete(self):
        self.check()
        need(self.anchor is not None and self.anchor.reaped and self.anchor.reason is None,
             "SUPERVISOR_INNER_KERNEL_REAP_UNVERIFIED")
        need(self.samples > 0 and self.peak_root > 0, "SUPERVISOR_LIVE_SAMPLING_COVERAGE_INSUFFICIENT")
        need(not self.ownership.present(), "SUPERVISOR_INNER_DESCENDANTS_REMAIN")
