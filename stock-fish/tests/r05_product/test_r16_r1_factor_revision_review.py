"""TEST_FIXTURE: factor review preserves immutable data and real PIT selection."""
from datetime import timedelta
from decimal import Decimal
from copy import deepcopy
from pathlib import Path
import sys
import pytest

from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
from stockfish_core.yaogu.factor_revision_review import apply_factor_revision_reviews, COMPARABLE, REASON
from stockfish_core.yaogu.strategy_experiments import _prepare_context, _bar
from stockfish_core.yaogu.source_resolution import canonical_price_source_view, source_token
from stockfish_core.yaogu.theme_capital_trend import _window_return_uncached, _ma_value_uncached
from tests.r03_sources.test_provider_boundaries import NOW, DAY, SESSIONS, base, result, raw, master
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from r16_r1_build_corrected_history import preserve_t_envelope_with_normalized_history

IID='600000.SH'

def setup(tmp_path):
    store=SQLiteStore(tmp_path/'FACTOR_REVIEW_TEST_FIXTURE.sqlite3')
    owner=HistoryArtifactStore(store)
    old=result('adjustment_factors',[{'code':'sh.600000','dividOperateDate':'2000-01-01','backAdjustFactor':'2'}],
        {'codes':['sh.600000'],'start_date':'1990-01-01','end_date':str(DAY)})
    rows=[raw(session=session) for session in SESSIONS[-3:]]
    rows += [raw(code='sh.600001',session=session) for session in SESSIONS[-3:]]
    sources=base(rows)
    sources[0]=result('mainboard_sse',[master(),master('600001')])
    bundle,_=normalize_capture_with_report(sources+[old],DAY,NOW,SESSIONS,history_store=owner,coverage_type='TEST_FIXTURE')
    new=result('adjustment_factors',[*old['rows'],{'code':'sh.600000','dividOperateDate':str(DAY),'backAdjustFactor':'2.5'}],
        {'codes':['sh.600000'],'start_date':'1990-01-01','end_date':str(DAY)},stamp=NOW+timedelta(seconds=10))
    return store,owner,bundle,sources,old,new

def reconstruct(owner,bundle,sources,old,new):
    normalized,report=normalize_capture_with_report(sources[:2]+[old,new],DAY,NOW+timedelta(seconds=10),SESSIONS,
        history_store=owner,history_base_ref=bundle.history_artifact_ref,coverage_type='TEST_FIXTURE',
        review_factor_conflicts=True,factor_review_base=bundle)
    corrected=preserve_t_envelope_with_normalized_history(bundle,normalized,report['factor_revision_review'])
    return corrected,report

def token(bundle,cutoff,session=DAY,iid=IID):
    return source_token(canonical_price_source_view(_prepare_context(bundle,cutoff),[session],[iid]),iid,session)

def sizes(store):
    with store.transaction(write=False) as con:
        return tuple(con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
                     ('history_evidence_rows','history_partition_members'))

def test_conflict_real_normalizer_and_both_production_consumers(tmp_path):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    original_bytes=bundle.model_dump_json()
    original_manifest=deepcopy(owner.manifest(bundle.history_artifact_ref))
    corrected,report=reconstruct(owner,bundle,sources,old,new)
    review=report['factor_revision_review']
    assert review['new_review_rows']==3  # Existing series-level refusal, not just effective DAY.
    assert {row['reason'] for row in review['reviews']}=={REASON}
    assert corrected.universe==bundle.universe
    assert bundle.model_dump_json()==original_bytes
    assert owner.manifest(bundle.history_artifact_ref)==original_manifest
    before=token(bundle,NOW)
    assert before['status']=='SELECTED'
    assert before['values']['comparable_close']=='22'
    assert token(corrected,NOW)['row_hash']==before['row_hash']
    after=token(corrected,NOW+timedelta(seconds=10))
    assert after['status']=='SELECTED' and after['values']['comparable_close'] is None
    for row in review['reviews']:
        assert row['review_row']['close']==row['old_row']['close']=='11'
        assert row['review_row']['quality']==row['old_row']['quality']=='VALID'
        assert all(row['review_row'][name] is None for name in COMPARABLE)
        assert row['review_row']['event_time']==row['old_row']['event_time']
        assert old['request_receipt_id'] in row['source_refs'] and new['request_receipt_id'] in row['source_refs']
        assert row['actual_revision_clocks']['ingested_at']==(NOW+timedelta(seconds=10)).isoformat()
    assert token(corrected,NOW+timedelta(seconds=10),iid='600001.SH')['row_hash']==token(bundle,NOW,iid='600001.SH')['row_hash']
    ctx=_prepare_context(corrected,NOW+timedelta(seconds=10))
    assert _bar(ctx,DAY,IID)['comparable_close'] is None
    assert _window_return_uncached(ctx,IID,len(SESSIONS)-1,1) is None
    assert _ma_value_uncached(ctx,IID,len(SESSIONS)-1,2)[0] is None
    assert all(row.comparable_close is None for row in corrected.bars if row.instrument_id==IID)

def test_repeated_normalization_is_idempotent_and_future_append_reuses_partitions(tmp_path):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    corrected,report=reconstruct(owner,bundle,sources,old,new)
    size=sizes(store)
    again,second=reconstruct(owner,corrected,sources,old,new)
    assert second['factor_revision_review']['new_review_rows']==0
    assert second['factor_revision_review']['status']=='FACTOR_REVIEW_ALREADY_UNKNOWN'
    assert again.history_artifact_ref==corrected.history_artifact_ref
    assert sizes(store)==size
    next_day=DAY+timedelta(days=3)
    next_raw=result('market_daily',[raw(session=next_day)],{'date':str(next_day)},stamp=NOW+timedelta(days=3))
    next_bundle,next_report=normalize_capture_with_report(sources[:2]+[old,new,next_raw],next_day,NOW+timedelta(days=3),SESSIONS+(next_day,),
        history_store=owner,history_base_ref=corrected.history_artifact_ref,coverage_type='TEST_FIXTURE',
        review_factor_conflicts=True,factor_review_base=corrected)
    assert next_report['factor_revision_review']['new_review_rows']==0
    a={p['session']:p['partition_hash'] for p in owner.manifest(corrected.history_artifact_ref)['partitions']}
    b={p['session']:p['partition_hash'] for p in owner.manifest(next_bundle.history_artifact_ref)['partitions']}
    assert all(b[session]==value for session,value in a.items())
    assert token(next_bundle,NOW+timedelta(days=3),session=DAY)['row_hash']==token(corrected,NOW+timedelta(seconds=10))['row_hash']

def test_no_factor_conflict_has_no_review_write_and_no_partition_changes(tmp_path):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    same=result('adjustment_factors',old['rows'],old['parameters'],stamp=NOW+timedelta(seconds=10))
    control,_=normalize_capture_with_report(sources[:2]+[old,same],DAY,NOW+timedelta(seconds=10),SESSIONS,
        history_store=owner,history_base_ref=bundle.history_artifact_ref,coverage_type='TEST_FIXTURE')
    # The normalizer can add dated security observations even without prices;
    # the factor-review opt-in itself must leave that real control unchanged.
    prior_counts=sizes(store)
    normalized,report=normalize_capture_with_report(sources[:2]+[old,same],DAY,NOW+timedelta(seconds=10),SESSIONS,
        history_store=owner,history_base_ref=bundle.history_artifact_ref,coverage_type='TEST_FIXTURE',
        review_factor_conflicts=True,factor_review_base=bundle)
    assert report['factor_revision_review']['status']=='NO_FACTOR_CONFLICT'
    assert normalized.history_artifact_ref==control.history_artifact_ref
    assert owner.manifest(normalized.history_artifact_ref)['partitions']==owner.manifest(control.history_artifact_ref)['partitions']
    assert sizes(store)==prior_counts
    corrected=preserve_t_envelope_with_normalized_history(bundle,normalized,report['factor_revision_review'])
    assert corrected.bars==bundle.bars

def test_wrong_base_binding_rejected_before_normalization(tmp_path):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    with pytest.raises(ValueError,match='EXACT_HISTORY_BASE'):
        normalize_capture_with_report(sources,DAY,NOW,SESSIONS,history_store=owner,
            history_base_ref=None,review_factor_conflicts=True,factor_review_base=bundle)

@pytest.mark.parametrize('kind',['future','missing','same_clock','raw_tie','budget'])
def test_unsafe_authority_or_ambiguous_raw_cannot_mint_review(tmp_path,kind):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    normalized,_=normalize_capture_with_report(sources[:2]+[old,new],DAY,NOW+timedelta(seconds=10),SESSIONS,
        history_store=owner,history_base_ref=bundle.history_artifact_ref,coverage_type='TEST_FIXTURE')
    captures={IID:[(r['rows'],r['request_receipt_id'],r['parameters']['end_date'],r['parameters']['start_date']) for r in (old,new)]}
    kwargs={'cutoff':NOW+timedelta(seconds=10),'base_bundle':bundle}
    match=''
    if kind=='future':kwargs['cutoff']=NOW;match='PARENT_NOT_VISIBLE'
    if kind=='missing':captures[IID][1]=(new['rows'],'MISSING',str(DAY),'1990-01-01');match='PARENT_REQUIRED'
    if kind in ('same_clock','raw_tie'):
        bars=list(bundle.bars)
        chosen=next(row for row in bars if row.instrument_id==IID and row.session==DAY)
        clock=NOW+timedelta(seconds=10) if kind=='same_clock' else NOW
        changed=chosen.model_copy(update={'source_published_at':clock,'first_seen_at':clock,'ingested_at':clock,
            **({'close':Decimal('12')} if kind=='raw_tie' else {})})
        normalized=normalized.model_copy(update={'bars':(changed,)})
        normalized._history_store=owner
        match='NO_STRICT_ACTUAL_CLOCK_ADVANCE' if kind=='same_clock' else 'CANONICAL_RAW_CONFLICT'
    if kind=='budget':kwargs['max_rows']=0;match='ROW_BUDGET'
    before=sizes(store)
    with pytest.raises((ValueError,TimeoutError),match=match):apply_factor_revision_reviews(normalized,captures,**kwargs)
    assert sizes(store)==before

def test_incomplete_or_failed_factor_fragment_does_not_create_conflict_authority(tmp_path):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    incomplete=deepcopy(new);incomplete['status']='PARTIAL';incomplete['pagination_complete']=False
    normalized,report=normalize_capture_with_report(sources[:2]+[old,incomplete],DAY,NOW+timedelta(seconds=10),SESSIONS,
        history_store=owner,history_base_ref=bundle.history_artifact_ref,coverage_type='TEST_FIXTURE',
        review_factor_conflicts=True,factor_review_base=bundle)
    assert report['factor_revision_review']['status']=='NO_FACTOR_CONFLICT'
    assert normalized.history_artifact_ref is not None
    assert token(normalized,NOW+timedelta(seconds=10))['values']['comparable_close']=='22'


def test_inline_only_original_revisions_survive_each_earlier_cutoff(tmp_path):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    current=next(row for row in bundle.bars if row.instrument_id==IID and row.session==DAY)
    raw_receipt=next(r for r in bundle.receipts if r.receipt_id==current.source_refs[0])
    inline=[];extra_receipts=[]
    for seconds,price in ((3,Decimal('12')),(5,Decimal('13'))):
        at=NOW+timedelta(seconds=seconds)
        receipt=raw_receipt.model_copy(update={'receipt_id':'INLINE_ONLY_TEST_FIXTURE_'+str(seconds),
            'content_hash':digest([seconds,str(price)]),'source_published_at':at,'first_seen_at':at,'ingested_at':at})
        extra_receipts.append(receipt)
        inline.append(current.model_copy(update={'close':price,'high':price,'comparable_close':price*2,
            'comparable_high':price*2,'source_refs':(receipt.receipt_id,old['request_receipt_id']),
            'source_published_at':at,'first_seen_at':at,'ingested_at':at}))
    bundle=bundle.model_copy(update={'bars':(*bundle.bars,*inline),'receipts':(*bundle.receipts,*extra_receipts)})
    bundle._history_store=owner
    expected={seconds:token(bundle,NOW+timedelta(seconds=seconds)) for seconds in (0,3,5)}
    corrected,report=reconstruct(owner,bundle,sources,old,new)
    retained=report['factor_revision_review']['retained_inline_originals']
    assert {row['row_hash'] for row in retained}=={digest(row) for row in inline}
    for seconds in (0,3,5):
        assert token(corrected,NOW+timedelta(seconds=seconds))['row_hash']==expected[seconds]['row_hash']
    assert token(corrected,NOW+timedelta(seconds=10))['values']['comparable_close'] is None


@pytest.mark.parametrize('inline_only_with_older_unknown',[False,True])
def test_independent_tushare_basis_is_not_revoked_by_baostock_factor_conflict(tmp_path,inline_only_with_older_unknown):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    current=next(row for row in bundle.bars if row.instrument_id==IID and row.session==DAY)
    factor=next(r for r in bundle.receipts if r.receipt_id==old['request_receipt_id'])
    at=NOW+timedelta(seconds=1)
    ts=factor.model_copy(update={'receipt_id':'TUSHARE_INDEPENDENT_TEST_FIXTURE','provider':'tushare',
        'dataset':'tushare_adj_factor','content_hash':digest('independent factor'),
        'source_published_at':at,'first_seen_at':at,'ingested_at':at})
    independent=current.model_copy(update={'comparable_basis':'TUSHARE_RAW_X_ADJ_FACTOR_R05_V1:'+IID+':TEST_FIXTURE',
        'source_refs':(current.source_refs[0],ts.receipt_id),
        'source_published_at':at,'first_seen_at':at,'ingested_at':at})
    identity={}
    if not inline_only_with_older_unknown:
        writer=owner.writer((*bundle.receipts,ts),base_ref=bundle.history_artifact_ref)
        writer.append(independent)
        identity=writer.finish()
    bundle=bundle.model_copy(update=identity|{'bars':(*bundle.bars,independent),'receipts':(*bundle.receipts,ts)})
    bundle._history_store=owner
    expected=token(bundle,NOW+timedelta(seconds=1))
    if inline_only_with_older_unknown:
        older_unknown=result('market_daily',[raw()],{'date':str(DAY)},stamp=NOW)
        normalized,report=normalize_capture_with_report(sources[:2]+[old,new,older_unknown],DAY,NOW+timedelta(seconds=10),SESSIONS,
            history_store=owner,history_base_ref=bundle.history_artifact_ref,coverage_type='TEST_FIXTURE',
            review_factor_conflicts=True,factor_review_base=bundle)
        corrected=preserve_t_envelope_with_normalized_history(bundle,normalized,report['factor_revision_review'])
        assert token(normalized,NOW+timedelta(seconds=10))['row_hash']==expected['row_hash']
    else:
        corrected,report=reconstruct(owner,bundle,sources,old,new)
    review=report['factor_revision_review']
    assert review['new_review_rows']==2
    assert len(review['outside_factor_scope'])==1
    assert review['outside_factor_scope'][0]['row_hash']==digest(independent)
    assert token(corrected,NOW+timedelta(seconds=10))['row_hash']==expected['row_hash']
    assert token(corrected,NOW+timedelta(seconds=10))['values']['comparable_close']=='22'


def test_existing_unknown_does_not_discard_earlier_inline_only_variant(tmp_path):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    current=next(row for row in bundle.bars if row.instrument_id==IID and row.session==DAY)
    at=NOW+timedelta(seconds=3)
    old_raw=next(r for r in bundle.receipts if r.receipt_id==current.source_refs[0])
    late=old_raw.model_copy(update={'receipt_id':'OLDER_INLINE_TEST_FIXTURE','content_hash':digest('older inline'),
        'source_published_at':at,'first_seen_at':at,'ingested_at':at})
    inline=current.model_copy(update={'close':Decimal('12'),'comparable_close':Decimal('24'),
        'source_refs':(late.receipt_id,old['request_receipt_id']),
        'source_published_at':at,'first_seen_at':at,'ingested_at':at})
    bundle=bundle.model_copy(update={'bars':(*bundle.bars,inline),'receipts':(*bundle.receipts,late)})
    bundle._history_store=owner
    expected=token(bundle,at)
    later=result('market_daily',[raw()],{'date':str(DAY)},stamp=NOW+timedelta(seconds=5))
    normalized,report=normalize_capture_with_report(sources[:2]+[old,new,later],DAY,NOW+timedelta(seconds=10),SESSIONS,
        history_store=owner,history_base_ref=bundle.history_artifact_ref,coverage_type='TEST_FIXTURE',
        review_factor_conflicts=True,factor_review_base=bundle)
    corrected=preserve_t_envelope_with_normalized_history(bundle,normalized,report['factor_revision_review'])
    assert any(row['row_hash']==digest(inline) for row in report['factor_revision_review']['retained_inline_originals'])
    assert token(corrected,at)['row_hash']==expected['row_hash']
    assert token(corrected,NOW+timedelta(seconds=10))['values']['comparable_close'] is None


def test_conflict_history_reads_are_always_instrument_indexed(tmp_path,monkeypatch):
    store,owner,bundle,sources,old,new=setup(tmp_path)
    actual=owner.iter_rows
    reads=[]
    def indexed(*args,**kwargs):
        assert kwargs.get('instrument_id')==IID
        reads.append(kwargs.copy())
        yield from actual(*args,**kwargs)
    monkeypatch.setattr(owner,'iter_rows',indexed)
    corrected,report=reconstruct(owner,bundle,sources,old,new)
    assert reads and report['factor_revision_review']['rows_read']<=20
