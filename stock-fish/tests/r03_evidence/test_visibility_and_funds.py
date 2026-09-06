from copy import deepcopy
from datetime import date,datetime,timedelta,timezone

import pytest

from stockfish_core.yaogu.contracts import HistoricalTimeProof,SourceReceipt,digest
from stockfish_core.yaogu.visibility import EvidenceResolver
from stockfish_core.yaogu.funds import calculate_funds,score_family_funds
from tests.r03_evidence.fixtures import clocks,funding,with_disclosure

CUTOFF=datetime(2026,9,4,13,tzinfo=timezone.utc)
SESSIONS=tuple(date(2026,9,i) for i in (1,2,3,4))
IID='000001.SZ'


def proof():
    return HistoricalTimeProof(policy_id='TEST_FIXTURE_HISTORICAL_PUBLISHED_RECORD',
        source_url='https://example.org/TEST_FIXTURE',evidence_hash=digest('TEST_FIXTURE_SOURCE_RULE'),
        event_time_basis='SOURCE_DATED_EVENT_RECORD',publication_time_basis='SOURCE_PUBLICATION_TIMESTAMP',
        applies_from=SESSIONS[0],applies_to=SESSIONS[-1],evidence_refs=('TEST_FIXTURE_SOURCE_DOCUMENT',),
        verified_at=CUTOFF+timedelta(days=1))


def resolver_case(late=True,proven=True,mode='RECONSTRUCTED_HISTORICAL'):
    capture=CUTOFF+timedelta(days=1) if late else CUTOFF
    clock=clocks(CUTOFF)|dict(first_seen_at=capture.isoformat(),ingested_at=capture.isoformat())
    source=SourceReceipt(receipt_id='TEST_FIXTURE_HISTORY',provider='TEST_FIXTURE',dataset='DAILY',
        content_hash=digest('SOURCE'),field_version='TEST_FIXTURE',row_count=1,pagination_complete=True,status='COMPLETE',
        instrument_ids=(IID,),business_sessions=(SESSIONS[-1],),historical_time_proof=proof() if proven else None,**clock)
    row=dict(instrument_id=IID,session=str(SESSIONS[-1]),source_refs=[source.receipt_id],close=10,**clock)
    return source,row,EvidenceResolver([source],cutoff=CUTOFF,coverage_type=mode,sessions=SESSIONS)


def test_legitimate_late_historical_source_with_evidence_is_usable_and_capture_retained():
    source,row,resolver=resolver_case()
    result=resolver.resolve(row,instrument_id=IID,session=SESSIONS[-1],require_session=True)
    assert result.allowed and result.reconstruction_used
    assert result.used_sources[0]['first_seen_at']==source.first_seen_at.isoformat().replace('+00:00','Z')
    assert resolver.effective_dependencies()['records'][0]['first_seen_at']==row['first_seen_at']


@pytest.mark.parametrize('mode,proven',[('PROSPECTIVE_CAPTURED',True),('RECONSTRUCTED_HISTORICAL',False)])
def test_capture_after_cutoff_without_mode_and_proof_is_rejected(mode,proven):
    source,row,resolver=resolver_case(proven=proven,mode=mode)
    assert not resolver.resolve(row,instrument_id=IID).allowed
    assert resolver.effective_dependencies()['records']==[]


@pytest.mark.parametrize('owner',['receipt','row'])
@pytest.mark.parametrize('field',['event_time','source_published_at'])
def test_future_event_or_publication_is_never_licensed_by_historical_proof(owner,field):
    source,row,_=resolver_case()
    if owner=='receipt':source=source.model_copy(update={field:CUTOFF+timedelta(seconds=1)})
    else:row[field]=(CUTOFF+timedelta(seconds=1)).isoformat()
    resolver=EvidenceResolver([source],cutoff=CUTOFF,coverage_type='RECONSTRUCTED_HISTORICAL',sessions=SESSIONS)
    assert not resolver.resolve(row,instrument_id=IID).allowed


@pytest.mark.parametrize('change,reason',[
    ({'instrument_id':'OTHER'},'INSTRUMENT_ID_MISMATCH'),
    ({'source_refs':['MISSING']},'SOURCE_REFERENCE_UNRESOLVED'),
    ({'session':'2026-09-07'},'FUTURE_BUSINESS_SESSION'),
    ({'first_seen_at':'2026-09-04T12:00:00+00:00'},'ROW_CAPTURE_PRECEDES_PARENT_RECEIPT')])
def test_row_parent_identity_session_and_capture_consistency(change,reason):
    source,row,_=resolver_case(late=False)
    row.update(change)
    resolver=EvidenceResolver([source],cutoff=CUTOFF,coverage_type='TEST_FIXTURE',sessions=(*SESSIONS,date(2026,9,7)))
    result=resolver.resolve(row,instrument_id=IID,require_session=True)
    assert not result.allowed and reason in result.reason_codes


def call(e,sources,**kwargs):
    return calculate_funds(e,CUTOFF,SESSIONS[-1],sessions=SESSIONS,receipts=sources,coverage_type='TEST_FIXTURE',**kwargs)


def test_fixed_funds_formula_with_complete_authoritative_context():
    e,sources=funding(IID,SESSIONS,CUTOFF)
    r=call(e,sources)
    assert (r['F_money'],r['F_lhb'],r['F'],r['lhb_raw_net_cny'])==(100,50,80,None)
    family=score_family_funds([e],{IID:76.75},CUTOFF,SESSIONS[-1],sessions=SESSIONS,receipts=sources,coverage_type='TEST_FIXTURE')
    assert family['items'][IID]['S_funds']==pytest.approx(77.075)
    assert call(with_disclosure(e,sources),sources)['F_lhb']==100


@pytest.mark.parametrize('mutate',[
    lambda e,r:(e,()),
    lambda e,r:(dict(e,source_refs=['MISSING']),r),
    lambda e,r:(e,tuple(x.model_copy(update={'pagination_complete':False}) for x in r)),
    lambda e,r:(e,tuple(x.model_copy(update={'source_published_at':CUTOFF+timedelta(days=1)}) for x in r)),
    lambda e,r:(e,tuple(x.model_copy(update={'measurement_definition':'OTHER_DEFINITION'}) for x in r)),
    lambda e,r:(e,tuple(x.model_copy(update={'field_version':'DIFFERENT_VERSION'}) for x in r)),
    lambda e,r:(e,tuple(x.model_copy(update={'instrument_ids':('OTHER',)}) for x in r)),
    lambda e,r:(e,tuple(x.model_copy(update={'currency':'USD'}) for x in r)),
])
def test_funds_authoritative_source_rejections(mutate):
    e,r=funding(IID,SESSIONS,CUTOFF)
    e,r=mutate(e,r)
    value=call(e,r)
    assert value['status']=='UNAVAILABLE' and value['F'] is None


def test_funds_observation_and_calendar_context_cannot_be_omitted():
    e,r=funding(IID,SESSIONS,CUTOFF)
    assert calculate_funds(e,CUTOFF)['reason_codes']==['FUNDS_AUTHORITY_CONTEXT_REQUIRED']


def test_decades_apart_dates_fail_even_with_existing_sources():
    e,r=funding(IID,SESSIONS,CUTOFF)
    assert call(e,r)['status']=='AVAILABLE'
    e['money_sessions']=['2000-01-03','2010-01-04',str(SESSIONS[-1])]
    for row,d in zip(e['money_rows'],e['money_sessions']):row['session']=d
    assert call(e,r)['reason_codes']==['CONSECUTIVE_THREE_EXCHANGE_SESSIONS_REQUIRED']


def test_future_disclosure_window_cannot_use_early_self_reported_clocks():
    e,r=funding(IID,SESSIONS,CUTOFF)
    e=with_disclosure(e,r,window=('2026-09-07',))
    assert call(e,r)['reason_codes']==['DISCLOSURE_WINDOW_OUTSIDE_OBSERVATION']


@pytest.mark.parametrize('mutation',[
    {'instrument_id':'OTHER'}, {'currency':'USD'}, {'amount_unit':'TEN_THOUSAND_CNY'},
    {'amount_definition':'OTHER_BASIS'}, {'source_refs':['MISSING']},
    {'window_sessions':['2026-09-02']}, {'session':'2026-09-03'}])
def test_window_denominator_identity_currency_basis_and_scope(mutation):
    e,r=funding(IID,SESSIONS,CUTOFF)
    e=with_disclosure(e,r)
    e['window_amounts'][0].update(mutation)
    assert call(e,r)['status']=='UNAVAILABLE'


def test_not_disclosed_requires_complete_same_day_set_and_not_a_failure():
    e,r=funding(IID,SESSIONS,CUTOFF)
    e['disclosure_universe']['disclosed_instrument_ids']=[IID]
    assert call(e,r)['reason_codes']==['NOT_DISCLOSED_UNIVERSE_CONTRADICTION']
    e['disclosure_universe']['disclosed_instrument_ids']=[]
    e['disclosure_universe']['session']='2026-09-03'
    assert call(e,r)['status']=='UNAVAILABLE'


def test_not_disclosed_cannot_overwrite_foreign_instrument_identity():
    e,r=funding(IID,SESSIONS,CUTOFF)
    e['disclosure_universe']['instrument_id']='OTHER'
    assert call(e,r)['reason_codes']==['DISCLOSURE_UNIVERSE_INSTRUMENT_MISMATCH']


def test_same_day_early_clock_cannot_claim_complete_closed_session_money():
    e,r=funding(IID,SESSIONS,CUTOFF)
    early=CUTOFF.replace(hour=1)
    e['money_rows'][-1].update(event_time=early.isoformat(),source_published_at=early.isoformat())
    assert call(e,r)['reason_codes']==['CLOSED_SESSION_FUNDS_NOT_YET_AVAILABLE']


def test_future_authority_duplicate_cannot_poison_earlier_valid_family():
    e,r=funding(IID,SESSIONS,CUTOFF)
    future_source=r[0].model_copy(update={'receipt_id':'FUTURE_RECEIPT','source_published_at':CUTOFF+timedelta(days=1)})
    future=deepcopy(e);future['source_refs']=['FUTURE_RECEIPT'];future['money_rows']='INVALID_FUTURE_BODY'
    context=dict(observation_date=SESSIONS[-1],sessions=SESSIONS,receipts=(*r,future_source),coverage_type='TEST_FIXTURE')
    assert score_family_funds([e,future],{IID:70},CUTOFF,**context)==score_family_funds([e],{IID:70},CUTOFF,**context)


def test_one_failed_stock_blocks_entire_family_funds_and_keeps_daily_scores_unchanged():
    a,ra=funding(IID,SESSIONS,CUTOFF)
    b,rb=funding('600000.SH',SESSIONS,CUTOFF)
    b['money_rows'][0]['source_refs']=['MISSING']
    daily={IID:76.75,'600000.SH':75}
    result=score_family_funds([a,b],daily,CUTOFF,SESSIONS[-1],sessions=SESSIONS,receipts=(*ra,*rb),coverage_type='TEST_FIXTURE')
    assert result['status']=='UNAVAILABLE'
    assert all(v['S_funds'] is None for v in result['items'].values())
    assert daily=={IID:76.75,'600000.SH':75}


def test_actual_money_changes_effective_dependency_but_future_and_outcome_columns_do_not():
    e,r=funding(IID,SESSIONS,CUTOFF)
    original=call(e,r)
    changed=deepcopy(e);changed['money_rows'][0]['net_large_cny']=-10
    assert call(changed,r)['effective_dependencies']!=original['effective_dependencies']
    altered=deepcopy(e);altered['money_rows'][0]['上榜后10日']=999
    future=deepcopy(altered['money_rows'][0]);future.update(clocks(CUTOFF+timedelta(days=1)))
    altered['money_rows'].append(future)
    assert call(altered,r)==original
