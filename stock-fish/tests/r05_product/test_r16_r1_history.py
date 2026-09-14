"""TEST_FIXTURE: R16-R1 history boundary checks use independent temporary stores."""
from datetime import timedelta
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import sys
import pytest

from stockfish_core.storage import SQLiteStore,ReadOnlySQLiteStore,StorageError,IntegrityError
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore,bind_history
from stockfish_core.yaogu.history_base_selection import validate_history_base
from stockfish_core.yaogu.provider_comparability import comparable_rows
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.source_resolution import canonical_price_source_view,source_token
from stockfish_core.yaogu.strategy_experiments import _prepare_context
from tests.r03_sources.test_provider_boundaries import DAY,NOW,SESSIONS,base,result,raw

SCRIPTS = Path(__file__).resolve().parents[2]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from r16_r1_evidence_assets import copy_input_closure,load_bundle,register_isolated_input,_copy_document
from r16_history_stability_audit import _classify,AuditToken,_root_cause_and_fix_status
from r16_r1_readiness import audit_readiness
from r16_r1_build_corrected_history import base_current_price_receipt_ids,preserve_t_envelope_with_normalized_history


def fixture_base(tmp_path):
    store = SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    owner = HistoryArtifactStore(store)
    factors = result('adjustment_factors',[{'code':'sh.600000','dividOperateDate':'2000-01-01','backAdjustFactor':'2'}],
        {'codes':['sh.600000'],'start_date':'1990-01-01','end_date':str(DAY)})
    bundle,_ = normalize_capture_with_report(base()+[factors],DAY,NOW,SESSIONS,
        history_store=owner,coverage_type='TEST_FIXTURE')
    return store,bundle,factors


def test_readonly_store_never_migrates_and_cannot_write(tmp_path,monkeypatch):
    store,bundle,_ = fixture_base(tmp_path)
    input_id = register_isolated_input(store,bundle)
    monkeypatch.setattr(SQLiteStore,'_migrate',lambda *_:(_ for _ in ()).throw(AssertionError('migration must not run')))
    readonly = ReadOnlySQLiteStore(store.path)
    loaded = load_bundle(readonly,input_id)
    assert loaded.history_artifact_ref == bundle.history_artifact_ref
    with pytest.raises(StorageError,match='READ_ONLY'):
        readonly.put_document('x','y',{})
    with pytest.raises(IntegrityError,match='READ_ONLY'):
        HistoryArtifactStore(readonly).writer()
    with readonly.transaction(write=False) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute('CREATE TEMP TABLE should_not_exist(x)')


def test_dependency_closure_preserves_bytes_without_official_records(tmp_path):
    store,bundle,_ = fixture_base(tmp_path)
    input_id = register_isolated_input(store,bundle)
    store.put_document('stage_aware_prospective_primary_runs','PRIVATE_OFFICIAL_TEST_FIXTURE',{})
    target = tmp_path/'copy.sqlite3'
    report = copy_input_closure(store.path,target,[input_id],include_config=False)
    loaded = load_bundle(target,input_id)
    assert loaded.history_artifact_ref == bundle.history_artifact_ref
    ro = ReadOnlySQLiteStore(target)
    assert ro.get_document_bytes('candidate_inputs',input_id) == store.get_document_bytes('candidate_inputs',input_id)
    assert ro.list_documents('stage_aware_prospective_primary_runs') == []
    assert report['official_records_copied'] is False


def test_closure_resume_rejects_extra_target_partition_member(tmp_path):
    store,bundle,_ = fixture_base(tmp_path)
    input_id = register_isolated_input(store,bundle)
    target = tmp_path/'copy-extra.sqlite3'
    copy_input_closure(store.path,target,[input_id],include_config=False)
    copied = SQLiteStore(target)
    with copied.transaction() as con:
        member = con.execute('SELECT * FROM history_partition_members LIMIT 1').fetchone()
        # Malformed TEST_FIXTURE target only: foreign extra member under the
        # original partition ID must not be accepted by a resumed closure.
        con.execute('INSERT INTO history_evidence_rows VALUES(?,?,?,?,?)',
                    ('f'*64,member['kind'],member['instrument_id'],member['session'],'{}'))
        con.execute('INSERT INTO history_partition_members VALUES(?,?,?,?,?)',
                    (member['partition_hash'],'f'*64,member['kind'],member['instrument_id'],member['session']))
    with pytest.raises(IntegrityError,match='TARGET_PARTITION_EXTRA_MEMBERS'):
        copy_input_closure(store.path,target,[input_id],include_config=False)


def test_readiness_keeps_ineligible_fixture_out_and_reports_calendar(tmp_path):
    store,bundle,_ = fixture_base(tmp_path)
    input_id = register_isolated_input(store,bundle)
    report = audit_readiness(store.path,input_id,NOW.isoformat(),instrument_limit=1,budget_seconds=20)
    assert report['scanned_eligible'] == 0
    assert report['required_sessions']['MA250'] == [str(day) for day in SESSIONS]
    assert report['coverage']['eligible']['ALL']['coverage'] is None
    assert report['trend_decision'].startswith('NOT_VERIFIED')


def test_corrected_t_requires_original_current_complete_capture_not_failed_attempt(tmp_path):
    store,bundle,_ = fixture_base(tmp_path)
    selected = base_current_price_receipt_ids(bundle)
    assert len(selected)==1
    current = next(r for r in bundle.receipts if r.receipt_id==selected[0])
    failed = current.model_copy(update={'receipt_id':'FAILED_TEST_FIXTURE','status':'FETCH_FAILED','row_count':0,'pagination_complete':False})
    assert base_current_price_receipt_ids(bundle.model_copy(update={'receipts':(*bundle.receipts,failed)}))==selected
    with pytest.raises(ValueError,match='COMPLETE_ORIGINAL_T_CURRENT_PRICE_CAPTURE_REQUIRED'):
        base_current_price_receipt_ids(bundle.model_copy(update={'receipts':(failed,)}))


def test_factor_end_does_not_certify_next_session():
    new_day = DAY+timedelta(days=3)
    factors = [([{'dividOperateDate':'2000-01-01','backAdjustFactor':'2'}],'FACTOR_TEST_FIXTURE',str(DAY),'1990-01-01')]
    values,status = comparable_rows('600000.SH',[(new_day,(raw(session=new_day),'RAW_TEST_FIXTURE'))],{},factors)
    assert values == {}
    assert status == 'FACTOR_CAPTURE_END_BEFORE_RAW_SESSION'
    values,status = comparable_rows('600000.SH',[(DAY,(raw(),'RAW_TEST_FIXTURE'))],{},factors)
    assert str(values[DAY][0]['comparable_close']) == '22'


@pytest.mark.parametrize('window',[(None,None),('2026-09-08','2026-09-08')])
def test_factor_unknown_or_disjoint_window_cannot_certify_gap(window):
    anchor = [{'dividOperateDate':'2000-01-01','backAdjustFactor':'2'}]
    captures = [(anchor,'old','2026-09-01','1990-01-01'),([], 'new',*window)] if window[0] else [(anchor,'old',*window)]
    session = DAY+timedelta(days=3)
    values,status = comparable_rows('600000.SH',[(session,(raw(session=session),'raw'))],{},captures)
    assert values == {}
    assert status == 'FACTOR_COMPLETE_ANCHORED_WINDOW_NOT_PROVEN'


@pytest.mark.parametrize('column,value',[('created_at','2000-01-01T00:00:00Z'),('payload','{"bad":true}')])
def test_closure_resume_rejects_existing_target_tuple_conflict(tmp_path,column,value):
    source = SQLiteStore(tmp_path/'source.sqlite3')
    target = SQLiteStore(tmp_path/'target.sqlite3')
    source.put_document('TEST_FIXTURE','id',{'valid':True})
    _copy_document(ReadOnlySQLiteStore(source.path),target,'TEST_FIXTURE','id')
    with target.transaction() as con:
        con.execute('DROP TRIGGER documents_no_update')
        con.execute(f'UPDATE documents SET {column}=? WHERE namespace=?',(value,'TEST_FIXTURE'))
    with pytest.raises(IntegrityError,match='TARGET_DOCUMENT_CONFLICT'):
        _copy_document(ReadOnlySQLiteStore(source.path),target,'TEST_FIXTURE','id')


def test_failed_capture_does_not_retract_newer_complete_base(tmp_path):
    store,bundle,factors = fixture_base(tmp_path)
    new_day = DAY+timedelta(days=3)
    stamp = NOW+timedelta(days=3)
    failed = result('market_daily',[],{'date':str(DAY)},stamp=NOW-timedelta(hours=1))
    failed.update(status='FETCH_FAILED',pagination_complete=False)
    current = result('market_daily',[raw(session=new_day)],{'date':str(new_day)},stamp=stamp)
    corrected,report = normalize_capture_with_report(base()[:2]+[failed,current,factors],new_day,stamp,
        (*SESSIONS,new_day),history_store=HistoryArtifactStore(store),history_base_ref=bundle.history_artifact_ref,
        coverage_type='RECONSTRUCTED_HISTORICAL')
    view = canonical_price_source_view(_prepare_context(corrected,stamp),[DAY,new_day],['600000.SH'])
    assert source_token(view,'600000.SH',DAY)['values']['comparable_close'] == '22'
    assert source_token(view,'600000.SH',new_day)['values']['comparable_close'] is None
    assert corrected.coverage_type == 'RECONSTRUCTED_HISTORICAL'
    assert any(r.status=='FETCH_FAILED' for r in corrected.receipts)
    assert any(g['reason']=='FACTOR_CAPTURE_END_BEFORE_RAW_SESSION' for g in report['gaps'])


@pytest.mark.parametrize('change',[{'tombstone':True},{'retracted':True},{'operation':'DELETE'}])
def test_retraction_is_not_silently_merged(change):
    sources = base([raw()|change])
    with pytest.raises(ValueError,match='SOURCE_RETRACTION_UNSUPPORTED'):
        normalize_capture_with_report(sources,DAY,NOW,SESSIONS)


def test_base_date_pit_provider_and_manifest_checks(tmp_path):
    store,bundle,_ = fixture_base(tmp_path)
    owner = HistoryArtifactStore(store)
    next_day = DAY+timedelta(days=3)
    proof = validate_history_base(bundle,owner,observation_date=next_day,cutoff=NOW,
                                  allowed_price_providers={'TEST_FIXTURE'})
    assert proof['partition_integrity']=='VERIFIED'
    with pytest.raises(ValueError,match='MUST_PRECEDE'):
        validate_history_base(bundle,owner,observation_date=DAY,cutoff=NOW)
    with pytest.raises(ValueError,match='NOT_AVAILABLE'):
        validate_history_base(bundle,owner,observation_date=next_day,cutoff=NOW-timedelta(seconds=1))
    with pytest.raises(ValueError,match='PROVIDER_INCOMPATIBLE'):
        validate_history_base(bundle,owner,observation_date=next_day,cutoff=NOW,allowed_price_providers={'other'})


@pytest.mark.parametrize('same_instant',[False,True])
def test_base_selector_uses_instants_and_rejects_ambiguous_ties(monkeypatch,same_instant):
    from stockfish_core.yaogu import history_base_selection as selector
    bundles = {key:SimpleNamespace(history_artifact_ref='history_'+key) for key in ('old','new')}
    clocks = {'old':'2026-09-04T15:00:00+08:00',
              'new':'2026-09-04T07:00:00Z' if same_instant else '2026-09-04T08:00:00Z'}
    indexes = [{'document_id':key,'payload':{'input_id':key,'observation_date':'2026-09-04'},
                'created_at':'2026-09-04T10:00:00Z' if key=='old' else '2026-09-04T18:00:00+08:00'}
               for key in ('old','new')]
    store = SimpleNamespace(list_documents=lambda *args,**kw:indexes,
                            get_document_bytes=lambda ns,iid,**kw:iid)
    monkeypatch.setattr(selector,'HistoryArtifactStore',lambda store:None)
    monkeypatch.setattr(selector,'MarketInput',SimpleNamespace(model_validate_json=lambda raw:bundles[raw]))
    monkeypatch.setattr(selector,'validate_history_base',lambda bundle,*args,**kw:{
        'observation_date':'2026-09-04','source_watermark':clocks[bundle.history_artifact_ref.removeprefix('history_')]})
    if same_instant:
        with pytest.raises(ValueError,match='AMBIGUOUS_HISTORY_BASE_SAME_CLOCKS'):
            selector.select_prior_history_base(store,observation_date=DAY+timedelta(days=3),cutoff=NOW)
    else:
        assert selector.select_prior_history_base(store,observation_date=DAY+timedelta(days=3),cutoff=NOW)['input_id']=='new'


def test_later_clock_is_not_proof_of_legal_source_revision():
    old = AuditToken('SELECTED','old','HISTORY_ARTIFACT',('x',),('2026-09-04T00:00:00+00:00',)*3,'v',tuple('1' for _ in range(10)))
    new = old._replace(row_hash='new',source_revision=('2026-09-06T00:00:00+00:00',)*3)
    assert _classify(old,new,'2026-09-04','2026-09-04','2026-09-07',NOW) == 'UNVERIFIED_SOURCE_REVISION'
    cause,status = _root_cause_and_fix_status({'UNVERIFIED_SOURCE_REVISION':1})
    assert 'REQUIRES' in cause and 'not established' in status


def test_corrected_t_preserves_current_envelope_while_using_real_added_history(tmp_path):
    store,base_bundle,factors = fixture_base(tmp_path)
    earlier = DAY-timedelta(days=1)
    historical = result('market_daily',[raw(session=earlier)],{'date':str(earlier)},stamp=NOW+timedelta(seconds=1))
    normalized,_ = normalize_capture_with_report(base()[:2]+[historical,factors],DAY,NOW+timedelta(seconds=1),SESSIONS,
        history_store=HistoryArtifactStore(store),history_base_ref=base_bundle.history_artifact_ref,
        coverage_type='RECONSTRUCTED_HISTORICAL')
    corrected = preserve_t_envelope_with_normalized_history(base_bundle,normalized)
    assert corrected.universe.model_dump(mode='json')==base_bundle.universe.model_dump(mode='json')
    assert corrected.bars==base_bundle.bars
    assert corrected.history_artifact_ref==normalized.history_artifact_ref!=base_bundle.history_artifact_ref
    assert historical['request_receipt_id'] in {r.receipt_id for r in corrected.receipts}
    before=canonical_price_source_view(_prepare_context(base_bundle,NOW+timedelta(seconds=1)),[DAY],['600000.SH'])
    after=canonical_price_source_view(_prepare_context(corrected,NOW+timedelta(seconds=1)),[earlier,DAY],['600000.SH'])
    assert source_token(before,'600000.SH',DAY)['row_hash']==source_token(after,'600000.SH',DAY)['row_hash']
    assert source_token(after,'600000.SH',earlier)['status']=='SELECTED'


@pytest.mark.parametrize('history_mode',[False,True])
def test_readiness_positive_pv_uses_full_selected_dailybar_payload(tmp_path,history_mode):
    from scripts import r16_production_oracle as oracle
    from stockfish_core.yaogu.history_artifacts import compact_input
    _,bundle,cutoff = oracle.load_case(oracle.DEFAULT_FIXTURES/'NORMAL.input.json',state_dir=tmp_path/'oracle')
    store = SQLiteStore(tmp_path/'POSITIVE_PV_TEST_FIXTURE.sqlite3')
    if history_mode:
        bundle = compact_input(bundle,store)
    input_id = register_isolated_input(store,bundle)
    report = audit_readiness(store.path,input_id,cutoff.isoformat(),instrument_limit=1,budget_seconds=20)
    assert report['scanned_eligible']==1
    assert report['coverage']['PV']['ALL']['numerator']==1
    assert report['missing_session_ledger'][0]['pv_ready'] is True
    assert report['missing_session_ledger'][0]['pv_reason'] is None


def test_exact_actual_price_schema_identity_and_failed_attempt_exclusion(tmp_path):
    store, bundle, factors = fixture_base(tmp_path)
    owner = HistoryArtifactStore(store)
    incoming = base() + [factors]
    failed = dict(factors, status='FETCH_FAILED', field_version='GENERIC_FAILED_RESPONSE_VERSION')
    proof = validate_history_base(bundle, owner, observation_date=DAY+timedelta(days=3), cutoff=NOW,
                                  incoming_price_receipts=incoming+[failed])
    assert proof['price_schema_compatibility']['status']=='VERIFIED_EXACT_USABLE_SCHEMA_IDENTITY'
    assert proof['price_schema_compatibility']['numeric_price_basis_validation']=='NOT_INFERRED_FROM_SCHEMA_IDENTITY'
    pending = validate_history_base(bundle, owner, observation_date=DAY+timedelta(days=3), cutoff=NOW)
    assert pending['price_schema_compatibility']['status']=='PENDING_ACTUAL_INCOMING_PRICE_SCHEMAS'


@pytest.mark.parametrize('mode', ['version_change', 'multiple_versions'])
def test_actual_price_schema_mismatch_fails_closed(tmp_path, mode):
    store, bundle, factors = fixture_base(tmp_path)
    incompatible = dict(factors, field_version='NONEMPTY_BUT_UNSUPPORTED_NEW_VERSION')
    incoming = base() + ([incompatible] if mode=='version_change' else [factors, incompatible])
    with pytest.raises(ValueError, match='HISTORY_BASE_PRICE_SCHEMA_'):
        validate_history_base(bundle, HistoryArtifactStore(store), observation_date=DAY+timedelta(days=3),
                              cutoff=NOW, incoming_price_receipts=incoming)


def test_pipeline_actual_capture_gate_does_not_reselect_or_swallow_incompatible_base(tmp_path):
    from stockfish_core.yaogu.data_pipeline import MainboardPipeline
    store, bundle, factors = fixture_base(tmp_path)
    input_id = register_isolated_input(store, bundle)
    owner = HistoryArtifactStore(store)
    selected = validate_history_base(bundle, owner, observation_date=DAY+timedelta(days=3), cutoff=NOW)
    selected['input_id'] = input_id
    pipeline = object.__new__(MainboardPipeline)
    pipeline.service = SimpleNamespace(store=store)
    pipeline.history_store = owner
    incoming = base()+[factors]
    checked = pipeline._validate_history_base_for_results(selected, incoming, DAY+timedelta(days=3), NOW)
    assert checked['input_id']==input_id and checked['partition_integrity']=='VERIFIED'
    assert checked['price_schema_compatibility']['status']=='VERIFIED_EXACT_USABLE_SCHEMA_IDENTITY'
    incompatible = [dict(value, field_version='MISMATCH') if value['endpoint']=='adjustment_factors' else value
                    for value in incoming]
    with pytest.raises(ValueError, match='HISTORY_BASE_PRICE_SCHEMA_INCOMPATIBLE'):
        pipeline._validate_history_base_for_results(selected, incompatible, DAY+timedelta(days=3), NOW)


def test_retained_schema_support_is_shared_with_actual_capture_adapter(monkeypatch):
    import sys
    from stockfish_core.yaogu import provider_requests_worker as worker
    monkeypatch.setitem(sys.modules, 'baostock', SimpleNamespace(__version__='TEST_FIXTURE_SDK'))
    schemas = worker.supported_price_schema_identities()
    assert ('baostock', 'adjustment_factors', worker.baostock_field_version(sys.modules['baostock'])) in schemas
    assert ('baostock', 'market_daily', 'BAOSTOCK_TEST_FIXTURE_SDK_R03_V1') in schemas
    assert ('tushare', 'tushare_daily', worker.TUSHARE_USABLE_FIELD_VERSION) in schemas
    assert all(version!='TUSHARE_HTTP_PRO_R05_V1' for _,_,version in schemas)


def test_pipeline_base_provider_scope_uses_actual_receipt_identity(monkeypatch):
    from stockfish_core.yaogu.data_pipeline import MainboardPipeline
    from stockfish_core.yaogu import history_base_selection
    seen = {}
    def select(store, **kwargs):
        seen.update(kwargs)
        return None
    monkeypatch.setattr(history_base_selection, 'select_prior_history_base', select)
    pipeline = object.__new__(MainboardPipeline)
    pipeline.service = SimpleNamespace(store=object(), allow_test_fixture=False)
    assert pipeline._latest_prior_history_artifact(DAY, cutoff=NOW) is None
    assert seen['allowed_price_providers'] == {'baostock','tushare'}


def test_supported_retained_multi_provider_schema_does_not_need_new_token_or_factor(monkeypatch):
    import sys
    from stockfish_core.yaogu import provider_requests_worker as worker
    from stockfish_core.yaogu.history_base_selection import validate_price_schema_compatibility
    monkeypatch.setitem(sys.modules, 'baostock', SimpleNamespace(__version__='TEST_FIXTURE_SDK'))
    monkeypatch.delenv('TUSHARE_TOKEN', raising=False)
    monkeypatch.delenv('TUSHARE_PRO_TOKEN', raising=False)
    schema = worker.baostock_field_version(sys.modules['baostock'])
    retained = [dict(provider='baostock', dataset='market_daily', field_version=schema, status='COMPLETE'),
                dict(provider='baostock', dataset='adjustment_factors', field_version=schema, status='PARTIAL'),
                dict(provider='tushare', dataset='tushare_daily', field_version=worker.TUSHARE_USABLE_FIELD_VERSION, status='COMPLETE')]
    incoming = [retained[0], dict(retained[1], status='FETCH_FAILED', field_version='FAILED_ENVELOPE'),
                dict(retained[2], status='FETCH_FAILED', field_version='TUSHARE_HTTP_PRO_R05_V1')]
    checked = validate_price_schema_compatibility(retained, incoming)['checked_base_schemas']
    assert sum(r['status']=='RETAINED_BASE_SCHEMA_SUPPORTED_NO_NEW_FAMILY_EVIDENCE' for r in checked)==2
    assert all(r['new_comparable_values_authorized'] is False for r in checked)
    with pytest.raises(ValueError, match='HISTORY_BASE_PRICE_SCHEMA_NOT_PROVEN'):
        validate_price_schema_compatibility([dict(retained[1], field_version='UNSUPPORTED_OLD_SCHEMA')], incoming)


def test_raw_only_refresh_with_failed_factor_retains_base_and_unknown_new_comparable(tmp_path, monkeypatch):
    from stockfish_core.yaogu import provider_requests_worker as worker
    store, bundle, factors = fixture_base(tmp_path)
    # Explicit TEST_FIXTURE adapter registry: no fixture schema is permitted by
    # the real production registry, and no external call is made here.
    monkeypatch.setattr(worker, 'supported_price_schema_identities', lambda:{
        ('TEST_FIXTURE', 'adjustment_factors', 'TEST_FIXTURE_R03')})
    new_day, cutoff = DAY+timedelta(days=3), NOW+timedelta(days=3)
    failed = result('adjustment_factors', [], {'codes':['sh.600000'], 'start_date':'1990-01-01', 'end_date':str(new_day)}, stamp=cutoff)
    failed.update(status='FETCH_FAILED', pagination_complete=False, field_version='FAILED_ENVELOPE')
    current = result('market_daily', [raw(session=new_day)], {'date':str(new_day)}, stamp=cutoff)
    incoming = base()[:2]+[current, failed]
    proof = validate_history_base(bundle, HistoryArtifactStore(store), observation_date=new_day, cutoff=cutoff,
                                  incoming_price_receipts=incoming)
    schemas = proof['price_schema_compatibility']['checked_base_schemas']
    assert next(row for row in schemas if row['dataset']=='adjustment_factors')['status']=='RETAINED_BASE_SCHEMA_SUPPORTED_NO_NEW_FAMILY_EVIDENCE'
    refreshed, _ = normalize_capture_with_report(incoming, new_day, cutoff, (*SESSIONS,new_day),
        history_store=HistoryArtifactStore(store), history_base_ref=bundle.history_artifact_ref, coverage_type='TEST_FIXTURE')
    view = canonical_price_source_view(_prepare_context(refreshed, cutoff), [DAY,new_day], ['600000.SH'])
    assert source_token(view, '600000.SH', DAY)['values']['comparable_close']=='22'
    current_token = source_token(view, '600000.SH', new_day)
    assert current_token['status']=='SELECTED' and current_token['values']['close']=='11'
    assert current_token['values']['comparable_close'] is None
    assert any(receipt.status=='FETCH_FAILED' for receipt in refreshed.receipts)
