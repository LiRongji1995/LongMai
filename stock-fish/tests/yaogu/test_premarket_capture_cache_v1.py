"""Isolated TEST_FIXTURE source bytes; no SDK, network, business DB or selector."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
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
    def __init__(self, *, basic_failure=False, factor_partial=False, crash=False):
        self.calls = []
        self.basic_failure = basic_failure
        self.factor_partial = factor_partial
        self.crash = crash

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
        spec = cache_module.RequestSpec('stock_basic',{'codes':['sh.600001']},'2026-10-15',current=True)
        spec.parameters['codes'].append('sh.600002')
        self.assertEqual(spec.parameters['codes'],['sh.600001'])
        with self.assertRaisesRegex(ValueError,'ASCII_REQUEST'):
            cache_module.RequestSpec('stock_basic',{'codes':['sh.６００００１']})

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

    def test_corrupt_cached_original_does_not_become_a_miss_or_redownload(self):
        executor = FixtureExecutor(); cache = self.cache(executor)
        spec = cache_module.RequestSpec('market_daily',{'date':'2026-10-15'},'2026-10-15')
        record = cache.capture_one(spec)
        raw = Path(record.metadata['raw']['path']);raw.write_bytes(raw.read_bytes().replace(b'500000000',b'400000000'))
        with self.assertRaises(ValueError):cache.capture_one(spec)
        self.assertEqual(len(executor.calls),1)

    def test_failed_original_stays_failed_and_explicit_retry_is_single(self):
        executor = FixtureExecutor(basic_failure=True); cache = self.cache(executor)
        spec = cache_module.RequestSpec('stock_basic_all',{},'2026-10-15',current=True)
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
        spec = cache_module.RequestSpec('stock_basic_all',{},'2026-10-15',current=True)
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
        self.assertEqual(len(collection.context_records),134)
        self.assertTrue(collection.ready_for_core)
        self.assertGreater(collection.target_session,collection.observation_date)
        self.assertEqual(collection.completeness,'PARTIAL')
        self.assertTrue(any(r['request_spec']['endpoint']=='stock_basic_all' for r in collection.failures))
        fallback=[params['codes'] for endpoint,params in executor.calls if endpoint=='stock_basic']
        self.assertEqual(fallback,[['sh.688001']])
        self.assertFalse(any(r.endpoint=='stock_basic_all' and r.status=='FETCH_FAILED' for r in collection.core_records))
        run=dict(results={'SHORT_HORIZON_STRENGTH_R2':dict(items=[dict(instrument_id='600001.SH',research_selected=True)],research_selected_ids=['600001.SH'])})
        sources=cache_module.capture_theme_sources(cache,core_run=run,collection=collection)
        self.assertEqual(len(sources.requested_industry_nodes),2)
        self.assertEqual(len(sources.requested_concept_symbols),1)
        core_ids={r.receipt_id for r in collection.core_records}
        self.assertFalse(core_ids & {r['receipt_id'] for r in sources.context_captures})
        self.assertFalse(sources.parent_acquisition['display_amount_filter_applied'])

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
        cache.budget.limits['max_requests']=100
        with self.assertRaisesRegex(ValueError,'COMPLETE_STAGE_REQUEST_BUDGET_REQUIRED'):
            cache_module.capture_collect(cache,now=datetime.now(cache_module.SH)+timedelta(minutes=1))
        self.assertEqual([endpoint for endpoint,_ in executor.calls],['calendar'])
        with self.assertRaisesRegex(ValueError,'BOUNDED_PROVIDER_POLICY_REQUIRED'):
            self.cache(max_http_requests_per_request=65)
        with self.assertRaisesRegex(ValueError,'CACHE_CONSTRUCTION_POLICY_MISMATCH:offline'):
            cache_module.capture_collect(cache,now=datetime.now(cache_module.SH),config={'offline':True})
        self.assertEqual([endpoint for endpoint,_ in executor.calls],['calendar'])

    def test_unknown_config_and_complete_member_plan_budget_fail_before_members(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN_CAPTURE_CONFIGURATION'):
            self.cache(typo_retry=True)
        cache=self.cache();cache.budget.limits['max_requests']=2
        run=dict(results={'SHORT_HORIZON_STRENGTH_R2':dict(items=[dict(instrument_id='600001.SH',research_selected=True)],research_selected_ids=['600001.SH'])})
        collection=cache_module.CaptureCollection('2026-10-15','2026-10-16',(),(),(),(),(),(),(),{},True)
        with self.assertRaisesRegex(ValueError,'COMPLETE_STAGE_REQUEST_BUDGET_REQUIRED'):
            cache_module.capture_theme_sources(cache,core_run=run,collection=collection)
        self.assertFalse(any(endpoint.endswith('members_sina') for endpoint,_ in cache.executor.calls))


if __name__=='__main__':
    unittest.main()
