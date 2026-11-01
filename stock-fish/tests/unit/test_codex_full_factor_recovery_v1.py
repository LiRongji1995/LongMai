"""TEST_FIXTURE historical originals and SDK; no real state or network."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import codex_full_factor_recovery_v1 as full
from scripts import build_premarket_source_bundle_v3 as builder
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu import provider_requests, providers
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, DEFAULT_LIMITS

ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-10-28T16:10:00+00:00'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(full.canonical(value))
    return builder.read(path)[1]


class OldClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat(NOW)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture(scope='module')
def source_bundle(tmp_path_factory):
    root = tmp_path_factory.mktemp('TEST_FIXTURE_factor_bundle').resolve()
    value = builder.create(ROOT, root / 'source')
    return root / 'source', value['manifest']['sha256']


@pytest.fixture
def historical(tmp_path, monkeypatch, source_bundle):
    root = tmp_path.resolve() / 'TEST_FIXTURE_original'
    root.mkdir()
    job = root / 'jobs/old-failed'
    job.mkdir(parents=True)
    limits = dict(DEFAULT_LIMITS, max_seconds=10800, max_requests=1024,
        max_sdk_calls=10000, max_http_attempts=32768, cache_bytes=1024**3,
        capture_read_bytes=32*1024**3, supervisor_read_bytes=8*1024**3,
        self_rss_bytes=3*1024**3, tree_rss_bytes=3*1024**3)
    configuration = dict(limits=limits, state_root=str(root), capture=dict(batch_size=50, retry_failed=False))
    configref = save(root / 'configuration.json', configuration)
    save(job / 'configuration.json', configuration)
    registryref = save(job / 'code-registry.json', [dict(test_fixture=True)])
    codes = ['sh.' + str(600000+i) for i in range(150)] + ['sz.000001']
    originals = []
    failure = {'mode': None}

    def executor(endpoint, parameters, output, **_kwargs):
        provider = provider_requests.ENDPOINTS[endpoint]['provider']
        status = 'PARTIAL' if failure['mode'] else 'COMPLETE'
        if endpoint == 'master_all':
            rows = [dict(code=code, code_name='TEST_FIXTURE') for code in codes]
        elif endpoint == 'mainboard_sse':
            rows = [{'证券代码':code[3:], '上市日期':'2000-01-01'} for code in codes if code.startswith('sh.')]
        elif endpoint == 'master_szse':
            rows = [{'A股代码':'000001', '板块':'主板', 'A股上市日期':'2000-01-01'}]
        else:
            selected = parameters['codes'][:2] if failure['mode'] else parameters['codes']
            rows = [dict(code=c, dividOperateDate='2000-01-01', backAdjustFactor='1',
                         foreAdjustFactor='1', adjustFactor='1') for c in selected]
        coverage = {}
        if endpoint == 'adjustment_factors':
            coverage = dict(requested_codes=parameters['codes'], security_statuses=[dict(code=c,
                status='COMPLETE' if any(r['code']==c for r in rows) else 'FETCH_FAILED',
                row_count=sum(r['code']==c for r in rows), cursor_exhausted=any(r['code']==c for r in rows),
                captured_at=NOW) for c in parameters['codes']])
        payload = dict(status=status, rows=rows, coverage=coverage, pagination_complete=status=='COMPLETE',
                       first_seen_at=NOW, field_version='TEST_FIXTURE_FACTOR_RECOVERY')
        if failure['mode'] == 'missing_clock':
            payload.pop('first_seen_at')
        with monkeypatch.context() as patch:
            patch.setattr(providers, 'datetime', OldClock)
            receipt = providers.archive_receipt(Path(output)/'raw', provider, endpoint, parameters, payload,
                status=status, pagination_complete=status=='COMPLETE', field_version=payload['field_version'], first_seen_at=NOW)
        result = dict(endpoint=endpoint, provider=provider, parameters=parameters, rows=rows,
            status=status, coverage=coverage, pagination_complete=status=='COMPLETE', gaps=[],
            field_version=payload['field_version'], started_at=NOW, completed_at=NOW,
            request_receipt_id=receipt['receipt_id'], receipts=[receipt], diagnostics={},
            implementation_source_hashes={name: hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                for name in native.PRODUCERS})
        save(Path(output)/'request-TEST_FIXTURE.json', result)
        return result

    cache = native.CaptureCache(root/'cache', Budget(limits), executor=executor)
    for ep in ('master_all','mainboard_sse','master_szse'):
        spec = native.RequestSpec(ep, {'date':full.OBSERVATION} if ep=='master_all' else {},
            full.OBSERVATION if ep=='master_all' else full.TARGET, role='FULL_MARKET_CONTEXT' if ep=='master_all' else 'CURRENT_OFFICIAL', current=ep!='master_all')
        originals.append(cache.capture_one(spec).to_dict())
    specs = full._batch_specs(codes)
    originals.append(cache.capture_one(specs[0]).to_dict())
    failure['mode'] = 'partial'
    originals.append(cache.capture_one(specs[1]).to_dict())
    failure['mode'] = 'missing_clock'
    with pytest.raises(ValueError, match='ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED'):
        cache.capture_one(specs[2])
    completion = dict(status='FAILED', phases=dict(capture='FAILED', core='NOT_RUN'),
        expected_sessions=[full.OBSERVATION,full.TARGET,NOW],
        error=dict(reason='ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED'),
        metrics=dict(limits=limits, elapsed_seconds=1625.210,
            calls=dict(requests=25,sdk_reserved=948,http_reserved=192,sdk_actual=None,http_actual=None,
                       sdk_actual_known_subtotal=896,http_actual_known_subtotal=3,actual_unknown=2),
            returned_read_bytes=dict(capture=2998127258)))
    cref = save(job/'worker-completion.json',completion)
    save(job/'final-control.json',dict(status='FAILED',job=str(job),external_worker_reaped=True,
        completion=cref,code_registry=registryref,elapsed_seconds=1628.056,supervisor_returned_read_bytes=46510618))
    directory=root/'TEST_FIXTURE_runtime_scope'
    save(directory/'operations/old.json',dict(phase='FAILED_OR_UNKNOWN',
        started_at='2026-10-28T16:00:00+00:00',finished_at='2026-10-28T16:41:59.627247+00:00',
        identity=dict(observation_date=full.OBSERVATION,target_session=full.TARGET,backend='premarket',
            execution_engine='original',configuration=configref)))
    runtime=SimpleNamespace(profile=dict(state_root=str(root),config=str(root/'configuration.json'),source_root=str(ROOT)),
                            directory=directory,check_binding=lambda:None)
    baseline=full.budget_baseline(runtime,job)
    budget=Budget(baseline['remaining_limits'])
    budget.utc_started_at=datetime.now(timezone.utc)
    value=full.plan(runtime,*source_bundle,job,budget=budget)
    return SimpleNamespace(root=root,job=job,runtime=runtime,source_bundle=source_bundle,
        baseline=baseline,plan=value,budget=budget,codes=codes,specs=specs,originals=originals)


def test_plan_rebuilds_original_scope_and_exact_failed_keys(historical):
    p=historical.plan
    assert p['requested_codes']==historical.codes
    assert [r['action'] for r in p['batches']]==['REUSE_COMPLETE','RETRY_PARTIAL_ONCE','RETRY_UNAUTHENTICATED_ONCE','NEW_ONCE']
    assert p['planned_new_calls']==dict(requests=3,sdk_reserved=107,http_reserved=0)
    assert p['budget_baseline']['consumed']['wall_seconds']==2580
    assert p['budget_baseline']['remaining_limits']['max_seconds']==8220


class Cursor:
    fields=['code','dividOperateDate','backAdjustFactor','foreAdjustFactor','adjustFactor']
    error_code='0'
    def __init__(self,code):self.code,self.done=code,False
    def next(self):
        if self.done:return False
        self.done=True;return True
    def get_row_data(self):return [self.code,'2000-01-01','1','1','1']


def install_sdk(historical,monkeypatch, *, fail=False):
    from scripts import codex_factor_capture_v3 as source
    path=historical.source_bundle[0]/source.WORKER
    spec=importlib.util.spec_from_file_location('stockfish_core.yaogu.TEST_FIXTURE_recovery_worker',path)
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    class SDK:
        __version__='TEST_FIXTURE'
        def login(self):return SimpleNamespace(error_code='0')
        def logout(self):pass
        def query_adjust_factor(self,*,code,**_kwargs):
            return SimpleNamespace(error_code='10002007') if fail and code==historical.codes[50] else Cursor(code)
    monkeypatch.setitem(sys.modules,'baostock',SDK())
    def run(_command,*,input,**_kwargs):
        request=json.loads(input);worker._transport=request;worker._attempt_counts={'HTTP':0,'SDK':0}
        result=worker._baostock_session_request(request['endpoint'],request['parameters'],request['partial_path'])
        result['coverage'].update(http_attempts=0,sdk_operations=worker._attempt_counts['SDK'])
        return SimpleNamespace(returncode=0,stdout=json.dumps(result))
    monkeypatch.setattr(subprocess,'run',run)


@pytest.mark.parametrize('partial',[False,True])
def test_collect_appends_original_cache_and_is_reusable_without_clock_rewrite(historical,monkeypatch,partial):
    install_sdk(historical,monkeypatch,fail=partial)
    job=historical.root/'TEST_FIXTURE_new_job';job.mkdir()
    events=[]
    answer=full.collect(historical.plan,job,historical.budget,checkpoint=events.append)
    assert answer['source_complete'] is (not partial)
    assert answer['requested_codes_count']==151 and answer['new_attempts']==3
    assert answer['original_complete_reused']==1 and not answer['historical_pit_restored']
    assert [e['event'] for e in events].count('AFTER_RESULT')==3
    fresh=native.CaptureCache(historical.root/'cache',historical.budget,dict(offline=True))
    assert fresh.lookup(historical.specs[0]).metadata==historical.originals[3]
    for spec in historical.specs[1:]:
        record=fresh.lookup(spec)
        assert record.status==('PARTIAL' if partial and spec==historical.specs[1] else 'COMPLETE')
    full.verify_result(answer,historical.plan,job,historical.budget)
    with pytest.raises(ValueError,match='ORIGINAL_CACHE_CHANGED'):
        full.collect(historical.plan,job,historical.budget,checkpoint=events.append)


def test_rehashed_scope_truncation_rejected(historical):
    p=json.loads(json.dumps(historical.plan));p['requested_codes'].pop();p['requested_codes_count']-=1
    p['scope_sha256']=full.digest(p['requested_codes']);p['plan_hash']=full.digest({k:v for k,v in p.items() if k!='plan_hash'})
    with pytest.raises(ValueError,match='EXACT_FULL_SCOPE'):
        full._validate_plan(p,historical.budget)


def test_original_file_change_cannot_be_treated_as_missing(historical):
    path=Path(historical.originals[3]['raw']['path']);path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='SOURCE_REFERENCE_CHANGED'):
        full._validate_plan(historical.plan,historical.budget)


def test_actual_utc_expiry_prevents_any_provider_after_sleep(historical, monkeypatch):
    calls=[]
    monkeypatch.setattr(full,'_parent',lambda _value:calls.append('provider_loaded'))
    historical.budget.utc_started_at=datetime.now(timezone.utc)-timedelta(seconds=historical.budget.limits['max_seconds']+1)
    job=historical.root/'TEST_FIXTURE_after_sleep';job.mkdir()
    with pytest.raises(ValueError,match='REAL_WALL_DEADLINE_EXCEEDED'):
        full.collect(historical.plan,job,historical.budget,checkpoint=lambda _event:None)
    assert not calls


def test_request_timeout_uses_less_than_remaining_real_wall(historical, monkeypatch):
    install_sdk(historical,monkeypatch)
    original=subprocess.run
    timeouts=[]
    def run(*args,**kwargs):
        timeouts.append(kwargs['timeout'])
        return original(*args,**kwargs)
    monkeypatch.setattr(subprocess,'run',run)
    historical.budget.remaining_seconds=lambda:17.5
    job=historical.root/'TEST_FIXTURE_short_wall';job.mkdir()
    full.collect(historical.plan,job,historical.budget,checkpoint=lambda _event:None)
    assert timeouts==[17.5,17.5,17.5]
