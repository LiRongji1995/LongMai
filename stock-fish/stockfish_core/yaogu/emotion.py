"""Breadth and frozen prior-limit cohorts with honest missing denominators."""
from statistics import mean, median
from .contracts import MarketEmotionSnapshot
from .config import R
from .events import TRUE, FALSE, UNKNOWN, NA
from .features import price_comparable

def classify_emotion(score,d,down_fraction,n,m):
    if score is None:return 'UNKNOWN'
    if (d is not None and d<=-12 and score<55) or (down_fraction>=.008 and n>=5 and m is not None and m<=-.02):return 'RETREAT'
    if score<30:return 'COLD'
    if 30<=score<50 and d is not None and d>=8:return 'REPAIR'
    if score>=75:return 'HOT'
    if score>=50 and d is not None and d>=0:return 'FERMENT'
    return 'SPLIT'

def compute_emotion(day,cutoff,current_pool,previous_pool,states,previous_states,bars,previous_bars,events,previous_events,previous_emotions,universe_complete,expected_count,config,previous_identity_missing=(),*,partition=None,history_selection=None):
    # pool includes eligible known suspensions for cohort preservation; N only actual trading.
    tradable=[i for i in current_pool if states[i].suspended is False]
    observed=[i for i in tradable if i in bars and i in events and events[i].close_limit_up in (TRUE,FALSE) and events[i].close_limit_down in (TRUE,FALSE) and bars[i].reference_prev_close is not None]
    partition=dict(partition or {'master_expected':max(expected_count or 0,len(states)),
        'identity_observed':len(states),'eligible':len(current_pool),'known_excluded':len(states)-len(current_pool),
        'eligibility_unknown':max(0,(expected_count or 0)-len(states))})
    expected=partition['master_expected']
    N=len(observed)
    coverage=N/len(tradable) if tradable else 0
    status_known=partition['eligible']+partition['known_excluded']
    status_fraction=status_known/expected if expected else 0
    reasons=[]
    partition.update(price_observed=N,price_missing=len(tradable)-N,
                     price_observed_ids=observed,price_missing_ids=sorted(set(tradable)-set(observed)))
    cov={**partition,'N_expected':expected,'N_status_known':status_known,'N_tradable_expected':len(tradable),'N_price_observed':N,'price_coverage':coverage,'status_coverage':status_fraction,'universe_complete':universe_complete,'previous_identity_missing':list(previous_identity_missing)}
    prior_status_complete=all(s.st is not None and s.suspended is not None and s.delisting_period is not None for s in previous_states.values() if s.exchange in ('SSE','SZSE') and s.board=='MAIN' and s.security_type=='A_COMMON')
    prior_complete=universe_complete and bool(previous_states) and not previous_identity_missing and prior_status_complete and all(i in previous_events and previous_events[i].close_limit_up != UNKNOWN for i in previous_pool)
    cohort=sorted(i for i in previous_pool if i in previous_events and previous_events[i].close_limit_up==TRUE)
    cohort_verified=prior_complete and all(i in states and states[i].suspended is not None and (states[i].suspended or i in events and events[i].close_limit_up in (TRUE,FALSE,NA)) for i in cohort)
    n=len(cohort);s=0;returns=[];suspended=[]
    for i in cohort:
        if i not in states:continue
        if states[i].suspended:
            suspended.append(i);continue
        if i in events:s+=int(events[i].close_limit_up==TRUE)
        if price_comparable([previous_bars.get(i),bars.get(i)]):
            returns.append(float(bars[i].comparable_close/previous_bars[i].comparable_close-1))
    u=sum(events[i].close_limit_up==TRUE for i in observed)
    dcount=sum(events[i].close_limit_down==TRUE for i in observed)
    b=sum(bars[i].close>bars[i].reference_prev_close for i in observed)/N if N else None
    raw_p=s/n if n and cohort_verified else None
    smooth=(s+1)/(n+2) if cohort_verified else None
    m=median(returns) if returns else None
    m_reason='OBSERVED'
    if not n and prior_complete:m_reason='PRIOR_NO_EVENT'
    elif n and len(suspended)==n:m_reason='PRIOR_NO_TRADABLE_EVENT'
    elif not returns:m_reason='COMPARABLE_COHORT_PRICE_UNAVAILABLE'
    comp={'p':R(smooth,.10,.50),'m':R(m,-.03,.05) if m is not None else 50.0 if m_reason in ('PRIOR_NO_EVENT','PRIOR_NO_TRADABLE_EVENT') else None,
          'up_fraction':R(u/N,.003,.020) if N else None,'breadth':R(b,.30,.70),'down_fraction':100-R(dcount/N,.001,.010) if N else None}
    if not universe_complete:reasons.append('UNIVERSE_INCOMPLETE')
    if coverage<config.thresholds.market_coverage_fraction or status_fraction<config.thresholds.market_coverage_fraction:reasons.append('MARKET_COVERAGE_BELOW_98_PERCENT')
    if not prior_complete:reasons.append('YESTERDAY_EVENT_UNIVERSE_INCOMPLETE')
    if previous_identity_missing:reasons.append('YESTERDAY_IDENTITY_COVERAGE_INCOMPLETE')
    if not cohort_verified:reasons.append('YESTERDAY_LIMIT_COHORT_NOT_FULLY_VERIFIED')
    if any(v is None for v in comp.values()):reasons.append('REQUIRED_EMOTION_COMPONENT_UNAVAILABLE')
    score=None if reasons else sum(comp[k]*w for k,w in (('p',.30),('m',.25),('up_fraction',.20),('breadth',.15),('down_fraction',.10)))
    prev=[e for e in previous_emotions if e.session<day and e.as_of<=cutoff and e.score is not None]
    # The selector freezes exactly one published version for each of t-1,t-2,t-3.
    # A partial history never substitutes a shorter mean; current M remains valid.
    delta=score-mean(e.score for e in prev) if score is not None and len(prev)==3 and history_selection and history_selection['status']=='COMPLETE' else None
    promotion={}
    for family,selector in [('ONE_TO_TWO',lambda c:c==1),('TWO_TO_THREE',lambda c:c==2),('HIGH_BOARD',lambda c:c is not None and c>=3)]:
        group=[i for i in cohort if selector(previous_events[i].streak_exchange)]
        hits=sum(i in events and events[i].close_limit_up==TRUE and i not in suspended for i in group)
        promotion[family]={'n':len(group),'s':hits if cohort_verified else None,'raw_rate':hits/len(group) if group and cohort_verified else None}
    streaks=[events[i].streak_exchange for i in observed if events[i].streak_exchange is not None]
    reading={'N':N,'U':u,'D':dcount,'up_fraction':u/N if N else None,'down_fraction':dcount/N if N else None,'breadth':b,'cohort_ids':cohort,'cohort_n':n if prior_complete else None,'cohort_s':s if cohort_verified else None,
             'cohort_raw_p':raw_p,'cohort_smoothed_p':smooth,'cohort_m':m,'cohort_m_n':len(returns),'cohort_suspended_ids':suspended,'cohort_m_status':m_reason,'cohort_p_status':'PRIOR_NO_EVENT' if prior_complete and not n else 'OBSERVED' if cohort_verified else 'UNAVAILABLE',
             'd':delta,'delta_status':'COMPLETE' if delta is not None else 'UNKNOWN_DELTA','promotion':promotion,'touched_not_closed_count':sum(events[i].touched_not_closed==TRUE for i in observed),'highest_exact_board':max(streaks,default=0),'ladder':{str(c):streaks.count(c) for c in sorted(set(streaks)) if c>0}}
    state=classify_emotion(score,delta,dcount/N if N else 0,n,m)
    return MarketEmotionSnapshot(session=day,as_of=cutoff,score=score,state=state,components=comp,readings=reading,coverage=cov,reasons=tuple(reasons),history_selection=history_selection or {})

class EmotionEngine:
    compute=staticmethod(compute_emotion)
