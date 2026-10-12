"""Genuine archived TEST_FIXTURE trios; no SDK, DB or observed prices."""
from copy import copy, deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import original_parent_field_derivation_v2 as subject
from stockfish_core.yaogu import original_parent_late_derivation as old
from stockfish_core.yaogu import providers
from stockfish_core.yaogu.history_artifacts import digest_market_input
from stockfish_core.yaogu.visibility import EvidenceResolver

DAY = date(2026, 9, 30)
SEEN = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
CUTOFF = SEEN + timedelta(hours=1)
FIELD = 'BAOSTOCK_00.9.30_R03_V1'
CODES = ['sh.600001', 'sz.000001', 'sz.000003']


def capture(root, monkeypatch, endpoint, parameters, rows, *, seen=SEEN,
        status='COMPLETE', page=True, coverage=None, raw_clock=True, omitted_version=False):
    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (seen+timedelta(seconds=1)).astimezone(tz or timezone.utc)
    monkeypatch.setattr(providers, 'datetime', FixtureClock)
    body = dict(rows=rows, status=status, pagination_complete=page,
        field_version=FIELD, coverage=coverage or {}, gaps=[])
    if status=='FETCH_FAILED':body['error_code']='TEST_FIXTURE_FAILED_ATTEMPT'
    if omitted_version:body.pop('field_version')
    if raw_clock:body['first_seen_at'] = seen.isoformat()
    envelope_version=old.FIELD_VERSION if omitted_version else FIELD
    receipt = providers.archive_receipt(root/'cache/raw', 'baostock', endpoint, parameters,
        body, status=status, pagination_complete=page, field_version=envelope_version,
        first_seen_at=seen.isoformat(), source_url='https://TEST_FIXTURE.invalid/original')
    result = dict(body, provider='baostock', endpoint=endpoint, parameters=parameters,
        field_version=envelope_version,
        request_receipt_id=receipt['receipt_id'], receipts=[receipt],
        started_at=(seen-timedelta(seconds=1)).isoformat(),
        completed_at=(seen+timedelta(seconds=2)).isoformat())
    path = root/'results'/(receipt['receipt_id']+'.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding='utf-8')
    return old.OriginalCaptureArtifact(root, path, hashlib.sha256(path.read_bytes()).hexdigest())


def originals(tmp_path, monkeypatch, *, partial=True, final=True, final_clock=True,
        revision=False, inherited_change=False, missing_ancestor=False,
        scope_change=False, bad_fragment_clock=False, raw_missing_turn=False,
        raw_status='COMPLETE', late=False, final_without_anchor=False,
        raw_close='10.5', factor_value='1.5'):
    root = tmp_path/'TEST_FIXTURE-originals'
    artifacts = []
    def add(*args, **kwargs):
        item = capture(root, monkeypatch, *args, **kwargs)
        artifacts.append(item)
        return item
    start = date(2026, 5, 1)
    axis = [start+timedelta(days=i) for i in range((DAY-start).days+1)]
    sessions = [d for d in axis if d.weekday()<5]
    add('calendar', dict(start_date=str(start), end_date=str(DAY)),
        [dict(calendar_date=str(d), is_trading_day='1' if d in sessions else '0') for d in axis])
    add('mainboard_sse', {}, [dict(证券代码='600001',证券简称='TEST_FIXTURE_SH',上市日期='2010-01-01')])
    add('master_szse', {}, [dict(A股代码='000001',A股简称='TEST_FIXTURE_SZ',A股上市日期='2010-01-01',板块='主板')])
    add('master_all', dict(date=str(DAY)), [dict(code=c,tradeStatus='1',code_name='TEST_FIXTURE_'+c) for c in CODES[:2]])
    add('delisting_sse', {}, [])
    for d in sessions[-26:]:
        rows = [dict(code=c,date=str(d),adjustflag='3',tradestatus='1',isST='0',
            open='10',high='11',low='9',close=raw_close,preclose='10',
            volume='1000',amount='10300',turn='' if raw_missing_turn else '5.1234') for c in CODES[:2]]
        add('market_daily',dict(date=str(d)),rows,
            seen=CUTOFF+timedelta(seconds=1) if late and d==DAY else SEEN,
            status=raw_status if d==DAY else 'COMPLETE',page=raw_status=='COMPLETE' if d==DAY else True)
    factor_rows = [dict(code=c,dividOperateDate='2010-01-01',backAdjustFactor=factor_value) for c in CODES]
    old_seen = SEEN-timedelta(minutes=1)
    records = [dict(code=c,status='COMPLETE',row_count=1,cursor_exhausted=True,
        captured_at=old_seen.isoformat()) for c in CODES[:2]]
    if bad_fragment_clock:records[0]['captured_at']=(SEEN+timedelta(seconds=10)).isoformat()
    scope = dict(codes=CODES,start_date='1990-01-01',end_date=str(DAY))
    if partial:
        parent = add('adjustment_factors', scope, factor_rows[:2], seen=old_seen,
            status='PARTIAL', page=False, coverage=dict(cursor_exhausted=False,
                security_statuses=records+[dict(code=CODES[2],status='FETCH_FAILED',row_count=0,cursor_exhausted=False)]))
        if missing_ancestor:artifacts.remove(parent)
    if final:
        final_rows = deepcopy(factor_rows)
        if revision or inherited_change:final_rows[0]['backAdjustFactor']='1.6'
        final_records = deepcopy(records)+[dict(code=CODES[2],status='COMPLETE',row_count=1,
            cursor_exhausted=True,captured_at=SEEN.isoformat())]
        if revision:
            for record in final_records:record['captured_at']=SEEN.isoformat()
        if not partial:
            for record in final_records:record['captured_at']=SEEN.isoformat()
        final_scope = dict(scope,codes=CODES[:2]) if scope_change else scope
        if scope_change:final_rows=final_rows[:2];final_records=final_records[:2]
        if final_without_anchor:
            final_scope=dict(scope,start_date=str(DAY))
            final_rows=[]
            final_records=[dict(record,row_count=0,captured_at=SEEN.isoformat()) for record in final_records]
        add('adjustment_factors',final_scope,final_rows,raw_clock=final_clock,
            coverage=dict(cursor_exhausted=True,security_statuses=final_records))
    return artifacts


def run(artifacts, **kwargs):
    return subject.run_original_parent_field_r2(artifacts,DAY,CUTOFF,
        datetime.now(timezone.utc)+timedelta(seconds=30), **kwargs)


def current(result, iid='000001.SZ'):
    return next(b for b in result['input']['bars'] if b['instrument_id']==iid and b['session']==str(DAY))


def owner(artifacts):
    deadline = subject.physical._Deadline(datetime.now(timezone.utc)+timedelta(seconds=30))
    job=('TEST_FIXTURE_PRIVATE_OWNER',str(artifacts[0].result_path))
    subject._JOBS.add(job)
    return subject._factory(artifacts,DAY,CUTOFF,old.DerivationLimits(),deadline,job)[0]


def test_partial_ancestor_cannot_pollute_raw_money_turn_or_native_price_visibility(tmp_path, monkeypatch):
    artifacts=originals(tmp_path,monkeypatch)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    baseline,_=old.normalize_original_parent_asof(artifacts,DAY,CUTOFF)
    legacy_current=next(b for b in baseline.bars if b.instrument_id=='000001.SZ' and b.session==DAY)
    resolver=EvidenceResolver(baseline.receipts,cutoff=CUTOFF,coverage_type=baseline.coverage_type,
        sessions=baseline.sessions,observation_date=DAY)
    assert resolver.resolve(legacy_current,session=DAY,instrument_id='000001.SZ',require_complete=True).reason_codes==('SOURCE_NOT_COMPLETE',)
    frozen_hash=digest_market_input(baseline)
    result=run(artifacts,requested_target_date='2026-10-09')
    row=current(result)
    assert Decimal(row['amount_cny'])==10300
    assert Fraction(str(row['turnover_fraction']))==Fraction(Decimal('5.1234'))/100
    assert Decimal(row['comparable_close'])==Decimal('10.5')*Decimal('1.5')
    receipts={r['receipt_id']:r for r in result['input']['receipts']}
    assert all(receipts[r]['status']=='COMPLETE' for r in row['source_refs'])
    partial_ids={rid for rid,r in receipts.items() if r['status']=='PARTIAL'}
    field=next(f for f in result['field_bindings'] if f['instrument_id']=='000001.SZ' and f['session']==str(DAY))
    assert partial_ids <= set(field['audit_dependency_refs'])
    assert result['original_source_audit']['field_factor_inheritance']
    assert result['native_R2_attempts']==result['normalizer_calls']==1
    assert result['target_calendar_status']=='UNKNOWN'
    assert result['source_asof_cutoff']==CUTOFF.isoformat()
    assert subject.stamp(row['first_seen_at'])>CUTOFF
    assert result['historical_PIT_count']==result['complete_independent_cycles']==0
    native=result['native_run']['results']['SHORT_HORIZON_STRENGTH_R2']
    assert native['counts']['current_source_unknown']==0
    assert native['counts']['complete_25_window']==2
    for item in native['items']:
        facts=item['exact_features']
        assert facts['current_visibility_valid'] is True
        assert facts['window_coverage']['complete_25'] is True
        assert facts['window_coverage']['selected_25_count']==25
        assert all(value is not None for value in facts['returns'].values())
    resource=result['resource_semantics']
    assert resource['source_byte_limit_role']=='UNIQUE_ORIGINAL_FILE_SIZE_NOT_CUMULATIVE_IO'
    assert resource['cumulative_physical_read_bytes'] is resource['OS_RSS_peak_bytes'] is None
    assert resource['cumulative_physical_read_guard'] is resource['OS_RSS_guard'] is False
    assert {p:p.read_bytes() for p in before}==before
    assert digest_market_input(baseline)==frozen_hash


@pytest.mark.parametrize('with_partial',[False,True])
def test_unproven_comparable_never_falls_back_to_raw_and_known_money_remains(tmp_path,monkeypatch,with_partial):
    result=run(originals(tmp_path,monkeypatch,partial=with_partial,final=False))
    row=current(result)
    assert row['comparable_close'] is row['comparable_basis'] is None
    assert Decimal(row['close'])==Decimal('10.5') and Decimal(row['amount_cny'])==10300
    assert Fraction(str(row['turnover_fraction']))==Fraction(Decimal('5.1234'))/100
    assert all(r['status']=='COMPLETE' for r in result['input']['receipts'] if r['receipt_id'] in row['source_refs'])


@pytest.mark.parametrize('case,pattern',[
    (dict(revision=True),'REVISION_CONFLICT'),
    (dict(inherited_change=True),'ANCESTOR'),
    (dict(missing_ancestor=True),'ANCESTOR'),
    (dict(scope_change=True),'ANCESTOR'),
    (dict(bad_fragment_clock=True),'FRAGMENT|AFTER_ARCHIVE'),
    (dict(late=True),'AFTER_CUTOFF'),
])
def test_semantic_source_failures_are_not_missing_explanations(tmp_path,monkeypatch,case,pattern):
    with pytest.raises(ValueError,match=pattern):run(originals(tmp_path,monkeypatch,**case))


def test_complete_factor_missing_raw_clock_retains_audit_but_not_value_authority(tmp_path,monkeypatch):
    result=run(originals(tmp_path,monkeypatch,final_clock=False))
    row=current(result)
    assert row['comparable_close'] is None and Decimal(row['amount_cny'])==10300
    assert any(check['capture_clock_audit']['raw_first_seen_status']=='UNKNOWN_MISSING_RAW_FIRST_SEEN_AT'
        for check in result['original_source_audit']['original_captures'])


def test_complete_retry_without_own_anchor_cannot_borrow_partial_anchor(tmp_path,monkeypatch):
    result=run(originals(tmp_path,monkeypatch,final_without_anchor=True))
    row=current(result)
    assert row['comparable_close'] is row['comparable_basis'] is None
    assert Decimal(row['amount_cny'])==10300
    field=next(f for f in result['field_bindings'] if f['instrument_id']=='000001.SZ' and f['session']==str(DAY))
    receipts={r['receipt_id']:r for r in result['input']['receipts']}
    assert any(receipts[r]['status']=='PARTIAL' for r in field['audit_dependency_refs'])
    assert all(receipts[r]['dataset']=='market_daily' for r in row['source_refs'])


def test_same_decimal_rounding_on_both_oracles_cannot_certify_exact_price(tmp_path,monkeypatch):
    artifacts=originals(tmp_path,monkeypatch,
        raw_close='10.50000000000000000000000000000000001',
        factor_value='1.50000000000000000000000000000000003')
    with pytest.raises(ValueError,match='COMPARABLE_DECIMAL_EXACT_ROUNDTRIP_UNPROVEN'):run(artifacts)


def test_identity_covers_every_original_authenticator_dependency():
    files=subject.source_identity()['source_file_hashes']
    inherited=old.source_identity()['source_file_hashes']
    assert inherited.keys() <= files.keys()
    assert all(files[name]==value for name,value in inherited.items())
    native=subject.short_horizon_module('SHORT_HORIZON_STRENGTH_R2').short_horizon_source_identity()
    assert native['source_file_hashes'].keys() <= files.keys()
    assert subject.source_identity()['native_strategy_identity']==native


@pytest.mark.parametrize('endpoint',['market_daily','history','adjustment_factors'])
def test_authenticated_failed_empty_schema_default_remains_hard_audit_not_value(tmp_path,monkeypatch,endpoint):
    artifacts=originals(tmp_path,monkeypatch)
    params=(dict(date=str(DAY)) if endpoint=='market_daily' else
            dict(codes=CODES,start_date='1990-01-01',end_date=str(DAY)))
    failed=capture(artifacts[0].archive_root,monkeypatch,endpoint,params,[],
        seen=SEEN-timedelta(minutes=2),status='FETCH_FAILED',page=False,omitted_version=True)
    result=run(artifacts+[failed])
    check=next(check for check in result['original_source_audit']['original_captures']
        if check['result_path']==str(failed.result_path.resolve()))
    assert check['failed_empty_schema_label_defaulted'] is True
    rid=check['receipt_id']
    assert any(r['receipt_id']==rid and r['status']=='FETCH_FAILED' for r in result['input']['receipts'])
    assert all(rid not in row['source_refs'] for row in result['input']['bars'])
    assert str(failed.result_path.resolve()) in result['source_registry']
    assert result['native_run']['results']['SHORT_HORIZON_STRENGTH_R2']['counts']['current_source_unknown']==0


def test_loaded_native_config_change_after_issuance_refused(tmp_path,monkeypatch):
    held=owner(originals(tmp_path,monkeypatch))
    module=subject.short_horizon_module('SHORT_HORIZON_STRENGTH_R2')
    monkeypatch.setattr(module,'CONFIG',{**module.CONFIG,'TEST_FIXTURE_CHANGED':True})
    with pytest.raises(ValueError,match='IMPLEMENTATION_CHANGED'):held.consume()


def test_missing_turn_is_unknown_not_zero(tmp_path,monkeypatch):
    result=run(originals(tmp_path,monkeypatch,raw_missing_turn=True))
    assert current(result)['turnover_fraction'] is None
    field=next(f for f in result['field_bindings'] if f['instrument_id']=='000001.SZ' and f['session']==str(DAY))
    assert field['field_bindings']['turnover_fraction']['value_exact'] is None


@pytest.mark.parametrize('kind',['bundle','fields','audit','registry','reader_status','reader_paths','identity','state','cutoff'])
def test_live_factory_seals_all_field_and_source_relationships(tmp_path,monkeypatch,kind):
    held=owner(originals(tmp_path,monkeypatch))
    if kind=='bundle':held.bundle=held.bundle.model_copy(update={'bars':()})
    elif kind=='fields':held.fields[0]['field_bindings']['amount_cny']['value_exact']['numerator']+=1
    elif kind=='audit':held.audit['normalizer_call_count']=0
    elif kind=='registry':next(iter(held.registry.files.values()))['sha256']='0'*64
    elif kind=='reader_status':
        rid=held.reader.receipts[0].receipt_id
        held.reader._receipts[rid]=held.reader._receipts[rid].model_copy(update={'status':'PARTIAL'})
    elif kind=='reader_paths':held.reader._paths.pop(next(iter(held.reader._paths)))
    elif kind=='identity':held.identity['source_hash']='0'*64
    elif kind=='state':held.state='COMPLETE'
    elif kind=='cutoff':held.cutoff=CUTOFF+timedelta(seconds=1)
    with pytest.raises(ValueError,match='SEAL|BINDING'):held.consume()


def test_actual_original_file_content_change_refused_before_consumer(tmp_path,monkeypatch):
    held=owner(originals(tmp_path,monkeypatch));path=Path(next(iter(held.registry.files)))
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='SHA|CHANGED'):held.consume()


def test_copy_json_token_reconstruction_and_second_consume_fail(tmp_path,monkeypatch):
    held=owner(originals(tmp_path,monkeypatch))
    for operation in (copy,deepcopy):
        with pytest.raises(ValueError,match='NOT_COPYABLE'):operation(held)
    fake=object.__new__(subject._FieldInput);fake.__dict__.update(held.__dict__)
    with pytest.raises(ValueError,match='SEAL'):fake.consume()
    with pytest.raises(ValueError,match='GENUINE_FACTORY'):subject._FieldInput(json.loads(json.dumps({'token':'test'})))
    held.consume()
    with pytest.raises(ValueError,match='ALREADY_CONSUMED'):held.consume()


def test_second_factory_in_same_registered_job_refused(tmp_path,monkeypatch):
    artifacts=originals(tmp_path,monkeypatch);held=owner(artifacts)
    with pytest.raises(ValueError,match='FACTORY_ALREADY_ISSUED'):
        subject._factory(artifacts,DAY,CUTOFF,old.DerivationLimits(),subject.physical._Deadline(
            datetime.now(timezone.utc)+timedelta(seconds=30)),held.job)


def test_callback_replacement_cannot_issue_genuine_input(tmp_path,monkeypatch):
    artifacts=originals(tmp_path,monkeypatch)
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',lambda *_a,**_k:{})
    with pytest.raises(ValueError,match='IMPLEMENTATION_CHANGED'):run(artifacts)


def test_deadline_and_saved_json_are_not_capabilities(tmp_path,monkeypatch):
    artifacts=originals(tmp_path,monkeypatch)
    with pytest.raises(TimeoutError):subject.run_original_parent_field_r2(artifacts,DAY,CUTOFF,
        datetime.now(timezone.utc)-timedelta(seconds=1))
    with pytest.raises(ValueError,match='ORIGINAL_LOCATORS'):
        subject.run_original_parent_field_r2([dict(result_path=str(a.result_path),result_sha256=a.result_sha256)
            for a in artifacts],DAY,CUTOFF,datetime.now(timezone.utc)+timedelta(seconds=30))


def test_parent_raw_partial_is_not_promoted_to_complete_numeric_evidence(tmp_path,monkeypatch):
    result=run(originals(tmp_path,monkeypatch,raw_status='PARTIAL'))
    row=current(result)
    assert row['amount_cny'] is row['close'] is row['comparable_close'] is row['turnover_fraction'] is None
    assert row['quality']=='PARTIAL'

