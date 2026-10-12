"""Isolated TEST_FIXTURE originals; no observed market data, SDK or DB."""
from copy import copy, deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import physical_historical_research as subject
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_market_input
from stockfish_core.yaogu.provider_normalization import _clock


def write(path, body):
    raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def capture(root, endpoint, rows, parameters, seen, *, coverage=None, trio=False,
            status='COMPLETE', page=True, started=None, raw_clock=True):
    body = dict(rows=rows, status=status, pagination_complete=page,
        field_version=subject.FIELD_VERSION, first_seen_at=seen.isoformat(), coverage=coverage or {}, gaps=[])
    if not raw_clock:
        body.pop('first_seen_at')
    raw = json.dumps(body).encode()
    physical = dict(provider='baostock', dataset=endpoint, parameters=parameters,
        field_version=subject.FIELD_VERSION, row_count=None, pagination_complete=page,
        status=status, first_seen_at=seen.isoformat(), ingested_at=seen.isoformat(),
        content_hash=hashlib.sha256(raw).hexdigest(), source_url='https://TEST_FIXTURE.invalid/source',
        integrity_scope='LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION')
    rid = hashlib.sha256(json.dumps(physical,sort_keys=True).encode()).hexdigest()
    physical = dict(physical, receipt_id=rid)
    end = parameters.get('end_date') or parameters.get('date')
    typed = SourceReceipt(receipt_id=rid, provider='baostock', dataset=endpoint,
        field_version=subject.FIELD_VERSION, row_count=len(rows), pagination_complete=page,
        status=status, content_hash=physical['content_hash'], source_url=physical['source_url'],
        parameters={key:str(value) if isinstance(value,list) else value for key,value in parameters.items()},
        **_clock(physical, date.fromisoformat(end) if end else None))
    registration = dict(raw=write(root/'cache/raw'/(physical['content_hash']+'.raw.json'),raw),
        receipt=write(root/'cache/raw'/(rid+'.receipt.json'),physical), typed_receipt=typed.model_dump(mode='json'))
    if trio:
        result = dict(body, provider='baostock', endpoint=endpoint, parameters=parameters,
            request_receipt_id=rid, receipts=[physical], started_at=(started or seen-timedelta(seconds=1)).isoformat(),
            completed_at=seen.isoformat())
        registration.update(archive_root=str(root), result=write(root/'results'/(rid+'.json'),result))
    return registration


def fixture(tmp_path, *, missing_day=False, source_edit=None, inherited=False, ancestor=True,
            factor_revision=False, partial_only=False, missing_ipo=False, missing_turn=False,
            factor_omission=False, factor_edit=None, intermediate=False, factor_raw_clock=True):
    start, end = date(2025,11,3), date(2026,5,20)
    calendar_rows, sessions = [], []
    for index in range((end-start).days+1):
        day = start+timedelta(days=index)
        trading = day.weekday()<5
        calendar_rows.append(dict(calendar_date=str(day), is_trading_day='1' if trading else '0'))
        if trading:sessions.append(day)
    window = sessions[-26:]
    seen = datetime(2026,5,21,12,tzinfo=timezone.utc)
    root = tmp_path/'TEST_FIXTURE-originals'
    sources = dict(schema_version='SAVED_R2_DELIVERY_SOURCES_V2', captures=[], raw_receipt_sources=[],
        business_sources=[], counted_nodes=[], reported_scope=None, extra_registered_files=[])
    sources['captures'].append(capture(root,'calendar',calendar_rows,
        dict(start_date=str(start),end_date=str(end)),seen,trio=True))
    codes = ['sz.000001','sz.000002','sh.600001','sh.688001','sz.300001']
    basic = [dict(code=c,ipoDate='2000-01-01',type='1') for c in codes]
    if missing_ipo:basic[0]['ipoDate']=''
    sources['captures'].append(capture(root,'stock_basic_all',basic,{},seen,trio=True))
    for index,day in enumerate(window):
        rows = [dict(code=c,date=str(day),adjustflag='3',tradestatus='1',isST='0',
            open=str(10+index/10),high=str(11+index/10),low=str(9+index/10),close=str(10.5+index/10),
            preclose=str(10.4+index/10),volume='10000',amount='100000000',turn='' if missing_turn else '5.1234') for c in codes]
        master = [dict(code=c,code_name='TEST_FIXTURE_'+c,tradeStatus='1') for c in codes]
        master += [dict(code='sh.000001',code_name='TEST_FIXTURE_INDEX',tradeStatus='1')]
        if source_edit and day==end:source_edit(rows,master)
        sources['raw_receipt_sources'].append(capture(root,'master_all',master,dict(date=str(day)),seen))
        if not missing_day or day!=window[3]:
            sources['raw_receipt_sources'].append(capture(root,'market_daily',rows,dict(date=str(day)),seen))
    factor_rows = [dict(code=c,dividOperateDate='2000-01-01',backAdjustFactor='1.2345') for c in codes[:2]]
    scope = dict(codes=codes[:2],start_date='1990-01-01',end_date=str(end))
    records = [dict(code=c,status='COMPLETE',row_count=1,cursor_exhausted=True,
                   captured_at=seen.isoformat()) for c in codes[:2]]
    cov = dict(security_statuses=records,cursor_exhausted=True,requested_codes=codes[:2])
    if inherited or factor_revision or factor_omission:
        old = capture(root,'adjustment_factors',factor_rows,scope,seen,trio=True,
            coverage=dict(cov,cursor_exhausted=False),status='PARTIAL',page=False)
        if ancestor or factor_revision or factor_omission:sources['captures'].append(old)
        if intermediate:
            sources['captures'].append(capture(root,'adjustment_factors',factor_rows,scope,
                seen+timedelta(seconds=2),trio=True,coverage=dict(cov,cursor_exhausted=False),
                status='PARTIAL',page=False))
    if factor_revision:
        factor_rows[0]=dict(factor_rows[0],backAdjustFactor='2')
    factor_seen = seen+timedelta(seconds=5) if inherited or factor_revision or factor_omission else seen
    if not inherited:
        cov = deepcopy(cov)
        for record in cov['security_statuses']:record['captured_at']=factor_seen.isoformat()
    if factor_omission:
        factor_rows=factor_rows[:1]
        cov['security_statuses'][1]['row_count']=0
    if factor_edit:factor_edit(factor_rows)
    sources['captures'].append(capture(root,'adjustment_factors',factor_rows,scope,factor_seen,trio=True,
        coverage=dict(cov,cursor_exhausted=not partial_only), status='PARTIAL' if partial_only else 'COMPLETE',
        page=not partial_only,raw_clock=factor_raw_clock))
    manifest = tmp_path/'TEST_FIXTURE-sources.json';write(manifest,sources)
    policy = tmp_path/'TEST_FIXTURE-policy'
    for name in ('baostock-home.md','baostock-DailyUpdates.md','baostock-StockBasicInfoAPI.md',
        'baostock-stockBasic.md','baostock-factorInfo.md','baostock-adjustment.pdf',
        'szse-mainboard-merger.html','szse-stock-rules.pdf'):
        write(policy/name,b'TEST_FIXTURE 17:30 18:00 000001-004999')
    return manifest, policy, sources, window


def run(data, **kwargs):
    manifest,policy,_,window=data
    return subject.run_physical_historical_r1(manifest,policy,window[-1],
        datetime.now(timezone.utc)+timedelta(seconds=60),**kwargs)


def test_genuine_physical_missing_wrappers_exact_native_success(tmp_path):
    data=fixture(tmp_path); before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=run(data)
    assert result['native_R1_attempts']==1
    assert result['normalization_view_role']=='NEW_DERIVED_INPUT_NOT_OLD_CAPTURE_RESULT'
    assert result['audit']['original_result_wrappers_reconstructed'] is False
    assert all('result' not in parent for parent in data[2]['raw_receipt_sources'])
    assert result['native_run']['qualification']['eligible']==2
    assert result['input']['universe']['complete'] is False
    assert result['audit']['historical_PIT_count']==0
    bars=result['input']['bars']; first=next(b for b in bars if b['instrument_id']=='000001.SZ')
    assert Decimal(first['comparable_close'])==Decimal(first['close'])*Decimal('1.2345')
    assert Fraction(str(first['turnover_fraction']))==Fraction(Decimal('5.1234'))/100
    assert Decimal(first['amount_cny'])==100000000
    assert subject.stamp(first['first_seen_at'])==subject.stamp(result['derived_at'])
    assert subject.stamp(result['issued_at']) <= subject.stamp(result['audit']['derivation_started_at']) \
        <= subject.stamp(result['derived_at']) <= subject.stamp(result['decision_cutoff']) \
        <= subject.stamp(result['terminal_verified_at'])
    assert all(p.read_bytes()==raw for path,raw in before.items() for p in [__import__('pathlib').Path(path)])
    assert len(list(tmp_path.rglob('*.json')))==sum(path.endswith('.json') for path in before)


@pytest.mark.parametrize('mutation',[
    lambda rows,master:rows.pop(),
    lambda rows,master:rows.append(deepcopy(rows[0])),
    lambda rows,master:rows[0].update(adjustflag='1'),
    lambda rows,master:rows[0].update(date='2026-05-19'),
    lambda rows,master:master[0].update(tradeStatus='0'),
    lambda rows,master:master.append(deepcopy(master[0])),
])
def test_whole_scope_and_dated_status_conflicts_reject(tmp_path,mutation):
    with pytest.raises(ValueError):run(fixture(tmp_path,source_edit=mutation))


def test_real_inherited_complete_requires_authenticated_ancestor(tmp_path):
    result=run(fixture(tmp_path,inherited=True))
    assert len(result['audit']['factor_inheritance'])==2
    partials={r['receipt_id'] for r in result['input']['receipts'] if r['status']=='PARTIAL'}
    assert partials
    assert all(not partials.intersection(b['source_refs']) for b in result['input']['bars'])


def test_inherited_origin_has_entire_clock_chain_not_intermediate_retry(tmp_path):
    data=fixture(tmp_path,inherited=True,intermediate=True)
    result=run(data)
    audits={item['receipt_id']:item for item in result['audit']['original_source_audits']}
    sources={item['typed_receipt']['receipt_id']:item for item in data[2]['captures']}
    for inherited in result['audit']['factor_inheritance']:
        child=audits[inherited['receipt_id']]
        fragment=next(row for row in json.loads(Path(sources[inherited['receipt_id']]['raw']['path']).read_bytes())
                      ['coverage']['security_statuses'] if row['code']==inherited['code'])
        for ancestor_id in inherited['ancestor_receipt_ids']:
            ancestor=audits[ancestor_id]
            clocks=ancestor['original_four_clocks']
            assert subject.stamp(ancestor['started_at']) <= subject.stamp(fragment['captured_at']) \
                <= subject.stamp(clocks['first_seen_at']) <= subject.stamp(clocks['ingested_at']) \
                <= subject.stamp(ancestor['completed_at']) <= subject.stamp(child['started_at'])
    # The middle retry is audited but cannot be called the fragment's origin.
    assert all(len(row['ancestor_receipt_ids'])==1 for row in result['audit']['factor_inheritance'])


def test_inherited_fragment_without_ancestor_rejects(tmp_path):
    with pytest.raises(ValueError,match='ANCESTOR_MISSING'):run(fixture(tmp_path,inherited=True,ancestor=False))


def test_all_attempt_revision_not_hidden_by_complete_retry(tmp_path):
    result=run(fixture(tmp_path,factor_revision=True))
    affected=[b for b in result['input']['bars'] if b['instrument_id']=='000001.SZ']
    assert affected and all(b['comparable_close'] is None for b in affected)
    assert 'ALL_ATTEMPT_FACTOR_REVISION_CONFLICT' in {x['comparable_status'] for x in result['audit']['numeric_observations']}


def test_partial_never_numeric_complete(tmp_path):
    result=run(fixture(tmp_path,partial_only=True))
    assert all(b['comparable_close'] is None for b in result['input']['bars'])
    assert any(r['status']=='PARTIAL' for r in result['input']['receipts'])


def test_missing_session_not_compressed_and_identity_unknown(tmp_path):
    result=run(fixture(tmp_path,missing_day=True,missing_ipo=True,missing_turn=True))
    assert len(result['audit']['sessions_26'])==26
    assert result['native_run']['qualification']['eligible']==1
    assert any(g['field']=='DAILY_RAW_AND_MASTER' for g in result['audit']['gaps'])
    assert any(g['field']=='HISTORICAL_IDENTITY' for g in result['audit']['gaps'])
    assert all(b['turnover_fraction'] is None for b in result['input']['bars'])


def test_original_physical_hash_drift_rejected(tmp_path):
    data=fixture(tmp_path);p=__import__('pathlib').Path(data[2]['raw_receipt_sources'][0]['raw']['path'])
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError,match='SHA_MISMATCH'):run(data)


def test_saved_json_or_callback_is_not_source_capability(tmp_path):
    data=fixture(tmp_path)
    with pytest.raises(ValueError,match='REAL_SOURCE_AND_POLICY_PATHS_REQUIRED'):
        subject.run_physical_historical_r1(data[2],data[1],data[3][-1],datetime.now(timezone.utc)+timedelta(seconds=60))
    with pytest.raises(ValueError,match='REAL_SOURCE_AND_POLICY_PATHS_REQUIRED'):
        subject.run_physical_historical_r1(lambda:True,data[1],data[3][-1],datetime.now(timezone.utc)+timedelta(seconds=60))


def test_fake_issuer_is_not_accepted(tmp_path,monkeypatch):
    data=fixture(tmp_path)
    monkeypatch.setattr(subject,'issue_saved_r2_source_reader',lambda **kw:{'reader':lambda rid:b'{}'})
    with pytest.raises(ValueError,match='GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED'):run(data)


def test_deadline_before_issuance_and_repeat_attempt(tmp_path):
    data=fixture(tmp_path);deadline=datetime.now(timezone.utc)-timedelta(seconds=1)
    for _ in range(2):
        with pytest.raises((ValueError,TimeoutError)):
            subject.run_physical_historical_r1(data[0],data[1],data[3][-1],deadline)


def test_same_registered_job_only_once(tmp_path):
    data=fixture(tmp_path);deadline=datetime.now(timezone.utc)+timedelta(seconds=60)
    subject.run_physical_historical_r1(data[0],data[1],data[3][-1],deadline)
    with pytest.raises(ValueError,match='ALREADY_ATTEMPTED'):
        subject.run_physical_historical_r1(data[0],data[1],data[3][-1],deadline)


def test_input_mutation_at_actual_native_return_rejected(tmp_path,monkeypatch):
    data=fixture(tmp_path); original=subject.se.run_short_horizon_strategy
    def changed(bundle,**kwargs):
        result=original(bundle,**kwargs)
        bundle.__dict__['bars']=()
        return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError,match='DERIVED_INPUT_CHANGED'):run(data)


def test_policy_bytes_changed_after_actual_native_return_rejected(tmp_path,monkeypatch):
    data=fixture(tmp_path);original=subject.se.run_short_horizon_strategy
    def changed(bundle,**kwargs):
        result=original(bundle,**kwargs)
        path=data[1]/'szse-mainboard-merger.html';path.write_bytes(path.read_bytes()+b' changed')
        return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError):run(data)


def test_registry_mapping_mutation_at_actual_native_return_rejected(tmp_path,monkeypatch):
    data=fixture(tmp_path);issuer=subject.issue_saved_r2_source_reader;issued=[]
    def keep(**kwargs):
        value=issuer(**kwargs);issued.append(value);return value
    monkeypatch.setattr(subject,'issue_saved_r2_source_reader',keep)
    original=subject.se.run_short_horizon_strategy
    def changed(bundle,**kwargs):
        result=original(bundle,**kwargs);issued[0]._reader._receipts.clear();return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError,match='LIVE_READER_BINDING_CHANGED'):run(data)


def test_implementation_drift_at_actual_native_return_rejected(tmp_path,monkeypatch):
    data=fixture(tmp_path);original=subject.se.run_short_horizon_strategy;identity=subject.source_identity
    def changed(bundle,**kwargs):
        result=original(bundle,**kwargs)
        monkeypatch.setattr(subject,'source_identity',lambda:dict(identity(),source_hash='0'*64))
        return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError,match='IMPLEMENTATION_CHANGED'):run(data)


@pytest.mark.parametrize('turn', ['5.000000000000000000001','NaN','-1',None])
def test_unrepresentable_invalid_or_missing_turn_is_unknown(tmp_path,turn):
    result=run(fixture(tmp_path,source_edit=lambda rows,master:rows[0].update(turn=turn)))
    current=next(b for b in result['input']['bars'] if b['instrument_id']=='000001.SZ'
                 and b['session']=='2026-05-20')
    assert current['turnover_fraction'] is None and current['turnover_basis'] is None


def test_missing_amount_not_zero(tmp_path):
    # Missing trading amount violates the full market context contract. It must
    # never be silently filled to zero just to make R1 or scope look complete.
    with pytest.raises(ValueError,match='TRADING_AMOUNT_UNAVAILABLE_OR_INVALID'):
        run(fixture(tmp_path,source_edit=lambda rows,master:rows[0].update(amount='')))


def test_original_input_and_rules_identity_have_separate_new_source_identity(tmp_path):
    result=run(fixture(tmp_path))
    assert result['input_hash']==result['native_run']['input_hash']
    assert result['source_identity']['source_hash']!=result['native_strategy_identity']['source_hash']
    assert result['selector_rule_changed'] is False
    assert result['native_strategy_identity']['feature_version']=='SHORT_HORIZON_EXACT_V2'
    json.dumps(result,allow_nan=False)


def test_live_issued_capability_deepcopy_rejected(tmp_path,monkeypatch):
    data=fixture(tmp_path);original=subject.issue_saved_r2_source_reader
    def copy_cap(**kwargs):
        return deepcopy(original(**kwargs))
    monkeypatch.setattr(subject,'issue_saved_r2_source_reader',copy_cap)
    with pytest.raises(ValueError,match='LIVE_CAPABILITY_NOT_COPYABLE'):run(data)


@pytest.mark.parametrize('attack',['shallow','deep','constructor','forged','pid','cutoff','factory_repeat'])
def test_live_factory_instance_and_exact_cutoff_are_sealed(tmp_path,monkeypatch,attack):
    data=fixture(tmp_path);original=subject._new_input_binding;native=subject.se.run_short_horizon_strategy;calls=[]
    def counted(*args,**kwargs):
        calls.append(1);return native(*args,**kwargs)
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',counted)
    def checked(*args,**kwargs):
        owner=original(*args,**kwargs)
        with pytest.raises(ValueError):
            if attack=='shallow':copy(owner)
            elif attack=='deep':deepcopy(owner)
            elif attack=='constructor':subject._InputBinding(subject._TOKEN,*args)
            elif attack=='forged':
                fake=object.__new__(subject._InputBinding);fake.__dict__.update(owner.__dict__);fake.validate()
            elif attack=='pid':
                actual_pid=subject.os.getpid()
                with monkeypatch.context() as changes:
                    changes.setattr(subject.os,'getpid',lambda:actual_pid+1);owner.validate()
            elif attack=='cutoff':owner.consume(owner.cutoff+timedelta(microseconds=1))
            elif attack=='factory_repeat':original(*args,**kwargs)
        owner.validate()
        return owner
    monkeypatch.setattr(subject,'_new_input_binding',checked)
    assert run(data)['native_R1_attempts']==1 and calls==[1]


def test_completed_binding_cannot_reset_state_or_consume_again(tmp_path,monkeypatch):
    data=fixture(tmp_path);original=subject._new_input_binding;owners=[]
    def keep(*args,**kwargs):
        owner=original(*args,**kwargs);owners.append(owner);return owner
    monkeypatch.setattr(subject,'_new_input_binding',keep)
    run(data);owner=owners[0]
    with pytest.raises(ValueError,match='NATIVE_R1_ALREADY_CONSUMED'):owner.consume(owner.cutoff)
    owner.state='READY'
    with pytest.raises(ValueError,match='GENUINE_INPUT_FACTORY_BINDING_CHANGED'):owner.consume(owner.cutoff)


def test_policy_registry_and_mutable_self_hash_cannot_be_changed_together(tmp_path,monkeypatch):
    data=fixture(tmp_path);factory=subject._new_input_binding;native=subject.se.run_short_horizon_strategy;owners=[]
    def keep(*args,**kwargs):
        owner=factory(*args,**kwargs);owners.append(owner);return owner
    def changed(*args,**kwargs):
        result=native(*args,**kwargs)
        owner=owners[0];owner.policy_registry.files.clear()
        owner._policy_registry_hash=subject.digest_json_stream(owner.policy_registry.files)
        return result
    monkeypatch.setattr(subject,'_new_input_binding',keep)
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError,match='GENUINE_INPUT_FACTORY_BINDING_CHANGED'):run(data)


def test_partial_current_raw_is_missing_source_not_false(tmp_path):
    data=fixture(tmp_path)
    target=next(i for i in data[2]['raw_receipt_sources'] if i['typed_receipt']['dataset']=='market_daily'
                and i['typed_receipt']['parameters']['date']=='2026-05-20')
    rows=json.loads(Path(target['raw']['path']).read_bytes())['rows']
    data[2]['raw_receipt_sources'].remove(target)
    data[2]['raw_receipt_sources'].append(capture(tmp_path/'TEST_FIXTURE-partial','market_daily',rows,
        {'date':'2026-05-20'},datetime(2026,5,21,12,tzinfo=timezone.utc),status='PARTIAL',page=False))
    write(data[0],data[2]);result=run(data)
    assert result['native_run']['qualification']['eligible']==0
    assert len(result['input']['universe']['securities'])==5
    assert result['input']['universe']['expected_count']==6
    assert result['native_run']['qualification']['eligibility_unknown']==6
    assert len(result['native_run']['qualification']['eligibility_unknown_records'])==5
    assert result['input']['universe']['complete'] is False
    assert any(g['session']=='2026-05-20' and 'SOURCE_NOT_COMPLETE' in g['reason'] for g in result['audit']['gaps'])


def test_missing_current_master_is_not_empty_known_qualification(tmp_path):
    data=fixture(tmp_path)
    data[2]['raw_receipt_sources']=[item for item in data[2]['raw_receipt_sources']
        if not(item['typed_receipt']['dataset']=='master_all'
               and item['typed_receipt']['parameters']['date']=='2026-05-20')]
    write(data[0],data[2])
    with pytest.raises(ValueError,match='CURRENT_DATED_MASTER_UNAVAILABLE_DENOMINATOR_UNKNOWN'):
        run(data)


@pytest.mark.parametrize('master_rows',[[],[dict(code='sh.000001',code_name='TEST_FIXTURE_INDEX',tradeStatus='1')]])
def test_missing_raw_empty_or_non_a_master_cannot_pass_by_vacuous_truth(tmp_path,master_rows):
    data=fixture(tmp_path)
    data[2]['raw_receipt_sources']=[item for item in data[2]['raw_receipt_sources']
        if item['typed_receipt']['parameters']['date']!='2026-05-20']
    data[2]['raw_receipt_sources'].append(capture(tmp_path/'TEST_FIXTURE-empty-master','master_all',master_rows,
        {'date':'2026-05-20'},datetime(2026,5,21,12,tzinfo=timezone.utc)))
    write(data[0],data[2])
    with pytest.raises(ValueError,match='DATED_MASTER_A_SHARE_SCOPE_EMPTY'):run(data)


def test_complete_factor_missing_original_raw_clock_stays_unknown(tmp_path):
    result=run(fixture(tmp_path,factor_raw_clock=False))
    assert all(b['comparable_close'] is None for b in result['input']['bars'])
    assert any(item['dataset']=='adjustment_factors' and item['raw_first_seen_present'] is False
               for item in result['audit']['original_source_audits'])
    assert {x['comparable_status'] for x in result['audit']['numeric_observations']}=={
        'COMPLETE_FACTOR_RAW_CAPTURE_CLOCK_UNKNOWN'}


def test_complete_replacement_omission_is_not_no_action(tmp_path):
    result=run(fixture(tmp_path,factor_omission=True))
    affected=[b for b in result['input']['bars'] if b['instrument_id']=='000002.SZ']
    assert affected and all(b['comparable_close'] is None for b in affected)
    assert all(x['comparable_status']=='ALL_ATTEMPT_FACTOR_REVISION_CONFLICT'
               for x in result['audit']['numeric_observations'] if x['instrument_id']=='000002.SZ')


@pytest.mark.parametrize('mutation',[
    lambda rows:rows[0].update(dividOperateDate='2026-05-21'),
    lambda rows:rows[0].update(backAdjustFactor='1e999999'),
    lambda rows:rows.append(deepcopy(rows[0])),
])
def test_factor_scope_duplicate_and_numeric_bounds_reject(tmp_path,mutation):
    with pytest.raises(ValueError):run(fixture(tmp_path,factor_edit=mutation))


def test_deadline_interrupts_native_call_without_second_attempt(tmp_path,monkeypatch):
    import time as clock
    data=fixture(tmp_path);original=subject.se.run_short_horizon_strategy;calls=[]
    def delayed(bundle,**kwargs):
        calls.append(1)
        result=original(bundle,**kwargs)
        clock.sleep(10)
        return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',delayed)
    deadline=datetime.now(timezone.utc)+timedelta(seconds=5)
    with pytest.raises(TimeoutError,match='REGISTERED_DEADLINE_EXCEEDED'):
        subject.run_physical_historical_r1(data[0],data[1],data[3][-1],deadline)
    assert calls==[1]


@pytest.mark.parametrize('interval',[0,.05])
def test_returning_caller_timer_is_preserved_and_refused_before_body(tmp_path,monkeypatch,interval):
    import signal
    data=fixture(tmp_path);calls=[];previous_handler=signal.getsignal(signal.SIGALRM)
    previous_timer=signal.getitimer(signal.ITIMER_REAL)
    handler=lambda *_:None
    monkeypatch.setattr(subject,'issue_saved_r2_source_reader',lambda **kwargs:calls.append(1))
    try:
        signal.signal(signal.SIGALRM,handler)
        signal.setitimer(signal.ITIMER_REAL,.05,interval)
        with pytest.raises(ValueError,match='ACTIVE_CALLER_TIMER_CONFLICT'):
            subject.run_physical_historical_r1(data[0],data[1],data[3][-1],
                datetime.now(timezone.utc)+timedelta(seconds=.15))
        remaining,actual_interval=signal.getitimer(signal.ITIMER_REAL)
        assert 0 < remaining <= .05 and actual_interval==interval
        assert signal.getsignal(signal.SIGALRM) is handler and calls==[]
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous_handler)
        signal.setitimer(signal.ITIMER_REAL,*previous_timer)
