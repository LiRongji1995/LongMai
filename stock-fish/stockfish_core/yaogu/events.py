"""Tick-exact daily limit facts and exchange-session continuity, never intraday paths."""
from decimal import Decimal
from .contracts import LimitEvent

TRUE, FALSE, UNKNOWN, NA = 'TRUE', 'FALSE', 'UNKNOWN', 'NOT_APPLICABLE'

def limit_event(bar, instrument_id=None, session=None, suspended=False):
    iid=instrument_id or bar.instrument_id
    day=session or bar.session
    def result(values, quality):
        return LimitEvent(instrument_id=iid, session=day, close_limit_up=values[0], touched_limit_up=values[1], touched_not_closed=values[2], one_price_up=values[3], close_limit_down=values[4], quality=quality, source_refs=bar.source_refs if bar else ())
    if suspended:
        return result([NA]*5, 'SUSPENSION_BREAK')
    if bar is None: return result([UNKNOWN]*5, 'MISSING_BAR')
    if bar.limit_applicable is False: return result([NA]*5, 'RULE_NOT_APPLICABLE')
    prices=(bar.open,bar.high,bar.low,bar.close)
    if bar.quality == 'INVALID' or any(v is None for v in prices): return result([UNKNOWN]*5, 'INVALID_OHLC')
    if any(v/bar.tick_size != (v/bar.tick_size).to_integral_value() for v in prices): return result([UNKNOWN]*5, 'OFF_TICK')
    if not bar.low <= min(bar.open,bar.close) <= max(bar.open,bar.close) <= bar.high:
        return result([UNKNOWN]*5, 'INVALID_OHLC_ORDER')
    if bar.limit_applicable is None or not bar.limit_verified or bar.limit_up is None or bar.limit_down is None:
        return result([UNKNOWN]*5, 'LIMIT_RULE_UNVERIFIED')
    if any(v/bar.tick_size != (v/bar.tick_size).to_integral_value() for v in (bar.limit_up,bar.limit_down)):
        return result([UNKNOWN]*5, 'LIMIT_OFF_TICK')
    if bar.high > bar.limit_up or bar.low < bar.limit_down:
        return result([UNKNOWN]*5, 'PRICE_OUTSIDE_VERIFIED_LIMIT')
    closed=bar.close == bar.limit_up
    touched=bar.high == bar.limit_up
    return result([TRUE if v else FALSE for v in (closed,touched,touched and not closed,all(v==bar.limit_up for v in prices),bar.close==bar.limit_down)],'VALID')

def annotate_streaks(events):
    result=[]
    count=0
    left_unknown=True
    for event in events:
        if event.close_limit_up == TRUE:
            count+=1
            value=None if left_unknown else count
            quality=f'LEFT_CENSORED_AT_LEAST_{count}' if left_unknown else 'EXACT'
        elif event.close_limit_up == FALSE:
            count=0; left_unknown=False; value=0; quality='KNOWN_NON_LIMIT_BREAK'
        elif event.close_limit_up == NA:
            count=0; left_unknown=False; value=0; quality=event.quality
        else:
            count=0; left_unknown=True; value=None; quality='MISSING_BREAK_UNKNOWN'
        result.append(event.model_copy(update={'streak_exchange':value,'streak_lower_bound':count if event.close_limit_up==TRUE else value,'streak_quality':quality}))
    return result

def count_window(events, n):
    window=events[-n:]
    if len(window) != n: return None, 'SHORT_HISTORY'
    if any(e.close_limit_up == UNKNOWN for e in window): return None, 'UNKNOWN_EVENTS'
    if any(e.close_limit_up == NA and e.quality!='SUSPENSION_BREAK' for e in window): return None, 'NON_APPLICABLE_WINDOW'
    return sum(e.close_limit_up == TRUE for e in window), 'COMPLETE_WITH_KNOWN_SUSPENSION' if any(e.quality=='SUSPENSION_BREAK' for e in window) else 'COMPLETE_APPLICABLE'

class LimitEventEngine:
    limit_event=staticmethod(limit_event)
    annotate_streaks=staticmethod(annotate_streaks)
    count_window=staticmethod(count_window)
