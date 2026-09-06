"""Past-only feature definitions with explicit units and completeness."""
from statistics import mean, median
from .contracts import FeatureValue
from .events import TRUE, FALSE, UNKNOWN, NA, count_window

def price_comparable(bars):
    return bool(bars) and all(b is not None and b.quality=='VALID' and b.close is not None and b.comparable_close is not None and b.comparable_basis for b in bars) and len({b.comparable_basis for b in bars}) == 1

def build_features(instrument_id, sessions, bars, events, cutoff):
    out={}
    refs=tuple(sorted({r for b in bars if b for r in b.source_refs}))
    # An invalid source row cannot silently contribute amount/volume/turnover
    # or a leadership denominator while only its close event is quarantined.
    bars=[None if b is not None and b.quality=='INVALID' else b for b in bars]
    def put(k,v,unit='fraction',window='t',quality=None):
        out[k]=FeatureValue(value=v,unit=unit,window=window,as_of=cutoff,source_refs=refs,quality=quality or ('VALID' if v is not None else 'UNAVAILABLE'))
    current=bars[-1] if bars else None
    current_event=events[-1]
    put('streak_exchange',current_event.streak_exchange,'sessions','strict continuous',current_event.streak_quality)
    put('streak_lower_bound',current_event.streak_lower_bound,'sessions','strict continuous lower bound',current_event.streak_quality)
    for key in ('close_limit_up','touched_limit_up','touched_not_closed','one_price_up','close_limit_down'):
        put(key,getattr(current_event,key),'state','t',current_event.quality)
    for n in (3,5,10,20):
        count,quality=count_window(events,n)
        put(f'M{n}',count,'events',f'last {n} exchange sessions',quality)
    last60=events[-60:]
    cmax=None
    if len(last60)==60 and all(e.close_limit_up != UNKNOWN and e.streak_exchange is not None for e in last60):
        cmax=max(e.streak_exchange for e in last60)
    put('cmax60',cmax,'sessions','last 60 exchange sessions','EXACT' if cmax is not None else 'UNKNOWN_OR_LEFT_CENSORED')
    count60,q60=count_window(events,60)
    put('limit_up_count_last60',count60,'events','last 60 exchange sessions',q60)
    for key,field in (('amount_ratio_5','amount_cny'),('volume_ratio_5','volume_shares')):
        prev=bars[-6:-1]
        valid=len(prev)==5 and all(b is not None and getattr(b,field) is not None for b in prev)
        denom=mean(float(getattr(b,field)) for b in prev) if valid else None
        today=getattr(current,field) if current else None
        put(key,float(today)/denom if today is not None and denom and denom>0 else None,'ratio','t / mean(t-5:t-1)')
    turnover=None; turnover_quality='UNVERIFIED_FLOAT_OR_TURNOVER'
    if current:
        if current.turnover_fraction is not None and current.turnover_basis:
            turnover=current.turnover_fraction;turnover_quality='VERIFIED_PROVIDER_BASIS'
        elif current.volume_shares is not None and current.float_shares is not None and current.float_shares_as_of is not None and current.float_shares_as_of <= min(cutoff,current.event_time):
            turnover=float(current.volume_shares/current.float_shares);turnover_quality='VERIFIED_FLOAT_SHARES'
    put('turnover_fraction',turnover,quality=turnover_quality)
    valid=current is not None and all(getattr(current,k) is not None for k in ('open','high','low','close')) and current.low<=min(current.open,current.close)<=max(current.open,current.close)<=current.high
    location=shadow=amplitude=None
    if valid:
        span=current.high-current.low
        location=float((current.close-current.low)/span) if span else (1.0 if current_event.one_price_up==TRUE else 0.0 if current_event.close_limit_down==TRUE else .5)
        shadow=float((current.high-max(current.open,current.close))/span) if span else 0.0
        amplitude=float(span/current.reference_prev_close) if current.reference_prev_close else None
    put('close_location',location)
    put('upper_shadow',shadow)
    put('amplitude',amplitude)
    put('single_price',bool(valid and current.high==current.low),'boolean')
    for k in ('open','high','low','close','reference_prev_close','amount_cny','volume_shares','comparable_close'):
        value=getattr(current,k) if current else None
        put(k,float(value) if value is not None else None,'CNY' if 'volume' not in k else 'shares')
    for n in (3,5,10):
        window=bars[-(n+1):]
        valid=len(window)==n+1 and price_comparable(window)
        put(f'ret{n}',float(window[-1].comparable_close/window[0].comparable_close-1) if valid else None,window=f'{n} exchange sessions')
    recent=bars[-5:]
    put('MA5',mean(float(b.comparable_close) for b in recent) if len(recent)==5 and price_comparable(recent) else None,'CNY comparable','5 exchange sessions')
    recent=bars[-20:]
    put('median_amount20',median(float(b.amount_cny) for b in recent) if len(recent)==20 and all(b is not None and b.amount_cny is not None for b in recent) else None,'CNY','20 exchange sessions')
    last_end=None
    # Find the last completed strict run: its next exchange session is known non-limit/NA.
    for idx in range(len(events)-2,-1,-1):
        if events[idx].close_limit_up==TRUE and events[idx+1].close_limit_up in (FALSE,NA):
            last_end=idx;break
    previous=events[last_end] if last_end is not None else None
    put('previous_run_streak',previous.streak_exchange if previous else None,'sessions','last completed run')
    put('sessions_since_run_end',len(events)-1-last_end if last_end is not None else None,'sessions','last completed run')
    end_close=None
    if last_end is not None and price_comparable([bars[last_end],current]):
        end_close=float(bars[last_end].comparable_close)
    put('previous_run_end_close',end_close,'CNY comparable','last completed run')
    put('previous_run_end_session',str(sessions[last_end]) if last_end is not None else None,'exchange session','last completed run')
    # Mature labels only: current event has no next session yet and is never counted in n.
    start=max(0,len(events)-250)
    history_events=events[start:]
    history_valid=len(history_events)>=60 and all(e.close_limit_up != UNKNOWN for e in history_events)
    n=s=censored=0
    for idx in range(start,len(events)-1):
        if events[idx].close_limit_up!=TRUE: continue
        pair=bars[idx:idx+2]
        if events[idx+1].quality=='SUSPENSION_BREAK' or not price_comparable(pair): censored+=1;continue
        n+=1
        s+=int(pair[1].comparable_close > pair[0].comparable_close)
    put('G_event_n',n if history_valid else None,'events','mature next-session close, <=250 sessions')
    put('G_success_s',s if history_valid else None,'events','mature next-session close, <=250 sessions')
    put('G_censored',censored,'events','mature next-session unavailable')
    put('G_mature_limit_event_count',n+censored if history_valid else None,'events','mature event identities, observed plus censored')
    put('G_history_sessions',len(history_events),'sessions','actual available <=250 sessions')
    history_status='HISTORY_UNAVAILABLE' if not history_valid else 'ALL_HISTORICAL_EVENTS_CENSORED' if not n and censored else 'PRIOR_NO_EVENT' if not n else 'OBSERVED'
    put('G_success_rate',s/n if history_valid and n else None,window='mature events',quality=history_status)
    put('G_smoothed_rate',(s+1)/(n+2) if history_valid and (n or not censored) else None,window='Beta(1,1)',quality=history_status)
    return out

class PriceVolumeFeatures:
    build=staticmethod(build_features)
