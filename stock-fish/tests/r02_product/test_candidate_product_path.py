"""Actual Flask -> durable job -> publication integration, isolated TEST_FIXTURE only."""
from datetime import timedelta
from pathlib import Path
import csv
import io
import threading
import time
import pytest

from stockfish_core.api import create_app
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.contracts import digest
from tests.yaogu.fixtures import make_bundle

class ForbiddenLegacyQuant:
    def __getattr__(self, name):
        raise AssertionError('Rule candidate main path attempted legacy quant: '+name)

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
    core=ApplicationService(root=tmp_path/'TEST_FIXTURE_state',store=store,quant=ForbiddenLegacyQuant(),allow_test_fixture=True,data_dir=tmp_path/'NO_QLIB_DATA')
    core.research=ForbiddenLegacyQuant()
    input_id=core.candidates.register_input(market[0],activate=True)
    client=create_app(core).test_client()
    yield client,core,input_id,tmp_path
    core.close()

def wait_job(client,job_id):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        response=client.get('/api/jobs/'+job_id)
        assert response.status_code==200,response.text
        job=response.json
        if job['state'] in ('SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'):return job
        time.sleep(.005)
    raise AssertionError('bounded candidate fixture job did not finish')

def publish(client,payload=None):
    started=client.post('/api/candidate-runs',json=payload or {})
    assert started.status_code==202,started.text
    job=wait_job(client,started.json['job_id'])
    assert job['state']=='SUCCEEDED',job
    value=client.get('/api/candidate-runs/'+job['result']['candidate_run_id'])
    assert value.status_code==200,value.text
    return value.json,job

def test_default_candidate_api_persists_thirty_twenty_without_account_model_llm(product):
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
    coverage=client.get('/api/universe/coverage').json
    assert coverage['account_required'] is False and coverage['qlib_required'] is False and coverage['llm_required'] is False

def test_family_full_export_and_source_evidence_are_same_publication(product):
    client,core,_,_=product;value,_=publish(client);rid=value['run_id']
    family=client.get('/api/candidate-runs/'+rid+'/family/ONE_TO_TWO')
    assert family.status_code==200 and len(family.json)==26
    assert family.json==[i for i in value['items'] if i['primary_family']=='ONE_TO_TWO']
    exported=client.get('/api/candidate-runs/'+rid+'/export?format=json')
    assert exported.json==value
    csv_response=client.get('/api/candidate-runs/'+rid+'/export?format=csv')
    rows=list(csv.DictReader(io.StringIO(csv_response.text.lstrip('\ufeff'))))
    assert len(rows)==30 and sum(r['selected_display']=='True' for r in rows)==20
    assert client.get('/api/instruments/TEST000/evidence?run_id='+rid).json[0]['candidate']==next(i for i in value['items'] if i['instrument_id']=='TEST000')
    assert client.get('/api/themes/TEST_THEME/snapshot').json[0]['theme_id']=='TEST_THEME'
    assert client.get('/api/candidate-runs/'+rid+'/family/UNKNOWN').status_code==400

@pytest.mark.parametrize('untrusted',[{'cash':0},{'cash':100000},{'holdings':[]},{'include_llm':True},{'model_id':'forged'},{'decision_cutoff':'2000-01-01T16:10:00+08:00'},{'generated_at':'2000-01-01T16:10:00+08:00'},{'bars':[]},{'coverage_type':'PROSPECTIVE_CAPTURED'},{'input_id':'D:\\secret.env'},{'formula':'__import__("os")'},{'approved':True}])
def test_http_cannot_supply_accounts_backfill_trust_models_or_paths(product,untrusted):
    client,core,_,_=product
    result=client.post('/api/candidate-runs',json=untrusted)
    assert result.status_code==400 and result.is_json
    assert not core.store.list_documents('candidate_runs')

def test_no_registered_real_input_has_no_automatic_fixture_or_virtual_account(tmp_path,monkeypatch):
    monkeypatch.delenv('STOCKFISH_API_KEY',raising=False)
    core=ApplicationService(root=tmp_path/'state',store=SQLiteStore(tmp_path/'isolated.sqlite3'),quant=ForbiddenLegacyQuant())
    try:
        client=create_app(core).test_client()
        result=client.post('/api/candidate-runs',json={})
        assert result.status_code in (409,503),result.text
        assert client.get('/api/universe/coverage').json['status']=='UNAVAILABLE'
        assert client.get('/api/candidate-runs').json==[]
        assert not core.store.list_documents('candidate_inputs')
    finally:core.close()

def test_default_page_uses_candidate_endpoints_and_keeps_portfolio_separate(product):
    client,_,_,_=product
    html=client.get('/').text
    js=client.get('/core.js').text
    assert '/core.js' in html and '/candidate.css' in html
    assert '主板候选研究' in html and '不是涨停概率或胜率' in html
    assert '/api/candidate-runs' in js and '/api/strategy-configs/current' in js
    assert '/api/recommendations' not in js and '/api/analyze' not in js and '/api/qlib' not in js
    assert 'id="cash"' not in html and 'id="holdings"' not in html and 'id="availableCash"' not in html
    assert '/portfolio.html' in html and client.get('/portfolio.html').status_code==200

def test_config_revision_restarts_without_rewriting_old_runs(product):
    client,core,_,tmp_path=product
    first,_=publish(client)
    old=client.get('/api/strategy-configs/current').json
    config=old['config'];config['thresholds']['focus_score']=71
    rejected=client.post('/api/strategy-configs',json=config)
    assert rejected.status_code==409
    config['strategy_id']='YAOGU_TEST_FIXTURE_CHANGED_VERSION'
    created=client.post('/api/strategy-configs',json=config)
    assert created.status_code==201,created.text
    assert client.get('/api/strategy-configs/current').json['config_hash']!=old['config_hash']
    assert core.candidates.get_run(first['run_id']).config_hash==old['config_hash']
    core.close()
    reopened=ApplicationService(root=tmp_path/'TEST_FIXTURE_state',store=SQLiteStore(tmp_path/'TEST_FIXTURE_product.sqlite3'),quant=ForbiddenLegacyQuant(),allow_test_fixture=True)
    try:
        fresh=create_app(reopened).test_client()
        assert fresh.get('/api/strategy-configs/current').json['config']['strategy_id']==config['strategy_id']
        assert fresh.get('/api/candidate-runs/'+first['run_id']).json==first
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
    response=client.post('/api/candidate-runs',json={})
    assert response.status_code==202
    job_id=response.json['job_id']
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
    response=client.post('/api/candidate-runs',json={'parent_run_id':first['run_id']})
    assert response.status_code==202
    assert wait_job(client,response.json['job_id'])['state']=='FAILED'
    assert client.get('/api/candidate-runs/'+first['run_id']).json==first
    assert len(core.store.list_documents('candidate_runs'))==1

def test_auction_unavailable_saves_review_without_mutating_original(product):
    client,core,_,_=product
    first,_=publish(client)
    response=client.post('/api/candidate-runs/'+first['run_id']+'/auction-review',json={})
    assert response.status_code==202
    job=wait_job(client,response.json['job_id'])
    assert job['state']=='SUCCEEDED',job
    reviews=client.get('/api/candidate-runs/'+first['run_id']+'/auction-review').json
    assert reviews[-1]['status']=='UNAVAILABLE'
    assert all(e['conditions'][k]['state']=='NOT_EVALUATED' for e in reviews[-1]['evaluations'] for k in e['conditions'])
    assert client.get('/api/candidate-runs/'+first['run_id']).json==first

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

def test_scheduler_three_times_durable_dedup_and_real_late_cutoff(product,market,monkeypatch):
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    import stockfish_core.yaogu.candidate_service as service_module
    client,core,old_id,_=product
    clock=ControlledFixtureClock(market[1].replace(hour=9,minute=0))
    monkeypatch.setattr(service_module,'utc_now',clock)
    captures=[]
    def capture(path,requested):
        captures.append((path,requested))
        clock.current+=timedelta(seconds=7)
        return captured_at(market[0],clock())
    scheduler=CandidateScheduler(core.candidates,capture=capture,clock=clock)
    plan=scheduler.plan()
    assert len(plan['sessions'])==20 and plan['observations']=='FUTURE_NOT_OBSERVED'
    assert not core.store.list_documents('trading_session_observations')
    clock.current=clock.current.replace(hour=16,minute=10)
    events=scheduler.tick()
    preliminary=next(e for e in events if e['market_session']=='PRELIMINARY')
    first=wait_job(client,preliminary['job_id'])
    assert first['state']=='SUCCEEDED',first
    run1=core.candidates.get_run(first['result']['candidate_run_id'])
    assert run1.decision_cutoff==clock() and run1.decision_cutoff.second==7
    assert run1.market_session=='PRELIMINARY'
    assert core.candidates.input()[0]!=old_id
    assert scheduler.tick()==[]
    clock.current=clock.current.replace(hour=20,minute=30,second=0)
    evening=next(e for e in scheduler.tick() if e['market_session']=='EVENING')
    second=wait_job(client,evening['job_id'])
    assert second['state']=='SUCCEEDED',second
    run2=core.candidates.get_run(second['result']['candidate_run_id'])
    assert run2.revision==2 and run2.parent_run_id==run1.run_id and run2.market_session=='EVENING'
    assert run2.decision_cutoff.second==7 and len(captures)==2
    next_day=min(d for d in scheduler.sessions if d>market[0].observation_date)
    clock.current=clock.current.replace(year=next_day.year,month=next_day.month,day=next_day.day,hour=9,minute=26,second=0)
    auction=next(e for e in scheduler.tick() if e['market_session']=='AUCTION_REVIEW')
    assert wait_job(client,auction['job_id'])['state']=='SUCCEEDED'
    assert core.candidates.auction_reviews(run2.run_id)[-1]['status']=='UNAVAILABLE'
    reloaded=CandidateScheduler(core.candidates,capture=capture,clock=clock)
    assert reloaded.tick()==[] and len(captures)==2
    assert len(core.candidates._all('candidate_observation_plans'))==1

def test_scheduler_offline_missed_time_never_backfills_snapshot(product,market):
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    client,core,old_id,_=product
    clock=ControlledFixtureClock(market[1].replace(hour=21,minute=0))
    calls=[]
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:calls.append(a),clock=clock)
    events=scheduler.tick()
    assert len(events)==3 and all(e['status']=='MISSED_SERVICE_OFFLINE' and not e['snapshot_created'] for e in events)
    assert not calls and core.candidates.input()[0]==old_id
    assert scheduler.tick()==[]
    saturday=clock.current+timedelta(days=1)
    assert scheduler.tick(saturday)==[]

def test_scheduler_invalid_new_capture_cannot_replace_previous_active(product,market,monkeypatch):
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    import stockfish_core.yaogu.candidate_service as service_module
    client,core,old_id,_=product
    clock=ControlledFixtureClock(market[1].replace(hour=16,minute=0))
    monkeypatch.setattr(service_module,'utc_now',clock)
    invalid=market[0].model_copy(update={'bars':tuple(b.model_copy(update={'open':None,'high':None,'low':None,'close':None,'quality':'INVALID'}) for b in market[0].bars)})
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:invalid,clock=clock)
    clock.current=clock.current.replace(minute=10)
    event=next(e for e in scheduler.tick() if e['market_session']=='PRELIMINARY')
    completed=wait_job(client,event['job_id'])
    assert completed['state'] in ('FAILED','SUCCEEDED')
    assert core.candidates.input()[0]==old_id,'INVALID capture replaced prior usable active input'

def test_scheduler_cancel_capture_leaves_active_and_publication_unchanged(product,market,monkeypatch):
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    import stockfish_core.yaogu.candidate_service as service_module
    client,core,old_id,_=product
    clock=ControlledFixtureClock(market[1].replace(hour=16,minute=0))
    monkeypatch.setattr(service_module,'utc_now',clock)
    started=threading.Event();release=threading.Event()
    def capture(*args):
        started.set()
        assert release.wait(10)
        return captured_at(market[0],clock())
    scheduler=CandidateScheduler(core.candidates,capture=capture,clock=clock)
    clock.current=clock.current.replace(minute=10)
    event=next(e for e in scheduler.tick() if e['market_session']=='PRELIMINARY')
    try:
        assert started.wait(5)
        core.jobs.cancel(event['job_id'])
    finally:release.set()
    assert wait_job(client,event['job_id'])['state']=='CANCELLED'
    assert core.candidates.input()[0]==old_id
    assert not core.store.list_documents('candidate_runs')

def test_scheduler_start_stop_joins_thread_and_offline_does_not_capture(product,market):
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    client,core,_,_=product
    clock=ControlledFixtureClock(market[1]+timedelta(days=1)) # Saturday
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('weekend capture'),clock=clock,interval=.01)
    scheduler.start();scheduler.stop()
    assert scheduler.thread is not None and not scheduler.thread.is_alive()

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
