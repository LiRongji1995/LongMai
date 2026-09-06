"""R05 synthetic engineering tests, no market-performance claims."""
from datetime import timedelta
from decimal import Decimal as D
import csv, io, time
import pytest
from tests.yaogu.fixtures import make_bundle
from stockfish_core.yaogu.strategy_tracking import evaluate_strategy_experiment_outcomes as evaluate, build_strategy_tracking as track, build_strategy_feedback_report as review
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.api import create_app


def test_item_detail_filters_unrelated_labels_before_deserialization(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from stockfish_core.yaogu import candidate_service as module
    store = SQLiteStore(tmp_path/'detail.sqlite3')
    with store.transaction() as connection:
        for i in range(503):
            value={'run_id':'WANTED' if i<3 else 'OTHER','instrument_id':'TEST000','observation_id':str(i)}
            store._put_document(connection,'strategy_trading_session_observations',str(i),value)
    seen=[]; original=module.checked_payload
    def checked(row):
        value=original(row); seen.append(value['run_id']); return value
    monkeypatch.setattr(module,'checked_payload',checked)
    rows=module.CandidateService._strategy_item_observations(SimpleNamespace(store=store),
        'strategy_trading_session_observations','WANTED','TEST000')
    assert {r['observation_id'] for r in rows}=={'0','1','2'}
    assert seen==['WANTED']*3


def test_item_detail_latest_hundred_preserves_chronological_order(tmp_path):
    from types import SimpleNamespace
    from stockfish_core.yaogu.candidate_service import CandidateService
    store=SQLiteStore(tmp_path/'detail.sqlite3')
    with store.transaction() as connection:
        for i in range(103):
            value={'run_id':'WANTED','instrument_id':'TEST000','observation_id':f'{i:03d}'}
            store._put_document(connection,'research_episode_observations',value['observation_id'],value)
    rows=CandidateService._strategy_item_observations(SimpleNamespace(store=store),
        'research_episode_observations','WANTED','TEST000',latest=100)
    assert [r['observation_id'] for r in rows]==[f'{i:03d}' for i in range(3,103)]


def signal(b,index=-3,rid='TEST_SIGNAL'):
    day=b.sessions[index]; bar=next(x for x in b.bars if x.instrument_id=='TEST000' and x.session==day)
    return {'run_id':rid,'input_id':'TEST_INPUT','observation_date':str(day),'implementation_version':'TEST_CONFORMANT',
      'results':{'PV_OBSERVATION_V1':{'strategy_id':'PV_OBSERVATION_V1','status':'COMPLETE','items':[{'instrument_id':'TEST000','rank':1,'S_PV':80,'comparable_close':str(bar.comparable_close),'comparable_basis':bar.comparable_basis}]}},
      'selection_results':{'PV_FOCUS_PREVIEW_V0.1':{'selected_ids':['TEST000']}}}


def test_missing_interim_only_blocks_path_metrics():
    b,t=make_bundle(1);r=signal(b); b=b.model_copy(update={'bars':tuple(x for x in b.bars if x.session!=b.sessions[-2])})
    row=evaluate(r,b,t,horizons=(2,))[0]
    assert row['metrics']['close_reference_change'] is not None
    assert row['metric_states']['close_reference_change']['status']=='MATURE_VALID'
    assert row['metrics']['mfe_bound'] is None and row['metrics']['next_open_gap'] is None


def test_exact_direction_and_conflicting_same_priority_bar():
    b,t=make_bundle(1);r=signal(b,-2);base=D(r['results']['PV_OBSERVATION_V1']['items'][0]['comparable_close'])
    b=b.model_copy(update={'bars':tuple(x.model_copy(update={'comparable_close':base*D('1.0000000001')}) if x.session==b.observation_date else x for x in b.bars)})
    row=evaluate(r,b,t,horizons=(1,))[0]
    assert row['price_direction']=='UP' and D(row['return_exact'])>0 and row['metrics']['close_reference_change']>0
    b=b.model_copy(update={'bars':(*b.bars,b.bars[-1].model_copy(update={'comparable_close':D('100')}))})
    assert evaluate(r,b,t,horizons=(1,))[0]['metrics']['close_reference_change'] is None


def test_actual_multistrategy_items_exclude_alias_and_fallback():
    b,t=make_bundle(1);r=signal(b);item=r['results']['PV_OBSERVATION_V1']['items'][0]
    r['results']['DNA_RERANK_V1']={'strategy_id':'DNA_RERANK_V1','items':[{**item,'status':'EVALUATED'}]}
    r['results']['DNA_REVERSAL_V1']={'canonical_strategy_id':'DNA_RERANK_V1','items':[{**item,'status':'EVALUATED'}]}
    r['results']['THEME_CONTEXT_V1']={'strategy_id':'THEME_CONTEXT_V1','status':'UNAVAILABLE','items':[{**item,'status':'UNAVAILABLE'}],'top_ids':['TEST000']}
    rows=evaluate(r,b,t,horizons=(1,))
    assert len(rows)==2 and {x['strategy_id'] for x in rows}=={'PV_OBSERVATION_V1','DNA_RERANK_V1'}


def test_repeat_archive_shadow_and_reentry():
    b,t=make_bundle(1);r=signal(b,60);a=track(r,b,t);eid=a['episodes'][0]['episode_id']
    z=track(signal(b,61,'NEXT'),b,t,existing_episodes=a['episodes'],prior_observations=a['observations'])
    assert z['episodes'][0]['episode_id']==eid and z['episodes'][0]['observation_date']==r['observation_date']
    event={'episode_id':eid,'effective_session':str(b.sessions[70]),'state':'ARCHIVED'}
    archived=track(r,b,t,existing_episodes=a['episodes'],episode_events=[event])
    assert {x['horizon'] for x in archived['shadow_outcomes']}=={5,10}
    assert all(x['observation_date']==str(b.sessions[70]) and x['metrics']['close_reference_change'] is not None for x in archived['shadow_outcomes'])
    new=track(signal(b,82,'NEW'),b,t,existing_episodes=a['episodes'],episode_events=[event])
    assert new['episodes'][0]['episode_id']!=eid and new['episodes'][0]['previous_episode_id']==eid


def test_unknown_volume_and_report_logical_revisions():
    b,t=make_bundle(1);r=signal(b);rows=evaluate(r,b,t,horizons=(1,2));r2=dict(r)
    revisions=evaluate(r2,b,t+timedelta(minutes=1),horizons=(1,2))
    empty=signal(b,-2,'EMPTY');empty['results']['PV_OBSERVATION_V1']['items']=[];empty['selection_results']={}
    # A later evaluation is a revision of the same run; a new run has its own label key.
    value=review(r,rows+revisions,[],t+timedelta(minutes=1),runs=[r,empty],sessions=b.sessions)
    assert value['counts']['mature']==2 and set(value['rolling_windows'])=={'5','20','60'}
    assert any(d['run_id']=='EMPTY' and d['focus_count']==0 for d in value['rolling_windows']['5']['date_diagnostics'])
    b=b.model_copy(update={'bars':tuple(x.model_copy(update={'comparable_basis':'PRICE_ONLY'}) for x in b.bars)})
    row=track(signal(b,-1),b,t)['observations'][0]
    assert row['volume_ratio_since_signal_peak'] is None and row['data_state']=='PARTIAL'


@pytest.fixture
def service(tmp_path,monkeypatch):
    import stockfish_core.yaogu.candidate_service as module
    b,t=make_bundle(1);monkeypatch.setattr(module,'utc_now',lambda:t)
    core=ApplicationService(root=tmp_path/'state',store=SQLiteStore(tmp_path/'test.sqlite3'),allow_test_fixture=True,data_dir=tmp_path/'no_model')
    core.candidates.register_input(b,activate=False)
    yield core,b,t
    core.close()


def save(s,r):
    s.store.put_document('strategy_experiment_runs',r['run_id'],r)
    with s.store.transaction() as connection:s._write_strategy_projection(connection,r)


def test_summary_bounded_reads_and_125_focus_export_match(service,monkeypatch):
    core,b,t=service;s=core.candidates;r=signal(b);template=r['results']['PV_OBSERVATION_V1']['items'][0]
    rows=[{**template,'instrument_id':f'TEST{i:03d}','rank':i+1} for i in range(125)]
    r['results']['PV_OBSERVATION_V1']['items']=rows;r['selection_results']['PV_FOCUS_PREVIEW_V0.1']['selected_ids']=[x['instrument_id'] for x in rows];save(s,r)
    original=core.store.get_document;reads=[]
    def spy(namespace,key):
        reads.append(namespace);assert namespace!='strategy_experiment_runs';return original(namespace,key)
    monkeypatch.setattr(core.store,'get_document',spy)
    first=s.strategy_experiment_summary(r['run_id'],page_size=1);cross=s.strategy_experiment_summary(r['run_id'],page=2,page_size=30,selection='focus');listed=s.list_strategy_experiments()
    assert first['filtered_total']==125 and len(first['items'])==1 and [x['rank'] for x in cross['items']]==list(range(31,61))
    assert 'items' not in listed[0] and len(reads)<10
    monkeypatch.setattr(core.store,'get_document',original)
    exported=list(csv.DictReader(io.StringIO(s.export_strategy_experiment_csv(r['run_id'],strategy_id='PV_OBSERVATION_V1',selection='focus'))))
    assert len(exported)==125 and exported[30]['instrument_id']==cross['items'][0]['instrument_id']


def test_more_than_100_old_runs_and_outside_pool_pending(service):
    core,b,t=service;s=core.candidates
    for i in range(105):
        r=signal(b,rid=f'TEST_RUN_{i:03d}');r['results']['PV_OBSERVATION_V1']['items'][0]['instrument_id']='OLD_LOSER_OUTSIDE_POOL';save(s,r)
    pending=s.pending_strategy_research();demand=s.experiment_outcome_demand(b.observation_date,b.sessions)
    assert len(pending)==105 and pending[0]['run_id']=='TEST_RUN_000'
    assert len(demand)==105 and all(r['instrument_id']=='OLD_LOSER_OUTSIDE_POOL' for r in demand)
    assert len(s._all('strategy_research_pending'))==105


def test_latest_outcome_input_and_http_view_consistency(service):
    core,b,t=service;s=core.candidates
    old=b.model_copy(update={'observation_date':b.sessions[-3],'bars':tuple(x for x in b.bars if x.session<=b.sessions[-3])})
    old_id=s.register_input(old,activate=False);r=signal(b);r['input_id']=old_id;save(s,r)
    job=s.submit_strategy_outcomes(r['run_id'],horizons=(1,61));deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        state=core.jobs.get(job['job_id'])
        if state['state'] in {'SUCCEEDED','FAILED'}:break
        time.sleep(.01)
    assert state['state']=='SUCCEEDED',state
    labels={row['horizon']:row for row in s.strategy_outcomes(r['run_id'])}
    assert all(row['signal_input_id']==old_id and row['input_id']!=old_id for row in labels.values())
    assert labels[1]['metrics']['close_reference_change'] is not None and labels[61]['status']=='NOT_MATURED'
    client=create_app(service=core).test_client();path='/api/strategy-experiments/'+r['run_id']
    summary=client.get(path+'/summary?selection=focus&page_size=1');exported=client.get(path+'/export?format=json&strategy_id=PV_OBSERVATION_V1&selection=focus')
    assert summary.status_code==200 and exported.status_code==200
    assert summary.json['items'][0]['instrument_id']==exported.json['results']['PV_OBSERVATION_V1']['items'][0]['instrument_id']
    assert client.post(path+'/outcomes',json={'horizons':[True]}).status_code==400


def test_scheduler_drains_beyond_100_and_failed_attempt_retries_with_backoff(service,monkeypatch):
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    core,b,t=service;s=core.candidates
    pending=[{'run_id':f'OLD_{i:03d}','observation_date':str(b.sessions[-3]),'horizons':[1,61]} for i in range(105)]
    monkeypatch.setattr(s,'pending_strategy_research',lambda:pending)
    jobs={};submitted=[]
    monkeypatch.setattr(core.jobs,'get',lambda job_id:jobs.get(job_id))
    def submit(run_id,input_id,*,horizons,job_id):
        assert horizons==[1,61]
        submitted.append((run_id,job_id));jobs[job_id]={'job_id':job_id,'state':'SUCCEEDED'};return jobs[job_id]
    monkeypatch.setattr(s,'submit_strategy_tracking',submit)
    scheduler=CandidateScheduler(s,clock=lambda:t)
    for _ in range(21):scheduler.schedule_strategy_research(t)
    assert len(submitted)==105 and {r for r,_ in submitted}=={p['run_id'] for p in pending}
    first=submitted[0][1];jobs[first]['state']='FAILED'
    assert not scheduler.schedule_strategy_research(t+timedelta(seconds=10))
    retried=scheduler.schedule_strategy_research(t+timedelta(seconds=61))
    assert len(retried)==1 and retried[0]['attempt']==1
    assert retried[0]['run_id']=='OLD_000' and retried[0]['job_id'].endswith('_retry_1')
    assert len(s._all('candidate_schedule_events'))==106


def test_legacy_projection_migration_is_append_only_and_assessed(service):
    core,b,t=service;s=core.candidates;r=signal(b)
    r['implementation_version']='R05_STRATEGY_EXPERIMENTS_V2'
    original=dict(r);s.store.put_document('strategy_experiment_runs',r['run_id'],r)
    assert s.rebuild_strategy_projections()['rebuilt']==1
    assert s.rebuild_strategy_projections()['rebuilt']==0
    summary=s.strategy_experiment_summary(r['run_id'],page_size=1)
    assert summary['implementation_assessment']['status']=='SPEC_NONCONFORMANT'
    assert s.strategy_experiment(r['run_id'])==original
    labels=evaluate(r,b,t,horizons=(1,))
    assert review(r,labels,[],t)['counts']['mature']==0


def test_cooling_checks_ret5_on_current_day_after_three_low_volume_days():
    b,t=make_bundle(1)
    prices={b.sessions[81]:D(12),b.sessions[82]:D(12),b.sessions[83]:D(9)}
    b=b.model_copy(update={'bars':tuple(x.model_copy(update={
        'comparable_close':prices.get(x.session,D(10)),
        'volume_shares':D(100000) if x.session in prices else D(1000000)}) for x in b.bars)})
    value=track(signal(b,80),b,t)
    rows={r['session']:r for r in value['observations']}
    assert rows[str(b.sessions[81])]['r5']>0 and rows[str(b.sessions[82])]['r5']>0
    assert rows[str(b.sessions[83])]['cooling_verified_streak']==3
    assert rows[str(b.sessions[83])]['state']=='COOLING'


def test_partial_ohlc_does_not_block_valid_endpoint_comparable_close():
    b,t=make_bundle(1);r=signal(b,-2)
    b=b.model_copy(update={'bars':tuple(x.model_copy(update={'quality':'PARTIAL','high':None,'comparable_high':None}) if x.session==b.observation_date else x for x in b.bars)})
    label=evaluate(r,b,t,horizons=(1,))[0]
    assert label['price_label_status']=='MATURE_VALID' and label['metrics']['close_reference_change'] is not None
    assert label['metrics']['mfe_bound'] is None


def test_normal_summary_and_review_never_read_full_run_or_full_report(service,monkeypatch):
    core,b,t=service;s=core.candidates;r=signal(b);save(s,r)
    labels=evaluate(r,b,t,horizons=(1,));report=review(r,labels,[],t)
    with s.store.transaction() as connection:s._write_research_review(connection,report)
    original=core.store.get_document;reads=[]
    def spy(namespace,key):
        reads.append(namespace)
        assert namespace not in {'strategy_experiment_runs','strategy_research_reviews'}
        return original(namespace,key)
    monkeypatch.setattr(core.store,'get_document',spy)
    client=create_app(core).test_client();base='/api/strategy-experiments/'+r['run_id']
    assert client.get(base+'/summary?page_size=1').status_code==200
    actual=client.get(base+'/review')
    assert actual.status_code==200 and actual.json['counts']['mature']==1
    assert 'revision_selection' not in actual.json and actual.json['full_report_available']


def test_non_pv_strategies_do_not_borrow_pv_episode_denominator():
    b,t=make_bundle(1);r=signal(b);item=r['results']['PV_OBSERVATION_V1']['items'][0]
    r['results']['DNA_RERANK_V1']={'strategy_id':'DNA_RERANK_V1','items':[{**item,'status':'EVALUATED'}]}
    r['evaluation_baselines']={'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC':{'strategy_id':'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC','items':[item]}}
    tracked=track(r,b,t);report=review(r,evaluate(r,b,t,horizons=(1,)),[tracked],t,sessions=b.sessions)
    stats={x['strategy_id']:x for x in report['rolling_windows']['5']['statistics']}
    assert stats['PV_OBSERVATION_V1']['episode_count']==1
    for sid in ('DNA_RERANK_V1','ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC'):
        assert stats[sid]['episode_count']==0 and stats[sid]['signals_without_episode']==1
        assert stats[sid]['episode_first_signal']['status']=='UNAVAILABLE'
