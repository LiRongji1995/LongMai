"""Independent R02 TEST_FIXTURE counterexamples, never written to normal history.

This fixture is authored separately from tests/yaogu/fixtures.py and uses no
network/provider/model mocks in the rule engine. Weekday-only dates are synthetic.
"""
from datetime import date,datetime,timedelta,time,timezone
from decimal import Decimal,ROUND_HALF_UP
from zoneinfo import ZoneInfo
from copy import deepcopy
import pytest
from stockfish_core.yaogu.contracts import MarketInput,FeatureValue,ThemeSnapshot,DailyBar,digest
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.engine import run
from stockfish_core.yaogu.features import build_features,price_comparable
from stockfish_core.yaogu.events import limit_event,annotate_streaks,count_window,TRUE,FALSE,UNKNOWN,NA
from stockfish_core.yaogu.themes import compute_theme,percentile_rank
from stockfish_core.yaogu.funds import score_family_funds as _score_family_funds,calculate_funds as _calculate_funds,visible_seat_aliases
from tests.r03_evidence.fixtures import funding

TZ=ZoneInfo('Asia/Shanghai')
def clocks(stamp):return {k:stamp for k in ('event_time','source_published_at','first_seen_at','ingested_at')}

def market(count=8,patterns=None):
    sessions=[];day=date(2026,1,5)
    while len(sessions)<70:
        if day.weekday()<5:sessions.append(day)
        day+=timedelta(days=1)
    if patterns is None:patterns={0:{69},1:{68,69},2:{67,68,69}}
    stamp=datetime.combine(sessions[0],time(0),TZ);ids=[f'INDEPENDENT_{i:03d}' for i in range(count)]
    securities=[];bars=[]
    for pos,iid in enumerate(ids):
        securities.append(dict(instrument_id=iid,exchange='SSE',board='MAIN',security_type='A_COMMON',listed_on=sessions[0],effective_from=sessions[0],st=False,suspended=False,source_refs=['M'],name='TEST_FIXTURE',**clocks(stamp)))
        previous=Decimal('10')
        for index,day in enumerate(sessions):
            up=(previous*Decimal('1.1')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP);down=(previous*Decimal('.9')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            close=up if index in patterns.get(pos,set()) else previous+Decimal('.01')
            bars.append(dict(instrument_id=iid,session=day,open=previous,high=close,low=previous,close=close,reference_prev_close=previous,
                volume_shares=100000,amount_cny=1000000+pos*100000,turnover_fraction=.10,turnover_basis='TEST_FIXTURE_EXPLICIT_FLOAT',
                limit_up=up,limit_down=down,limit_applicable=True,limit_verified=True,rule_id='TEST_FIXTURE_LIMIT',
                comparable_open=previous,comparable_high=close,comparable_low=previous,comparable_close=close,comparable_basis='TEST_FIXTURE_NO_ACTION',source_refs=['B'],
                **clocks(datetime.combine(day,time(15),TZ))))
            previous=close
    theme=dict(theme_id='T_EVENT',name='TEST_FIXTURE_REGISTERED_EVENT',theme_type='EVENT',member_ids=ids,effective_from=sessions[0],definition_version='TEST_FIXTURE_EVENT_V1',source_refs=['T'],**clocks(stamp))
    receipts=[dict(receipt_id=rid,provider='TEST_FIXTURE',dataset=rid,field_version='INDEPENDENT_V1',content_hash=digest(data),row_count=len(data),pagination_complete=True,status='COMPLETE',**clocks(stamp)) for rid,data in [('M',securities),('B',bars),('T',[theme])]]
    bundle=MarketInput.model_validate(dict(observation_date=sessions[-1],sessions=sessions,universe=dict(snapshot_id='INDEPENDENT_TEST_FIXTURE',securities=securities,complete=True,expected_count=count,source_refs=['M'],**clocks(stamp)),bars=bars,themes=[theme],receipts=receipts,coverage_type='TEST_FIXTURE'))
    return bundle,datetime.combine(sessions[-1],time(20,30),TZ)

def mutate_bars(bundle,predicate,**changes):
    return bundle.model_copy(update={'bars':tuple(b.model_copy(update=changes) if predicate(b) else b for b in bundle.bars)})

def test_previous_eligible_identity_hole_must_not_shrink_yesterday_cohort():
    bundle,cutoff=market();baseline=run(bundle,cutoff=cutoff)
    assert baseline.emotion.readings['cohort_n']==2
    records=tuple(s.model_copy(update={'effective_from':bundle.observation_date}) if s.instrument_id=='INDEPENDENT_001' else s for s in bundle.universe.securities)
    changed=run(bundle.model_copy(update={'universe':bundle.universe.model_copy(update={'securities':records})}),cutoff=cutoff)
    # This is an old-listed currently eligible stock with no verified yesterday
    # status, not a newly listed stock. Its cohort membership is UNKNOWN.
    assert changed.emotion.score is None
    assert changed.emotion.readings['cohort_p_status']=='UNAVAILABLE'

def test_unknown_current_cohort_member_cannot_be_counted_failure_or_dropped():
    bundle,cutoff=market();iid='INDEPENDENT_001'
    records=tuple(s.model_copy(update={'suspended':None}) if s.instrument_id==iid else s for s in bundle.universe.securities)
    r=run(bundle.model_copy(update={'universe':bundle.universe.model_copy(update={'securities':records})}),cutoff=cutoff)
    assert r.emotion.score is None and r.emotion.readings['cohort_raw_p'] is None

def test_known_suspension_keeps_frozen_cohort_and_no_tradable_prior():
    bundle,cutoff=market(patterns={0:{68}})
    records=[]
    for s in bundle.universe.securities:
        records.append(s.model_copy(update={'effective_to':bundle.observation_date}) if s.instrument_id=='INDEPENDENT_000' else s)
        if s.instrument_id=='INDEPENDENT_000':records.append(s.model_copy(update={'effective_from':bundle.observation_date,'suspended':True}))
    changed=run(bundle.model_copy(update={'universe':bundle.universe.model_copy(update={'securities':tuple(records)})}),cutoff=cutoff)
    reading=changed.emotion.readings
    assert reading['cohort_n']==1 and reading['cohort_s']==0
    assert reading['cohort_raw_p']==0 and reading['cohort_m'] is None
    assert reading['cohort_m_status']=='PRIOR_NO_TRADABLE_EVENT' and changed.emotion.components['m']==50

def test_true_zero_events_and_download_failure_have_distinct_priors():
    bundle,cutoff=market(patterns={})
    clean=run(bundle,cutoff=cutoff).emotion
    assert clean.readings['cohort_raw_p'] is None and clean.readings['cohort_m'] is None
    assert clean.readings['cohort_smoothed_p']==.5 and clean.components['m']==50
    broken=bundle.model_copy(update={'bars':tuple(b for b in bundle.bars if not(b.instrument_id=='INDEPENDENT_007' and b.session==bundle.sessions[-2]))})
    unavailable=run(broken,cutoff=cutoff).emotion
    assert unavailable.score is None and unavailable.readings['cohort_smoothed_p'] is None

@pytest.mark.parametrize('missing_count,valid',[(1,True),(2,False)])
def test_98_percent_price_boundary_reports_observed_not_complete(missing_count,valid):
    bundle,cutoff=market(count=50,patterns={0:{69}})
    absent={f'INDEPENDENT_{i:03d}' for i in range(50-missing_count,50)}
    broken=bundle.model_copy(update={'bars':tuple(b for b in bundle.bars if not(b.instrument_id in absent and b.session==bundle.observation_date))})
    r=run(broken,cutoff=cutoff)
    assert r.emotion.coverage['N_price_observed']==50-missing_count
    assert r.emotion.coverage['price_coverage']==(50-missing_count)/50
    assert (r.emotion.score is not None)==valid

def feature(value,quality='VALID'):
    return FeatureValue(value=value,unit='TEST_FIXTURE',window='TEST_FIXTURE',as_of=datetime(2026,9,4,15,tzinfo=TZ),quality=quality)

def theme_inputs(count=5):
    names=[f'I{x}' for x in range(count)]
    theme=ThemeSnapshot(theme_id='FIXED_EVENT',name='TEST_FIXTURE_EVENT',theme_type='EVENT',member_ids=names,effective_from=date(2026,1,1),definition_version='TEST_FIXTURE',source_refs=['TEST_FIXTURE'],**clocks(datetime(2026,1,1,tzinfo=TZ)))
    features={iid:{'close_limit_up':feature(FALSE),'close':feature(10),'reference_prev_close':feature(10),'ret3':feature(0),
        'streak_exchange':feature(0),'M10':feature(pos),'ret5':feature(pos/100),'median_amount20':feature(100+pos)} for pos,iid in enumerate(names)}
    return names,theme,features

def test_candidate_cannot_create_its_own_theme_resonance():
    names,theme,features=theme_inputs()
    base=compute_theme('I0',[theme],names,features,StrategyConfig())
    features['I0'].update(close_limit_up=feature(TRUE),close=feature(11),ret3=feature(.30),streak_exchange=feature(7))
    changed=compute_theme('I0',[theme],names,features,StrategyConfig())
    assert changed[0]==base[0] and changed[3][0]['peer_close_limit_up_count']==0

def test_invalid_quality_peer_is_not_a_valid_quote_for_95_percent_gate():
    names,theme,features=theme_inputs()
    features['I4']['ret3']=feature(.05,'INVALID')
    t,l,primary,evidence=compute_theme('I0',[theme],names,features,StrategyConfig())
    assert t is None and primary is None

@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-float('inf')])
def test_percentile_nonfinite_is_missing_not_an_artificial_rank(bad):
    assert percentile_rank(bad,[1,2,bad]) is None

def test_leader_reference_is_not_the_display_budget():
    bundle,cutoff=market(count=12)
    normal=run(bundle,cutoff=cutoff)
    cfg=StrategyConfig();small=cfg.model_copy(update={'display':cfg.display.model_copy(update={'observe_count':1,'focus_count':1})})
    changed=run(bundle,config=small,cutoff=cutoff)
    assert len(changed.display_ids)==1
    assert [(i.instrument_id,i.components['L']) for i in changed.items]==[(i.instrument_id,i.components['L']) for i in normal.items]
    assert all(i.theme_evidence[0]['rank_denominator']==12 for i in changed.items)

def test_l_rank_uses_average_ties_and_full_nonselected_members():
    names,theme,features=theme_inputs(6)
    features['I0']['M10']=feature(3);features['I0']['ret5']=feature(.03);features['I0']['median_amount20']=feature(103)
    answer=compute_theme('I0',[theme],names,features,StrategyConfig())
    # Values are [3,1,2,3,4,5], average ascending rank 3.5 of 6 -> 50.
    assert answer[1]==50 and answer[3][0]['rank_denominator']==6

def test_independent_missing_amplification_does_not_erase_other_candidates():
    bundle,cutoff=market();baseline=run(bundle,cutoff=cutoff)
    changed=run(mutate_bars(bundle,lambda b:b.instrument_id=='INDEPENDENT_000' and b.session==bundle.observation_date,amount_cny=None),cutoff=cutoff)
    # Candidate 0 loses V and (shared L) median_amount20; T/structural other
    # candidates still persist even where this explicitly shared dependency fails.
    assert {i.instrument_id for i in changed.items}=={i.instrument_id for i in baseline.items}
    assert next(i for i in changed.items if i.instrument_id=='INDEPENDENT_000').components['V'] is None

def test_g_censored_labels_cannot_be_presented_as_no_historical_events_prior():
    bundle,cutoff=market(patterns={0:{10,69}})
    changed=mutate_bars(bundle,lambda b:b.instrument_id=='INDEPENDENT_000' and b.session==bundle.sessions[11],comparable_close=None)
    item=next(i for i in run(changed,cutoff=cutoff).items if i.instrument_id=='INDEPENDENT_000')
    assert item.features['G_censored'].value==1
    assert item.features['G_smoothed_rate'].value is None
    assert item.features['G_success_rate'].quality!='PRIOR_NO_EVENT'

def test_invalid_historical_bar_cannot_supply_valid_comparable_return():
    bundle,cutoff=market()
    bars=[b for b in bundle.bars if b.instrument_id=='INDEPENDENT_000']
    bars[-2]=bars[-2].model_copy(update={'quality':'INVALID'})
    events=annotate_streaks([limit_event(b) for b in bars])
    features=build_features('INDEPENDENT_000',bundle.sessions,bars,events,cutoff)
    assert not price_comparable(bars[-4:])
    assert features['ret3'].value is None

def test_future_current_member_snapshot_cannot_backfill_historical_theme():
    bundle,cutoff=market();future=cutoff+timedelta(days=1)
    theme=bundle.themes[0].model_copy(update=clocks(future))
    reconstructed=bundle.model_copy(update={'themes':(theme,),'coverage_type':'RECONSTRUCTED_HISTORICAL'})
    r=run(reconstructed,cutoff=cutoff)
    assert all(i.components['T'] is None for i in r.items)

def funds_row(iid,stamp):
    return funding(iid,tuple(date(2026,9,i) for i in (2,3,4)),stamp,net=0)[0]

def funds_authority(stamp):
    sessions=tuple(date(2026,9,i) for i in (2,3,4))
    return dict(observation_date=sessions[-1],sessions=sessions,
                receipts=tuple(r for iid in ('A','B') for r in funding(iid,sessions,stamp)[1]),coverage_type='TEST_FIXTURE')

def score_family_funds(evidence,scores,stamp):
    return _score_family_funds(evidence,scores,stamp,**funds_authority(stamp))

def calculate_funds(evidence,stamp):
    return _calculate_funds(evidence,stamp,**funds_authority(stamp))

def test_unrelated_duplicate_funds_envelopes_cannot_block_another_family():
    stamp=datetime(2026,9,4,20,30,tzinfo=TZ);good=funds_row('A',stamp);unrelated=funds_row('B',stamp)
    baseline=score_family_funds([good],{'A':70},stamp)
    assert baseline['status']=='AVAILABLE'
    changed=score_family_funds([good,unrelated,unrelated],{'A':70},stamp)
    assert changed==baseline

def test_future_outcome_columns_cannot_change_funds_or_raw_disclosure_status():
    stamp=datetime(2026,9,4,20,30,tzinfo=TZ);good=funds_row('A',stamp);changed=deepcopy(good)
    changed['上榜后10日']=999;changed['future_seat_alias']='Legendary future winner'
    for row in changed['money_rows']:row.update(上榜后1日=999,next_return=999)
    assert calculate_funds(good,stamp)['status']=='AVAILABLE'
    assert calculate_funds(changed,stamp)==calculate_funds(good,stamp)

def test_an_alias_first_seen_tomorrow_is_not_visible_today():
    stamp=datetime(2026,9,4,20,30,tzinfo=TZ);row=dict(seat_id='S',alias='TEST_FIXTURE_UNVERIFIED_PERSON',source_refs=['TEST_FIXTURE'],valid_from='2020-01-01',sample_count=99,**clocks(stamp+timedelta(days=1)))
    assert visible_seat_aliases([row],stamp,date(2026,9,4))==[]

def test_in_family_duplicate_funds_still_blocks_the_entire_frozen_family():
    stamp=datetime(2026,9,4,20,30,tzinfo=TZ);good=funds_row('A',stamp)
    result=score_family_funds([good,good],{'A':70},stamp)
    assert result['status']=='UNAVAILABLE' and 'DUPLICATE_FUNDS_ENVELOPE' in result['reason_codes']

def test_future_duplicate_funds_does_not_change_past_family():
    stamp=datetime(2026,9,4,20,30,tzinfo=TZ);good=funds_row('A',stamp)
    future=deepcopy(good);future.update(clocks(stamp+timedelta(seconds=1)))
    future['money_status']='FETCH_FAILED';future['money_rows']=None
    assert score_family_funds([good,future],{'A':70},stamp)==score_family_funds([good],{'A':70},stamp)

@pytest.mark.parametrize('theme_type',['QUALIFICATION','UNKNOWN','INDUSTRY_PROXY'])
def test_administrative_or_unknown_theme_never_contributes_t_or_l(theme_type):
    names,theme,features=theme_inputs()
    changed=ThemeSnapshot.model_validate(dict(theme.model_dump(),theme_type=theme_type))
    t,l,primary,evidence=compute_theme('I0',[changed],names,features,StrategyConfig())
    assert t is None and l is None and primary is None

def test_selected_theme_uses_stable_id_when_max_t_ties_without_summing():
    names,theme,features=theme_inputs()
    first=theme.model_copy(update={'theme_id':'A'});second=theme.model_copy(update={'theme_id':'Z'})
    one=compute_theme('I0',[first],names,features,StrategyConfig())
    both=compute_theme('I0',[second,first],names,features,StrategyConfig())
    assert both[:3]==one[:3] and both[2]=='A' and len(both[3])==2

def test_one_price_and_daily_touch_never_supply_intraday_order_fields():
    bundle,_=market();bar=next(b for b in bundle.bars if b.instrument_id=='INDEPENDENT_000' and b.session==bundle.observation_date)
    one=bar.model_copy(update={key:bar.limit_up for key in ('open','high','low','close')})
    result=limit_event(one)
    assert result.one_price_up==TRUE and result.touched_not_closed==FALSE
    forbidden={'open_board_count','first_seal_time','fill_probability','executable','auction_price'}
    assert not forbidden.intersection(result.model_dump())

@pytest.mark.parametrize('break_kind,expected,quality',[
    ('missing',None,'MISSING_BREAK_UNKNOWN'),('suspended',0,'SUSPENSION_BREAK'),('no_limit',0,'RULE_NOT_APPLICABLE')])
def test_exchange_window_break_types_are_distinct(break_kind,expected,quality):
    bundle,_=market();bar=next(b for b in bundle.bars if b.instrument_id=='INDEPENDENT_000' and b.session==bundle.observation_date)
    prior=limit_event(bar)
    if break_kind=='missing':broken=limit_event(None,'INDEPENDENT_000',bar.session)
    elif break_kind=='suspended':broken=limit_event(bar,suspended=True)
    else:broken=limit_event(bar.model_copy(update={'limit_applicable':False}))
    series=annotate_streaks([prior,broken,prior])
    assert series[1].streak_exchange==expected and series[1].streak_quality==quality
    assert series[2].streak_exchange==(None if break_kind=='missing' else 1)
    count,state=count_window(series,3)
    if break_kind=='suspended':assert count==2 and state=='COMPLETE_WITH_KNOWN_SUSPENSION'
    else:assert count is None

def test_truncated_left_limit_run_is_lower_bound_not_invented_first_board():
    bundle,_=market();bar=next(b for b in bundle.bars if b.instrument_id=='INDEPENDENT_000' and b.session==bundle.observation_date)
    events=annotate_streaks([limit_event(bar)]*4)
    assert events[-1].streak_exchange is None and events[-1].streak_lower_bound==4
    assert events[-1].streak_quality=='LEFT_CENSORED_AT_LEAST_4'

def test_non_tick_price_cannot_be_rescued_by_near_limit_epsilon():
    bundle,_=market();bar=next(b for b in bundle.bars if b.instrument_id=='INDEPENDENT_000' and b.session==bundle.observation_date)
    event=limit_event(bar.model_copy(update={'close':bar.limit_up-Decimal('0.000000001')}))
    assert event.close_limit_up==UNKNOWN and event.quality=='OFF_TICK'

def test_historical_identity_has_no_current_snapshot_backfill():
    bundle,cutoff=market();current_only=bundle.universe.model_copy(update={
        'securities':tuple(s.model_copy(update={'effective_from':bundle.observation_date}) for s in bundle.universe.securities)})
    previous_day=bundle.sessions[-2]
    changed=bundle.model_copy(update={'universe':current_only,'observation_date':previous_day,
        'sessions':bundle.sessions[:-1],'bars':tuple(b for b in bundle.bars if b.session<=previous_day),
        'coverage_type':'RECONSTRUCTED_HISTORICAL'})
    result=run(changed,cutoff=datetime.combine(previous_day,time(20,30),TZ))
    assert result.items==() and result.emotion.score is None

def test_account_like_extra_fields_are_rejected_not_silently_used():
    bundle,_=market();payload=bundle.model_dump(mode='json');payload['cash']=100000
    with pytest.raises(ValueError):MarketInput.model_validate(payload)
