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

    def test_physical_four_and_all_seven_fingerprint_fields_are_enforced(self):
        _, reference = runtime.Budget().read(self.path)
        self.assertEqual(set(reference), {'path', 'sha256', 'bytes', 'fp'})
        self.assertEqual(set(reference['fp']), set(runtime.FP_KEYS))
        raw, actual = runtime.Budget().read(self.path, expected=reference)
        self.assertEqual(raw, self.payload)
        self.assertEqual(actual, reference)
        for key in runtime.FP_KEYS:
            bad = deepcopy(reference)
            bad['fp'][key] += 1
            budget = runtime.Budget()
            with self.subTest(key=key), self.assertRaises(runtime.BudgetExceeded):
                budget.read(self.path, expected=bad)
            with self.assertRaises(runtime.BudgetExceeded):
                budget.check('caught_read_error_cannot_pass')
        for mutate in (lambda r: r['fp'].pop('st_flags'),
                       lambda r: r['fp'].update(extra=1),
                       lambda r: r.pop('fp'),
                       lambda r: r.update(extra='not-a-physical-reference')):
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


if __name__ == '__main__':
    unittest.main()
