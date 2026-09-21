"""Explicit TEST_FIXTURE: single-request harness and source suitability boundaries."""
from copy import deepcopy
from datetime import datetime, time as day_time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import pytest

from stockfish_core.api import create_app
from stockfish_core.observed_data import exchange_calendar_2026
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.e2e_tracing import DailyPrimaryTrace
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.providers import archive_receipt
from stockfish_core.yaogu.strategy_experiments import _prepare_context, run_active_daily_strategy
from tests.r03_sources.test_provider_boundaries import DAY, result, raw, master
from tests.r05_product.test_r14_daily_fast_path import _wait_job
from tests.r05_product.test_r16_r1_e2e import assert_measured

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import r16_service_path_trace as harness
from r16_r1_evidence_assets import register_isolated_input, load_bundle

SESSIONS = tuple(s for s in exchange_calendar_2026().sessions if s <= DAY)
NOW = datetime.combine(DAY,day_time(20),timezone.utc)
NEXT = min(s for s in exchange_calendar_2026().sessions if s > DAY)
LATER = min(s for s in exchange_calendar_2026().sessions if s > NEXT)


def sources(day=DAY, *, provider='TEST_FIXTURE', version='TEST_FIXTURE_R03'):
    stamp = datetime.combine(day,day_time(20),timezone.utc)
    sessions = [s for s in exchange_calendar_2026().sessions if s <= day]
    values = [result('mainboard_sse',[master()],stamp=stamp),result('master_szse',[],stamp=stamp),
        result('delisting_sse',[],stamp=stamp),
        result('adjustment_factors',[{'code':'sh.600000','dividOperateDate':'2000-01-01','backAdjustFactor':'2'}],
            {'codes':['sh.600000'],'start_date':'1990-01-01','end_date':str(day)},stamp=stamp)]
    values.extend(result('market_daily',[raw(session=s)],{'date':str(s)},stamp=stamp)
                  for s in sessions[-6:])
    for value in values:
        value['provider'],value['field_version'] = provider,version
    return values


def make_base(tmp_path, **kwargs):
    store = SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    source = sources(**kwargs)
    bundle, _ = normalize_capture_with_report(source,DAY,NOW,SESSIONS,
        history_store=HistoryArtifactStore(store),coverage_type='TEST_FIXTURE')
    return store,bundle,source


def test_retained_reference_cannot_create_next_session_known_identity(tmp_path):
    store,base,old = make_base(tmp_path)
    old_receipts = {r.receipt_id:r for r in base.receipts}
    old_parts = HistoryArtifactStore(store).manifest(base.history_artifact_ref)['partitions']
    capture = result('market_daily',[raw(session=LATER)],{'date':str(LATER)},
        stamp=datetime.combine(LATER,day_time(20),timezone.utc))
    retained = [v for v in old if v['endpoint']!='market_daily']
    new, report = normalize_capture_with_report(retained+[capture],LATER,
        datetime.combine(LATER,day_time(21),timezone.utc),(*SESSIONS,NEXT,LATER),
        history_store=HistoryArtifactStore(store),history_base_ref=base.history_artifact_ref,
        coverage_type='TEST_FIXTURE',retained_identity_base=base)
    assert not new.universe.complete
    state = next(s for s in new.universe.securities if s.effective_from==LATER)
    assert state.board == 'UNKNOWN' and state.delisting_period is None
    assert state.st is False and state.suspended is False  # Actual new raw fields.
    assert len(_prepare_context(new,datetime.combine(LATER,day_time(21),timezone.utc)).eligible_ids) == 0
    bar = next(b for b in new.bars if b.session==LATER)
    assert bar.open == 10 and bar.high == 11 and bar.close == 11
    assert bar.comparable_close is None and capture['request_receipt_id'] in bar.source_refs
    assert any(g.get('reason')=='FACTOR_CAPTURE_END_BEFORE_RAW_SESSION' for g in report['gaps'])
    assert NEXT in new.sessions and not any(b.session==NEXT for b in new.bars)
    assert all(r == old_receipts[r.receipt_id] for r in new.receipts if r.receipt_id in old_receipts)
    after = HistoryArtifactStore(store).manifest(new.history_artifact_ref)
    assert {p['session']:p for p in after['partitions'] if p['session']<=str(DAY)} == {p['session']:p for p in old_parts}
    stored = list(HistoryArtifactStore(store).iter_rows(new.history_artifact_ref,'security',start=LATER,end=LATER))
    assert any(s.effective_from==LATER and s.board=='UNKNOWN' and s.delisting_period is None for s in stored)
    assert report['retained_identity_scope']['receipts']


@pytest.mark.parametrize('mutation',['unbound','changed','wrong_base'])
def test_retained_only_mode_rejects_unbound_or_changed_identity(tmp_path,mutation):
    store,base,old = make_base(tmp_path)
    incoming = deepcopy([v for v in old if v['endpoint']!='market_daily'])
    expected = {'unbound':'UNBOUND_CURRENT_IDENTITY','changed':'RETAINED_IDENTITY_RECEIPT_CHANGED',
                'wrong_base':'RETAINED_IDENTITY_REQUIRES_MATCHING'}[mutation]
    base_ref = base.history_artifact_ref
    if mutation=='unbound':
        incoming[0] = result('mainboard_sse',[master()],stamp=NOW+timedelta(days=10),identity='NEW_UNBOUND')
    elif mutation=='changed':
        incoming[0]['rows'].append(master('600001'))
    else:
        base_ref = 'history_NOT_THE_BOUND_BASE'
    with pytest.raises(ValueError,match=expected):
        normalize_capture_with_report(incoming,NEXT,NOW+timedelta(days=20),(*SESSIONS,NEXT),
            history_store=HistoryArtifactStore(store),history_base_ref=base_ref,
            coverage_type='TEST_FIXTURE',retained_identity_base=base)


def test_current_same_session_fixture_preserves_nonempty_strategy_and_pv(tmp_path):
    # The current directory response and delisting observation explicitly belong
    # to this fixture's same target-session capture, not to a retained base.
    store,bundle,_ = make_base(tmp_path)
    ctx = _prepare_context(bundle,NOW)
    assert ctx.eligible_ids == ['600000.SH']
    computed = run_active_daily_strategy(bundle,cutoff=NOW)
    assert computed['results']['STAGE_AWARE_MOMENTUM_V2']['counts']['eligible'] == 1
    assert computed['data_readiness']['PV_READINESS']['coverage']['ALL']['numerator'] == 1


def archive_capture(path,day, *, mutation=None):
    stamp = datetime.now(timezone.utc).isoformat()
    rows = [raw(session=day)]
    if mutation=='duplicate': rows.append(dict(rows[0]))
    if mutation=='wrong_day': rows[0]['date']=str(DAY)
    payload = {'rows':rows,'status':'PARTIAL' if mutation=='partial' else 'COMPLETE',
        'field_version':'BAOSTOCK_00.9.30_R03_V1','pagination_complete':mutation!='partial',
        'coverage':{'http_attempts':0,'sdk_operations':3},'TEST_FIXTURE':True}
    receipt = archive_receipt(Path(path)/'raw','baostock','market_daily',{'date':str(day)},payload,
        status=payload['status'],pagination_complete=payload['pagination_complete'],
        field_version=payload['field_version'],first_seen_at=stamp)
    value = {**payload,'provider':'baostock','endpoint':'market_daily','parameters':{'date':str(day)},
        'receipts':[receipt],'request_receipt_id':receipt['receipt_id']}
    if mutation=='no_receipt':value['receipts']=[]
    if mutation=='corrupt_raw':
        (Path(path)/'raw'/(receipt['content_hash']+'.raw.json')).write_bytes(b'{}')
    return value


@pytest.mark.parametrize('mutation,reason',[(None,None),('duplicate','LIVE_DUPLICATE_PRICE_KEY'),
    ('wrong_day','LIVE_RAW_IDENTITY_SESSION'),('partial','LIVE_SINGLE_SESSION_COMPLETE'),
    ('no_receipt','LIVE_REQUEST_RECEIPT_NOT_UNIQUE'),('corrupt_raw','LIVE_ARCHIVE_CONTENT_MISMATCH')])
def test_live_result_archive_proof_is_not_an_unchecked_receipt_label(tmp_path,mutation,reason):
    capture = archive_capture(tmp_path,LATER,mutation=mutation)
    if reason:
        with pytest.raises(ValueError,match=reason):
            harness.validate_live_daily_capture(capture,tmp_path,LATER,datetime.now(timezone.utc))
    else:
        proof = harness.validate_live_daily_capture(capture,tmp_path,LATER,datetime.now(timezone.utc))
        assert proof['row_count']==1 and proof['raw_sha256']==capture['receipts'][0]['content_hash']


@pytest.mark.parametrize('telemetry_failure',[False,True])
def test_one_request_fixture_reaches_real_api_commit_and_result_ready_with_unknown_identity(tmp_path,monkeypatch,telemetry_failure):
    store,base,source = make_base(tmp_path,provider='baostock',version='BAOSTOCK_00.9.30_R03_V1')
    base_id = register_isolated_input(store,base)
    assets = tmp_path/'TEST_FIXTURE-assets';assets.mkdir()
    inventory = {'asset_bindings':{}}
    for index,value in enumerate(source):
        path = assets/(str(index)+'.json');data=json.dumps(value,ensure_ascii=False).encode();path.write_bytes(data)
        rid = value['request_receipt_id']
        inventory['asset_bindings'][rid] = [{'path':str(path),'file_sha256':hashlib.sha256(data).hexdigest(),
            'result_canonical_sha256':digest(value),'source_content_hash':value['receipts'][0]['content_hash']}]
    inventory_path = tmp_path/'TEST_FIXTURE-inventory.json';inventory_path.write_text(json.dumps(inventory),encoding='utf-8')
    output = tmp_path/'TEST_FIXTURE-live';output.mkdir()
    calls = []
    def execute(endpoint,parameters,path,**kwargs):
        calls.append((endpoint,parameters,kwargs))
        return archive_capture(path,LATER)
    import stockfish_core.yaogu.provider_requests as requests
    import stockfish_core.yaogu.request_queue as queue
    import stockfish_core.yaogu.scheduler as scheduler
    def forbidden(*args,**kwargs): raise AssertionError('TEST_FIXTURE_NO_QUEUE_OR_SCHEDULER')
    monkeypatch.setattr(queue.RequestQueue,'__init__',forbidden)
    monkeypatch.setattr(scheduler.CandidateScheduler,'__init__',forbidden)
    monkeypatch.setattr(requests,'execute_request',execute)
    monkeypatch.setattr(harness,'last_closed_session',lambda now:LATER)
    trace = DailyPrimaryTrace(boundary='TEST_FIXTURE_CLI_TO_REAL_API_BYTES')
    trace.update(execution_mode='TEST_FIXTURE',actual_network_requests=None)
    with trace.activate():
        iid,manifest = harness.build_live_incremental(store.path,base_id,inventory_path,output,trace,
            started=time.perf_counter(),budget_seconds=30,fixture=True)
    assert calls == [('market_daily',{'date':str(LATER)},{'timeout':45,'max_http_requests':1})]
    assert manifest['coverage_type']=='TEST_FIXTURE' and manifest['prior_partitions_unchanged']
    assert manifest['normalized_current_bars']==1
    assert manifest['calendar']['missing_intervening_price_sessions']==[str(NEXT)]
    core = ApplicationService(root=tmp_path/'TEST_FIXTURE-state',store=store,allow_test_fixture=True,data_dir=tmp_path/'no_model')
    try:
        if telemetry_failure:
            put=store.put_document
            def fail_ready_snapshot(namespace,document_id,value):
                if namespace=='daily_primary_trace_snapshots' and value.get('snapshot_phase')=='RESULT_READY':
                    raise OSError('TEST_FIXTURE_READY_PERSISTENCE_FAILURE')
                return put(namespace,document_id,value)
            monkeypatch.setattr(store,'put_document',fail_ready_snapshot)
        client = create_app(core).test_client()
        with trace.activate():
            accepted = client.post('/api/daily-primary/run',json={'input_id':iid})
        assert accepted.status_code==202
        job = _wait_job(core,accepted.json['job_id'])
        deadline=time.monotonic()+5
        while trace.trace['status']=='RUNNING' and time.monotonic()<deadline:time.sleep(.01)
        assert job['state']=='SUCCEEDED'
        assert job['result']['final_status']=='NOT_READY'
        assert job['result']['strategy_started'] is False
        assert job['result']['recommendation_published'] is False
        report=core.candidates.daily_traces.report(trace)
        assert report['e2e_verified'] is False
        summary=harness.live_incremental_summary(manifest,job,report)
        assert summary['normal_incremental_live_e2e']=='FAILED_TRACE_VALIDATION'
        assert summary['nonempty_feature_path']=='NOT_EXERCISED_NO_ELIGIBLE_FACT_POPULATION'
        assert_measured(trace)
        live=[s for s in trace.trace['stages'] if s['name']=='PROVIDER_REFRESH_INCREMENTAL_INGESTION' and s['status']=='SUCCESS']
        assert len(live)==1 and trace.trace['actual_network_requests'] is None
        assert trace.trace['actual_http_attempts']==0 and trace.trace['actual_sdk_operations']==3
        assert report['persistence_by_phase']['NOT_READY']['status']=='SUCCESS'
        assert not store.list_documents('candidate_input_activations')
        assert not store.list_documents('stage_aware_prospective_primary_runs')
        assert not store.list_documents('strategy_experiment_runs')
    finally:
        core.close()
