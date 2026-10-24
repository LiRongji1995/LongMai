"""Bounded local execution support for the explicit standalone R2 workflow.

JSON job files describe work; they are not a business database or source
capability. The read meter counts returned Python file bytes and gzip.read
output. It deliberately makes no kernel-I/O or startup-import accounting claim.
"""
from __future__ import annotations

import builtins
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from threading import local
import time
from urllib.parse import urlsplit

VERSION = 'PREMARKET_EXECUTION_PROFILE_V1'
ATOMIC_CONTROL_PUBLICATION_VERSION = 'PREMARKET_ATOMIC_CONTROL_PUBLICATION_V2'
CONTROL_LOCK_WAIT_SECONDS = 30
CONTROL_LOCK_POLL_SECONDS = 0.05
GIB = 1024 ** 3
MIB = 1024 ** 2
FP_KEYS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns',
           'st_ctime_ns', 'st_flags')
DEFAULT_LIMITS = dict(capture_read_bytes=4*GIB, core_read_bytes=4*GIB,
    theme_read_bytes=4*GIB, presentation_read_bytes=GIB, supervisor_read_bytes=GIB,
    max_requests=512, max_sdk_calls=10000, max_http_attempts=32768,
    request_http_attempts=64, request_timeout_seconds=90,
    max_seconds=None, self_rss_bytes=int(2.5*GIB), tree_rss_bytes=3*GIB,
    output_bytes=1024*MIB, cache_bytes=8*GIB)
_ACTIVE_METER = None
_CONTROL_LOCKS = local()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def fingerprint(path):
    value = Path(path).lstat()
    if not stat.S_ISREG(value.st_mode) or getattr(value, 'st_flags', 0) & 0x40000000 \
            or value.st_size and getattr(value, 'st_blocks', 1) == 0:
        raise ValueError('RESIDENT_REGULAR_FILE_REQUIRED')
    return {key: int(getattr(value, key, 0)) for key in FP_KEYS}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(',', ':')).encode('utf-8')


def _control_cleanup_error(primary, phase, error):
    if primary is None:
        raise error
    primary.add_note('ATOMIC_CONTROL_' + phase + ': ' + repr(error))
    errors = getattr(primary, 'atomic_control_cleanup_errors', ())
    primary.atomic_control_cleanup_errors = (*errors, (phase, error))


class ControlPublicationLockTimeout(TimeoutError):
    pass


@contextmanager
def _windows_control_mutex(name):
    """A cross-process mutex; no lock files are added to existing state/cache."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create, wait = kernel.CreateMutexW, kernel.WaitForSingleObject
    release, close = kernel.ReleaseMutex, kernel.CloseHandle
    create.argtypes, create.restype = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR), wintypes.HANDLE
    wait.argtypes, wait.restype = (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD
    release.argtypes, release.restype = (wintypes.HANDLE,), wintypes.BOOL
    close.argtypes, close.restype = (wintypes.HANDLE,), wintypes.BOOL
    handle = create(None, False, 'Global\\StockFishAtomicControlV2-' + name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    primary, acquired = None, False
    try:
        deadline = time.monotonic() + CONTROL_LOCK_WAIT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControlPublicationLockTimeout('ATOMIC_CONTROL_LOCK_WAIT_TIMEOUT')
            result = wait(handle, max(1, min(50, int(remaining * 1000))))
            if result in (0, 0x80):
                break
            if result != 0x102:  # WAIT_TIMEOUT is a bounded poll, never success.
                raise OSError('ATOMIC_CONTROL_MUTEX_WAIT_FAILED:' + str(result))
        # WAIT_ABANDONED grants the mutex; it never proves a prior write succeeded.
        acquired = True
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        errors = []
        if acquired and not release(handle):
            errors.append(('MUTEX_RELEASE', ctypes.WinError(ctypes.get_last_error())))
        if not close(handle):
            errors.append(('MUTEX_CLOSE', ctypes.WinError(ctypes.get_last_error())))
        for phase, error in errors:
            if primary is None:
                primary = error
            else:
                _control_cleanup_error(primary, phase, error)
        if errors and primary is errors[0][1]:
            raise primary


@contextmanager
def _control_directory_lock(directory, *, exclusive):
    """Coordinate only control publication and one directory's metadata scan.

    Computing, source reads, recursive traversal and whole jobs are not locked.
    POSIX locks the directory inode itself. Windows uses a named mutex and
    retains its existing file-fsync + replace durability level.
    """
    directory = Path(directory).resolve(strict=True)
    before = directory.stat()
    if not stat.S_ISDIR(before.st_mode):
        raise NotADirectoryError(str(directory))
    identity = (before.st_dev, before.st_ino, before.st_mode)
    key = (os.path.normcase(str(directory)), *identity)
    owner_pid = os.getpid()
    active_key = (owner_pid, *key)
    active = getattr(_CONTROL_LOCKS, 'active', None)
    if active is None:
        active = _CONTROL_LOCKS.active = {}
    prior = active.get(active_key)
    if prior is not None:
        if exclusive and not prior['exclusive']:
            raise RuntimeError('ATOMIC_CONTROL_LOCK_UPGRADE_FORBIDDEN')
        yield prior['descriptor']
        return
    descriptor, primary, acquired = None, None, False
    if os.name == 'posix':
        import fcntl
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(directory, flags)
        try:
            deadline = time.monotonic() + CONTROL_LOCK_WAIT_SECONDS
            operation = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ControlPublicationLockTimeout('ATOMIC_CONTROL_LOCK_WAIT_TIMEOUT')
                try:
                    fcntl.flock(descriptor, operation)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(min(CONTROL_LOCK_POLL_SECONDS, remaining))
            opened = os.fstat(descriptor)
            current = directory.stat()
            if any((v.st_dev, v.st_ino, v.st_mode) != identity for v in (opened, current)):
                raise RuntimeError('ATOMIC_CONTROL_DIRECTORY_IDENTITY_CHANGED')
            active[active_key] = dict(descriptor=descriptor, exclusive=exclusive)
            yield descriptor
        except BaseException as error:
            primary = error
            raise
        finally:
            active.pop(active_key, None)
            operations = [('DIRECTORY_CLOSE', lambda: os.close(descriptor))]
            if acquired and os.getpid() == owner_pid:
                operations.insert(0, ('DIRECTORY_UNLOCK', lambda: fcntl.flock(descriptor, fcntl.LOCK_UN)))
            for phase, operation in operations:
                try:
                    operation()
                except BaseException as error:
                    if primary is None:
                        primary = error
                    else:
                        _control_cleanup_error(primary, phase, error)
            if primary is not None and sys.exc_info()[0] is None:
                raise primary
    elif os.name == 'nt':
        name = hashlib.sha256(json_bytes(key)).hexdigest()
        with _windows_control_mutex(name):
            current = directory.stat()
            if (current.st_dev, current.st_ino, current.st_mode) != identity:
                raise RuntimeError('ATOMIC_CONTROL_DIRECTORY_IDENTITY_CHANGED')
            active[active_key] = dict(descriptor=None, exclusive=exclusive)
            try:
                yield None
            finally:
                active.pop(active_key, None)
    else:
        raise RuntimeError('ATOMIC_CONTROL_DIRECTORY_LOCK_UNAVAILABLE')


def atomic_json(path, value):
    """Publish an owned control; never remove another writer's temporary file."""
    raw = json_bytes(value)
    path = Path(path)
    directory = path.parent.resolve(strict=True)
    target = directory / path.name
    temporary, owned_identity, primary = None, None, None
    with _control_directory_lock(directory, exclusive=True) as directory_fd:
        try:
            descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.writing-', dir=directory)
            temporary = Path(name)
            try:
                opened = os.fstat(descriptor)
                owned_identity = (opened.st_dev, opened.st_ino, opened.st_mode)
                handle = os.fdopen(descriptor, 'wb')
            except BaseException:
                os.close(descriptor)
                raise
            with handle:
                if handle.write(raw) != len(raw):
                    raise OSError('ATOMIC_CONTROL_SHORT_WRITE')
                handle.flush()
                os.fsync(handle.fileno())
                completed = os.fstat(handle.fileno())
            actual = temporary.lstat()
            if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                    or {key: getattr(actual, key, 0) for key in FP_KEYS}
                    != {key: getattr(completed, key, 0) for key in FP_KEYS}
                    or actual.st_size != len(raw)):
                raise RuntimeError('ATOMIC_CONTROL_TEMPORARY_IDENTITY_CHANGED')
            os.replace(temporary, target)
            temporary = None
            if directory_fd is not None:
                os.fsync(directory_fd)
        except BaseException as error:
            primary = error
            raise
        finally:
            if temporary is not None:
                try:
                    current = temporary.lstat()
                    if (current.st_dev, current.st_ino, current.st_mode) == owned_identity:
                        temporary.unlink()
                except FileNotFoundError:
                    pass  # Only this invocation's now-absent temporary is eligible.
                except BaseException as error:
                    _control_cleanup_error(primary, 'TEMPORARY_CLEANUP', error)


def directory_bytes(root):
    """Follow an explicit root alias, counting files without child links."""
    total = 0
    try:
        mode = os.stat(root).st_mode
    except FileNotFoundError:
        return 0
    if not stat.S_ISDIR(mode):
        return 0
    pending = [os.fspath(root)]
    while pending:
        directory = Path(pending.pop()).resolve(strict=True)
        with _control_directory_lock(directory, exclusive=False):
            with os.scandir(directory) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(entry.path)
                    else:
                        total += info.st_size
    return total


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Sticky per-phase limits. No phase can borrow another phase's budget."""
    def __init__(self, limits=None, *, job_dir=None, cache_dir=None):
        if limits is not None and not isinstance(limits, dict):
            raise ValueError('EXECUTION_LIMITS_MAPPING_REQUIRED')
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        if set(self.limits) != set(DEFAULT_LIMITS):
            raise ValueError('UNKNOWN_EXECUTION_LIMIT')
        for key, value in self.limits.items():
            if key == 'max_seconds' and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value <= 0:
                raise ValueError('FINITE_POSITIVE_EXECUTION_LIMIT_REQUIRED:' + key)
            if key not in ('max_seconds', 'request_timeout_seconds') and type(value) is not int:
                raise ValueError('INTEGER_EXECUTION_LIMIT_REQUIRED:' + key)
        if self.limits['request_timeout_seconds'] > 90:
            raise ValueError('PROVIDER_TIMEOUT_MUST_BE_0_TO_90_SECONDS')
        self.job_dir = Path(job_dir) if job_dir else None
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.started = time.monotonic()
        self.phase_name = 'capture'
        self._read = {key: 0 for key in ('capture', 'core', 'theme', 'presentation')}
        self._calls = dict(requests=0, sdk_reserved=0, http_reserved=0,
                           sdk_actual=0, http_actual=0, actual_unknown=0,
                           sdk_actual_unknown_requests=0, http_actual_unknown_requests=0,
                           direct_http_get_attempts=0)
        self._pending_requests = []
        self._failures = []
        self._meter_active = False
        self._last_check = 0.0
        self._last_metrics = 0.0
        self.peak_self_rss = 0

    @property
    def metrics(self):
        calls = dict(self._calls)
        for prefix in ('sdk', 'http'):
            calls[prefix + '_actual_known_subtotal'] = calls[prefix + '_actual']
            if calls[prefix + '_actual_unknown_requests']:
                calls[prefix + '_actual'] = None
        return dict(schema_version=VERSION, phase=self.phase_name,
            control_publication_version=ATOMIC_CONTROL_PUBLICATION_VERSION,
            control_parent_directory_fsync=os.name == 'posix',
            control_lock_wait_seconds=CONTROL_LOCK_WAIT_SECONDS,
            elapsed_seconds=round(time.monotonic()-self.started, 3),
            returned_read_bytes=dict(self._read), calls=calls,
            failures=list(self._failures), pending_request_results=len(self._pending_requests),
            peak_self_rss_bytes=self.peak_self_rss,
            accounting_scope='RETURNED_PYTHON_FILE_AND_EXPLICIT_HTTP_BODY_BYTES_AND_GZIP_READ_OUTPUT; '
                'TEXT_REENCODED_UTF8; NOT_ALL_KERNEL_IO_OR_STARTUP_IMPORTS; '
                'PROVIDER_SUBPROCESS_INTERNAL_IO_EXCLUDED; PARENT_PIPE_RETURNS_INCLUDED',
            limits=dict(self.limits))

    def _fail(self, reason):
        if reason not in self._failures:
            self._failures.append(reason)
        raise BudgetExceeded(reason)

    def charge_read(self, count, stage=None):
        if type(count) is not int or count < 0:
            self._fail('INVALID_READ_CHARGE')
        phase = stage or self.phase_name
        if phase not in self._read:
            self._fail('UNKNOWN_READ_PHASE')
        if phase != self.phase_name:
            self._fail('READ_PHASE_BORROW_FORBIDDEN')
        self._read[phase] += count
        if self._read[phase] > self.limits[phase+'_read_bytes']:
            self._fail('READ_LIMIT_EXCEEDED:' + phase)
        self.check(phase)

    def check(self, stage, *, force=False):
        if self._failures:
            raise BudgetExceeded(self._failures[0])
        now = time.monotonic()
        if self.limits['max_seconds'] is not None and now-self.started > self.limits['max_seconds']:
            self._fail('TOTAL_TIME_LIMIT_EXCEEDED')
        if os.name == 'posix':
            import resource
            raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            self.peak_self_rss = max(self.peak_self_rss,
                int(raw if sys.platform == 'darwin' else raw*1024))
            if self.peak_self_rss > self.limits['self_rss_bytes']:
                self._fail('SELF_RSS_LIMIT_EXCEEDED')
        elif os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class MemoryCounters(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                    'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
            value = MemoryCounters()
            value.cb = ctypes.sizeof(value)
            get_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
            get_info.restype = wintypes.BOOL
            if not get_info(wintypes.HANDLE(-1), ctypes.byref(value), value.cb):
                self._fail('SELF_RSS_OBSERVATION_UNAVAILABLE')
            self.peak_self_rss = max(self.peak_self_rss, int(value.PeakWorkingSetSize))
            if self.peak_self_rss > self.limits['self_rss_bytes']:
                self._fail('SELF_RSS_LIMIT_EXCEEDED')
        else:
            self._fail('SELF_RSS_OBSERVATION_UNAVAILABLE')
        if not force and now-self._last_check < .5:
            return
        if self.job_dir and directory_bytes(self.job_dir) > self.limits['output_bytes']:
            self._fail('OUTPUT_LIMIT_EXCEEDED')
        if self.cache_dir and self.phase_name == 'capture' \
                and directory_bytes(self.cache_dir) > self.limits['cache_bytes']:
            self._fail('CACHE_SIZE_LIMIT_EXCEEDED')
        if self.job_dir and now-self._last_metrics >= 2:
            self._last_metrics = now
            atomic_json(self.job_dir/'metrics.json', self.metrics)
        # A slow full-tree scan must not make the next read immediately scan
        # again. Time/RSS/read/request guards above still run on every check.
        self._last_check = time.monotonic()

    def read(self, path, expected=None, limit=None):
        target = Path(path).resolve(strict=True)
        if isinstance(expected, dict):
            if set(expected) != {'path', 'sha256', 'bytes', 'fp'} or \
                    not isinstance(expected['path'], str) or not Path(expected['path']).is_absolute() or \
                    expected['path'] != str(target) or not isinstance(expected['fp'], dict) or \
                    set(expected['fp']) != set(FP_KEYS) or \
                    any(type(value) is not int for value in expected['fp'].values()) or \
                    type(expected['bytes']) is not int or expected['bytes'] < 0 or \
                    expected['fp']['st_size'] != expected['bytes']:
                self._fail('INVALID_PHYSICAL4_REFERENCE')
            declared_sha = expected['sha256']
        else:
            if expected is not None and not isinstance(expected, str):
                self._fail('INVALID_FILE_REFERENCE')
            declared_sha = expected
        if declared_sha is not None and (not isinstance(declared_sha, str) or
                re.fullmatch('[0-9a-f]{64}', declared_sha) is None):
            self._fail('INVALID_SHA256_REFERENCE')
        if limit is not None and (type(limit) is not int or limit < 0):
            self._fail('INVALID_FILE_READ_LIMIT')
        try:
            initial = fingerprint(target)
        except ValueError:
            self._fail('RESIDENT_REGULAR_FILE_REQUIRED')
        if limit is not None and initial['st_size'] > limit:
            self._fail('FILE_READ_LIMIT_EXCEEDED')
        pieces = []
        with target.open('rb') as handle:
            while piece := handle.read(65536):
                if not self._meter_active:
                    self.charge_read(len(piece))
                pieces.append(piece)
        raw = b''.join(pieces)
        try:
            final = fingerprint(target)
        except (OSError, ValueError):
            self._fail('FILE_CHANGED_DURING_READ')
        ref = dict(path=str(target), sha256=hashlib.sha256(raw).hexdigest(),
                   bytes=len(raw), fp=final)
        if initial != ref['fp'] or len(raw) != initial['st_size']:
            self._fail('FILE_CHANGED_DURING_READ')
        if isinstance(expected, str) and ref['sha256'] != expected:
            self._fail('FILE_SHA256_CHANGED')
        if isinstance(expected, dict) and ref != expected:
            self._fail('FILE_REFERENCE_CHANGED')
        self.check('read')
        return raw, ref

    def write_json(self, path, value):
        target = Path(path)
        with target.open('xb') as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        self.check('write')
        return self.read(target)[1]

    def preclaim_request(self, endpoint, params):
        self.check('request_admission')
        from .provider_requests import ENDPOINTS
        if endpoint not in ENDPOINTS or not isinstance(params, dict):
            self._fail('INVALID_REQUEST_ADMISSION')
        provider = ENDPOINTS[endpoint]['provider']
        sdk = (len(params.get('codes', ()))+2 if endpoint in
            ('adjustment_factors', 'stock_basic', 'history', 'history_adjusted') else 3) \
            if provider == 'baostock' else 0
        http = self.limits['request_http_attempts'] if provider != 'baostock' else 0
        proposed = dict(requests=self._calls['requests']+1,
            sdk_reserved=self._calls['sdk_reserved']+sdk,
            http_reserved=self._calls['http_reserved']+http)
        for key, maximum in (('requests','max_requests'),('sdk_reserved','max_sdk_calls'),
                             ('http_reserved','max_http_attempts')):
            if proposed[key] > self.limits[maximum]:
                self._fail('REQUEST_ADMISSION_LIMIT_EXCEEDED:' + key)
        self._calls.update(proposed)
        self._pending_requests.append(dict(kind='provider', endpoint=endpoint, sdk_reserved=sdk, http_reserved=http))
        self.check('request_admission')
        return dict(endpoint=endpoint, sdk_reserved=sdk, http_reserved=http)

    def record_request(self, result):
        # Unreported provider counts remain unknown; reservations are not actuals.
        if not self._pending_requests or not isinstance(result, dict):
            self._fail('REQUEST_RESULT_WITHOUT_ADMISSION')
        admission = self._pending_requests.pop(0)
        if admission.get('kind') != 'provider':
            self._fail('PROVIDER_RESULT_CANNOT_CONSUME_HTTP_ADMISSION')
        if 'endpoint' in result and result['endpoint'] != admission['endpoint']:
            self._fail('REQUEST_RESULT_ENDPOINT_CHANGED')
        coverage = result.get('coverage')
        coverage = coverage if isinstance(coverage, dict) else {}
        for key, name, reserved in (('sdk_actual', 'sdk_operations', 'sdk_reserved'),
                                    ('http_actual', 'http_attempts', 'http_reserved')):
            value = coverage.get(name)
            if type(value) is int and value >= 0:
                if value > admission[reserved]:
                    self._fail('ACTUAL_REQUEST_EXCEEDED_RESERVATION:' + name)
                self._calls[key] += value
            else:
                self._calls['actual_unknown'] += 1
                self._calls[key + '_unknown_requests'] += 1
        self.check('request_complete')

    def preclaim_http_request(self, url, *, max_attempts=1):
        """Reserve a direct HTTP operation without inventing a provider endpoint.

        Attempts count admitted urllib GET invocations, including connection
        failures and redirects, not confirmed wire sends or server arrivals.
        """
        self.check('http_admission')
        parsed = urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme != 'https' or not parsed.hostname or \
                parsed.username is not None or parsed.password is not None or \
                type(max_attempts) is not int or not 0 < max_attempts <= self.limits['request_http_attempts']:
            self._fail('INVALID_DIRECT_HTTP_ADMISSION')
        proposed_requests = self._calls['requests'] + 1
        proposed_http = self._calls['http_reserved'] + max_attempts
        if proposed_requests > self.limits['max_requests'] or proposed_http > self.limits['max_http_attempts']:
            self._fail('DIRECT_HTTP_ADMISSION_LIMIT_EXCEEDED')
        admission = dict(kind='direct_http', url=url, sdk_reserved=0,
                         http_reserved=max_attempts)
        self._calls.update(requests=proposed_requests, http_reserved=proposed_http)
        self._pending_requests.append(admission)
        return admission

    def record_http_request(self, admission, *, attempts):
        if not self._pending_requests or self._pending_requests[0] is not admission or \
                admission.get('kind') != 'direct_http':
            self._fail('DIRECT_HTTP_LIVE_ADMISSION_REQUIRED')
        if type(attempts) is not int or not 0 <= attempts <= admission['http_reserved']:
            self._fail('ACTUAL_DIRECT_HTTP_EXCEEDED_RESERVATION')
        self._pending_requests.pop(0)
        self._calls['http_actual'] += attempts
        self._calls['direct_http_get_attempts'] += attempts
        self.check('http_complete')

    @contextmanager
    def phase(self, name):
        if name not in self._read:
            raise ValueError('UNKNOWN_EXECUTION_PHASE')
        previous, self.phase_name = self.phase_name, name
        try:
            self.check(name, force=True)
            try:
                yield self
            except BaseException:
                try:
                    self.check(name, force=True)
                except BaseException as error:
                    # Preserve the primary exception while keeping a failed
                    # exit check sticky, even for an unavailable filesystem.
                    if not self._failures:
                        self._failures.append('PHASE_EXIT_CHECK_FAILED:' + type(error).__name__)
                raise
            else:
                self.check(name, force=True)
        finally:
            self.phase_name = previous

    @contextmanager
    def meter_files(self):
        """Owned scope; restore every patch even after an exception."""
        global _ACTIVE_METER
        if self._meter_active or _ACTIVE_METER is not None:
            self._fail('READ_METER_ALREADY_ACTIVE')
        old_builtin, old_io = builtins.open, io.open
        old_read, old_pread = os.read, getattr(os, 'pread', None)
        names = ('read', 'read1', 'readline', 'readlines', 'readinto', 'readinto1', 'peek', '__next__')
        originals = {name: (name in gzip.GzipFile.__dict__, getattr(gzip.GzipFile, name))
                     for name in names if hasattr(gzip.GzipFile, name)}
        budget = self

        def charge(value):
            budget.charge_read(len(value.encode('utf-8')) if isinstance(value, str) else len(value))
            return value

        class Handle:
            def __init__(self, handle): self.handle = handle
            def __getattr__(self, name): return getattr(self.handle, name)
            def __enter__(self): self.handle.__enter__(); return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def __iter__(self): return self
            def __next__(self): return charge(next(self.handle))
            def read(self, *args, **kw): return charge(self.handle.read(*args, **kw))
            def read1(self, *args, **kw): return charge(self.handle.read1(*args, **kw))
            def readline(self, *args, **kw): return charge(self.handle.readline(*args, **kw))
            def readlines(self, *args, **kw):
                values = self.handle.readlines(*args, **kw)
                for value in values: charge(value)
                return values
            def readinto(self, *args, **kw):
                count = self.handle.readinto(*args, **kw)
                if count is not None: budget.charge_read(count)
                return count
            def readinto1(self, *args, **kw):
                count = self.handle.readinto1(*args, **kw)
                if count is not None: budget.charge_read(count)
                return count

        def opener(file, mode='r', *args, **kw):
            handle = old_io(file, mode, *args, **kw)
            return Handle(handle) if 'r' in mode or '+' in mode else handle

        # Gzip methods can call each other. Count a returned logical buffer
        # once at the outermost gzip operation while still counting the real
        # compressed file reads made by Handle. BytesIO has no physical read.
        depths, wrappers = {}, {}
        for name, (_, method) in originals.items():
            def logical(handle, *args, _name=name, _method=method, **kw):
                key = id(handle)
                depth = depths.get(key, 0)
                depths[key] = depth + 1
                try:
                    value = _method(handle, *args, **kw)
                finally:
                    if depth:
                        depths[key] = depth
                    else:
                        depths.pop(key, None)
                if not depth:
                    if _name in ('readinto', 'readinto1'):
                        if value is not None:
                            budget.charge_read(value)
                    elif _name == 'readlines':
                        for line in value:
                            charge(line)
                    else:
                        charge(value)
                return value
            wrappers[name] = logical
        def read_fd(*args, **kw): return charge(old_read(*args, **kw))
        def pread_fd(*args, **kw): return charge(old_pread(*args, **kw))
        self._meter_active = True
        _ACTIVE_METER = self
        builtins.open = io.open = opener
        for name, method in wrappers.items():
            setattr(gzip.GzipFile, name, method)
        os.read = read_fd
        if old_pread is not None: os.pread = pread_fd
        try:
            yield self
            self.check('meter_complete')
        finally:
            primary_error = sys.exc_info()[0] is not None
            changed = builtins.open is not opener or io.open is not opener \
                or any(getattr(gzip.GzipFile, name, None) is not method
                       for name, method in wrappers.items()) or os.read is not read_fd \
                or old_pread is not None and os.pread is not pread_fd or _ACTIVE_METER is not self
            builtins.open, io.open = old_builtin, old_io
            for name, (owned, method) in originals.items():
                if owned:
                    setattr(gzip.GzipFile, name, method)
                elif name in gzip.GzipFile.__dict__:
                    delattr(gzip.GzipFile, name)
            os.read = old_read
            if old_pread is not None: os.pread = old_pread
            self._meter_active = False
            _ACTIVE_METER = None
            if changed:
                if 'READ_METER_SCOPE_CHANGED' not in self._failures:
                    self._failures.append('READ_METER_SCOPE_CHANGED')
                if not primary_error:
                    raise BudgetExceeded('READ_METER_SCOPE_CHANGED')


class JobLock:
    """OS-owned exclusive lock; a crashed process does not leave a false owner."""
    def __init__(self, path): self.path, self.handle = Path(path), None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+b')
        try:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                self.handle.seek(0); self.handle.write(b' '); self.handle.flush()
                self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            raise RuntimeError('PREMARKET_CACHE_ALREADY_OWNED') from None
        self.handle.seek(0); self.handle.truncate()
        self.handle.write(json_bytes(dict(pid=os.getpid(), started_at=utc_now())))
        self.handle.flush()
        return self
    def __exit__(self, *args):
        if self.handle:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            else:
                import msvcrt
                self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
