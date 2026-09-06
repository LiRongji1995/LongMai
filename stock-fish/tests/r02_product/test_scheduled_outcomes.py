"""Independent after-close observation jobs: explicit TEST_FIXTURE input revisions."""
from datetime import datetime,time,timedelta
from decimal import Decimal,ROUND_HALF_UP
import pytest

from stockfish_core.api import create_app
from stockfish_core.observed_data import exchange_calendar_2026
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateRequest
from stockfish_core.yaogu.scheduler import CandidateScheduler
from tests.yaogu.fixtures import make_bundle,TZ
from tests.r02_product.test_candidate_product_path import ControlledFixtureClock,ForbiddenLegacyQuant,wait_job

@pytest.fixture
def observer(tmp_path,monkeypatch):
    import stockfish_core.yaogu.candidate_service as candidate_module
    monkeypatch.delenv('STOCKFISH_API_KEY',raising=False)
    bundle,cutoff=make_bundle(6)
    next_dates=tuple(d for d in exchange_calendar_2026().sessions if d>bundle.observation_date)[:10]
    bundle=bundle.model_copy(update={'sessions':(*bundle.sessions,*next_dates)})
    clock=ControlledFixtureClock(cutoff)
    monkeypatch.setattr(candidate_module,'utc_now',clock)
    core=ApplicationService(root=tmp_path/'TEST_FIXTURE_observer_state',store=SQLiteStore(tmp_path/'TEST_FIXTURE_observer.sqlite3'),quant=ForbiddenLegacyQuant(),allow_test_fixture=True)
    original_input=core.candidates.register_input(bundle,activate=True)
    client=create_app(core).test_client()
    job=core.candidates.submit(CandidateRequest(input_id=original_input))
    done=wait_job(client,job['job_id'])
    assert done['state']=='SUCCEEDED',done
    saved=core.candidates.get_run(done['result']['candidate_run_id'])
    yield client,core,bundle,clock,saved,tmp_path
    core.close()

def future_input(bundle,session,*,missing=False,revision=0):
    bars=list(bundle.bars)
    if not missing:
        for prior in (b for b in bundle.bars if b.session==bundle.observation_date):
            base=prior.close
            close=(base*(Decimal('1.01')+Decimal('.001')*revision)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            stamp=datetime.combine(session,time(15),TZ)+timedelta(seconds=revision)
            bars.append(prior.model_copy(update={'session':session,'open':base,'low':base,'high':close,'close':close,
                'reference_prev_close':base,'limit_up':(base*Decimal('1.1')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),
                'limit_down':(base*Decimal('.9')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),
                'comparable_open':base,'comparable_low':base,'comparable_high':close,'comparable_close':close,
                'event_time':stamp,'source_published_at':stamp,'first_seen_at':stamp,'ingested_at':stamp}))
    return bundle.model_copy(update={'observation_date':session,'bars':tuple(bars)})

def next_session(bundle):return min(d for d in bundle.sessions if d>bundle.observation_date)

def test_tick_appends_observations_after_successful_current_capture_without_rewriting_run(observer):
    client,core,bundle,clock,saved,_=observer
    next_day=next_session(bundle)
    clock.current=datetime.combine(next_day,time(16),TZ)
    captured=future_input(bundle,next_day)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *args:captured,clock=clock)
    clock.current=clock.current.replace(minute=10)
    first_events=scheduler.tick()
    capture=next(e for e in first_events if e['market_session']=='PRELIMINARY')
    done=wait_job(client,capture['job_id'])
    assert done['state']=='SUCCEEDED',done
    capture_run=done['result']['candidate_run_id']
    # A fast capture can finish before the first tick reaches its independent
    # observer stage. Retain both batches rather than requiring a resubmission.
    events=first_events+scheduler.tick()
    submissions=[e for e in events if e['market_session']=='OUTCOME_OBSERVATION' and e['status']=='SUBMITTED']
    assert len(submissions)==1
    observation=submissions[0]
    assert observation['run_id']==saved.run_id and observation['coverage']['current_close_present_count']==5
    job=wait_job(client,observation['job_id'])
    assert job['state']=='SUCCEEDED',job
    outcomes=core.candidates.outcomes(saved.run_id)
    assert len(outcomes)==25
    first=[o for o in outcomes if o['horizon']==1]
    assert len(first)==5 and all(o['status']=='MATURE' and o['metrics']['close_reference_change'] is not None for o in first)
    assert all(o['status']=='NOT_MATURED' and o['metrics']['close_reference_change'] is None for o in outcomes if o['horizon']>1)
    assert core.candidates.get_run(saved.run_id)==saved
    assert core.candidates.get_run(capture_run).observation_date==next_day
    assert scheduler.tick()==[]

def test_current_active_input_is_observed_once_and_restart_does_not_duplicate(observer):
    client,core,bundle,clock,saved,tmp_path=observer
    day=next_session(bundle);clock.current=datetime.combine(day,time(20,40),TZ)
    current=core.candidates.register_input(future_input(bundle,day),activate=True)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('observer may not capture'),clock=clock)
    events=scheduler.schedule_observations()
    assert len(events)==1 and events[0]['input_id']==current
    assert wait_job(client,events[0]['job_id'])['state']=='SUCCEEDED'
    reloaded=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('observer may not capture'),clock=clock)
    assert reloaded.schedule_observations()==[]
    assert len(core.candidates.outcomes(saved.run_id))==25
    core.close()
    reopened=ApplicationService(root=tmp_path/'TEST_FIXTURE_observer_state',store=SQLiteStore(tmp_path/'TEST_FIXTURE_observer.sqlite3'),quant=ForbiddenLegacyQuant(),allow_test_fixture=True)
    try:
        after_restart=CandidateScheduler(reopened.candidates,capture=lambda *a:pytest.fail('restart may not capture'),clock=clock)
        assert after_restart.schedule_observations()==[]
        assert len(reopened.candidates.outcomes(saved.run_id))==25
    finally:reopened.close()

def test_new_registered_input_revision_appends_distinct_evaluation(observer):
    client,core,bundle,clock,saved,_=observer
    day=next_session(bundle);clock.current=datetime.combine(day,time(20,40),TZ)
    first_id=core.candidates.register_input(future_input(bundle,day),activate=True)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('no capture'),clock=clock)
    first=scheduler.schedule_observations()[0]
    assert wait_job(client,first['job_id'])['state']=='SUCCEEDED'
    clock.current+=timedelta(seconds=10)
    second_id=core.candidates.register_input(future_input(bundle,day,revision=1),activate=True)
    second=scheduler.schedule_observations()[0]
    assert first_id!=second_id and first['job_id']!=second['job_id']
    assert wait_job(client,second['job_id'])['state']=='SUCCEEDED'
    assert len(core.candidates.outcomes(saved.run_id))==50
    assert len({o['input_hash'] for o in core.candidates.outcomes(saved.run_id)})==2
    assert scheduler.schedule_observations()==[]

def test_diagnostic_capture_missing_old_candidates_saves_nulls_and_preserves_active(observer):
    client,core,bundle,clock,saved,_=observer
    old_active=core.candidates.input()[0]
    day=next_session(bundle);clock.current=datetime.combine(day,time(16),TZ)
    missing=future_input(bundle,day,missing=True)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:missing,clock=clock)
    clock.current=clock.current.replace(minute=10)
    first_events=scheduler.tick()
    cap=next(e for e in first_events if e['market_session']=='PRELIMINARY')
    done=wait_job(client,cap['job_id'])
    assert done['state']=='SUCCEEDED' and done['result']['activated'] is False
    assert core.candidates.input()[0]==old_active
    # A diagnostic capture may complete before the same tick schedules the
    # observer. Keep its first event batch; a second submission would be wrong.
    events=first_events+scheduler.tick()
    submissions=[e for e in events if e['market_session']=='OUTCOME_OBSERVATION' and e['status']=='SUBMITTED']
    assert len(submissions)==1
    obs=submissions[0]
    assert obs['coverage']['current_close_present_count']==0 and len(obs['coverage']['current_close_missing_ids'])==5
    assert wait_job(client,obs['job_id'])['state']=='SUCCEEDED'
    rows=core.candidates.outcomes(saved.run_id)
    assert len(rows)==25
    assert all(o['metrics']['close_reference_change'] is None for o in rows)
    assert all(o['structure_success'] is None for o in rows)
    assert all(o['status'] in ('UNAVAILABLE','CENSORED') for o in rows if o['horizon']==1)
    assert all(o['status']=='NOT_MATURED' for o in rows if o['horizon']>1)
    assert core.candidates.get_run(saved.run_id)==saved
    assert scheduler.tick()==[]
    assert len(core.candidates.outcomes(saved.run_id))==25
    assert core.candidates.input()[0]==old_active

def test_no_current_input_is_explicit_waiting_not_synthetic_observation(observer):
    client,core,bundle,clock,saved,_=observer
    day=next_session(bundle);clock.current=datetime.combine(day,time(20,40),TZ)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('no backfill capture'),clock=clock)
    events=scheduler.schedule_observations()
    assert len(events)==1 and events[0]['status']=='NO_CURRENT_SESSION_REGISTERED_INPUT'
    assert events[0]['outcome_rows_created'] is False and scheduler.schedule_observations()==[]
    assert not core.candidates.outcomes(saved.run_id)

def test_observer_does_not_mark_future_session_as_observed_before_close(observer):
    client,core,bundle,clock,saved,_=observer
    day=next_session(bundle);clock.current=datetime.combine(day,time(9,26),TZ)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('no capture'),clock=clock)
    assert scheduler.schedule_observations()==[] and not core.candidates.outcomes(saved.run_id)

def test_r03_unresolved_published_outcomes_do_not_expire_after_twenty_sessions(observer):
    client,core,bundle,clock,saved,_=observer
    sessions=tuple(sorted(exchange_calendar_2026().sessions))
    index=sessions.index(saved.observation_date)
    clock.current=datetime.combine(sessions[index+21],time(20,40),TZ)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('no capture'),clock=clock)
    # R03 explicitly retains missing/losing outcomes until independently
    # observed or censored. The 20-session forward plan is not an expiry rule.
    pending=scheduler.schedule_observations()
    assert len(pending)==1 and pending[0]['status']=='NO_CURRENT_SESSION_REGISTERED_INPUT'
    assert not core.candidates.outcomes(saved.run_id)

def test_observation_plan_does_not_invent_2027_exchange_calendar(observer):
    client,core,bundle,clock,saved,_=observer
    clock.current=datetime(2026,12,30,20,40,tzinfo=TZ)
    scheduler=CandidateScheduler(core.candidates,capture=lambda *a:pytest.fail('no capture'),clock=clock)
    plan=scheduler.plan()
    assert plan['status']=='CALENDAR_COVERAGE_PARTIAL' and plan['requested_sessions']==20
    assert plan['sessions']==['2026-12-31']
    assert plan['outcome_schedule']['deduplication']==['run_id','evaluation_exchange_session','input_id']
    assert plan['observations']=='FUTURE_NOT_OBSERVED'
