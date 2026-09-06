from datetime import timedelta

import pytest

from stockfish_core.yaogu.contracts import SourceReceipt,HistoricalTimeProof,digest
from stockfish_core.yaogu.auction import evaluate_auction
from stockfish_core.yaogu.outcomes import evaluate_outcomes
from stockfish_core.yaogu.evidence_service import EvidenceService
from stockfish_core.storage import SQLiteStore
from tests.r02_product.test_independent_time_boundaries import tiny_fixture,quote,at,SESSIONS,IID,clocks
from tests.r02_product.test_public_evidence import record,receipt,DAY,AT
from tests.r03_evidence.test_visibility_and_funds import proof


def auction_context(q):
    source=SourceReceipt(receipt_id=q.source_ref,provider='TEST_FIXTURE',dataset='AUCTION_0925',source_type='AUCTION_0925',
        instrument_ids=(q.instrument_id,),business_sessions=(q.session,),content_hash=digest(q),field_version='TEST_FIXTURE',
        row_count=1,pagination_complete=True,status='COMPLETE',**clocks(q.event_time))
    return dict(receipts=(source,),sessions=SESSIONS,coverage_type='TEST_FIXTURE')


@pytest.mark.parametrize('mode',['TEST_FIXTURE','RECONSTRUCTED_HISTORICAL'])
def test_auction_parent_future_publication_is_never_an_available_0925(mode):
    run,_=tiny_fixture();q=quote();cutoff=at(q.session,10)
    context=auction_context(q)
    assert evaluate_auction(run,(q,),cutoff,**context)[0].conditions['GAP_WEAK']['state']=='TRUE'
    source=context['receipts'][0].model_copy(update={'source_published_at':cutoff+timedelta(days=1)})
    result=evaluate_auction(run,(q,),cutoff,**(context|dict(receipts=(source,),coverage_type=mode)))
    assert result[0].status=='UNAVAILABLE'
    assert result[0].conditions['GAP_WEAK']['state']=='NOT_EVALUATED'


def test_auction_missing_source_or_wrong_calendar_is_not_evaluated():
    run,_=tiny_fixture();q=quote();cutoff=at(q.session,10)
    assert evaluate_auction(run,(q,),cutoff)[0].status=='UNAVAILABLE'
    assert evaluate_auction(run,(q,),cutoff,**(auction_context(q)|dict(sessions=SESSIONS[:1])))[0].status=='UNAVAILABLE'


@pytest.mark.parametrize('mode',['TEST_FIXTURE','RECONSTRUCTED_HISTORICAL'])
def test_outcome_future_bar_parent_is_not_known_success_or_zero_return(mode):
    run,bundle=tiny_fixture();cutoff=at(SESSIONS[1],16)
    original=evaluate_outcomes(run,bundle,cutoff,horizons=(1,))[0]
    assert original.status=='MATURE' and original.metrics['close_reference_change']==0
    target='TEST_FIXTURE_BAR_'+SESSIONS[1].isoformat()
    sources=tuple(r.model_copy(update={'source_published_at':cutoff+timedelta(days=1)}) if r.receipt_id==target else r for r in bundle.receipts)
    changed=bundle.model_copy(update={'receipts':sources,'coverage_type':mode})
    result=evaluate_outcomes(run,changed,cutoff,horizons=(1,))[0]
    assert result.metrics['close_reference_change'] is None
    assert result.structure_success is None


def test_late_historical_outcome_with_source_proof_retains_actual_capture():
    run,bundle=tiny_fixture();cutoff=at(SESSIONS[1],16);late=cutoff+timedelta(days=7)
    p=HistoricalTimeProof(policy_id='TEST_FIXTURE_HISTORY',source_url='https://example.org/TEST_FIXTURE',
        evidence_hash=digest('TEST_FIXTURE_RULE'),event_time_basis='SOURCE_DATED_EVENT_RECORD',
        publication_time_basis='SOURCE_PUBLICATION_TIMESTAMP',applies_from=SESSIONS[0],applies_to=SESSIONS[-1],
        evidence_refs=('TEST_FIXTURE_PROOF',),verified_at=late)
    cap=dict(first_seen_at=late,ingested_at=late)
    sources=tuple(r.model_copy(update=cap|dict(historical_time_proof=p)) for r in bundle.receipts)
    securities=tuple(s.model_copy(update=cap) for s in bundle.universe.securities)
    reconstructed=bundle.model_copy(update={'receipts':sources,'coverage_type':'RECONSTRUCTED_HISTORICAL',
        'universe':bundle.universe.model_copy(update=cap|dict(securities=securities)),
        'bars':tuple(b.model_copy(update=cap) for b in bundle.bars)})
    result=evaluate_outcomes(run,reconstructed,cutoff,horizons=(1,))[0]
    assert result.status=='MATURE' and result.metrics['close_reference_change']==0
    assert all(r.first_seen_at==late for r in reconstructed.receipts)


def test_public_evidence_query_uses_same_historical_proof_rule(tmp_path):
    service=EvidenceService(SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'),allow_test_fixture=True)
    later=AT+timedelta(days=1)
    raw=record(session=DAY.isoformat(),first_seen_at=later.isoformat(),ingested_at=later.isoformat())
    source=receipt(first_seen_at=later.isoformat(),ingested_at=later.isoformat(),historical_time_proof=proof().model_dump(mode='json'))
    service.register_public_records([raw],[source],coverage_type='RECONSTRUCTED_HISTORICAL')
    result=service.instrument('605398.SH',AT,DAY)
    assert len(result['records'])==1
    assert result['records'][0]['first_seen_at']==later.isoformat().replace('+00:00','Z')
    assert result['score_status']=='UNAVAILABLE'


def test_public_evidence_future_parent_remains_hidden_in_reconstruction(tmp_path):
    service=EvidenceService(SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'),allow_test_fixture=True)
    later=AT+timedelta(days=1)
    raw=record(session=DAY.isoformat(),first_seen_at=later.isoformat(),ingested_at=later.isoformat())
    source=receipt(first_seen_at=later.isoformat(),ingested_at=later.isoformat(),source_published_at=later.isoformat(),historical_time_proof=proof().model_dump(mode='json'))
    service.register_public_records([raw],[source],coverage_type='RECONSTRUCTED_HISTORICAL')
    assert service.instrument('605398.SH',AT,DAY)['records']==[]
