from datetime import timedelta
from decimal import Decimal
import math
import pytest
from pydantic import ValidationError
from stockfish_core.yaogu.contracts import FeatureValue, SecurityRecord, digest
from stockfish_core.yaogu.config import R,Trap,StrategyConfig,Weights,weighted_score,load_config
from stockfish_core.yaogu.engine import run,match_families,score_components,qualification,round_robin
from stockfish_core.yaogu.events import limit_event,annotate_streaks,count_window,TRUE,FALSE,UNKNOWN,NA
from stockfish_core.yaogu.features import build_features
from stockfish_core.yaogu.themes import percentile_rank,compute_theme
from stockfish_core.yaogu.emotion import classify_emotion
from .fixtures import make_bundle

@pytest.fixture(scope='module')
def fixture():return make_bundle()

@pytest.fixture(scope='module')
def result(fixture):return run(fixture[0],cutoff=fixture[1])

def test_five_real_rule_heads_complete(result):
    assert result.status=='COMPLETE'
    assert {x.primary_family for x in result.items}=={'ONE_TO_TWO','TWO_TO_THREE','HIGH_BOARD','TREND_NM','REBOUND_SETUP'}
    assert len(result.items)==5
    assert all(x.score is not None and not x.missing for x in result.items)
    assert result.model_id is None
    assert result.parameter_status=='INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'

def test_golden_76_75():
    assert weighted_score(dict(B=80,V=76,T=85,L=80,G=60,M=62),StrategyConfig().weights['TWO_TO_THREE'])==76.75

@pytest.mark.parametrize('x,want',[(-1,0),(0,0),(.5,50),(1,100),(2,100),(None,None),(float('nan'),None),(float('inf'),None)])
def test_r_boundaries(x,want):assert R(x,0,1)==want

@pytest.mark.parametrize('x,want',[(-1,0),(0,0),(.5,50),(1,100),(1.5,100),(2,100),(2.5,50),(3,0),(4,0),(None,None),(float('nan'),None)])
def test_trap_boundaries(x,want):assert Trap(x,0,1,2,3)==want

@pytest.mark.parametrize('bad',[{'unknown':1},{'units':'percent_yuan_lots'},{'thresholds':{'focus_score':float('nan')}},{'auction':{'gap_weak':-3}},{'weights':{'ONE_TO_TWO':{'B':100,'V':0,'T':0,'L':0,'G':0,'M':0}}},{'amount_traps':{'ONE_TO_TWO':[0,2,1,3]}},{'turnover_trap':[0,1,2,float('inf')]}])
def test_strong_schema_rejects(bad):
    with pytest.raises(ValidationError):StrategyConfig.model_validate(bad)

def test_weights_no_renormalize():
    with pytest.raises(ValidationError):Weights(B=10,V=10,T=10,L=10,G=10,M=10)
    assert weighted_score(dict(B=80,V=None,T=85,L=80,G=60,M=62),StrategyConfig().weights['TWO_TO_THREE']) is None

def test_yaml_is_exact_initial_config():assert load_config('config/yaogu_v1.defaults.yaml')==StrategyConfig()

def test_no_account_qlib_llm_semantics(fixture,result,monkeypatch):
    # Pure public contract cannot accept hidden account capital or model objects.
    monkeypatch.setenv('OPENAI_API_KEY','TEST_FIXTURE_DISABLED')
    monkeypatch.setenv('QLIB_DATA_DIR','TEST_FIXTURE_UNAVAILABLE')
    assert run(fixture[0],cutoff=fixture[1]).semantic_hash==result.semantic_hash
    with pytest.raises(TypeError):run(fixture[0],cutoff=fixture[1],account={'cash':100000})

def test_p02_twenty_display_not_five_holdings():
    b,c=make_bundle(31);r=run(b,cutoff=c)
    assert len(r.items)==30 and len(r.display_ids)==20 and len(r.focus_ids)<=5

def test_p03_limit_up_not_candidate_veto(result):
    assert sum(x.features['close_limit_up'].value==TRUE for x in result.items)==3
    assert not any('LIMIT_UP'==reason for i in result.items for reason in i.reason_codes)

@pytest.mark.parametrize('board,exchange,security_type',[('STAR','SSE','A_COMMON'),('CHINEXT','SZSE','A_COMMON'),('BJ','BSE','A_COMMON'),('MAIN','SSE','B_SHARE'),('UNKNOWN','SSE','A_COMMON')])
def test_p04_historical_identity_not_prefix(fixture,board,exchange,security_type):
    b,c=fixture
    securities=(b.universe.securities[0].model_copy(update=dict(board=board,exchange=exchange,security_type=security_type)),*b.universe.securities[1:])
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':securities})}),cutoff=c)
    assert 'TEST000' not in [i.instrument_id for i in r.items]
    assert any(x['instrument_id']=='TEST000' for x in r.excluded)

def test_p05_independent_turnover_missing_isolated(fixture,result):
    b,c=fixture
    bars=tuple(x.model_copy(update={'turnover_fraction':None}) if x.instrument_id=='TEST000' and x.session==b.observation_date else x for x in b.bars)
    r=run(b.model_copy(update={'bars':bars}),cutoff=c)
    a=next(x for x in r.items if x.instrument_id=='TEST000')
    assert a.score is None and 'V' in a.missing
    assert all(x.score==next(y.score for y in result.items if y.instrument_id==x.instrument_id) for x in r.items if x.instrument_id!='TEST000')

def test_future_clocks_before_business_fields(fixture,result):
    b,c=fixture
    future=b.universe.securities[0].model_copy(update={'event_time':c+timedelta(hours=1),'source_published_at':c+timedelta(hours=1),'first_seen_at':c+timedelta(hours=1),'ingested_at':c+timedelta(hours=1),'st':True,'risk_flags':('OBSERVE_ONLY',)})
    late=b.bars[-1].model_copy(update={'event_time':c+timedelta(hours=1),'source_published_at':c+timedelta(hours=1),'first_seen_at':c+timedelta(hours=1),'ingested_at':c+timedelta(hours=1),'close':Decimal('999')})
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,future)}),'bars':(*b.bars,late)}),cutoff=c)
    assert r.semantic_hash==result.semantic_hash and r.input_hash==result.input_hash

def test_current_new_theme_never_changes_old_score(fixture,result):
    b,c=fixture;future=b.themes[0].model_copy(update={'source_published_at':c+timedelta(hours=1),'first_seen_at':c+timedelta(hours=1),'ingested_at':c+timedelta(hours=1),'member_ids':('TEST000',),'theme_id':'FUTURE_THEME'})
    assert run(b.model_copy(update={'themes':(*b.themes,future)}),cutoff=c).semantic_hash==result.semantic_hash

def test_1610_cannot_see_2030_late_revision(fixture):
    b,c=fixture;early=c.replace(hour=16,minute=10)
    late=b.themes[0].model_copy(update={'first_seen_at':c,'ingested_at':c})
    changed=b.model_copy(update={'themes':(late,)})
    assert all(x.score is None for x in run(changed,cutoff=early).items)
    assert all(x.score is not None for x in run(changed,cutoff=c).items)

def test_actual_limit_tick_not_percentage(fixture):
    b,_=fixture;bar=b.bars[0]
    close=Decimal('11.02');prev=Decimal('10.02') # 9.9800399...%, valid actual rounded limit
    test=bar.model_copy(update={'open':prev,'low':prev,'high':close,'close':close,'reference_prev_close':prev,'limit_up':close,'limit_down':Decimal('9.02')})
    assert limit_event(test).close_limit_up==TRUE
    test=test.model_copy(update={'close':Decimal('11.01')})
    assert limit_event(test).close_limit_up==FALSE

def test_limit_above_high_invalid(fixture):
    bar=fixture[0].bars[0]
    assert limit_event(bar.model_copy(update={'high':bar.limit_up+Decimal('.01')})).quality=='PRICE_OUTSIDE_VERIFIED_LIMIT'

def test_no_limit_day_and_no_intraday_invention(fixture):
    bar=fixture[0].bars[0]
    assert limit_event(bar.model_copy(update={'limit_applicable':False})).close_limit_up==NA
    assert 'open_count' not in limit_event(bar).model_dump()

@pytest.mark.parametrize('preceding,want,quality',[(FALSE,1,'EXACT'),(UNKNOWN,None,'LEFT_CENSORED_AT_LEAST_1'),(NA,1,'EXACT')])
def test_unknown_suspend_nonlimit_streak_distinct(fixture,preceding,want,quality):
    e=limit_event(fixture[0].bars[0]);p=e.model_copy(update={'close_limit_up':preceding,'quality':'SUSPENSION_BREAK' if preceding==NA else e.quality});now=e.model_copy(update={'close_limit_up':TRUE})
    got=annotate_streaks([p,now])[-1]
    assert got.streak_exchange==want and got.streak_quality==quality

def test_censored_left_window_not_lifetime_count(fixture):
    e=limit_event(fixture[0].bars[0]).model_copy(update={'close_limit_up':TRUE})
    assert annotate_streaks([e]*3)[-1].streak_exchange is None
    assert annotate_streaks([e]*3)[-1].streak_quality=='LEFT_CENSORED_AT_LEAST_3'
    assert count_window([e]*3,5)==(None,'SHORT_HISTORY')

def test_amount_denominator_excludes_today(fixture):
    b,c=fixture;bars=[x for x in b.bars if x.instrument_id=='TEST000'];bars[-1]=bars[-1].model_copy(update={'amount_cny':Decimal('40000000')})
    es=annotate_streaks([limit_event(x) for x in bars]);f=build_features('TEST000',b.sessions,bars,es,c)
    assert f['amount_ratio_5'].value==2.0

def test_future_float_share_clock_unavailable(fixture):
    b,c=fixture;bars=tuple(x.model_copy(update={'turnover_fraction':None,'float_shares':Decimal('10000000'),'float_shares_as_of':c+timedelta(days=1)}) if x.instrument_id=='TEST000' else x for x in b.bars)
    r=run(b.model_copy(update={'bars':bars}),cutoff=c)
    assert next(i for i in r.items if i.instrument_id=='TEST000').components['V'] is None

def test_basis_mix_blocks_comparable_features(fixture):
    b,c=fixture;bars=tuple(x.model_copy(update={'comparable_basis':'DIFFERENT_CURRENT_ADJUSTMENT'}) if x.instrument_id=='TEST000' and x.session==b.observation_date else x for x in b.bars)
    r=run(b.model_copy(update={'bars':bars}),cutoff=c)
    item=next(i for i in r.items if i.instrument_id=='TEST000')
    assert item.features['ret3'].value is None and item.score is None

def test_g_only_mature_labels(result):
    one=next(x for x in result.items if x.primary_family=='ONE_TO_TWO')
    assert one.features['G_event_n'].value==0 and one.features['G_smoothed_rate'].value==.5
    assert one.features['G_success_rate'].value is None and one.features['G_history_sessions'].value==85

def test_themes_peer_exclusion_full_reference(result):
    for item in result.items:
        e=item.theme_evidence[0]
        assert item.instrument_id not in e['peer_ids'] and item.instrument_id in e['member_ids']
        assert e['rank_denominator']==6
        assert e['selection_reason']=='MAX_T_STABLE_THEME_ID'

def test_average_ties():
    assert percentile_rank(2,[1,2,2,3])==50
    assert percentile_rank(1,[1,1])==50
    assert percentile_rank(1,[1]) is None

def test_qualification_themes_disallowed(fixture):
    b,c=fixture;theme=b.themes[0].model_copy(update={'theme_type':'QUALIFICATION'})
    r=run(b.model_copy(update={'themes':(theme,)}),cutoff=c)
    assert all(i.components['T'] is None for i in r.items)

def test_market_cohort_identity_fixed(result):
    r=result.emotion.readings
    assert set(r['cohort_ids'])=={'TEST001','TEST002','TEST004'}
    assert r['cohort_n']==3 and r['cohort_s']==2 and r['cohort_raw_p']==2/3

def test_market_data_failure_never_no_event_prior(fixture):
    b,c=fixture;prev=b.sessions[-2]
    bars=tuple(x for x in b.bars if not (x.instrument_id=='TEST004' and x.session==prev))
    r=run(b.model_copy(update={'bars':bars}),cutoff=c)
    assert r.emotion.score is None and r.emotion.readings['cohort_p_status']=='UNAVAILABLE'

def test_market_same_component_per_family(result):
    assert {i.components['M'] for i in result.items}=={result.emotion.score}

@pytest.mark.parametrize('m,d,down,n,ret,state',[(40,-12,0,0,None,'RETREAT'),(80,0,.008,5,-.02,'RETREAT'),(29,None,0,0,None,'COLD'),(30,8,0,0,None,'REPAIR'),(75,None,0,0,None,'HOT'),(50,0,0,0,None,'FERMENT'),(60,None,0,0,None,'SPLIT'),(None,None,0,0,None,'UNKNOWN')])
def test_emotion_thresholds(m,d,down,n,ret,state):assert classify_emotion(m,d,down,n,ret)==state

def test_round_robin_display_not_cross_family_rank(result):
    assert result.display_ids==('TEST000','TEST001','TEST003','TEST002','TEST004')
    assert all(i.family_rank==1 for i in result.items)

def test_priority_rebound_before_trend(result):
    i=next(i for i in result.items if i.instrument_id=='TEST004')
    assert i.primary_family=='REBOUND_SETUP' and 'TREND_NM' in i.secondary_tags

def test_config_change_new_hash():
    c=StrategyConfig();updated=c.model_copy(update={'strategy_id':'YAOGU_V1.0.1'})
    assert updated.config_hash!=c.config_hash

def test_price_only_explicit_diagnostic(fixture):
    b,c=fixture;r=run(b.model_copy(update={'themes':()}),cutoff=c,profile='PRICE_ONLY')
    assert all(i.daily_score is None and i.tier=='DIAGNOSTIC' for i in r.items)
    assert r.coverage['price_only_diagnostic'] is True

def test_serialization_roundtrip(fixture,result):
    from stockfish_core.yaogu.contracts import CandidateRun,MarketInput
    assert CandidateRun.model_validate_json(result.model_dump_json())==result
    assert run(MarketInput.model_validate_json(fixture[0].model_dump_json()),cutoff=fixture[1]).semantic_hash==result.semantic_hash

@pytest.mark.parametrize('bad',[{'funds_primary':'false'},{'funds_primary':0},{'display':{'observe_count':True}},{'display':{'focus_count':5.0}},{'thresholds':{'focus_v':'45'}},{'weights':{**StrategyConfig().model_dump()['weights'],'ONE_TO_TWO':{'B':True,'V':25,'T':25,'L':10,'G':5,'M':34}}},{'amount_traps':{**StrategyConfig().model_dump()['amount_traps'],'ONE_TO_TWO':[True,.8,2.5,5]}}])
def test_no_type_coercion(bad):
    with pytest.raises(ValidationError):StrategyConfig.model_validate(bad)

def changed_features(original,**changes):
    # These are authored golden FeatureValues, not edits of a live invalid receipt.
    return {**original,**{k:original[k].model_copy(update={'value':v,'quality':'VALID'}) for k,v in changes.items()}}

@pytest.mark.parametrize('family,key,boundary,bad',[('ONE_TO_TWO','streak_exchange',1,None),('TWO_TO_THREE','streak_exchange',2,1),('HIGH_BOARD','streak_exchange',3,2),('TREND_NM','ret3',0,-.000001),('REBOUND_SETUP','close_location',.6,.599999)])
def test_each_family_boundary_and_counterexample(result,family,key,boundary,bad):
    item=next(i for i in result.items if i.primary_family==family)
    valid=changed_features(item.features,**{key:boundary})
    assert match_families(valid)[0]==family
    assert match_families(changed_features(item.features,**{key:bad}))[0]!=family

@pytest.mark.parametrize('family,want',[('ONE_TO_TWO',40+30+30),('TWO_TO_THREE',40+30+30),('HIGH_BOARD',40+30+30),('TREND_NM',50+30+20),('REBOUND_SETUP',40+30+30)])
def test_exact_B_component_formula_saturation(result,family,want):
    f=changed_features(result.items[0].features,M5=3,M10=7,M20=10,cmax60=5,streak_exchange=8,ret10=.30,previous_run_streak=6,comparable_close=10.2,previous_run_end_close=10)
    assert score_components(family,f,0,0,0,StrategyConfig())['B']==pytest.approx(want)

def test_family_specific_volume_trapezoids(result):
    f=changed_features(result.items[0].features,amount_ratio_5=.35)
    low=score_components('ONE_TO_TWO',f,0,0,0,StrategyConfig())['V']
    high=score_components('HIGH_BOARD',f,0,0,0,StrategyConfig())['V']
    assert high-low==pytest.approx(27)

def test_one_stock_cannot_self_generate_peer_limit_count(fixture,result):
    own=next(i for i in result.items if i.primary_family=='ONE_TO_TWO')
    features={i.instrument_id:i.features for i in result.items}
    features['TEST005']=own.features
    for iid in features:
        features[iid]=changed_features(features[iid],close_limit_up=TRUE if iid=='TEST000' else FALSE,streak_exchange=1 if iid=='TEST000' else 0)
    t,l,theme,ev=compute_theme('TEST000',fixture[0].themes,list(features),features,StrategyConfig())
    assert ev[0]['peer_close_limit_up_count']==0 and ev[0]['peer_streak_ge2_count']==0

def test_future_next_session_labels_never_enter_G(fixture,result):
    b,c=fixture;future_day=b.sessions[-1]+timedelta(days=1)
    future=tuple(bar.model_copy(update={'session':future_day,'event_time':c+timedelta(days=1),'source_published_at':c+timedelta(days=1),'first_seen_at':c+timedelta(days=1),'ingested_at':c+timedelta(days=1),'comparable_close':Decimal('999')}) for bar in b.bars if bar.session==b.observation_date)
    r=run(b.model_copy(update={'bars':(*b.bars,*future),'sessions':(*b.sessions,future_day)}),cutoff=c)
    assert r.semantic_hash==result.semantic_hash

def test_no_receipts_no_valid_product(fixture):
    b,c=fixture;r=run(b.model_copy(update={'receipts':()}),cutoff=c)
    assert r.status=='UNAVAILABLE' and not r.display_ids

def test_missing_system_prices_never_emotion_prior(fixture):
    b,c=fixture;r=run(b.model_copy(update={'bars':()}),cutoff=c)
    assert r.emotion.score is None and r.emotion.state=='UNKNOWN' and not r.display_ids

def test_59_vs_60_listing_boundary(fixture):
    b,c=fixture;s=b.universe.securities[0];days=b.sessions
    assert qualification(s,days[58],days,StrategyConfig())=='INSUFFICIENT_LISTING_SESSIONS'
    assert qualification(s,days[59],days,StrategyConfig()) is None

def test_st_enabled_is_independent_not_mixed(fixture):
    b,c=fixture;records=tuple(s.model_copy(update={'st':True}) if s.instrument_id=='TEST000' else s for s in b.universe.securities)
    b=b.model_copy(update={'universe':b.universe.model_copy(update={'securities':records})})
    assert 'TEST000' not in [i.instrument_id for i in run(b,cutoff=c).items]
    config=StrategyConfig.model_validate({'universe':{'include_st':True}})
    r=run(b,config,cutoff=c)
    assert [i.instrument_id for i in r.items]==['TEST000'] and r.coverage['research_pool']=='ST_INDEPENDENT'

def test_current_suspension_preserves_prior_cohort(fixture):
    b,c=fixture;old=b.universe.securities[4];stamp=c.replace(hour=9,minute=0)
    today=old.model_copy(update={'effective_from':b.observation_date,'suspended':True,'event_time':stamp,'source_published_at':stamp,'first_seen_at':stamp,'ingested_at':stamp})
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,today)})}),cutoff=c)
    assert r.emotion.readings['cohort_n']==3 and r.emotion.readings['cohort_s']==2
    assert r.emotion.readings['cohort_suspended_ids']==['TEST004']
    assert 'TEST004' not in [i.instrument_id for i in r.items]

def test_unknown_today_cohort_not_counted_as_failure(fixture):
    b,c=fixture;bars=tuple(x for x in b.bars if not (x.instrument_id=='TEST004' and x.session==b.observation_date))
    r=run(b.model_copy(update={'bars':bars}),cutoff=c)
    assert r.emotion.readings['cohort_n']==3 and r.emotion.readings['cohort_s'] is None
    assert r.emotion.score is None

def test_high_board_7_risk_is_warning_only(result):
    from stockfish_core.yaogu.engine import tier_for
    assert tier_for(80,{'V':80,'T':80},'HOT',('HIGH_BOARD_RISK',),StrategyConfig())=='FOCUS'
    assert tier_for(80,{'V':80,'T':80},'HOT',('SERIOUS_ABNORMAL_VOLATILITY',),StrategyConfig())=='WATCH'

@pytest.mark.parametrize('score,v,t,emotion,want',[(70,45,50,'HOT','FOCUS'),(69.999,45,50,'HOT','WATCH'),(70,44.999,50,'HOT','WATCH'),(70,45,49.999,'HOT','WATCH'),(70,45,50,'COLD','WATCH'),(54.999,80,80,'HOT','STRUCTURAL_MATCH'),(None,80,80,'UNKNOWN','DATA_PENDING')])
def test_focus_watch_thresholds(score,v,t,emotion,want):
    from stockfish_core.yaogu.engine import tier_for
    assert tier_for(score,{'V':v,'T':t},emotion,(),StrategyConfig())==want

def test_security_effective_to_is_exclusive(fixture):
    from stockfish_core.yaogu.engine import security_at
    b,c=fixture;record=b.universe.securities[0].model_copy(update={'effective_to':b.observation_date})
    assert security_at([record],record.instrument_id,b.observation_date,c) is None
    assert security_at([record],record.instrument_id,b.sessions[-2],c)==record

def test_theme_effective_to_is_exclusive(fixture):
    b,c=fixture;theme=b.themes[0].model_copy(update={'effective_to':b.observation_date})
    assert all(i.components['T'] is None for i in run(b.model_copy(update={'themes':(theme,)}),cutoff=c).items)

def test_future_outer_universe_cannot_be_bypassed_by_nested_clocks(fixture):
    b,c=fixture;outer=b.universe.model_copy(update={'first_seen_at':c+timedelta(hours=1),'ingested_at':c+timedelta(hours=1)})
    one=run(b.model_copy(update={'universe':outer}),cutoff=c)
    changed=outer.model_copy(update={'securities':tuple(s.model_copy(update={'st':True,'risk_flags':('OBSERVE_ONLY',)}) for s in outer.securities)})
    two=run(b.model_copy(update={'universe':changed}),cutoff=c)
    assert not one.items and one.semantic_hash==two.semantic_hash

def test_future_previous_emotion_revision_cannot_change_prior_tiers(fixture,result):
    b,c=fixture;old=result.emotion.model_copy(update={'session':b.sessions[-2],'as_of':c+timedelta(hours=1),'score':0})
    a=run(b.model_copy(update={'previous_emotions':(old,)}),cutoff=c)
    old=old.model_copy(update={'score':100})
    z=run(b.model_copy(update={'previous_emotions':(old,)}),cutoff=c)
    assert a.semantic_hash==z.semantic_hash==result.semantic_hash

def test_left_censored_threshold_allows_high_structure_without_exact_score(result):
    f=changed_features(result.items[0].features,close_limit_up=TRUE,streak_exchange=None,streak_lower_bound=3)
    assert match_families(f)[0]=='HIGH_BOARD'
    assert score_components('HIGH_BOARD',f,60,60,60,StrategyConfig())['B'] is None

def test_all_yesterday_limit_members_confirmed_suspended_get_named_m_prior(fixture):
    b,c=fixture;stamp=c.replace(hour=9,minute=0)
    updates=tuple(s.model_copy(update={'effective_from':b.observation_date,'suspended':True,'event_time':stamp,'source_published_at':stamp,'first_seen_at':stamp,'ingested_at':stamp}) for s in b.universe.securities if s.instrument_id in ('TEST001','TEST002','TEST004'))
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,*updates)})}),cutoff=c)
    assert r.emotion.readings['cohort_n']==3 and r.emotion.readings['cohort_s']==0
    assert r.emotion.readings['cohort_m'] is None and r.emotion.readings['cohort_m_n']==0
    assert r.emotion.readings['cohort_m_status']=='PRIOR_NO_TRADABLE_EVENT' and r.emotion.components['m']==50

def test_complete_no_yesterday_events_is_not_fetch_failure(fixture):
    b,c=fixture;previous=b.sessions[-2]
    bars=tuple(x.model_copy(update={'close':x.reference_prev_close,'comparable_close':x.reference_prev_close}) if x.session==previous else x for x in b.bars)
    e=run(b.model_copy(update={'bars':bars}),cutoff=c).emotion
    assert e.score is not None and e.readings['cohort_n']==0
    assert e.readings['cohort_raw_p'] is None and e.readings['cohort_m'] is None
    assert e.readings['cohort_smoothed_p']==.5 and e.components['m']==50 and e.readings['cohort_m_status']=='PRIOR_NO_EVENT'

def test_naive_emotion_timestamp_is_rejected(result):
    from stockfish_core.yaogu.contracts import MarketEmotionSnapshot
    with pytest.raises(ValidationError):
        MarketEmotionSnapshot.model_validate({**result.emotion.model_dump(),'as_of':result.emotion.as_of.replace(tzinfo=None)})

def test_industry_proxy_is_explicit_and_never_silently_daily_theme(fixture):
    b,c=fixture;proxy=b.themes[0].model_copy(update={'theme_type':'INDUSTRY_PROXY'})
    r=run(b.model_copy(update={'themes':(proxy,)}),cutoff=c)
    assert all(i.components['T'] is None for i in r.items)
    assert all(i.theme_evidence[0]['selection_reason']=='INDUSTRY_PROXY_REQUIRES_SEPARATE_PROFILE' for i in r.items)
