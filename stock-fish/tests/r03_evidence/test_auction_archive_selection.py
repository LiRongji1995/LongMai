from datetime import timedelta
import pytest
from stockfish_core.storage import SQLiteStore
from stockfish_core.jobs import JobService
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.scheduler import CandidateScheduler
from stockfish_core.yaogu.auction_input_selector import select_auction_inputs
from tests.r02_product.test_independent_time_boundaries import tiny_fixture,quote,at,SESSIONS,publish_fixture,settle
from tests.r03_evidence.test_consumer_visibility import auction_context


def stage_aware_primary_doc(run):
    from stockfish_core.yaogu.validation_identity import current_validation_identity
    return {**current_validation_identity(),'run_id':run.run_id,'signal_observation_date':str(run.observation_date),
        'decision_cutoff':run.decision_cutoff.isoformat(),'official_primary_signal_countable':True}


@pytest.mark.parametrize('bad_kind',['0930','foreign_instrument','future_receipt'])
def test_registered_later_archive_cannot_hide_prior_valid_0925_in_scheduler(tmp_path,bad_kind):
    store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    with JobService(store) as jobs:
        service=CandidateService(store,jobs,tmp_path/'TEST_FIXTURE_state',allow_test_fixture=True)
        run,_=tiny_fixture();run=run.model_copy(update={'market_session':'EVENING'})
        publish_fixture(service,run)
        service.store.put_document('stage_aware_prospective_primary_runs', run.run_id, stage_aware_primary_doc(run))
        q=quote();good=auction_context(q)
        first=service.register_auction((q,),**good)
        bad=q.model_copy(update={'instrument_id':'OTHER'}) if bad_kind=='foreign_instrument' else quote(minute=30) if bad_kind=='0930' else q
        context=auction_context(bad)
        if bad_kind=='future_receipt':context['receipts']=(context['receipts'][0].model_copy(update={'source_published_at':at(q.session,10)+timedelta(days=1)}),)
        service.register_auction((bad,),**context)
        scheduler=CandidateScheduler(service,clock=lambda:at(SESSIONS[1],9,0))
        events=scheduler.tick(at(SESSIONS[1],9,35))
        job=next(e for e in events if e['market_session']=='AUCTION_REVIEW')
        finished=settle(jobs,job['job_id'])
        assert finished['state']=='SUCCEEDED'
        value=store.get_document('auction_reviews',finished['result']['review_id'])
        assert value['evaluations'][0]['conditions']['GAP_WEAK']['state']=='TRUE'
        selection=service._all('auction_input_selections')[-1]
        assert selection['source_archive_ids']==[first]


def test_merge_preserves_known_observations_from_separate_matching_archives():
    run,_=tiny_fixture();q=quote();context=auction_context(q)
    archive=dict(quotes=[q.model_dump(mode='json')],receipts=[x.model_dump(mode='json') for x in context['receipts']],
                 sessions=[str(d) for d in SESSIONS],coverage_type='TEST_FIXTURE')
    original=select_auction_inputs(run,[archive],at(q.session,10))
    future=dict(archive,quotes=[q.model_copy(update={'event_time':at(q.session,10,30)}).model_dump(mode='json')])
    changed=select_auction_inputs(run,[archive,future],at(q.session,10))
    assert digest(original)==digest(changed)
