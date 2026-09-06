"""R03 independently stated numerical contracts; all inputs TEST_FIXTURE."""
from datetime import datetime,time,timedelta
from copy import deepcopy
import pytest
from stockfish_core.yaogu.contracts import MarketEmotionSnapshot,MarketInput,digest
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.engine import run
from stockfish_core.yaogu.emotion_history import context,select_history
from tests.yaogu.fixtures import make_bundle,TZ
from tests.r02_independent_strategy.test_adversarial_semantics import market
from tests.r03_evidence.fixtures import funding


def history(bundle, scores=(0,0,70), config=None):
    config=config or StrategyConfig()
    return tuple(MarketEmotionSnapshot(session=d,as_of=datetime.combine(d,time(20,30),TZ),
        published_at=datetime.combine(d,time(20,31),TZ),score=s,state='TEST_FIXTURE',
        document_id='TEST_FIXTURE_'+str(d),effective_input_hash=digest(('TEST_FIXTURE',d,s)),
        source_complete=True,**context(config,bundle.coverage_type))
        for d,s in zip(tuple(x for x in bundle.sessions if x<bundle.observation_date)[-3:],scores))


@pytest.fixture(scope='module')
def golden():return market(count=50,patterns={0:{69},**{i:{68} for i in range(1,11)}})


def test_external_d3_exact_value_and_ferment(golden):
    b,c=golden;r=run(b.model_copy(update={'previous_emotions':history(b)}),cutoff=c)
    assert r.emotion.score==pytest.approx(54.640957446808514)
    assert r.emotion.readings['d']==pytest.approx(31.307624113475182)
    assert r.emotion.state=='FERMENT'
    assert len(r.emotion.history_selection['selected'])==3
    assert r.emotion.history_selection['formula']=='M_t - mean(M_t-1, M_t-2, M_t-3)'


@pytest.mark.parametrize('missing',(0,1,2))
def test_one_missing_session_never_averages_two(golden,missing):
    b,c=golden;prior=history(b)
    r=run(b.model_copy(update={'previous_emotions':tuple(x for n,x in enumerate(prior) if n!=missing)}),cutoff=c)
    assert r.emotion.score is not None and r.emotion.readings['d'] is None
    assert r.emotion.readings['delta_status']=='UNKNOWN_DELTA'
    assert r.emotion.history_selection['missing_sessions']==[str(prior[missing].session)]


def test_exact_exchange_sessions_weekend_holiday_and_one_revision(golden):
    b,c=golden;days=b.sessions[:-3]+b.sessions[-2:]  # one declared exchange holiday
    b=b.model_copy(update={'sessions':days});prior=history(b)
    duplicate=prior[0].model_copy(update={'document_id':'TEST_FIXTURE_LATEST','score':20,'published_at':prior[0].published_at+timedelta(minutes=1)})
    chosen,detail=select_history((*prior,duplicate),day=b.observation_date,sessions=days,cutoff=c,config=StrategyConfig(),coverage_type='TEST_FIXTURE')
    assert tuple(x.session for x in chosen)==tuple(x for x in days if x<b.observation_date)[-3:]
    assert len(chosen)==3 and chosen[0].document_id=='TEST_FIXTURE_LATEST'
    assert detail['missing_sessions']==[]


@pytest.mark.parametrize('field,new',[('strategy_id','OTHER'),('config_hash','f'*64),('feature_definition_version','YAOGU_FEATURES_V1'),('coverage_type','PROSPECTIVE_CAPTURED'),('pool_profile','ST_INDEPENDENT'),('source_complete',False)])
def test_history_context_cannot_mix(golden,field,new):
    b,c=golden;p=history(b)
    r=run(b.model_copy(update={'previous_emotions':(p[0].model_copy(update={field:new}),*p[1:])}),cutoff=c)
    assert r.emotion.readings['d'] is None
    assert len(r.emotion.history_selection['selected'])==2


def test_future_history_changes_raw_only(golden):
    b,c=golden;p=history(b)
    a=run(b.model_copy(update={'previous_emotions':p}),cutoff=c)
    future=p[-1].model_copy(update={'document_id':'FUTURE','published_at':c+timedelta(hours=1),'score':99})
    z=run(b.model_copy(update={'previous_emotions':(*p,future)}),cutoff=c)
    assert a.raw_bundle_hash!=z.raw_bundle_hash
    assert a.effective_input_hash==z.effective_input_hash and a.semantic_hash==z.semantic_hash
    changed=p[0].model_copy(update={'score':1})
    used=run(b.model_copy(update={'previous_emotions':(changed,*p[1:])}),cutoff=c)
    assert a.effective_input_hash!=used.effective_input_hash and a.semantic_hash!=used.semantic_hash


def test_known_st_50_partition_and_other_known_exclusions():
    b,c=make_bundle(50)
    records=tuple(s.model_copy(update={'st':True}) if s.instrument_id in ('TEST040','TEST041','TEST042') else s for s in b.universe.securities)
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':records})}),cutoff=c)
    p=r.emotion.coverage
    assert (p['master_expected'],p['identity_observed'],p['eligible'],p['known_excluded'],p['eligibility_unknown'])==(50,50,47,3,0)
    assert p['status_coverage']==p['price_coverage']==1 and p['price_observed']==p['price_required']==47
    assert len(r.excluded)==3 and r.emotion.score is not None
    for values in ({'board':'STAR'},{'exchange':'BSE','board':'BJ'},{'listed_on':b.sessions[-10]}):
        changed=records[-1].model_copy(update=values)
        z=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*records[:-1],changed)})}),cutoff=c)
        assert z.emotion.coverage['known_excluded']==4 and z.emotion.coverage['eligibility_unknown']==0
        assert z.emotion.score is not None


@pytest.mark.parametrize('change',({'board':'UNKNOWN'},{'st':None}))
def test_real_identity_unknown_retained_in_denominator(change):
    b,c=make_bundle(50);records=(*b.universe.securities[:-1],b.universe.securities[-1].model_copy(update=change))
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':records})}),cutoff=c)
    p=r.emotion.coverage
    assert p['eligibility_unknown']==1 and p['known_excluded']==0 and p['status_coverage']==.98
    assert r.emotion.score is None  # yesterday cohort identity cannot be guessed


@pytest.mark.parametrize('missing,want',[(1,True),(2,False)])
def test_price_98_percent_boundary_is_eligible_tradable_denominator(missing,want):
    b,c=make_bundle(50);ids={s.instrument_id for s in b.universe.securities[-missing:]}
    bars=tuple(x for x in b.bars if not(x.session==b.observation_date and x.instrument_id in ids))
    r=run(b.model_copy(update={'bars':bars}),cutoff=c)
    assert r.emotion.coverage['price_missing']==missing
    assert (r.emotion.score is not None)==want


def test_today_st_and_suspension_preserve_yesterday_limit_cohort():
    b,c=make_bundle(50);stamp=c.replace(hour=9)
    updates=tuple(b.universe.securities[n].model_copy(update={'effective_from':b.observation_date,
        **values,**{k:stamp for k in ('event_time','source_published_at','first_seen_at','ingested_at')}})
        for n,values in ((1,{'st':True}),(2,{'suspended':True})))
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,*updates)})}),cutoff=c)
    assert r.emotion.coverage['known_excluded']==1 and r.emotion.coverage['known_suspended']==1
    assert 'TEST001' in r.emotion.readings['cohort_ids'] and 'TEST002' in r.emotion.readings['cohort_ids']
    assert r.emotion.readings['cohort_n']==3 and r.emotion.readings['cohort_suspended_ids']==['TEST002']


def test_random_generation_clock_and_cutoff_do_not_change_business_semantic():
    b,c=make_bundle();a=run(b,cutoff=c,generated_at=c,run_id='A')
    z=run(b,cutoff=c+timedelta(minutes=1),generated_at=c+timedelta(minutes=2),run_id='Z')
    assert a.semantic_hash==z.semantic_hash and a.effective_input_hash==z.effective_input_hash
    assert digest(a)!=digest(z)
    assert a.input_hash==a.effective_input_hash
    assert a.hash_contract_version=='R03_RAW_EFFECTIVE_INPUT_V1'


def test_score_driving_verified_funds_is_effective_dependency():
    b,c=make_bundle();base=run(b,cutoff=c)
    pairs=[funding(i.instrument_id,b.sessions,c,net=5) for i in base.items]
    b=b.model_copy(update={'funds':tuple(p[0] for p in pairs),'receipts':(*b.receipts,*(r for p in pairs for r in p[1]))})
    a=run(b,cutoff=c,profile='FUNDS_V1');changed=deepcopy(b.funds)
    for f in changed:
        for row in f['money_rows']:row['net_large_cny']=-5
    z=run(b.model_copy(update={'funds':changed}),cutoff=c,profile='FUNDS_V1')
    assert all(i.score is not None for i in a.items)
    assert 'TEST_FIXTURE_MONEY_FLOW_TEST000' in a.source_refs
    assert a.items[0].score!=z.items[0].score and a.input_hash!=z.input_hash and a.semantic_hash!=z.semantic_hash


def test_empty_requires_verified_structure_history_not_only_current_breadth():
    b,c=market(patterns={})
    complete=run(b,cutoff=c);assert complete.status=='EMPTY'
    bars=tuple(x for x in b.bars if x.session>=b.sessions[-2])
    result=run(b.model_copy(update={'bars':bars}),cutoff=c)
    assert result.emotion.score is not None and not result.items
    assert result.status=='UNAVAILABLE' and not result.coverage['structural_screening_complete']


def test_current_membership_can_be_used_after_first_seen_without_pit_backfill():
    b,c=make_bundle();tonight=c+timedelta(days=2)
    clocks={k:tonight.replace(hour=18) for k in ('event_time','source_published_at','first_seen_at','ingested_at')}
    t=b.themes[0].model_copy(update={'effective_from':tonight.date(),**clocks})
    receipts=tuple(r.model_copy(update=clocks) if r.receipt_id=='fixture-theme' else r for r in b.receipts)
    b=b.model_copy(update={'themes':(t,),'receipts':receipts})
    past=run(b,cutoff=c)
    current=run(b,cutoff=tonight)
    assert all(i.components['T'] is None for i in past.items)
    assert all(i.components['T'] is not None for i in current.items)
    assert current.observation_date==b.observation_date
    assert current.coverage['membership_as_of']==str(tonight.date())
    assert current.coverage['membership_semantics']=='CURRENT_MEMBERSHIP_AT_DECISION'
    assert all(i.theme_evidence[0]['membership_as_of']==str(tonight.date()) for i in current.items)
    assert past.effective_input_hash!=current.effective_input_hash
