"""Isolated TEST_FIXTURE source bytes; no SDK, network, business DB or selector."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.yaogu import premarket_capture_cache_v1 as cache_module
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.providers import archive_receipt


class FixtureBudget:
    def __init__(self, max_requests=1000):
        self.limits = dict(max_requests=max_requests, max_sdk_calls=10000,
            max_http_attempts=100000, request_http_attempts=64, request_timeout_seconds=90)
        self.calls = dict(requests=0, sdk_reserved=0, http_reserved=0)
        self.read_bytes = 0

    @property
    def metrics(self):
        return dict(calls=dict(self.calls), returned_read_bytes=self.read_bytes)

    def check(self, stage):
        if self.read_bytes > 1024**3:
            raise RuntimeError('TEST_FIXTURE_READ_BOUND')

    def read(self, path, expected=None, limit=None):
        path = Path(path).resolve(strict=True)
        before = cache_module._fp(path)
        raw = path.read_bytes()
        self.read_bytes += len(raw)
        self.check('read')
        if limit is not None and len(raw) > limit:
            raise ValueError('TEST_FIXTURE_FILE_BOUND')
        actual = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), fp=before)
        if isinstance(expected, str) and actual['sha256'] != expected:
            raise ValueError('TEST_FIXTURE_SHA_CHANGED')
        if before != cache_module._fp(path):
            raise ValueError('TEST_FIXTURE_FP_CHANGED')
        return raw, actual

    def preclaim_request(self, endpoint, params):
        if self.calls['requests'] >= self.limits['max_requests']:
            raise RuntimeError('TEST_FIXTURE_REQUEST_BOUND')
        self.calls['requests'] += 1

    def record_request(self, result):
        pass


class FixtureExecutor:
    def __init__(self, *, basic_failure=False, factor_partial=False, crash=False, lhb_status='COMPLETE'):
        self.calls = []
        self.basic_failure = basic_failure
        self.factor_partial = factor_partial
        self.crash = crash
        self.lhb_status = lhb_status

    def __call__(self, endpoint, params, output, **kwargs):
        self.calls.append((endpoint, params))
        if self.crash:
            raise RuntimeError('TEST_FIXTURE_EXECUTOR_DIED')
        started = datetime.now(timezone.utc).isoformat()
        seen = datetime.now(timezone.utc).isoformat()
        status, complete, coverage = 'COMPLETE', True, {}
        if endpoint == 'calendar':
            start = datetime.fromisoformat(params['start_date']).date()
            end = datetime.fromisoformat(params['end_date']).date()
            rows = [dict(calendar_date=str(start+timedelta(days=n)),
                is_trading_day='1' if (start+timedelta(days=n)).weekday() < 5 else '0')
                for n in range((end-start).days+1)]
        elif endpoint == 'market_daily':
            rows = [dict(code=c, date=params['date'], adjustflag='3', open='10', high='11',
                low='9', close='10', preclose='10', amount='500000000', volume='50000000',
                turn='10', isST='0', tradestatus='1') for c in ('sh.600001','sh.688001')]
        elif endpoint == 'master_all':
            rows = [dict(code=c, code_name='TEST_FIXTURE_NAME', tradeStatus='1')
                    for c in ('sh.600001','sh.688001')]
        elif endpoint == 'mainboard_sse':
            rows = [{'证券代码':'600001','证券简称':'TEST_FIXTURE_MAIN','上市日期':'2000-01-01'}]
        elif endpoint == 'master_szse':
            rows = [{'A股代码':'000001','A股简称':'TEST_FIXTURE_SZ','A股上市日期':'2000-01-01','板块':'主板'}]
        elif endpoint == 'delisting_sse':
            rows = []
        elif endpoint in ('stock_basic_all','stock_basic'):
            if endpoint == 'stock_basic_all' and self.basic_failure:
                rows, status, complete = [], 'FETCH_FAILED', False
            else:
                codes = params.get('codes', ['sh.600001','sh.688001','sz.000001'])
                rows = [dict(code=c, ipoDate='2000-01-01', outDate='', code_name='TEST_FIXTURE_'+c,
                             type='1',status='1') for c in codes]
        elif endpoint == 'adjustment_factors':
            rows, statuses = [], []
            for ordinal, code in enumerate(params['codes']):
                good = not self.factor_partial or ordinal == 0
                if good:
                    rows.append(dict(code=code,dividOperateDate='2000-01-01',backAdjustFactor='1'))
                statuses.append(dict(code=code,status='COMPLETE' if good else 'FETCH_FAILED',
                    cursor_exhausted=good,row_count=1 if good else 0))
            coverage = dict(requested_codes=params['codes'],security_statuses=statuses)
            if self.factor_partial:
                status, complete = 'PARTIAL', False
        elif endpoint == 'lhb_summary':
            rows, status = [], self.lhb_status
            complete = status == 'COMPLETE'
        elif endpoint == 'industry_list_sina':
            rows = [dict(label='hangye_TEST_FIXTURE_A',板块='TEST_FIXTURE行业',公司家数=1),
                    dict(label='hangye_TEST_FIXTURE_B',板块='TEST_FIXTURE其他',公司家数=1)]
        elif endpoint == 'concept_list_sina':
            rows = [dict(label='gn_TEST_FIXTURE',板块='TEST_FIXTURE概念',公司家数=1)]
        elif endpoint in ('industry_members_sina','concept_members_sina'):
            rows = [dict(code='600001',symbol='sh600001',name='TEST_FIXTURE_MAIN')]
        else:
            raise AssertionError('UNEXPECTED_TEST_FIXTURE_ENDPOINT:'+endpoint)
        payload = dict(status=status,pagination_complete=complete,rows=rows,
                       coverage=coverage,gaps=[],field_version='TEST_FIXTURE_PROVIDER_ROWS_V1')
        # A timeout has no original RAW first_seen. The cache must retain that
        # unknown, rather than inventing it or losing the failure/fallback chain.
        if status != 'FETCH_FAILED':
            payload['first_seen_at'] = seen
        else:
            del payload['field_version']
        physical = archive_receipt(Path(output)/'raw','TEST_FIXTURE',endpoint,params,payload,
            status=status,pagination_complete=complete,field_version='TEST_FIXTURE_PROVIDER_ROWS_V1',
            first_seen_at=seen)
        result = dict(endpoint=endpoint,provider='TEST_FIXTURE',parameters=params,rows=rows,
            status=status,pagination_complete=complete,coverage=coverage,gaps=[],
            field_version='TEST_FIXTURE_PROVIDER_ROWS_V1',request_receipt_id=physical['receipt_id'],
            receipts=[physical],started_at=started,completed_at=datetime.now(timezone.utc).isoformat(),
            implementation_source_hashes={name:hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                for name in cache_module.PRODUCERS},diagnostics=dict(fixture='TEST_FIXTURE'))
        Path(output).mkdir(parents=True,exist_ok=True)
        Path(output,'request-TEST_FIXTURE.json').write_text(json.dumps(result),encoding='utf-8')
        return result


class CaptureCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_premarket_cache_')
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def cache(self, executor=None, **config):
        return cache_module.CaptureCache(self.root/'cache',FixtureBudget(),
            config=dict(allow_test_fixture=True,**config),executor=executor or FixtureExecutor())

    def test_request_is_immutable_and_ascii(self):
        spec = cache_module.RequestSpec('stock_basic',{'codes':['sh.600001']},
            datetime.now(cache_module.SH).date().isoformat(),current=True)
        spec.parameters['codes'].append('sh.600002')
        self.assertEqual(spec.parameters['codes'],['sh.600001'])
        with self.assertRaisesRegex(ValueError,'ASCII_REQUEST'):
            cache_module.RequestSpec('stock_basic',{'codes':['sh.６００００１']})

    def test_lhb_request_uses_five_calendar_sessions_and_role_does_not_change_key(self):
        sessions = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08',
                    '2026-01-09', '2026-01-12', '2026-01-13']
        spec = cache_module.lhb_request_spec(sessions)
        self.assertEqual(spec.endpoint, 'lhb_summary')
        self.assertEqual(spec.parameters, dict(start_date='2026-01-07', end_date='2026-01-13'))
        self.assertEqual(spec.business_session, '2026-01-13')
        self.assertFalse(spec.current)
        same = cache_module.RequestSpec('lhb_summary', spec.parameters, spec.business_session,
                                        role='TEST_FIXTURE_OTHER_DESCRIPTION')
        self.assertEqual(spec.key, same.key)
        for invalid in (sessions[:4], list(reversed(sessions)), sessions + [sessions[-1]]):
            with self.subTest(sessions=invalid):
                with self.assertRaisesRegex(ValueError, 'LHB_FIVE_ORDERED'):
                    cache_module.lhb_request_spec(invalid)

    def test_changed_lhb_window_cannot_retry_prior_three_session_failure(self):
        executor = FixtureExecutor(lhb_status='FETCH_FAILED')
        cache = self.cache(executor)
        old = cache_module.RequestSpec('lhb_summary', dict(start_date='2026-01-08', end_date='2026-01-12'),
                                      '2026-01-12', role='TEST_FIXTURE_OLD_THREE_SESSIONS')
        failed = cache.capture_one(old)
        current = cache_module.lhb_request_spec(['2026-01-07', '2026-01-08', '2026-01-09',
                                                 '2026-01-12', '2026-01-13'])
        self.assertNotEqual(old.key, current.key)
        calls = list(executor.calls)
        with self.assertRaisesRegex(ValueError, 'LHB_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
            cache.capture_one(current)
        self.assertEqual(executor.calls, calls)
        self.assertEqual(cache.lookup(old).receipt_id, failed.receipt_id)

    def test_changed_lhb_window_cannot_retry_prior_unfinished_claim(self):
        executor = FixtureExecutor(crash=True)
        cache = self.cache(executor)
        old = cache_module.RequestSpec('lhb_summary', dict(start_date='2026-01-08', end_date='2026-01-12'),
                                      '2026-01-12', role='TEST_FIXTURE_OLD_THREE_SESSIONS')
        with self.assertRaisesRegex(RuntimeError, 'TEST_FIXTURE_EXECUTOR_DIED'):
            cache.capture_one(old)
        current = cache_module.lhb_request_spec(['2026-01-07', '2026-01-08', '2026-01-09',
                                                 '2026-01-12', '2026-01-13'])
        calls = list(executor.calls)
        with self.assertRaisesRegex(ValueError, 'LHB_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
            cache.capture_one(current)
        self.assertEqual(executor.calls, calls)
        self.assertEqual(cache.lhb_overlap_blockers(current)[0]['request_key'], old.key)

    def test_lhb_nonoverlapping_source_window_is_not_an_overlapping_retry(self):
        executor = FixtureExecutor(lhb_status='FETCH_FAILED')
        cache = self.cache(executor)
        old = cache_module.RequestSpec('lhb_summary', dict(start_date='2026-01-08', end_date='2026-01-12'),
                                      '2026-01-12', role='TEST_FIXTURE_OLD_THREE_SESSIONS')
        cache.capture_one(old)
        executor.lhb_status = 'COMPLETE'
        current = cache_module.lhb_request_spec(['2026-01-19', '2026-01-20', '2026-01-21',
                                                 '2026-01-22', '2026-01-23'])
        self.assertEqual(cache.lhb_overlap_blockers(current), [])
        self.assertEqual(cache.capture_one(current).status, 'COMPLETE')
        self.assertEqual(len(executor.calls), 2)

    def test_lhb_successful_old_cache_without_claim_is_not_reclassified_as_unknown(self):
        executor = FixtureExecutor()
        cache = self.cache(executor)
        old = cache_module.RequestSpec('lhb_summary', dict(start_date='2026-01-08', end_date='2026-01-12'),
                                      '2026-01-12', role='TEST_FIXTURE_OLD_THREE_SESSIONS')
        record = cache.capture_one(old)
        claim = Path(record.metadata['capture_attempt_dir']) / 'request-claim.json'
        claim.unlink()  # TEST_FIXTURE emulates a committed older cache format.
        reopened = self.cache(executor)
        current = cache_module.lhb_request_spec(['2026-01-07', '2026-01-08', '2026-01-09',
                                                 '2026-01-12', '2026-01-13'])
        self.assertEqual(reopened.lhb_overlap_blockers(current), [])
        self.assertEqual(reopened.capture_one(current).status, 'COMPLETE')
        self.assertEqual(reopened.lookup(old).receipt_id, record.receipt_id)
        self.assertEqual(len(executor.calls), 2)

    def test_lhb_known_nonoverlapping_old_cache_needs_no_claim_or_attempt_mapping(self):
        executor = FixtureExecutor()
        cache = self.cache(executor)
        old = cache_module.RequestSpec('lhb_summary', dict(start_date='2026-01-08', end_date='2026-01-12'),
                                      '2026-01-12', role='TEST_FIXTURE_OLD_THREE_SESSIONS')
        record = cache.capture_one(old)
        (Path(record.metadata['capture_attempt_dir']) / 'request-claim.json').unlink()
        # A prior known disjoint window cannot be a retry of the new window,
        # even if an older attempt has no newly introduced claim structure.
        orphan = cache.root / 'raw' / old.key / 'raw' / 'TEST_FIXTURE-old-attempt'
        orphan.mkdir()
        reopened = self.cache(executor)
        current = cache_module.lhb_request_spec(['2026-01-19', '2026-01-20', '2026-01-21',
                                                 '2026-01-22', '2026-01-23'])
        self.assertEqual(reopened.lhb_overlap_blockers(current), [])
        self.assertEqual(reopened.capture_one(current).status, 'COMPLETE')
        self.assertEqual(len(executor.calls), 2)

    def test_lhb_does_not_guess_unidentified_prior_attempt_is_unrelated(self):
        executor = FixtureExecutor()
        cache = self.cache(executor)
        prior = cache.root / 'raw' / ('0' * 64)
        prior.mkdir()
        (prior / 'TEST_FIXTURE-unidentified-attempt').write_bytes(b'UNKNOWN retained request')
        current = cache_module.lhb_request_spec(['2026-01-07', '2026-01-08', '2026-01-09',
                                                 '2026-01-12', '2026-01-13'])
        with self.assertRaisesRegex(ValueError, 'LHB_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY'):
            cache.capture_one(current)
        self.assertEqual(executor.calls, [])
        self.assertEqual(cache.lhb_overlap_blockers(current)[0]['status'],
                         'UNKNOWN_PRIOR_REQUEST_SCOPE_NO_IMPLICIT_RETRY')

    def test_single_capture_hit_and_full_mature_verification(self):
        executor = FixtureExecutor(); cache = self.cache(executor)
        spec = cache_module.RequestSpec('market_daily',{'date':'2026-10-15'},'2026-10-15')
        record = cache.capture_one(spec)
        self.assertEqual(len(executor.calls),1)
        self.assertEqual(cache.capture_one(spec).receipt_id,record.receipt_id)
        self.assertEqual(len(executor.calls),1)
        self.assertIsNone(record.metadata['physical_row_count'])
        self.assertEqual(cache.verify(record)['rows'][0]['code'],'sh.600001')
        self.assertEqual(record.archive_root,str(cache.root/'raw'/spec.key))
        cache.terminal()
        self.assertFalse(list(self.root.rglob('*.sqlite*')))

    def test_native_repeated_reads_use_one_authenticated_transaction_snapshot(self):
        cache = self.cache(FixtureExecutor())
        spec = cache_module.RequestSpec('market_daily', {'date': '2026-10-15'}, '2026-10-15')
        record = cache.capture_one(spec)
        value = record.metadata
        required = [value[role] for role in ('raw', 'receipt', 'result')] + value['producer_files']
        expected_bytes = sum(ref['bytes'] for ref in required)
        mature = cache_module.historical_replay.verify_capture
        with patch.object(cache_module.historical_replay, 'verify_capture', wraps=mature) as verify:
            before = cache.budget.read_bytes
            first = cache.verify(record)
            self.assertEqual(cache.budget.read_bytes - before, expected_bytes)
            first['rows'][0]['code'] = 'TEST_FIXTURE_CALLER_MUTATION'
            before = cache.budget.read_bytes
            second = cache.verify(record)
            self.assertEqual(cache.budget.read_bytes - before, expected_bytes)
            self.assertEqual(second['rows'][0]['code'], 'sh.600001')
            self.assertEqual(verify.call_count, 2)  # Mature semantics still execute every time.
        before = cache.budget.read_bytes
        terminal_bytes = sum(ref['bytes'] for ref in cache._known.values())
        cache.terminal()
        self.assertEqual(cache.budget.read_bytes - before, terminal_bytes)
        reopened = self.cache(FixtureExecutor())
        reopened.verify(record)
        self.assertEqual(reopened.budget.read_bytes, expected_bytes)

    def test_native_memory_snapshot_preserves_actual_runtime_file_read_meter(self):
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        seeded = self.cache(FixtureExecutor())
        spec = cache_module.RequestSpec('market_daily', {'date': '2026-10-15'}, '2026-10-15')
        record = seeded.capture_one(spec)
        value = record.metadata
        expected = sum(value[role]['bytes'] for role in ('raw', 'receipt', 'result'))
        expected += sum(ref['bytes'] for ref in value['producer_files'])
        budget = Budget()
        cache = cache_module.CaptureCache(seeded.root, budget, config=dict(allow_test_fixture=True))
        with budget.phase('capture'), budget.meter_files():
            cache.verify(record)
            self.assertEqual(budget.metrics['returned_read_bytes']['capture'], expected)
            cache.verify(record)
            self.assertEqual(budget.metrics['returned_read_bytes']['capture'], 2 * expected)
            cache.terminal()
            self.assertEqual(budget.metrics['returned_read_bytes']['capture'], 3 * expected)
        self.assertEqual(budget.metrics['calls']['requests'], 0)

    def test_new_transaction_hashes_bytes_even_when_mtime_and_length_are_restored(self):
        cache = self.cache(FixtureExecutor())
        spec = cache_module.RequestSpec('market_daily', {'date': '2026-10-15'}, '2026-10-15')
        record = cache.capture_one(spec)
        cache.verify(record)
        path = Path(record.metadata['raw']['path'])
        before = path.stat()
        raw = path.read_bytes()
        changed = raw.replace(b'500000000', b'400000000')
        self.assertEqual(len(changed), len(raw))
        self.assertNotEqual(changed, raw)
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'SHA_CHANGED|SHA256_CHANGED'):
            cache.verify(record)
        self.assertEqual(len(cache.executor.calls), 1)

    def test_native_snapshot_rejects_changes_before_or_after_mature_read(self):
        for role, when in (('raw', 'before'), ('raw', 'after'), ('receipt', 'after'),
                           ('result', 'after'), ('producer', 'after')):
            with self.subTest(role=role, when=when):
                root = self.root / ('TEST_FIXTURE_' + role + '_' + when)
                cache = cache_module.CaptureCache(root, FixtureBudget(),
                    config=dict(allow_test_fixture=True), executor=FixtureExecutor())
                spec = cache_module.RequestSpec('market_daily', {'date': '2026-10-15'}, '2026-10-15')
                record = cache.capture_one(spec)
                value = record.metadata
                if role == 'producer':
                    references = []
                    for ref in value['producer_files']:
                        copy = root / 'TEST_FIXTURE_producers' / Path(ref['path']).name
                        copy.parent.mkdir(exist_ok=True)
                        copy.write_bytes(Path(ref['path']).read_bytes())
                        references.append(cache.budget.read(copy)[1])
                    value['producer_files'] = references
                    path = Path(references[0]['path'])
                else:
                    path = Path(value[role]['path'])
                original = path.read_bytes()
                mature = cache_module.historical_replay.verify_capture
                def mutate(*args):
                    if when == 'before':
                        path.write_bytes(original + b' ')
                    result = mature(*args)
                    if when == 'after':
                        path.write_bytes(original + b' ')
                    return result
                methods = Path.read_bytes, Path.read_text
                with patch.object(cache_module.historical_replay, 'verify_capture', side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, 'CHANGED_DURING_AUTHENTICATION'):
                        cache.verify(value)
                self.assertEqual((Path.read_bytes, Path.read_text), methods)
                self.assertEqual(len(cache.executor.calls), 1)

    def test_transaction_rejects_replacement_but_new_read_accepts_same_original_bytes(self):
        cache = self.cache(FixtureExecutor())
        spec = cache_module.RequestSpec('market_daily', {'date': '2026-10-15'}, '2026-10-15')
        record = cache.capture_one(spec)
        path = Path(record.metadata['raw']['path'])
        original = path.read_bytes()
        mature = cache_module.historical_replay.verify_capture
        def replace_after(*args):
            result = mature(*args)
            replacement = self.root / 'TEST_FIXTURE_same_bytes.raw'
            replacement.write_bytes(original)
            os.replace(replacement, path)
            return result
        with patch.object(cache_module.historical_replay, 'verify_capture', side_effect=replace_after):
            with self.assertRaisesRegex(ValueError, 'CHANGED_DURING_AUTHENTICATION'):
                cache.verify(record)
        self.assertEqual(cache.verify(record)['rows'][0]['code'], 'sh.600001')
        self.assertEqual(self.cache(FixtureExecutor()).verify(record)['rows'][0]['code'], 'sh.600001')
        self.assertEqual(len(cache.executor.calls), 1)

    def test_snapshot_never_allows_unregistered_reads_and_restores_path_methods(self):
        cache = self.cache(FixtureExecutor())
        spec = cache_module.RequestSpec('market_daily', {'date': '2026-10-15'}, '2026-10-15')
        record = cache.capture_one(spec)
        unrelated = self.root / 'TEST_FIXTURE_unregistered.json'
        unrelated.write_text('{}')
        methods = Path.read_bytes, Path.read_text
        with patch.object(cache_module.historical_replay, 'verify_capture',
                          side_effect=lambda *_: unrelated.read_bytes()):
            with self.assertRaisesRegex(ValueError, 'UNREGISTERED_MATURE_CAPTURE_READ'):
                cache.verify(record)
        self.assertEqual((Path.read_bytes, Path.read_text), methods)

    def test_native_snapshot_rejects_deleted_or_symlinked_original(self):
        for replacement in ('delete', 'symlink'):
            with self.subTest(replacement=replacement):
                cache = cache_module.CaptureCache(self.root / ('TEST_FIXTURE_' + replacement), FixtureBudget(),
                    config=dict(allow_test_fixture=True), executor=FixtureExecutor())
                spec = cache_module.RequestSpec('market_daily', {'date': '2026-10-15'}, '2026-10-15')
                record = cache.capture_one(spec)
                path = Path(record.metadata['raw']['path'])
                copy = self.root / ('TEST_FIXTURE_copy_' + replacement)
                copy.write_bytes(path.read_bytes())
                mature = cache_module.historical_replay.verify_capture
                def change_after(*args):
                    result = mature(*args)
                    path.unlink()
                    if replacement == 'symlink':
                        path.symlink_to(copy)
                    return result
                methods = Path.read_bytes, Path.read_text
                with patch.object(cache_module.historical_replay, 'verify_capture', side_effect=change_after):
                    with self.assertRaises((ValueError, OSError)):
                        cache.verify(record)
                self.assertEqual((Path.read_bytes, Path.read_text), methods)

    def test_corrupt_cached_original_does_not_become_a_miss_or_redownload(self):
        executor = FixtureExecutor(); cache = self.cache(executor)
        spec = cache_module.RequestSpec('market_daily',{'date':'2026-10-15'},'2026-10-15')
        record = cache.capture_one(spec)
        raw = Path(record.metadata['raw']['path']);raw.write_bytes(raw.read_bytes().replace(b'500000000',b'400000000'))
        with self.assertRaises(ValueError):cache.capture_one(spec)
        self.assertEqual(len(executor.calls),1)

    def test_failed_original_stays_failed_and_explicit_retry_is_single(self):
        executor = FixtureExecutor(basic_failure=True); cache = self.cache(executor)
        spec = cache_module.RequestSpec('stock_basic_all',{},
            datetime.now(cache_module.SH).date().isoformat(),current=True)
        record = cache.capture_one(spec)
        self.assertEqual(record.status,'FETCH_FAILED')
        self.assertFalse(record.metadata['raw_first_seen_present'])
        cache.capture_one(spec)
        self.assertEqual(len(executor.calls),1)
        retry_executor = FixtureExecutor()
        retry = self.cache(retry_executor,retry_failed=True)
        self.assertEqual(retry.capture_one(spec).status,'COMPLETE')
        retry.capture_one(spec)
        self.assertEqual(len(retry_executor.calls),1)
        self.assertTrue(Path(record.metadata['raw']['path']).exists())

    def test_crash_claim_cannot_silently_retry_and_path_patches_restore(self):
        executor = FixtureExecutor(crash=True); cache = self.cache(executor)
        spec = cache_module.RequestSpec('stock_basic_all',{},
            datetime.now(cache_module.SH).date().isoformat(),current=True)
        with self.assertRaisesRegex(RuntimeError,'EXECUTOR_DIED'):cache.capture_one(spec)
        with self.assertRaisesRegex(ValueError,'NO_IMPLICIT_RETRY'):cache.capture_one(spec)
        self.assertEqual(len(executor.calls),1)
        other = self.cache(FixtureExecutor(),retry_failed=True)
        before = (Path.read_bytes,Path.read_text)
        with patch.object(cache_module.historical_replay,'verify_capture',side_effect=ValueError('TEST_FIXTURE_VERIFY_DENIED')):
            with self.assertRaisesRegex(ValueError,'VERIFY_DENIED'):other.capture_one(spec)
        self.assertEqual(before,(Path.read_bytes,Path.read_text))

    def test_factor_partial_fragments_remain_partial_and_bad_scope_rejected(self):
        result = FixtureExecutor(factor_partial=True)('adjustment_factors',
            {'codes':['sh.600001','sz.000001'],'start_date':'1990-01-01','end_date':'2026-10-15'},self.root/'direct')
        envelope = cache_module.factor_contract(result)
        self.assertEqual(envelope['parent_status'],'PARTIAL')
        self.assertEqual(envelope['independent_complete_codes'],['sh.600001'])
        result['coverage']['requested_codes']=['sh.600001']
        with self.assertRaisesRegex(ValueError,'COVERAGE_DIFFERS'):cache_module.factor_contract(result)

    def test_seed_direct_trio_uses_only_locator_and_offline_reads(self):
        direct = self.root/'old-direct'/'request-01'
        result = FixtureExecutor()('market_daily',{'date':'2026-10-15'},direct)
        receipt = direct/'raw'/(result['request_receipt_id']+'.receipt.json')
        physical = json.loads(receipt.read_bytes()); raw=receipt.parent/(physical['content_hash']+'.raw.json')
        before = (cache_module._fp(raw),cache_module._fp(receipt))
        seed=self.root/'seed.json';seed.write_text(json.dumps(dict(sources=[dict(result=str(direct/'request-TEST_FIXTURE.json'),raw=str(raw),receipt=str(receipt),archive_root=str(direct))])))
        cache=self.cache(offline=True)
        summary=cache.import_seed(seed)
        self.assertEqual(summary['captures'],1)
        spec=cache_module.RequestSpec('market_daily',{'date':'2026-10-15'},'2026-10-15')
        record=cache.capture_one(spec)
        self.assertTrue(record.metadata['verification_locator']['owned'])
        self.assertEqual((cache_module._fp(raw),cache_module._fp(receipt)),before)
        self.assertEqual(cache.verify(record)['rows'],result['rows'])
        with self.assertRaisesRegex(ValueError,'OFFLINE_SOURCE_MISSING'):
            cache.capture_one(cache_module.RequestSpec('master_all',{'date':'2026-10-15'},'2026-10-15'))

    def test_collection_complete_window_fallback_and_next_session(self):
        executor=FixtureExecutor(basic_failure=True);cache=self.cache(executor,calendar_lookback_days=110,calendar_lookahead_days=7)
        now=datetime.now(cache_module.SH)+timedelta(minutes=1)
        collection=cache_module.capture_collect(cache,now=now)
        self.assertEqual(len(collection.price_sessions),25)
        self.assertEqual(len(collection.context_records),50)
        self.assertEqual({r.parameters['date'] for r in collection.context_records}, set(collection.price_sessions))
        self.assertTrue(collection.ready_for_core)
        self.assertGreater(collection.target_session,collection.observation_date)
        self.assertEqual(collection.completeness,'PARTIAL')
        self.assertTrue(any(r['request_spec']['endpoint']=='stock_basic_all' for r in collection.failures))
        fallback=[params['codes'] for endpoint,params in executor.calls if endpoint=='stock_basic']
        self.assertEqual(fallback,[['sh.688001']])
        self.assertFalse(any(r.endpoint=='stock_basic_all' and r.status=='FETCH_FAILED' for r in collection.core_records))
        self.assertEqual(len(collection.prescreen_records), 1)
        self.assertEqual(collection.prescreen_records[0].endpoint, 'lhb_summary')
        self.assertEqual(collection.prescreen_records[0].parameters,
                         cache_module.lhb_request_spec(collection.price_sessions).parameters)
        self.assertEqual(collection.to_dict()['prescreen_records'],
                         [collection.prescreen_records[0].to_dict()])
        self.assertFalse(any(r.endpoint == 'lhb_summary'
                             for r in collection.core_records + collection.context_records))
        self.assertEqual(collection.identity_coverage['factor_query_codes'], ['sh.600001'])
        factor_calls = [params for endpoint, params in executor.calls if endpoint == 'adjustment_factors']
        self.assertEqual(factor_calls, [dict(codes=['sh.600001'], start_date='1990-01-01',
                                           end_date=collection.observation_date)])
        endpoints = [endpoint for endpoint, _ in executor.calls]
        self.assertEqual(endpoints.count('lhb_summary'), 1)
        self.assertLess(endpoints.index('lhb_summary'), endpoints.index('adjustment_factors'))
        run=dict(results={'SHORT_HORIZON_STRENGTH_R2':dict(items=[dict(instrument_id='600001.SH',research_selected=True)],research_selected_ids=['600001.SH'])})
        calls = len(executor.calls)
        sources=cache_module.capture_theme_sources(cache,core_run=run,collection=collection)
        self.assertEqual(len(executor.calls), calls)
        self.assertEqual(sources.requested_industry_nodes, ())
        self.assertEqual(sources.requested_concept_symbols, ())
        self.assertFalse(sources.parent_acquisition['context_ready'])
        self.assertTrue(sources.parent_acquisition['missing_source_is_not_empty'])
        core_ids={r.receipt_id for r in collection.core_records}
        self.assertFalse(core_ids & {r['receipt_id'] for r in sources.context_captures})
        self.assertFalse(sources.parent_acquisition['display_amount_filter_applied'])

    def test_lhb_incomplete_capture_is_retained_without_changing_core_scope_or_retry(self):
        for status in ('PARTIAL', 'FETCH_FAILED'):
            with self.subTest(status=status):
                executor = FixtureExecutor(lhb_status=status)
                cache = cache_module.CaptureCache(self.root / status, FixtureBudget(),
                    config=dict(allow_test_fixture=True, calendar_lookback_days=110,
                                calendar_lookahead_days=7), executor=executor)
                collection = cache_module.capture_collect(cache,
                    now=datetime.now(cache_module.SH) + timedelta(minutes=1))
                self.assertTrue(collection.ready_for_core)
                self.assertEqual(collection.completeness, 'PARTIAL')
                self.assertEqual(collection.prescreen_records[0].status, status)
                self.assertEqual([row['request_spec']['endpoint'] for row in collection.failures],
                                 ['lhb_summary'])
                self.assertEqual(collection.identity_coverage['factor_query_codes'], ['sh.600001'])
                self.assertFalse(any(row.endpoint == 'lhb_summary' for row in collection.core_records))
                prior_calls = list(executor.calls)
                retained = cache.capture_one(cache_module.lhb_request_spec(collection.price_sessions))
                self.assertEqual(retained.receipt_id, collection.prescreen_records[0].receipt_id)
                self.assertEqual(executor.calls, prior_calls)

    def test_lhb_stage_respects_remaining_http_budget_before_any_lhb_request(self):
        class ReservingBudget(FixtureBudget):
            def preclaim_request(self, endpoint, params):
                super().preclaim_request(endpoint, params)
                if provider_requests.ENDPOINTS[endpoint]['provider'] != 'baostock':
                    self.calls['http_reserved'] += self.limits['request_http_attempts']
        budget = ReservingBudget()
        budget.limits['max_http_attempts'] = 3 * budget.limits['request_http_attempts']
        executor = FixtureExecutor()
        cache = cache_module.CaptureCache(self.root / 'bounded', budget,
            config=dict(allow_test_fixture=True, calendar_lookback_days=110,
                        calendar_lookahead_days=7), executor=executor)
        with self.assertRaisesRegex(ValueError, 'COMPLETE_STAGE_REQUEST_BUDGET_REQUIRED:max_http_attempts'):
            cache_module.capture_collect(cache, now=datetime.now(cache_module.SH) + timedelta(minutes=1))
        endpoints = [endpoint for endpoint, _ in executor.calls]
        self.assertIn('stock_basic_all', endpoints)
        self.assertNotIn('lhb_summary', endpoints)
        self.assertNotIn('adjustment_factors', endpoints)

    def test_raw_only_seed_keeps_original_typed_clock_and_has_no_result(self):
        from stockfish_core.yaogu.contracts import SourceReceipt
        direct=self.root/'raw-only'
        result=FixtureExecutor()('market_daily',{'date':'2026-10-15'},direct)
        receipt=direct/'raw'/(result['request_receipt_id']+'.receipt.json')
        physical=json.loads(receipt.read_bytes())
        raw=receipt.parent/(physical['content_hash']+'.raw.json')
        typed=SourceReceipt(receipt_id=physical['receipt_id'],provider='TEST_FIXTURE',dataset='market_daily',
            content_hash=physical['content_hash'],field_version=physical['field_version'],row_count=2,
            pagination_complete=True,status='COMPLETE',parameters=physical['parameters'],
            event_time=datetime(2026,10,15,15,tzinfo=cache_module.SH),
            source_published_at=physical['first_seen_at'],first_seen_at=physical['first_seen_at'],
            ingested_at=physical['ingested_at']).model_dump(mode='json')
        seed=self.root/'raw-seed.json'
        seed.write_text(json.dumps(dict(raw_receipt_sources=[dict(raw=str(raw),receipt=str(receipt),typed_receipt=typed)])))
        cache=self.cache(offline=True)
        summary=cache.import_seed(seed)
        self.assertEqual((summary['captures'],summary['raw_recipes']),(0,1))
        recipe=cache.context_recipe('market_daily','2026-10-15')
        self.assertEqual(recipe['typed_receipt'],typed)
        self.assertEqual(set(recipe['raw']),{'path','sha256','bytes','fp'})
        self.assertNotIn('result',recipe)
        self.assertIsNone(cache.lookup(cache_module.RequestSpec('market_daily',{'date':'2026-10-15'},'2026-10-15')))
        cache.terminal()

    def test_provider_release_cutoff_uses_eighteen_and_zero_selection_skips_members(self):
        executor=FixtureExecutor();cache=self.cache(executor,calendar_lookback_days=110,calendar_lookahead_days=7)
        natural=datetime.now(cache_module.SH).date()
        now=datetime.combine(natural,cache_module.time(17),cache_module.SH)
        collection=cache_module.capture_collect(cache,now=now)
        self.assertLess(collection.observation_date,str(natural))
        calls=len(executor.calls)
        run=dict(results={'SHORT_HORIZON_STRENGTH_R2':dict(items=[],research_selected_ids=[])})
        sources=cache_module.capture_theme_sources(cache,core_run=run,collection=collection)
        self.assertEqual(len(executor.calls),calls)
        self.assertEqual(sources.parent_acquisition['native_confirmation'],'UNKNOWN_NOT_COMPUTED')

    def test_real_runtime_meter_and_cache_request_scope_binding(self):
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        (self.root/'job').mkdir()
        budget=Budget(job_dir=self.root/'job',cache_dir=self.root/'cache')
        cache=cache_module.CaptureCache(self.root/'cache',budget,
            config={'allow_test_fixture':True},executor=FixtureExecutor())
        with budget.phase('capture'),budget.meter_files():
            spec=cache_module.RequestSpec('market_daily',{'date':'2026-10-15'},'2026-10-15')
            record=cache.capture_one(spec)
            cache.verify(record)
            cache.terminal()
        self.assertGreater(budget.metrics['returned_read_bytes']['capture'],0)
        altered=record.metadata
        altered['request_spec']=cache_module.RequestSpec('market_daily',{'date':'2026-10-14'},'2026-10-14').to_dict()
        altered['request_key']=cache_module.RequestSpec(**altered['request_spec']).key
        with self.assertRaisesRegex(ValueError,'CACHE_REQUEST_DOES_NOT_MATCH'):
            cache.verify(altered)

    def test_full_known_capture_stage_preflight_and_registered_transport_cap(self):
        executor=FixtureExecutor();cache=self.cache(executor,calendar_lookback_days=110)
        cache.budget.limits['max_requests']=50
        with self.assertRaisesRegex(ValueError,'COMPLETE_STAGE_REQUEST_BUDGET_REQUIRED'):
            cache_module.capture_collect(cache,now=datetime.now(cache_module.SH)+timedelta(minutes=1))
        self.assertEqual([endpoint for endpoint,_ in executor.calls],['calendar'])
        with self.assertRaisesRegex(ValueError,'BOUNDED_PROVIDER_POLICY_REQUIRED'):
            self.cache(max_http_requests_per_request=65)
        with self.assertRaisesRegex(ValueError,'CACHE_CONSTRUCTION_POLICY_MISMATCH:offline'):
            cache_module.capture_collect(cache,now=datetime.now(cache_module.SH),config={'offline':True})
        self.assertEqual([endpoint for endpoint,_ in executor.calls],['calendar'])

    def test_unknown_config_and_optional_members_never_spend_request_budget(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN_CAPTURE_CONFIGURATION'):
            self.cache(typo_retry=True)
        cache=self.cache();cache.budget.limits['max_requests']=2
        run=dict(results={'SHORT_HORIZON_STRENGTH_R2':dict(items=[dict(instrument_id='600001.SH',research_selected=True)],research_selected_ids=['600001.SH'])})
        collection=cache_module.CaptureCollection('2026-10-15','2026-10-16',(),(),(),(),(),(),(),{},True)
        sources = cache_module.capture_theme_sources(cache,core_run=run,collection=collection)
        self.assertFalse(sources.parent_acquisition['context_ready'])
        self.assertEqual(cache.executor.calls, [])
        specs = [cache_module.RequestSpec('industry_members_sina', {'symbol':'hangye_TEST_FIXTURE_'+str(i)},
            '2026-10-15', current=True) for i in range(3)]
        with self.assertRaisesRegex(ValueError, 'COMPLETE_STAGE_REQUEST_BUDGET_REQUIRED'):
            cache_module._preflight_requests(cache, specs)
        self.assertEqual(cache.executor.calls, [])


if __name__=='__main__':
    unittest.main()
