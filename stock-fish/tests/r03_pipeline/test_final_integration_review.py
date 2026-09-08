"""Independent final integration witnesses, using actual typed outcome rows."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.data_pipeline import published_outcome_demand
from stockfish_core.yaogu.outcomes import evaluate_outcomes
from stockfish_core.yaogu.scheduler import CandidateScheduler
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.storage import SQLiteStore
from tests.r02_product.test_independent_time_boundaries import tiny_fixture,SESSIONS,at


def service_for(run,rows):
    return SimpleNamespace(list_runs=lambda limit:[run],outcomes=lambda rid:[r.model_dump(mode='json') for r in rows])


def test_mature_price_with_unavailable_limit_events_keeps_structure_dependency_pending():
    run,bundle=tiny_fixture()
    bars=tuple(b.model_copy(update={'limit_verified':False,'limit_up':None,'limit_down':None})
               if b.session==SESSIONS[1] else b for b in bundle.bars)
    bundle=bundle.model_copy(update={'bars':bars})
    observations=evaluate_outcomes(run,bundle,at(SESSIONS[1],16),horizons=(1,))
    row=observations[0]
    assert row.status=='MATURE' and row.metrics['close_reference_change']==0
    assert row.structure_success is None and row.metrics['any_close_limit_up'] is None
    demand=published_outcome_demand(service_for(run,observations),SESSIONS[1],SESSIONS)
    assert 1 in demand[0]['horizons']
    assert 'structure_success' in demand[0]['pending_fields']['1']


def test_terminal_suspension_does_not_trigger_impossible_price_recovery():
    run,bundle=tiny_fixture()
    securities=tuple(s.model_copy(update={'suspended':True}) for s in bundle.universe.securities)
    bundle=bundle.model_copy(update={'universe':bundle.universe.model_copy(update={'securities':securities})})
    observations=evaluate_outcomes(run,bundle,at(SESSIONS[1],16),horizons=(1,))
    assert observations[0].reason_codes==('SUSPENDED',)
    demand=published_outcome_demand(service_for(run,observations),SESSIONS[1],SESSIONS)
    assert 1 not in demand[0]['horizons']


def test_terminal_censor_with_one_price_note_is_not_repeated_as_missing_data():
    run,bundle=tiny_fixture()
    old=bundle.universe.securities[0]
    first=old.model_copy(update={'suspended':True,'effective_to':SESSIONS[2]})
    after=old.model_copy(update={'effective_from':SESSIONS[2]})
    bars=tuple(b.model_copy(update={'limit_up':Decimal(10)}) if b.session==SESSIONS[2] else b for b in bundle.bars)
    bundle=bundle.model_copy(update={'bars':bars,'universe':bundle.universe.model_copy(update={'securities':(first,after)})})
    observations=evaluate_outcomes(run,bundle,at(SESSIONS[2],16),horizons=(2,))
    assert set(observations[0].reason_codes)=={'SUSPENDED','ONE_PRICE_EXECUTION_UNCONFIRMED'}
    demand=published_outcome_demand(service_for(run,observations),SESSIONS[2],SESSIONS)
    assert 2 not in demand[0]['horizons']


def test_suspension_censor_does_not_hide_other_tradable_session_event_gap():
    run,bundle=tiny_fixture('TREND_NM')
    old=bundle.universe.securities[0]
    first=old.model_copy(update={'suspended':True,'effective_to':SESSIONS[2]})
    after=old.model_copy(update={'effective_from':SESSIONS[2]})
    bars=tuple(b.model_copy(update={'limit_verified':False,'limit_up':None,'limit_down':None})
               if b.session==SESSIONS[2] else b for b in bundle.bars)
    bundle=bundle.model_copy(update={'bars':bars,'universe':bundle.universe.model_copy(update={'securities':(first,after)})})
    observations=evaluate_outcomes(run,bundle,at(SESSIONS[3],16),horizons=(3,))
    row=observations[0]
    assert row.status=='CENSORED' and row.reason_codes==('SUSPENDED',) and row.structure_success is None
    demand=published_outcome_demand(service_for(run,observations),SESSIONS[3],SESSIONS)
    assert 3 in demand[0]['horizons']
    assert any('close_limit_up' in field for field in demand[0]['pending_fields']['3'])
    assert not any('close_reference_change' in field for field in demand[0]['pending_fields']['3'])


class SchedulerReadService:
    def __init__(self,tmp_path,rows,primary_rows=()):
        self.store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3');self.rows=rows;self.primary_rows=list(primary_rows);self.submissions=[]
        self.jobs=SimpleNamespace(register=lambda *a:None,get=lambda key:None)
    def current_config(self):return StrategyConfig()
    def _all(self,kind):
        if kind=='candidate_runs':return [r.model_dump(mode='json') for r in self.rows]
        if kind=='stage_aware_prospective_primary_runs':return list(self.primary_rows)
        return []
    def get_run(self,rid):return next(r for r in self.rows if r.run_id==rid)
    def list_runs(self,limit):return list(reversed(self.rows[-limit:]))
    def submit_auction(self,rid,snapshot_id,job_id):
        self.submissions.append(rid);return {'job_id':job_id}


def test_recent_other_revisions_do_not_hide_last_evening_run_from_auction(tmp_path):
    evening,_=tiny_fixture()
    others=[evening.model_copy(update={'run_id':'TEST_OTHER_'+str(i),'market_session':'ON_DEMAND',
        'generated_at':evening.generated_at+timedelta(seconds=i+1)}) for i in range(101)]
    from stockfish_core.yaogu.validation_identity import current_validation_identity
    primary={**current_validation_identity(),'run_id':evening.run_id,'signal_observation_date':str(evening.observation_date),
        'decision_cutoff':evening.decision_cutoff.isoformat(),'official_primary_signal_countable':True}
    service=SchedulerReadService(tmp_path,[evening,*others],[primary])
    scheduler=CandidateScheduler(service,clock=lambda:at(SESSIONS[1],9))
    result=scheduler.tick(at(SESSIONS[1],9,26))
    assert service.submissions==[evening.run_id]
    assert next(e for e in result if e['market_session']=='AUCTION_REVIEW')['status']=='SUBMITTED'


def test_capture_parent_selection_is_before_recent_page_limit(tmp_path):
    run,bundle=tiny_fixture()
    # Future/current unrelated-date rows may exist in explicit historical
    # capture research. They must not erase the earlier same-date lineage.
    other=[run.model_copy(update={'run_id':'OTHER_DATE_'+str(i),'observation_date':SESSIONS[1],
        'generated_at':run.generated_at+timedelta(seconds=i+1)}) for i in range(101)]
    service=SchedulerReadService(tmp_path,[run,*other])
    captured=[]
    service.register_input=lambda value,activate:'TEST_FIXTURE_INPUT'
    service.config=lambda value:StrategyConfig()
    service.root=tmp_path
    service._candidate_job=lambda ctx,payload,activation:captured.append(payload) or payload
    scheduler=CandidateScheduler(service,capture=lambda *a:bundle,clock=lambda:at(SESSIONS[0],21))
    context=SimpleNamespace(payload={'session':str(SESSIONS[0]),'config_hash':StrategyConfig().config_hash,
        'market_session':'ON_DEMAND_CAPTURE'},job_id='TEST_FIXTURE_JOB',update=lambda *a:None,check_cancelled=lambda:None)
    scheduler._capture_job(context)
    assert captured[0]['parent_run_id']==run.run_id and captured[0]['revision']==run.revision+1
