"""Independent TEST_FIXTURE runtime limits; no provider or business computation."""
import builtins
from copy import deepcopy
import gzip
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.yaogu import premarket_runtime_v1 as runtime


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='premarket-runtime-TEST_FIXTURE-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'TEST_FIXTURE.bin'
        self.payload = b'first line\nsecond line\n'
        self.path.write_bytes(self.payload)

    def total(self, budget):
        return sum(budget.metrics['returned_read_bytes'].values())

    def test_directory_sum_tracks_growth_without_following_any_links(self):
        tree, outside = self.root/'tree', self.root/'outside'
        (tree/'nested').mkdir(parents=True)
        outside.mkdir()
        (tree/'a').write_bytes(b'1234')
        (tree/'nested'/'b').write_bytes(b'1234567')
        (outside/'large').write_bytes(b'x'*9000)
        (tree/'file-link').symlink_to(outside/'large')
        (tree/'directory-link').symlink_to(outside, target_is_directory=True)
        (tree/'cycle-link').symlink_to(tree, target_is_directory=True)
        (tree/'dangling-link').symlink_to(outside/'missing')
        self.assertEqual(runtime.directory_bytes(tree), 11)
        with (tree/'a').open('ab') as handle:
            handle.write(b'56')
        self.assertEqual(runtime.directory_bytes(tree), 13)
        self.assertEqual(runtime.directory_bytes(self.root/'missing'), 0)
        root_link = self.root/'root-link'
        root_link.symlink_to(tree, target_is_directory=True)
        self.assertEqual(runtime.directory_bytes(root_link), 13)
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'CACHE_SIZE_LIMIT'):
            with runtime.Budget({'cache_bytes':12}, cache_dir=root_link).phase('capture'):
                self.fail('root alias must not bypass the cache budget')

    def test_slow_full_scan_is_throttled_from_completion_not_each_read(self):
        clock, scans = [10.0], []
        def slow_scan(path):
            scans.append(path)
            clock[0] += .75
            return 0
        with patch.object(runtime.time, 'monotonic', side_effect=lambda:clock[0]), \
                patch.object(runtime, 'directory_bytes', side_effect=slow_scan), \
                patch.object(runtime, 'atomic_json'):
            budget = runtime.Budget(job_dir=self.root/'job', cache_dir=self.root/'cache')
            budget.check('initial', force=True)
            self.assertEqual(len(scans), 2)
            for _ in range(50):
                budget.charge_read(1)
            self.assertEqual(len(scans), 2)
            clock[0] += .49
            budget.check('still_throttled')
            self.assertEqual(len(scans), 2)
            clock[0] += .02
            budget.check('next_period')
            self.assertEqual(len(scans), 4)
            self.assertEqual(self.total(budget), 50)

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-anchored directory scan')
    def test_scan_resolves_only_root_and_rechecks_growth_without_parent_mtime_change(self):
        tree = self.root/'tree'
        (tree/'a'/'b').mkdir(parents=True)
        target = tree/'a'/'b'/'payload'
        target.write_bytes(b'old')
        original, resolved = Path.resolve, []
        def resolve(path, *args, **kwargs):
            resolved.append(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve):
            self.assertEqual(runtime.directory_bytes(tree), 3)
        self.assertEqual(resolved, [tree])
        parent_clock = target.parent.stat().st_mtime_ns
        with target.open('ab') as handle:
            handle.write(b'x'*5000)
        self.assertEqual(target.parent.stat().st_mtime_ns, parent_clock)
        self.assertEqual(runtime.directory_bytes(tree), 5003)
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'CACHE_SIZE_LIMIT_EXCEEDED'):
            runtime.Budget({'cache_bytes': 4096}, cache_dir=tree).check('grown', force=True)

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-anchored directory scan')
    def test_child_replaced_after_enumeration_never_redirects_or_undercounts(self):
        for replacement in ('directory', 'symlink'):
            with self.subTest(replacement=replacement):
                tree = self.root/replacement
                child, outside = tree/'child', self.root/(replacement+'-outside')
                child.mkdir(parents=True); outside.mkdir()
                (child/'payload').write_bytes(b'x'*5000)
                (outside/'foreign').write_bytes(b'foreign'*1000)
                original, changed = os.open, []
                def open_directory(path, flags, *args, **kwargs):
                    if path == 'child' and kwargs.get('dir_fd') is not None and not changed:
                        child.rename(tree/'retained-child'); changed.append(True)
                        if replacement == 'symlink': child.symlink_to(outside, target_is_directory=True)
                        else: child.mkdir()
                    return original(path, flags, *args, **kwargs)
                with patch.object(runtime.os, 'open', side_effect=open_directory):
                    with self.assertRaises((OSError, RuntimeError)):
                        runtime.directory_bytes(tree)
                self.assertEqual(changed, [True])
                self.assertEqual((tree/'retained-child'/'payload').stat().st_size, 5000)
                self.assertEqual((outside/'foreign').read_bytes(), b'foreign'*1000)

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-anchored directory scan')
    def test_ancestor_replaced_during_descendant_scan_is_rejected_without_following_link(self):
        tree, outside = self.root/'tree', self.root/'outside'
        child = tree/'a'/'b'
        child.mkdir(parents=True); (outside/'b').mkdir(parents=True)
        (child/'own').write_bytes(b'own')
        (outside/'b'/'foreign').write_bytes(b'foreign'*1000)
        own_inode, foreign_inode = child.stat().st_ino, (outside/'b').stat().st_ino
        original, visited, changed = os.scandir, [], []
        def scandir(descriptor):
            inode = os.fstat(descriptor).st_ino
            visited.append(inode)
            if inode == own_inode and not changed:
                (tree/'a').rename(tree/'retained-a')
                (tree/'a').symlink_to(outside, target_is_directory=True)
                changed.append(True)
            return original(descriptor)
        with patch.object(runtime.os, 'scandir', side_effect=scandir):
            with self.assertRaisesRegex(RuntimeError, 'DIRECTORY_IDENTITY_CHANGED'):
                runtime.directory_bytes(tree)
        self.assertEqual(changed, [True])
        self.assertNotIn(foreign_inode, visited)

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-anchored directory scan')
    def test_scan_error_closes_held_ancestor_descriptors(self):
        tree = self.root/'tree'; (tree/'child').mkdir(parents=True)
        original_open, original_scandir = os.open, os.scandir
        descriptors, child_inode = [], (tree/'child').stat().st_ino
        def opened(*args, **kwargs):
            descriptor = original_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor
        def scandir(descriptor):
            if os.fstat(descriptor).st_ino == child_inode:
                raise OSError('TEST_FIXTURE interrupted child scan')
            return original_scandir(descriptor)
        with patch.object(runtime.os, 'open', side_effect=opened), \
                patch.object(runtime.os, 'scandir', side_effect=scandir):
            with self.assertRaisesRegex(OSError, 'interrupted child scan'):
                runtime.directory_bytes(tree)
        self.assertEqual(len(descriptors), 2)
        for descriptor in descriptors:
            with self.assertRaises(OSError): os.fstat(descriptor)
        self.assertEqual(runtime.directory_bytes(tree), 0)

    def test_phase_entry_and_exit_force_complete_growth_checks(self):
        for scope in ('job', 'cache'):
            for boundary in ('entry', 'exit'):
                with self.subTest(scope=scope, boundary=boundary):
                    folder = self.root/(scope+'-'+boundary)
                    job, cache = folder/'job', folder/'cache'
                    job.mkdir(parents=True); cache.mkdir()
                    target = (job if scope=='job' else cache)/'TEST_FIXTURE_growth'
                    with patch.object(runtime.time, 'monotonic', return_value=100.0):
                        budget = runtime.Budget({'output_bytes':4096,'cache_bytes':4096},
                            job_dir=job, cache_dir=cache)
                        if boundary=='entry':
                            target.write_bytes(b'x'*5000)
                        entered = False
                        reason = 'OUTPUT_LIMIT' if scope=='job' else 'CACHE_SIZE_LIMIT'
                        with self.assertRaisesRegex(runtime.BudgetExceeded, reason):
                            with budget.phase('capture'):
                                entered = True
                                target.write_bytes(b'x'*5000)
                        self.assertEqual(entered, boundary=='exit')
                        self.assertEqual(budget.phase_name, 'capture')
                        with self.assertRaisesRegex(runtime.BudgetExceeded, reason):
                            budget.check('caught_limit_stays_sticky')

    def test_phase_exit_keeps_primary_error_and_failed_check_sticky(self):
        cache = self.root/'cache'
        cache.mkdir()
        budget = runtime.Budget({'cache_bytes':32}, cache_dir=cache)
        with self.assertRaisesRegex(LookupError, 'TEST_FIXTURE_PRIMARY'):
            with budget.phase('capture'):
                (cache/'growth').write_bytes(b'x'*64)
                raise LookupError('TEST_FIXTURE_PRIMARY')
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'CACHE_SIZE_LIMIT'):
            budget.check('primary_caught_cannot_hide_exit_budget')
        other = runtime.Budget(cache_dir=cache)
        with patch.object(runtime, 'directory_bytes', side_effect=[0, OSError('TEST_FIXTURE_FS')]):
            with self.assertRaisesRegex(LookupError, 'TEST_FIXTURE_PRIMARY'):
                with other.phase('capture'):
                    raise LookupError('TEST_FIXTURE_PRIMARY')
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'PHASE_EXIT_CHECK_FAILED:OSError'):
            other.check('primary_caught_cannot_hide_unavailable_exit_check')

    def test_legacy_fingerprint_is_metadata_and_content_is_enforced(self):
        _, reference = runtime.Budget().read(self.path)
        self.assertEqual(set(reference), {'path', 'sha256', 'bytes', 'fp'})
        for key in set(runtime.FP_KEYS) - {'st_size'}:
            copied = deepcopy(reference)
            copied['fp'][key] += 1
            with self.subTest(key=key):
                raw, _ = runtime.Budget().read(self.path, expected=copied)
                self.assertEqual(raw, self.payload)
        replacement = self.path.with_suffix('.replacement')
        replacement.write_bytes(self.payload)
        os.replace(replacement, self.path)
        raw, _ = runtime.Budget().read(self.path, expected=reference)
        self.assertEqual(raw, self.payload)
        self.path.write_bytes(b'X' + self.payload[1:])
        budget = runtime.Budget()
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'SHA256|REFERENCE'):
            budget.read(self.path, expected=reference)
        with self.assertRaises(runtime.BudgetExceeded):
            budget.check('content_failure_stays_sticky')
        for mutate in (lambda r: r['fp'].pop('st_flags'),
                       lambda r: r['fp'].update(extra=1),
                       lambda r: r.pop('fp'),
                       lambda r: r.update(extra='not-a-file-reference')):
            bad = deepcopy(reference)
            mutate(bad)
            with self.assertRaisesRegex(runtime.BudgetExceeded, 'REFERENCE'):
                runtime.Budget().read(self.path, expected=bad)

    def test_unknown_negative_nonfinite_and_noninteger_budgets_rejected(self):
        for limits in ({'unknown': 1}, {'theme_read_bytes': -1}, {'max_requests': 0},
                       {'max_requests': True}, {'capture_read_bytes': 1.5},
                       {'max_seconds': float('nan')}, {'max_seconds': -1},
                       {'request_timeout_seconds': 91}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                runtime.Budget(limits)
        self.assertIsNone(runtime.Budget().limits['max_seconds'])
        self.assertEqual(runtime.Budget().limits['theme_read_bytes'], 4 * runtime.GIB)

    def test_budget_read_inside_meter_is_not_counted_twice(self):
        budget = runtime.Budget()
        with budget.meter_files():
            raw, _ = budget.read(self.path)
        self.assertEqual(raw, self.payload)
        self.assertEqual(self.total(budget), len(self.payload))

    def test_gzip_physical_and_logical_are_distinct_and_counted_once(self):
        compressed = gzip.compress(self.payload, mtime=0)
        path = self.root / 'TEST_FIXTURE.gz'
        path.write_bytes(compressed)
        budget = runtime.Budget()
        with budget.meter_files():
            raw, _ = budget.read(path)
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
                decoded = handle.read()
        self.assertEqual(decoded, self.payload)
        self.assertEqual(self.total(budget), len(compressed) + len(self.payload))
        direct = runtime.Budget()
        with direct.meter_files():
            with gzip.open(path, 'rb') as handle:
                self.assertEqual(handle.read(), self.payload)
        self.assertEqual(self.total(direct), len(compressed) + len(self.payload))

    def test_gzip_read1_lines_and_readinto_returned_bytes_are_metered(self):
        compressed = gzip.compress(self.payload, mtime=0)
        for method in ('read1', 'readline', 'readlines', 'readinto', 'readinto1', 'iterator'):
            if method not in ('iterator',) and not hasattr(gzip.GzipFile, method):
                continue
            budget = runtime.Budget()
            with self.subTest(method=method), budget.meter_files():
                with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as handle:
                    if method in ('read1', 'readline'):
                        returned = []
                        while data := getattr(handle, method)(4):
                            returned.append(data)
                        value = b''.join(returned)
                    elif method in ('readinto', 'readinto1'):
                        returned = []
                        buffer = bytearray(4)
                        while count := getattr(handle, method)(buffer):
                            returned.append(bytes(buffer[:count]))
                        value = b''.join(returned)
                    elif method == 'readlines':
                        value = b''.join(handle.readlines())
                    else:
                        value = b''.join(handle)
            self.assertEqual(value, self.payload)
            self.assertEqual(self.total(budget), len(self.payload))

    def test_integer_descriptors_handle_methods_and_pread(self):
        budget = runtime.Budget()
        fd = os.open(self.path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with budget.meter_files():
            with open(fd, 'rb', closefd=False) as handle:
                self.assertEqual(handle.fileno(), fd)
                self.assertTrue(handle.readable())
                first = handle.read(3)
                handle.seek(0)
                lines = handle.readlines()
                handle.seek(0)
                buffer = bytearray(4)
                count = handle.readinto(buffer)
                self.assertEqual(handle.tell(), count)
            os.lseek(fd, 0, os.SEEK_SET)
            extra = os.read(fd, 2)
            positioned = os.pread(fd, 2, 0) if hasattr(os, 'pread') else b''
        self.assertEqual(self.total(budget), len(first) + sum(map(len, lines)) + count + len(extra) + len(positioned))
        os.fstat(fd)  # closefd=False was preserved by the Handle.

    def test_scope_exception_restores_exact_patches_and_preserves_primary_error(self):
        before = (builtins.open, io.open, os.read, getattr(os, 'pread', None), dict(gzip.GzipFile.__dict__))
        budget = runtime.Budget()
        with self.assertRaisesRegex(LookupError, 'TEST_FIXTURE_PRIMARY'):
            with budget.meter_files():
                budget.read(self.path)
                gzip.GzipFile.read = lambda *_: b'not-used'
                raise LookupError('TEST_FIXTURE_PRIMARY')
        after = (builtins.open, io.open, os.read, getattr(os, 'pread', None), dict(gzip.GzipFile.__dict__))
        self.assertEqual(after, before)
        self.assertFalse(budget._meter_active)
        self.assertIsNone(runtime._ACTIVE_METER)
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'SCOPE_CHANGED'):
            budget.check('cannot_ignore_patch_tampering')

    def test_caught_limit_and_reentered_phase_cannot_borrow_or_reset(self):
        budget = runtime.Budget({'capture_read_bytes': 10, 'core_read_bytes': 100})
        budget.charge_read(8)
        with budget.phase('core'):
            budget.charge_read(90)
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'capture'):
            budget.charge_read(3)
        self.assertEqual(budget.metrics['returned_read_bytes']['core'], 90)
        with self.assertRaises(runtime.BudgetExceeded):
            with budget.phase('theme'):
                pass
        other = runtime.Budget()
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'BORROW'):
            other.charge_read(1, stage='theme')

    def test_reservation_is_not_actual_and_real_coverage_counters_only(self):
        budget = runtime.Budget()
        claim = budget.preclaim_request('calendar', {'start_date': '2026-01-01', 'end_date': '2026-01-02'})
        self.assertEqual(claim['sdk_reserved'], 3)
        budget.record_request(dict(endpoint='calendar', coverage={'sdk_operations': 2, 'http_attempts': 0}))
        self.assertEqual(budget.metrics['calls']['sdk_actual'], 2)
        self.assertEqual(budget.metrics['calls']['sdk_reserved'], 3)
        budget.preclaim_request('calendar', {})
        budget.record_request(dict(endpoint='calendar', sdk_calls=99, http_attempts=99, coverage={}))
        metrics = budget.metrics['calls']
        self.assertIsNone(metrics['sdk_actual'])
        self.assertIsNone(metrics['http_actual'])
        self.assertEqual(metrics['sdk_actual_known_subtotal'], 2)
        self.assertEqual(metrics['sdk_actual_unknown_requests'], 1)
        self.assertEqual(metrics['http_actual_unknown_requests'], 1)

    def test_request_admission_and_actual_overflow_stay_failed(self):
        budget = runtime.Budget({'max_requests': 1})
        budget.preclaim_request('calendar', {})
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'ADMISSION'):
            budget.preclaim_request('calendar', {})
        with self.assertRaises(runtime.BudgetExceeded):
            budget.check('caught_admission')
        other = runtime.Budget()
        other.preclaim_request('calendar', {})
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'RESERVATION'):
            other.record_request(dict(coverage={'sdk_operations': 4, 'http_attempts': 0}))

    def test_direct_http_admission_is_separate_and_requires_live_result_handle(self):
        budget = runtime.Budget()
        admission = budget.preclaim_http_request('https://www.cls.cn/', max_attempts=1)
        budget.record_http_request(admission, attempts=1)
        self.assertEqual(budget.metrics['calls']['http_reserved'], 1)
        self.assertEqual(budget.metrics['calls']['http_actual'], 1)
        self.assertEqual(budget.metrics['calls']['sdk_reserved'], 0)
        self.assertEqual(budget.metrics['calls']['direct_http_get_attempts'], 1)
        self.assertEqual(budget.metrics['pending_request_results'], 0)
        other = runtime.Budget()
        live = other.preclaim_http_request('https://www.stcn.com/')
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'LIVE_ADMISSION'):
            other.record_http_request(dict(live), attempts=1)
        self.assertEqual(runtime.DEFAULT_LIMITS['supervisor_read_bytes'], runtime.GIB)
        self.assertNotIn('supervisor', budget.metrics['returned_read_bytes'])

    def test_os_exclusive_lock_is_released_by_close(self):
        lock = self.root / 'TEST_FIXTURE.lock'
        code = ('from stockfish_core.yaogu.premarket_runtime_v1 import JobLock\n'
                'import sys\n'
                'try:\n'
                '    with JobLock(sys.argv[1]): pass\n'
                'except RuntimeError:\n'
                '    raise SystemExit(7)\n')
        with runtime.JobLock(lock):
            blocked = subprocess.run([sys.executable, '-B', '-c', code, str(lock)],
                                     capture_output=True, text=True, timeout=10)
        self.assertEqual(blocked.returncode, 7, blocked.stderr)
        available = subprocess.run([sys.executable, '-B', '-c', code, str(lock)],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(available.returncode, 0, available.stderr)


# Original SOURCE04 helpers, retained as deterministic failure witnesses.
def _legacy_atomic_json(path, value):
    target = Path(path)
    temporary = target.with_name(target.name + '.writing')
    with temporary.open('xb') as handle:
        handle.write(runtime.json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def _legacy_directory_bytes(root):
    total = 0
    try:
        mode = os.stat(root).st_mode
    except FileNotFoundError:
        return 0
    if not runtime.stat.S_ISDIR(mode):
        return 0
    pending = [os.fspath(root)]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if runtime.stat.S_ISLNK(info.st_mode):
                    continue
                if runtime.stat.S_ISDIR(info.st_mode):
                    pending.append(entry.path)
                else:
                    total += info.st_size
    return total


class AtomicControlFixture(unittest.TestCase):
    """TEST_FIXTURE control files only; no providers, source captures or DB."""
    def setUp(self):
        import threading
        self.threading = threading
        self.tmp = tempfile.TemporaryDirectory(prefix='atomic-control-TEST_FIXTURE-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root/'metrics.json'
        self.old = runtime.json_bytes({'TEST_FIXTURE': 'old', 'calls': 2})
        self.target.write_bytes(self.old)

    def start(self, function, *, name):
        errors, result = [], []
        def run():
            try:
                result.append(function())
            except BaseException as error:
                errors.append(error)
        thread = self.threading.Thread(target=run, name=name, daemon=True)
        thread.start()
        return thread, errors, result

    def joined(self, thread):
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'TEST_FIXTURE thread must finish')

    def wait(self, event):
        self.assertTrue(event.wait(5), 'TEST_FIXTURE coordination timed out')

    def test_original_fixed_temp_collides_with_another_writer(self):
        fixed = self.root/'metrics.json.writing'
        fixed.write_bytes(b'TEST_FIXTURE unfinished other writer')
        with self.assertRaises(FileExistsError):
            _legacy_atomic_json(self.target, {'TEST_FIXTURE': 'new'})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(fixed.read_bytes(), b'TEST_FIXTURE unfinished other writer')

    def test_original_scanner_reproduces_published_temp_enoent(self):
        ready, allow, published = (self.threading.Event() for _ in range(3))
        original_replace, original_scandir = os.replace, os.scandir
        fixed = self.root/'metrics.json.writing'
        def replace(source, target):
            ready.set(); self.wait(allow)
            original_replace(source, target); published.set()
        class Entry:
            def __init__(self, entry): self.entry, self.path = entry, entry.path
            def stat(inner, **kwargs):
                if inner.path == str(fixed):
                    allow.set(); self.wait(published)
                return inner.entry.stat(**kwargs)
        class Entries:
            def __init__(self, path): self.source = original_scandir(path)
            def __enter__(self): return (Entry(e) for e in self.source)
            def __exit__(self, *args): self.source.close()
        with patch.object(runtime.os, 'replace', side_effect=replace), \
                patch.object(runtime.os, 'scandir', side_effect=Entries):
            thread, errors, _ = self.start(lambda: _legacy_atomic_json(self.target, {'TEST_FIXTURE': 'new'}), name='old-publisher')
            try:
                self.wait(ready)
                with self.assertRaises(FileNotFoundError) as caught:
                    _legacy_directory_bytes(self.root)
                self.assertIn('metrics.json.writing', str(caught.exception))
            finally:
                allow.set(); self.joined(thread)
        self.assertEqual(errors, [])
        self.assertEqual(runtime.strict_json(self.target.read_bytes()), {'TEST_FIXTURE': 'new'})

    def test_unique_publish_retains_legacy_and_unrelated_temps(self):
        legacy, other = self.root/'metrics.json.writing', self.root/'.metrics.json.writing-foreign'
        legacy.write_bytes(b'old failed attempt'); other.write_bytes(b'foreign')
        runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new', 'calls': 3})
        self.assertEqual(runtime.strict_json(self.target.read_bytes()), {'TEST_FIXTURE': 'new', 'calls': 3})
        self.assertEqual(legacy.read_bytes(), b'old failed attempt')
        self.assertEqual(other.read_bytes(), b'foreign')
        self.assertEqual({p.name for p in self.root.iterdir()}, {'metrics.json', legacy.name, other.name})

    def test_concurrent_writers_publish_only_complete_snapshots(self):
        barrier = self.threading.Barrier(9)
        payloads = [{'TEST_FIXTURE': str(i), 'calls': i, 'nested': [i]*100} for i in range(8)]
        def write(value): barrier.wait(5); runtime.atomic_json(self.target, value)
        workers = [self.start(lambda v=value: write(v), name='publisher-'+str(i)) for i, value in enumerate(payloads)]
        barrier.wait(5)
        for thread, errors, _ in workers:
            self.joined(thread); self.assertEqual(errors, [])
        self.assertIn(runtime.strict_json(self.target.read_bytes()), payloads)
        self.assertEqual([p.name for p in self.root.iterdir()], ['metrics.json'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory FD flock test')
    def test_scanner_waits_for_same_parent_atomic_publish(self):
        import fcntl
        ready, scan_attempted, allow = (self.threading.Event() for _ in range(3))
        original_replace, original_flock = os.replace, fcntl.flock
        def replace(source, target):
            ready.set(); self.wait(allow); return original_replace(source, target)
        def flock(descriptor, operation):
            if operation & fcntl.LOCK_SH and self.threading.current_thread().name == 'new-scanner':
                scan_attempted.set()
            return original_flock(descriptor, operation)
        with patch.object(runtime.os, 'replace', side_effect=replace), patch.object(fcntl, 'flock', side_effect=flock):
            publisher, pe, _ = self.start(lambda: runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new'*100}), name='new-publisher')
            scanner = None
            try:
                self.wait(ready)
                scanner, se, result = self.start(lambda: runtime.directory_bytes(self.root), name='new-scanner')
                self.wait(scan_attempted)
                self.assertTrue(scanner.is_alive())
            finally:
                allow.set(); self.joined(publisher)
                if scanner is not None: self.joined(scanner)
        self.assertEqual(pe, []); self.assertEqual(se, [])
        self.assertEqual(result, [self.target.stat().st_size])

    def test_different_parent_publish_does_not_wait_for_unrelated_lock(self):
        other = self.root/'other'; other.mkdir()
        with runtime._control_directory_lock(self.root, exclusive=True):
            thread, errors, _ = self.start(lambda: runtime.atomic_json(other/'metrics.json', {'TEST_FIXTURE': True}), name='other-parent')
            self.joined(thread)
        self.assertEqual(errors, [])

    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor-anchored directory scan')
    def test_descendant_scan_retains_parent_descriptor_without_parent_publication_lock(self):
        child = self.root/'child'; child.mkdir()
        payload = child/'payload'; payload.write_bytes(b'child')
        ready, allow = self.threading.Event(), self.threading.Event()
        original, child_inode = os.scandir, child.stat().st_ino
        def scandir(descriptor):
            if os.fstat(descriptor).st_ino == child_inode:
                ready.set(); self.wait(allow)
            return original(descriptor)
        with patch.object(runtime.os, 'scandir', side_effect=scandir):
            scanner, se, result = self.start(lambda: runtime.directory_bytes(self.root), name='descendant-scanner')
            try:
                self.wait(ready)
                publisher, pe, _ = self.start(lambda: runtime.atomic_json(self.target,
                    {'TEST_FIXTURE': 'parent can publish during descendant scan'}), name='parent-publisher')
                self.joined(publisher)
                self.assertEqual(pe, [])
                self.assertTrue(scanner.is_alive())
            finally:
                allow.set(); self.joined(scanner)
        self.assertEqual(se, [])
        self.assertEqual(result, [len(self.old) + 5])
        self.assertEqual(runtime.directory_bytes(self.root), self.target.stat().st_size + 5)

    def test_same_thread_nested_publish_scan_does_not_self_lock(self):
        def nested():
            with runtime._control_directory_lock(self.root, exclusive=True):
                runtime.atomic_json(self.target, {'TEST_FIXTURE': 'nested'})
                return runtime.directory_bytes(self.root)
        thread, errors, result = self.start(nested, name='nested-owner')
        self.joined(thread); self.assertEqual(errors, [])
        self.assertEqual(result, [self.target.stat().st_size])

    def test_shared_to_exclusive_upgrade_fails_without_deadlock(self):
        def nested():
            with runtime._control_directory_lock(self.root, exclusive=False):
                runtime.atomic_json(self.target, {'TEST_FIXTURE': 'forbidden'})
        thread, errors, _ = self.start(nested, name='upgrade-owner')
        self.joined(thread)
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), 'LOCK_UPGRADE_FORBIDDEN')
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_cross_process_scanner_waits_for_directory_publication_lock(self):
        source = Path(__file__).resolve().parents[2]
        code = ('import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n'
                'from stockfish_core.yaogu import premarket_runtime_v1 as runtime\n'
                'print("READY",flush=True)\nprint(runtime.directory_bytes(sys.argv[2]),flush=True)\n')
        process = None
        try:
            with runtime._control_directory_lock(self.root, exclusive=True):
                process = subprocess.Popen([sys.executable,'-I','-B','-c',code,str(source),str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(process.stdout.readline().strip(), 'READY')
                self.assertIsNone(process.poll())
                runtime.atomic_json(self.target, {'TEST_FIXTURE': 'child sees committed control'})
            out, err = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, err)
            self.assertEqual(int(out.strip()), self.target.stat().st_size)
        finally:
            if process is not None and process.poll() is None:
                process.kill(); process.communicate(timeout=5)

    def test_unrelated_disappearing_file_is_not_swallowed(self):
        victim = (self.root/'ordinary.bin').resolve(); victim.write_bytes(b'ordinary')
        original = os.scandir
        class Entry:
            def __init__(self, entry): self.entry, self.path = entry, entry.path
            def stat(inner, **kwargs):
                # scandir(fd) returns a name relative to its held directory.
                if Path(inner.path).name == victim.name: victim.unlink()
                return inner.entry.stat(**kwargs)
        class Entries:
            def __init__(self, path): self.source = original(path)
            def __enter__(self): return (Entry(e) for e in self.source)
            def __exit__(self, *args): self.source.close()
        with patch.object(runtime.os, 'scandir', side_effect=Entries):
            with self.assertRaises(FileNotFoundError): runtime.directory_bytes(self.root)

    def test_invalid_json_creates_no_temp_or_target_change(self):
        with self.assertRaises(ValueError): runtime.atomic_json(self.target, {'bad': float('nan')})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual([p.name for p in self.root.iterdir()], ['metrics.json'])

    def test_file_fsync_and_replace_failures_preserve_old_and_cleanup_own_temp(self):
        for failing in ('file_fsync', 'replace'):
            with self.subTest(failing=failing):
                self.target.write_bytes(self.old)
                fault = OSError('TEST_FIXTURE '+failing)
                if failing == 'file_fsync':
                    context = patch.object(runtime.os, 'fsync', side_effect=fault)
                else:
                    context = patch.object(runtime.os, 'replace', side_effect=fault)
                with context, self.assertRaises(OSError) as caught:
                    runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new'})
                self.assertIs(caught.exception, fault)
                self.assertEqual(self.target.read_bytes(), self.old)
                self.assertEqual([p.name for p in self.root.iterdir()], ['metrics.json'])
        runtime.atomic_json(self.target, {'TEST_FIXTURE': 'lock released after errors'})

    @unittest.skipUnless(os.name == 'posix', 'POSIX parent-directory durability')
    def test_directory_fsync_failure_is_reported_after_complete_publication(self):
        original = os.fsync
        def fsync(descriptor):
            if runtime.stat.S_ISDIR(os.fstat(descriptor).st_mode): raise OSError('TEST_FIXTURE directory fsync')
            return original(descriptor)
        with patch.object(runtime.os, 'fsync', side_effect=fsync), self.assertRaisesRegex(OSError, 'directory fsync'):
            runtime.atomic_json(self.target, {'TEST_FIXTURE': 'already replaced but not acknowledged durable'})
        self.assertEqual(runtime.strict_json(self.target.read_bytes()), {'TEST_FIXTURE': 'already replaced but not acknowledged durable'})
        self.assertEqual([p.name for p in self.root.iterdir()], ['metrics.json'])

    def test_cleanup_failure_keeps_primary_and_does_not_remove_old_control(self):
        primary = OSError('TEST_FIXTURE primary fsync')
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path.name.startswith('.metrics.json.writing-'): raise PermissionError('TEST_FIXTURE cleanup denied')
            return original(path, *args, **kwargs)
        with patch.object(runtime.os, 'fsync', side_effect=primary), patch.object(Path, 'unlink', unlink), self.assertRaises(OSError) as caught:
            runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new'})
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.atomic_control_cleanup_errors[0][0], 'TEMPORARY_CLEANUP')
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(len(list(self.root.glob('.metrics.json.writing-*'))), 1)

    def test_temp_inode_replacement_is_rejected_and_foreign_temp_not_deleted(self):
        original_fsync, original_mkstemp = os.fsync, runtime.tempfile.mkstemp
        paths = []
        def mkstemp(**kwargs):
            descriptor, name = original_mkstemp(**kwargs); paths.append(Path(name)); return descriptor, name
        def fsync(descriptor):
            result = original_fsync(descriptor)
            if runtime.stat.S_ISREG(os.fstat(descriptor).st_mode):
                original = paths[0]; original.rename(self.root/'our-unlinked-preimage')
                original.write_bytes(b'TEST_FIXTURE foreign replacement')
            return result
        with patch.object(runtime.tempfile, 'mkstemp', side_effect=mkstemp), patch.object(runtime.os, 'fsync', side_effect=fsync), self.assertRaisesRegex(RuntimeError, 'TEMPORARY_IDENTITY_CHANGED'):
            runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new'})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(paths[0].read_bytes(), b'TEST_FIXTURE foreign replacement')

    def test_publication_contract_is_explicit_without_changing_limits(self):
        budget = runtime.Budget()
        self.assertEqual(budget.metrics['control_publication_version'], 'PREMARKET_ATOMIC_CONTROL_PUBLICATION_V2')
        self.assertEqual(budget.metrics['control_parent_directory_fsync'], os.name == 'posix')
        self.assertEqual(budget.metrics['limits'], runtime.DEFAULT_LIMITS)

    def test_windows_mutex_mock_acquires_releases_closes_and_preserves_primary(self):
        import ctypes
        class Function:
            def __init__(self, value): self.value, self.calls = value, []
            def __call__(self, *args): self.calls.append(args); return self.value
        kernel = type('Kernel', (), {})()
        for name, value in [('CreateMutexW', 123), ('WaitForSingleObject', 0), ('ReleaseMutex', 1), ('CloseHandle', 1)]: setattr(kernel, name, Function(value))
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            with runtime._windows_control_mutex('TEST_FIXTURE'): pass
            self.assertEqual(len(kernel.ReleaseMutex.calls), 1); self.assertEqual(len(kernel.CloseHandle.calls), 1)
            primary = OSError('TEST_FIXTURE body')
            with self.assertRaises(OSError) as caught:
                with runtime._windows_control_mutex('TEST_FIXTURE'): raise primary
            self.assertIs(caught.exception, primary)
        self.assertTrue(kernel.CreateMutexW.calls[0][2].startswith('Global\\StockFishAtomicControlV2-'))

    def test_windows_mutex_mock_wait_failure_closes_without_unlock(self):
        import ctypes
        class Function:
            def __init__(self, value): self.value, self.calls = value, []
            def __call__(self, *args): self.calls.append(args); return self.value
        kernel = type('Kernel', (), {})()
        for name, value in [('CreateMutexW', 123), ('WaitForSingleObject', 0xffffffff), ('ReleaseMutex', 1), ('CloseHandle', 1)]: setattr(kernel, name, Function(value))
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), self.assertRaisesRegex(OSError, 'MUTEX_WAIT_FAILED'):
            with runtime._windows_control_mutex('TEST_FIXTURE'): self.fail('wait failed')
        self.assertEqual(len(kernel.ReleaseMutex.calls), 0); self.assertEqual(len(kernel.CloseHandle.calls), 1)

    def test_fdopen_failure_closes_descriptor_and_cleans_only_own_temp(self):
        original, descriptors = runtime.tempfile.mkstemp, []
        def create(**kwargs):
            descriptor, name = original(**kwargs); descriptors.append(descriptor); return descriptor, name
        with patch.object(runtime.tempfile, 'mkstemp', side_effect=create), patch.object(runtime.os, 'fdopen', side_effect=OSError('TEST_FIXTURE fdopen')), self.assertRaisesRegex(OSError, 'fdopen'):
            runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new'})
        with self.assertRaises(OSError): os.fstat(descriptors[0])
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual([p.name for p in self.root.iterdir()], ['metrics.json'])

    def test_short_write_is_not_published_and_own_temp_is_cleaned(self):
        original = os.fdopen
        class ShortWriter:
            def __init__(self, descriptor, mode): self.handle = original(descriptor, mode)
            def __enter__(self): return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def write(self, raw): self.handle.write(raw[:3]); return 3
            def __getattr__(self, name): return getattr(self.handle, name)
        with patch.object(runtime.os, 'fdopen', side_effect=ShortWriter), self.assertRaisesRegex(OSError, 'SHORT_WRITE'):
            runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new'})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual([p.name for p in self.root.iterdir()], ['metrics.json'])

    def test_windows_atomic_publish_mock_uses_mutex_file_fsync_replace_level(self):
        import ctypes
        from pathlib import PosixPath
        class Function:
            def __init__(self, value): self.value, self.calls = value, []
            def __call__(self, *args): self.calls.append(args); return self.value
        kernel = type('Kernel', (), {})()
        for name, value in [('CreateMutexW', 123), ('WaitForSingleObject', 0), ('ReleaseMutex', 1), ('CloseHandle', 1)]: setattr(kernel, name, Function(value))
        original, kinds = os.fsync, []
        def fsync(descriptor): kinds.append(runtime.stat.S_ISDIR(os.fstat(descriptor).st_mode)); return original(descriptor)
        with patch.object(runtime, 'Path', PosixPath), patch.object(runtime.os, 'name', 'nt'), patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), patch.object(runtime.os, 'fsync', side_effect=fsync):
            runtime.atomic_json(self.target, {'TEST_FIXTURE': 'windows simulation'})
            self.assertFalse(runtime.Budget().metrics['control_parent_directory_fsync'])
        self.assertEqual(kinds, [False])
        self.assertEqual(len(kernel.CreateMutexW.calls), 1)
        self.assertEqual(len(kernel.ReleaseMutex.calls), 1)
        self.assertEqual(len(kernel.CloseHandle.calls), 1)
        self.assertEqual(runtime.strict_json(self.target.read_bytes()), {'TEST_FIXTURE': 'windows simulation'})

    def test_windows_mutex_mock_release_error_does_not_mask_body_error(self):
        import ctypes
        class Function:
            def __init__(self, value): self.value, self.calls = value, []
            def __call__(self, *args): self.calls.append(args); return self.value
        kernel = type('Kernel', (), {})()
        for name, value in [('CreateMutexW', 123), ('WaitForSingleObject', 0), ('ReleaseMutex', 0), ('CloseHandle', 1)]: setattr(kernel, name, Function(value))
        primary = OSError('TEST_FIXTURE primary')
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), patch.object(ctypes, 'get_last_error', return_value=5, create=True), patch.object(ctypes, 'WinError', side_effect=lambda code: OSError(code, 'TEST_FIXTURE kernel'), create=True), self.assertRaises(OSError) as caught:
            with runtime._windows_control_mutex('TEST_FIXTURE'): raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.atomic_control_cleanup_errors[0][0], 'MUTEX_RELEASE')
        self.assertEqual(len(kernel.CloseHandle.calls), 1)

    def test_windows_mutex_mock_creation_failure_has_no_unowned_close(self):
        import ctypes
        class Function:
            def __init__(self, value): self.value, self.calls = value, []
            def __call__(self, *args): self.calls.append(args); return self.value
        kernel = type('Kernel', (), {})()
        for name, value in [('CreateMutexW', 0), ('WaitForSingleObject', 0), ('ReleaseMutex', 1), ('CloseHandle', 1)]: setattr(kernel, name, Function(value))
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), patch.object(ctypes, 'get_last_error', return_value=5, create=True), patch.object(ctypes, 'WinError', side_effect=lambda code: OSError(code, 'TEST_FIXTURE create'), create=True), self.assertRaisesRegex(OSError, 'create'):
            with runtime._windows_control_mutex('TEST_FIXTURE'): self.fail('creation failed')
        self.assertEqual(kernel.ReleaseMutex.calls, []); self.assertEqual(kernel.CloseHandle.calls, [])

    def test_new_large_metrics_still_trigger_output_limit(self):
        runtime.atomic_json(self.target, {'TEST_FIXTURE': 'x'*5000})
        budget = runtime.Budget({'output_bytes': 4096}, job_dir=self.root)
        with self.assertRaisesRegex(runtime.BudgetExceeded, 'OUTPUT_LIMIT_EXCEEDED'):
            budget.check('TEST_FIXTURE', force=True)
        self.assertIn('OUTPUT_LIMIT_EXCEEDED', budget.metrics['failures'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory FD flock test')
    def test_publisher_waits_for_same_parent_metadata_scan(self):
        import fcntl
        ready, attempted, allow = (self.threading.Event() for _ in range(3))
        original_flock, original_scandir = fcntl.flock, os.scandir
        def flock(descriptor, operation):
            if operation & fcntl.LOCK_EX and self.threading.current_thread().name == 'waiting-publisher': attempted.set()
            return original_flock(descriptor, operation)
        class Entry:
            def __init__(self, entry): self.entry, self.path = entry, entry.path
            def stat(inner, **kwargs):
                if Path(inner.path).name == 'metrics.json': ready.set(); self.wait(allow)
                return inner.entry.stat(**kwargs)
        class Entries:
            def __init__(self, path): self.source = original_scandir(path)
            def __enter__(self): return (Entry(e) for e in self.source)
            def __exit__(self, *args): self.source.close()
        with patch.object(runtime.os, 'scandir', side_effect=Entries), patch.object(fcntl, 'flock', side_effect=flock):
            scanner, se, result = self.start(lambda: runtime.directory_bytes(self.root), name='holding-scanner')
            publisher = None
            try:
                self.wait(ready)
                publisher, pe, _ = self.start(lambda: runtime.atomic_json(self.target, {'TEST_FIXTURE': 'new'*100}), name='waiting-publisher')
                self.wait(attempted); self.assertTrue(publisher.is_alive())
            finally:
                allow.set(); self.joined(scanner)
                if publisher is not None: self.joined(publisher)
        self.assertEqual(se, []); self.assertEqual(pe, [])
        self.assertEqual(result, [len(self.old)])
        self.assertEqual(runtime.directory_bytes(self.root), self.target.stat().st_size)

    def test_windows_mutex_mock_abandoned_owner_still_requires_current_operation(self):
        import ctypes
        class Function:
            def __init__(self, value): self.value, self.calls = value, []
            def __call__(self, *args): self.calls.append(args); return self.value
        kernel = type('Kernel', (), {})()
        for name, value in [('CreateMutexW', 123), ('WaitForSingleObject', 0x80), ('ReleaseMutex', 1), ('CloseHandle', 1)]: setattr(kernel, name, Function(value))
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            with runtime._windows_control_mutex('TEST_FIXTURE'):
                self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(len(kernel.ReleaseMutex.calls), 1); self.assertEqual(len(kernel.CloseHandle.calls), 1)

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX fork ownership test')
    def test_fork_child_does_not_reuse_parent_thread_local_lock_authority(self):
        import fcntl
        original, parent = fcntl.flock, os.getpid()
        reader, writer = os.pipe()
        child, notified = None, [False]
        def flock(descriptor, operation):
            if os.getpid() != parent and operation & fcntl.LOCK_SH and not notified[0]:
                notified[0] = True
                os.write(writer, b'LOCK\n')
            return original(descriptor, operation)
        stream = os.fdopen(reader, 'rb')
        try:
            with runtime._control_directory_lock(self.root, exclusive=True):
                with patch.object(fcntl, 'flock', side_effect=flock):
                    child = os.fork()
                    if child == 0:
                        try:
                            count = runtime.directory_bytes(self.root)
                            os.write(writer, ('COUNT:'+str(count)+'\n').encode())
                            os._exit(0)
                        except BaseException as error:
                            os.write(writer, ('ERROR:'+repr(error)+'\n').encode()); os._exit(7)
                self.assertEqual(stream.readline(), b'LOCK\n')
                runtime.atomic_json(self.target, {'TEST_FIXTURE': 'fork observes publication'})
            self.assertEqual(stream.readline(), ('COUNT:'+str(self.target.stat().st_size)+'\n').encode())
            waited, status = os.waitpid(child, 0); child = None
            self.assertEqual(status, 0)
        finally:
            stream.close(); os.close(writer)
            if child:
                try: os.kill(child, 9)
                except ProcessLookupError: pass
                os.waitpid(child, 0)

    def test_independent_process_lock_timeout_keeps_control_and_limits(self):
        source = Path(__file__).resolve().parents[2]
        code = ('import sys\nsys.path.insert(0,sys.argv[1])\n'
                'from stockfish_core.yaogu import premarket_runtime_v1 as runtime\n'
                'with runtime._control_directory_lock(sys.argv[2],exclusive=True):\n'
                ' print("LOCKED",flush=True)\n sys.stdin.read(1)\n')
        process = subprocess.Popen([sys.executable,'-I','-B','-c',code,str(source),str(self.root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        original_limits = dict(runtime.DEFAULT_LIMITS)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
            with patch.object(runtime, 'CONTROL_LOCK_WAIT_SECONDS', 0.15):
                with self.assertRaisesRegex(runtime.ControlPublicationLockTimeout, 'LOCK_WAIT_TIMEOUT'):
                    runtime.atomic_json(self.target, {'TEST_FIXTURE': 'must not publish'})
                with self.assertRaises(runtime.ControlPublicationLockTimeout):
                    runtime.directory_bytes(self.root)
            self.assertEqual(self.target.read_bytes(), self.old)
            self.assertEqual([p.name for p in self.root.iterdir()], ['metrics.json'])
            self.assertEqual(runtime.DEFAULT_LIMITS, original_limits)
            self.assertEqual(runtime.CONTROL_LOCK_WAIT_SECONDS, 30)
        finally:
            if process.poll() is None:
                process.stdin.write('x'); process.stdin.flush()
            try: out, err = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate(timeout=5); raise
        self.assertEqual(process.returncode, 0, err)
        runtime.atomic_json(self.target, {'TEST_FIXTURE': 'holder released'})

    @unittest.skipUnless(os.name == 'posix', 'POSIX interruptible nonblocking lock poll')
    def test_lock_poll_keyboard_interrupt_is_not_swallowed(self):
        import fcntl
        interruption = KeyboardInterrupt('TEST_FIXTURE cancellation')
        with patch.object(fcntl, 'flock', side_effect=BlockingIOError()), patch.object(runtime.time, 'sleep', side_effect=interruption), self.assertRaises(KeyboardInterrupt) as caught:
            runtime.atomic_json(self.target, {'TEST_FIXTURE': 'must not publish'})
        self.assertIs(caught.exception, interruption)
        self.assertEqual(self.target.read_bytes(), self.old)
        runtime.atomic_json(self.target, {'TEST_FIXTURE': 'no leaked lock'})

    def test_windows_mutex_mock_wait_timeout_is_failure_not_acquisition(self):
        import ctypes
        class Function:
            def __init__(self, value): self.value, self.calls = value, []
            def __call__(self, *args): self.calls.append(args); return self.value
        kernel = type('Kernel', (), {})()
        for name, value in [('CreateMutexW', 123), ('WaitForSingleObject', 0x102), ('ReleaseMutex', 1), ('CloseHandle', 1)]: setattr(kernel, name, Function(value))
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), patch.object(runtime.time, 'monotonic', side_effect=[0.0, 0.0, 31.0]), self.assertRaises(runtime.ControlPublicationLockTimeout):
            with runtime._windows_control_mutex('TEST_FIXTURE'): self.fail('timeout must not enter')
        self.assertEqual(len(kernel.WaitForSingleObject.calls), 1)
        self.assertEqual(kernel.WaitForSingleObject.calls[0][1], 50)
        self.assertEqual(kernel.ReleaseMutex.calls, [])
        self.assertEqual(len(kernel.CloseHandle.calls), 1)


if __name__ == '__main__':
    unittest.main()
