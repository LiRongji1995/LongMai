"""Local service -> durable job -> publication integration, isolated TEST_FIXTURE only."""
from datetime import timedelta
from pathlib import Path
import csv
import io
import threading
import time
import pytest

from stockfish_core.yaogu.candidate_service import CandidateRequest
from stockfish_core.common import UnavailableError
from stockfish_core.storage import ImmutableConflict
from tests.support.local_service import ResearchService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.contracts import digest
from tests.yaogu.fixtures import make_bundle

def expanded_bundle():
    bundle,cutoff=make_bundle(31)
    future=[];day=bundle.observation_date
    while len(future)<10:
        day+=timedelta(days=1)
        if day.weekday()<5:future.append(day)
    return bundle.model_copy(update={'sessions':(*bundle.sessions,*future)}),cutoff

@pytest.fixture(scope='module')
def market():return expanded_bundle()

@pytest.fixture
def product(tmp_path,monkeypatch,market):
    import stockfish_core.yaogu.candidate_service as candidate_module
    # R03 default entry rejects stale active data. This historical TEST_FIXTURE
    # exercises its own declared market day, including the production check.
    fixture_started=time.monotonic()
    monkeypatch.setattr(candidate_module,'utc_now',lambda:market[1]+timedelta(seconds=time.monotonic()-fixture_started))
    monkeypatch.delenv('STOCKFISH_API_KEY',raising=False)
    store=SQLiteStore(tmp_path/'TEST_FIXTURE_product.sqlite3')
    core=ResearchService(root=tmp_path/'TEST_FIXTURE_state',store=store,allow_test_fixture=True,data_dir=tmp_path/'NO_QLIB_DATA')
    input_id=core.candidates.register_input(market[0],activate=True)
    client=core.candidates
    yield client,core,input_id,tmp_path
    core.close()

def wait_job(client,job_id):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        job=client.jobs.get(job_id)
        if job['state'] in ('SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'):return job
        time.sleep(.005)
    raise AssertionError('bounded candidate fixture job did not finish')

def publish(client,payload=None):
    started=client.submit(CandidateRequest(**(payload or {})))
    job=wait_job(client,started['job_id'])
    assert job['state']=='SUCCEEDED',job
    return client.get_run(job['result']['candidate_run_id']).model_dump(mode='json'),job

def test_default_candidate_service_persists_thirty_twenty_without_account_model_llm(product):
    client,core,input_id,_=product
    value,job=publish(client)
    assert len(value['items'])==30 and len(value['display_ids'])==20 and len(value['focus_ids'])<=5
    assert value['status']=='COMPLETE' and value['model_id'] is None
    assert value['coverage_type']=='TEST_FIXTURE'
    assert {i['primary_family'] for i in value['items']}=={'ONE_TO_TWO','TWO_TO_THREE','HIGH_BOARD','TREND_NM','REBOUND_SETUP'}
    assert all(i['score'] is not None for i in value['items'])
    saved=core.store.get_document('candidate_runs',value['run_id'])
    authority=core.store.get_document('candidate_publications',value['run_id'])
    assert digest(saved)==authority['run_hash'] and authority['job_id']==job['job_id']
    assert not core.store.list_documents('recommendations')
    coverage=client.coverage()
    assert coverage['account_required'] is False and coverage['qlib_required'] is False and coverage['llm_required'] is False

def test_family_full_export_and_source_evidence_are_same_publication(product):
    client,core,_,_=product;value,_=publish(client);rid=value['run_id']
    family=[row.model_dump(mode='json') for row in client.family(rid,'ONE_TO_TWO')]
    assert len(family)==26
    assert family==[i for i in value['items'] if i['primary_family']=='ONE_TO_TWO']
    assert client.get_run(rid).model_dump(mode='json')==value
    rows=list(csv.DictReader(io.StringIO(client.export_csv(rid).lstrip('\ufeff'))))
    assert len(rows)==30 and sum(r['selected_display']=='True' for r in rows)==20
    assert client.item_detail(rid,'TEST000')['item']==next(i for i in value['items'] if i['instrument_id']=='TEST000')
    assert client.raw_instrument_evidence('TEST000',run_id=rid)['source_receipts']
    with pytest.raises(ValueError):client.family(rid,'UNKNOWN')

@pytest.mark.parametrize('untrusted',[{'cash':0},{'cash':100000},{'holdings':[]},{'include_llm':True},{'model_id':'forged'},{'decision_cutoff':'2000-01-01T16:10:00+08:00'},{'generated_at':'2000-01-01T16:10:00+08:00'},{'bars':[]},{'coverage_type':'PROSPECTIVE_CAPTURED'},{'input_id':'D:\\secret.env'},{'formula':'__import__("os")'},{'approved':True}])
def test_request_cannot_supply_accounts_backfill_trust_models_or_paths(product,untrusted):
    client,core,_,_=product
    with pytest.raises((ValueError, UnavailableError)):
        client.submit(CandidateRequest(**untrusted))
    assert not core.store.list_documents('candidate_runs')

def test_no_registered_real_input_has_no_automatic_fixture_or_virtual_account(tmp_path,monkeypatch):
    monkeypatch.delenv('STOCKFISH_API_KEY',raising=False)
    core=ResearchService(root=tmp_path/'state',store=SQLiteStore(tmp_path/'isolated.sqlite3'))
    try:
        client=core.candidates
        with pytest.raises(UnavailableError):client.submit(CandidateRequest())
        assert client.coverage()['status']=='UNAVAILABLE'
        assert client.list_runs()==[]
        assert not core.store.list_documents('candidate_inputs')
    finally:core.close()


def test_config_revision_restarts_without_rewriting_old_runs(product):
    client,core,_,tmp_path=product
    first,_=publish(client)
    old=client.current_config(); config=old.model_dump(mode='json')
    config['thresholds']['focus_score']=71
    with pytest.raises(ImmutableConflict):client.register_config(StrategyConfig.model_validate(config))
    config['strategy_id']='YAOGU_TEST_FIXTURE_CHANGED_VERSION'
    client.register_config(StrategyConfig.model_validate(config))
    assert client.current_config().config_hash!=old.config_hash
    assert client.get_run(first['run_id']).config_hash==old.config_hash
    core.close()
    reopened=ResearchService(root=tmp_path/'TEST_FIXTURE_state',store=SQLiteStore(tmp_path/'TEST_FIXTURE_product.sqlite3'),allow_test_fixture=True)
    try:
        assert reopened.candidates.current_config().strategy_id==config['strategy_id']
        assert reopened.candidates.get_run(first['run_id']).model_dump(mode='json')==first
    finally:reopened.close()

def test_partial_missing_theme_preserves_structural_previews_and_honest_status(product,market):
    client,core,old_id,_=product
    no_theme=market[0].model_copy(update={'themes':()})
    missing_id=core.candidates.register_input(no_theme,activate=False)
    value,_=publish(client,{'input_id':missing_id})
    assert value['status']=='PARTIAL' and len(value['items'])==30 and not value['display_ids']
    assert all(i['score'] is None and i['tier']=='DATA_PENDING' for i in value['items'])
    assert core.candidates.input()[0]==old_id

def test_candidate_cancellation_race_cannot_publish(product,monkeypatch):
    import stockfish_core.yaogu.engine as engine
    client,core,_,_=product
    started=threading.Event();release=threading.Event();original=engine.run
    def blocked(*args,**kwargs):
        started.set()
        assert release.wait(10),'test driver did not release controlled candidate race'
        return original(*args,**kwargs)
    monkeypatch.setattr(engine,'run',blocked)
    response=client.submit(CandidateRequest())
    job_id=response['job_id']
    try:
        assert started.wait(5)
        core.jobs.cancel(job_id)
    finally:release.set()
    assert wait_job(client,job_id)['state']=='CANCELLED'
    assert not core.store.list_documents('candidate_runs') and not core.store.list_documents('candidate_publications')

def test_failed_next_revision_does_not_remove_prior_publication(product,monkeypatch):
    import stockfish_core.yaogu.candidate_service as service_module
    client,core,_,_=product
    first,_=publish(client)
    def crash(*args,**kwargs):raise RuntimeError('TEST_FIXTURE controlled crash before publication')
    monkeypatch.setattr(service_module,'freeze_theses',crash)
    response=client.submit(CandidateRequest(parent_run_id=first['run_id']))
    assert wait_job(client,response['job_id'])['state']=='FAILED'
    assert client.get_run(first['run_id']).model_dump(mode='json')==first
    assert len(core.store.list_documents('candidate_runs'))==1

def test_auction_unavailable_saves_review_without_mutating_original(product):
    client,core,_,_=product
    first,_=publish(client)
    response=client.submit_auction(first['run_id'])
    job=wait_job(client,response['job_id'])
    assert job['state']=='SUCCEEDED',job
    reviews=client.auction_reviews(first['run_id'])
    assert reviews[-1]['status']=='UNAVAILABLE'
    assert all(e['conditions'][k]['state']=='NOT_EVALUATED' for e in reviews[-1]['evaluations'] for k in e['conditions'])
    assert client.get_run(first['run_id']).model_dump(mode='json')==first

def test_same_explicit_capture_cutoff_has_stable_semantic_hash_across_jobs(product,market):
    from stockfish_core.yaogu.candidate_service import CandidateRequest
    client,core,_,_=product
    results=[]
    for _ in range(2):
        submitted=core.candidates.submit(CandidateRequest(),cutoff=market[1])
        job=wait_job(client,submitted['job_id'])
        assert job['state']=='SUCCEEDED',job
        results.append(core.candidates.get_run(job['result']['candidate_run_id']))
    assert results[0].run_id!=results[1].run_id and results[0].generated_at!=results[1].generated_at
    assert results[0].semantic_hash==results[1].semantic_hash
    assert [i.daily_score for i in results[0].items]==[i.daily_score for i in results[1].items]

class ControlledFixtureClock:
    def __init__(self,current):self.current=current
    def __call__(self):return self.current

def captured_at(bundle,stamp):
    clocks={'first_seen_at':stamp,'ingested_at':stamp}
    universe=bundle.universe.model_copy(update={**clocks,'securities':tuple(s.model_copy(update=clocks) for s in bundle.universe.securities)})
    return bundle.model_copy(update={'universe':universe,'bars':tuple(b.model_copy(update=clocks) for b in bundle.bars),'themes':tuple(t.model_copy(update=clocks) for t in bundle.themes),'receipts':tuple(r.model_copy(update=clocks) for r in bundle.receipts)})


def stage_aware_primary_doc(run):
    from stockfish_core.yaogu.validation_identity import current_validation_identity
    return {**current_validation_identity(),'run_id':run.run_id,'signal_observation_date':str(run.observation_date),
        'decision_cutoff':run.decision_cutoff.isoformat(),'official_primary_signal_countable':True}







@pytest.mark.parametrize('missing',['themes','bars'])
def test_admin_cannot_activate_input_unusable_for_daily_consumer(product,market,missing):
    from stockfish_core.common import UnavailableError
    client,core,old_id,_=product
    bundle=market[0].model_copy(update={missing:()})
    try:core.candidates.register_input(bundle,activate=True)
    except UnavailableError:pass
    assert core.candidates.input()[0]==old_id

def test_outcome_limit_event_rejects_impossible_ohlc_order(market):
    from decimal import Decimal
    from stockfish_core.yaogu.outcomes import _limit
    bar=market[0].bars[0]
    malformed=bar.model_copy(update={'close':bar.limit_up,'high':bar.limit_up-Decimal('.01')})
    assert _limit(malformed) is None,'Outcome labeled an impossible high < close bar as close limit up'

def test_outcome_security_cannot_bypass_future_universe_envelope(market):
    from stockfish_core.yaogu.outcomes import _states
    bundle,cutoff=market
    future=bundle.universe.model_copy(update={'first_seen_at':cutoff+timedelta(hours=1),'ingested_at':cutoff+timedelta(hours=1)})
    assert _states(bundle.model_copy(update={'universe':future}),'TEST000',bundle.observation_date,cutoff) is None
