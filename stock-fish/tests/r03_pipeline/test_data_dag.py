"""TEST_FIXTURE provider rows, real DAG + rule engine; no fixture in user DB."""
from copy import deepcopy
from datetime import datetime
import json
from types import SimpleNamespace

from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.data_pipeline import MainboardPipeline, master_ids, published_outcome_demand
from stockfish_core.yaogu.request_queue import RequestBudget
from tests.yaogu.fixtures import make_bundle


def fixtures():
    b,c=make_bundle(12)
    mapping={s.instrument_id:f'{600000+i:06d}.SH' for i,s in enumerate(b.universe.securities)}
    def convert(v):
        if isinstance(v,dict):return {k:convert(x) for k,x in v.items()}
        if isinstance(v,list):return [convert(x) for x in v]
        if isinstance(v,str):return mapping.get(v,v)
        return v
    return MarketInput.model_validate(convert(b.model_dump(mode='json'))),c


def source(bundle, calls, reverse=False, fail=None):
    def executor(endpoint, params, root, timeout):
        calls.append((endpoint,deepcopy(params)))
        rid=endpoint+str(params)
        rows=[]
        if endpoint=='mainboard_sse':
            rows=[{'证券代码':s.instrument_id[:6]} for s in bundle.universe.securities]
            if reverse:rows.reverse()
        elif endpoint=='calendar':rows=[{'calendar_date':str(s),'is_trading_day':'1'} for s in bundle.sessions]
        elif endpoint=='concept_list':rows=[{'theme_id':t.theme_id} for t in bundle.themes]
        elif endpoint=='concept_members':rows=[{'instrument_id':iid} for t in bundle.themes if t.theme_id==params['symbol'] for iid in t.member_ids]
        elif endpoint in ('history','history_adjusted'):
            rows=[b.model_dump(mode='json') for b in bundle.bars if b.instrument_id in params['codes'] and params['start_date']<=str(b.session)<=params['end_date']]
        elif endpoint=='market_daily':
            rows=[b.model_dump(mode='json') for b in bundle.bars if str(b.session)==params['date']]
        status='PARTIAL' if endpoint==fail else 'COMPLETE'
        return dict(status=status,endpoint=endpoint,parameters=params,rows=rows,request_receipt_id=rid,
                    pagination_complete=endpoint!=fail,receipts=[{'receipt_id':rid,'status':status,'pagination_complete':endpoint!=fail}])
    return executor


def normalizer(base):
    def normalize(results,**kwargs):
        rows={}
        for result in results:
            if result.get('endpoint') in ('history','market_daily'):
                for row in result['rows']:rows[(row['instrument_id'],row['session'])]=row
        value=base.model_dump(mode='json')
        value['bars']=list(rows.values())
        return MarketInput.model_validate(value),[]
    return normalize


def test_full_directory_discovers_non_limitup_families_not_max6(tmp_path):
    b,c=fixtures();calls=[]
    p=MainboardPipeline(tmp_path/'cache',executor=source(b,calls),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    actual=p.capture(tmp_path/'capture',c,observation_date=b.observation_date)
    from stockfish_core.yaogu.engine import run
    result=run(actual,cutoff=c)
    assert {'TREND_NM','REBOUND_SETUP'} <= {i.primary_family for i in result.items}
    assert len(p.last_report['master_ids'])==12
    requested={iid for ep,params in calls if ep=='adjustment_factors' for iid in params['codes']}
    assert requested=={s.instrument_id for s in b.universe.securities}
    assert len(p.last_report['structure_candidate_ids'])>6


def test_v2_full_universe_long_history_demand_independent_of_daily_candidates(tmp_path):
    b,c=fixtures();calls=[]
    p=MainboardPipeline(tmp_path/'cache',executor=source(b,calls),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    p.capture(tmp_path/'capture',c,observation_date=b.observation_date)

    requested_market_days = {params['date'] for ep, params in calls if ep == 'market_daily'}
    assert requested_market_days == {str(day) for day in b.sessions}
    assert p.last_report['v2_long_history']['universe_policy'] == 'FULL_CURRENT_MAINBOARD_DIRECTORY_BEFORE_DAILY_PREVIEW_OR_STAGE_SCORING'
    assert p.last_report['v2_long_history']['requested_sessions'] == len(b.sessions)
    assert p.last_report['v2_long_history']['requested_sessions'] > 22


def test_v2_ma250_capture_resumes_market_daily_from_cache(tmp_path):
    b,c=fixtures();calls=[]
    root = tmp_path/'shared'
    p=MainboardPipeline(root/'cache',executor=source(b,calls),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    p.capture(root/'first',c,observation_date=b.observation_date)
    first_market_calls = sum(ep == 'market_daily' for ep, _ in calls)

    p=MainboardPipeline(root/'cache',executor=source(b,calls),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    p.capture(root/'second',c,observation_date=b.observation_date)

    assert first_market_calls == len(b.sessions)
    assert sum(ep == 'market_daily' for ep, _ in calls) == first_market_calls
    assert p.last_report['queue']['cache_hits_this_run'] >= len(b.sessions)


def test_reversing_directory_order_does_not_change_discovery_scope(tmp_path):
    b,c=fixtures();reports=[]
    for reverse in (False,True):
        root=tmp_path/str(reverse)
        p=MainboardPipeline(root/'cache',executor=source(b,[],reverse),normalizer=normalizer(b),
            budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
        p.capture(root/'capture',c,observation_date=b.observation_date)
        reports.append(p.last_report)
    assert reports[0]['master_ids']==reports[1]['master_ids']
    assert reports[0]['structure_candidate_ids']==reports[1]['structure_candidate_ids']


def test_budget_keeps_all_pool_requests_pending_and_resume_uses_cache(tmp_path):
    b,c=fixtures();calls=[]
    p=MainboardPipeline(tmp_path/'cache',executor=source(b,calls),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=3,max_seconds=60,minimum_interval_seconds=0))
    p.capture(tmp_path/'first',c,observation_date=b.observation_date)
    assert p.last_report['queue']['budget_exhausted']
    assert len(p.last_report['master_ids'])==12
    assert p.last_report['queue']['states']['PENDING']>0
    pending_codes={iid for r in p.last_report['remaining_requests']
                   for iid in json.loads(r['payload'])['parameters'].get('codes', ())}
    assert pending_codes >= {s.instrument_id for s in b.universe.securities}
    assert not p.last_report['actual_daily_ready']
    p=MainboardPipeline(tmp_path/'cache',executor=source(b,calls),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    p.capture(tmp_path/'second',c,observation_date=b.observation_date)
    assert p.last_report['queue']['cache_hits_this_run']>=3
    assert sum(ep=='mainboard_sse' for ep,_ in calls)==1


def test_known_other_board_is_not_master_scope(tmp_path):
    results=[{'endpoint':'master_szse','rows':[{'A股代码':'000001','板块':'主板'},{'A股代码':'300001','板块':'创业板'},{'A股代码':'000002'}]}]
    assert master_ids(results)==['000001.SZ']


def test_published_loser_is_requested_even_absent_from_current_pool(tmp_path):
    b,c=fixtures()
    saved=SimpleNamespace(run_id='published_loser',observation_date=b.sessions[-4],
                          items=[SimpleNamespace(instrument_id='000999.SZ')])
    service=SimpleNamespace(list_runs=lambda limit:[saved],outcomes=lambda rid:[],current_config=lambda:None)
    demand=published_outcome_demand(service,b.observation_date,b.sessions)
    assert demand[0]['instrument_id']=='000999.SZ' and demand[0]['horizons']==[1,2,3,5,10]
    calls=[]
    p=MainboardPipeline(tmp_path/'cache',service=service,executor=source(b,calls),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    p.capture(tmp_path/'capture',c,observation_date=b.observation_date)
    assert any(ep=='history' and '000999.SZ' in params['codes'] for ep,params in calls)


def test_mature_outcomes_stop_requests_missing_prices_do_not():
    b,c=fixtures();iid='000999.SZ'
    saved=SimpleNamespace(run_id='past',observation_date=b.sessions[-12],items=[SimpleNamespace(instrument_id=iid,primary_family='ONE_TO_TWO')])
    rows=[{'instrument_id':iid,'horizon':h,'status':'MATURE','label_end':str(b.sessions[-1]),
        'session_states':[{'status':'OBSERVED'}]*h,'family':'ONE_TO_TWO','structure_success':False,
        'future_three_session_limit_up':False if h>=3 else None,
        'metrics':{k:0 for k in ('close_reference_change','next_open_gap','next_open_reference_change',
            'mfe_bound','mae_bound','close_max_drawdown','any_close_limit_up','any_close_limit_down','one_price_up_count')}}
          for h in (1,2,3,5,10)]
    service=SimpleNamespace(list_runs=lambda limit:[saved],outcomes=lambda rid:rows)
    assert published_outcome_demand(service,b.observation_date,b.sessions)==[]
    rows[-1]['status']='UNAVAILABLE'
    assert published_outcome_demand(service,b.observation_date,b.sessions)[0]['horizons']==[10]


def test_long_capture_freezes_config_before_provider_calls(tmp_path):
    from stockfish_core.yaogu.config import StrategyConfig
    b,c=fixtures();first=StrategyConfig();later=first.model_copy(update={'strategy_id':'TEST_FIXTURE_NEXT_CONFIG'})
    active=[first]
    service=SimpleNamespace(list_runs=lambda limit:[],outcomes=lambda rid:[],current_config=lambda:active[0])
    delegate=source(b,[])
    def executor(*a,**kw):
        active[0]=later
        return delegate(*a,**kw)
    p=MainboardPipeline(tmp_path/'cache',service=service,executor=executor,normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    p.capture(tmp_path/'capture',c,observation_date=b.observation_date)
    assert p.last_report['frozen_config_hash']==first.config_hash
    assert active[0].config_hash!=first.config_hash


def test_future_theme_does_not_expand_peer_requests(tmp_path):
    from datetime import timedelta
    b,c=fixtures()
    valid=b.themes[0].model_copy(update={'member_ids':tuple(s.instrument_id for s in b.universe.securities[:6])})
    future_at=datetime.now(c.tzinfo)+timedelta(days=1)
    future=b.themes[0].model_copy(update={'theme_id':'future-theme','event_time':future_at,
        'source_published_at':future_at,'first_seen_at':future_at,'ingested_at':future_at})
    b=b.model_copy(update={'themes':(valid,future)})
    p=MainboardPipeline(tmp_path/'cache',executor=source(b,[]),normalizer=normalizer(b),
                        budget=RequestBudget(max_requests=1000,max_seconds=60,minimum_interval_seconds=0))
    p.capture(tmp_path/'capture',c,observation_date=b.observation_date)
    assert set(p.last_report['frozen_peer_ids'])<=set(valid.member_ids)
