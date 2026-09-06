"""Independent paper-policy examples. TEST_FIXTURE, never market evidence."""
from datetime import datetime,time
from decimal import Decimal as D
from tests.yaogu.fixtures import make_bundle,TZ
from stockfish_core.yaogu.paper_execution import evaluate_paper_execution,summarize_paper


def example():
    b,t=make_bundle(1);day=b.sessions[-4]
    bar=next(x for x in b.bars if x.session==day)
    run={'run_id':'TEST_PAPER','observation_date':str(day),'results':{'PV_OBSERVATION_V1':{'items':[
        {'instrument_id':'TEST000','rank':1,'comparable_close':str(bar.comparable_close),'comparable_basis':bar.comparable_basis}]}},
        'selection_results':{}}
    return b,t,run


def test_entry_holding_day_separate_from_signal_target_and_exact_cost():
    b,t,r=example();row=evaluate_paper_execution(r,b,t,horizons=(1,))[0]
    assert row['entry_attempt_date']==str(b.sessions[-3])
    assert row['signal_price_target_date']==str(b.sessions[-3])
    assert row['mark_session']==row['exit_date']
    assert row['mark_to_market_return']==row['gross_return']
    assert row['planned_exit_date']==str(b.sessions[-2])
    assert row['closed']
    entry=next(x for x in b.bars if x.session==b.sessions[-3])
    exit=next(x for x in b.bars if x.session==b.sessions[-2])
    expected=(exit.close*D('0.9985'))/(entry.open*D('1.0015'))-1
    assert abs(D(row['net_return_by_cost_bps']['30'])-expected)<D('1e-25')


def test_no_limit_evidence_retains_attempt_as_unverified():
    b,t,r=example();b=b.model_copy(update={'bars':tuple(x.model_copy(update={'limit_verified':False}) for x in b.bars)})
    row=evaluate_paper_execution(r,b,t,horizons=(1,))[0]
    assert row['attempted'] and row['status']=='FILL_UNVERIFIED' and not row['closed']
    result=summarize_paper([row]);assert result['attempted']==1 and result['closed']==0
    assert result['cost_scenarios']['30']['win_rate'] is None


def test_blocked_exit_stays_open_and_in_attempt_denominator():
    b,t,r=example();target=b.sessions[-2]
    bars=[]
    for bar in b.bars:
        if bar.session>=target:
            low=bar.limit_down
            bar=bar.model_copy(update={'open':low,'high':low,'low':low,'close':low,
                                      'comparable_open':low,'comparable_high':low,'comparable_low':low,'comparable_close':low})
        bars.append(bar)
    b=b.model_copy(update={'bars':tuple(bars)})
    row=evaluate_paper_execution(r,b,t,horizons=(1,))[0]
    assert row['status']=='OPEN_BLOCKED' and row['entry_confirmed'] and not row['closed']
    assert len(row['blocked_exit_sessions'])==2 and row['mark_to_market_return'] is not None
    result=summarize_paper([row]);assert result['attempted']==1 and result['states']['OPEN_BLOCKED']==1


def test_blocked_exit_followed_by_actual_later_model_exit():
    b,t,r=example();target=b.sessions[-2]
    b=b.model_copy(update={'bars':tuple(x.model_copy(update={'limit_verified':False}) if x.session==target else x for x in b.bars)})
    row=evaluate_paper_execution(r,b,t,horizons=(1,))[0]
    assert row['closed'] and row['delayed_exit'] and row['exit_date']==str(b.sessions[-1])
    assert len(row['blocked_exit_sessions'])==1


def test_unmature_entry_never_attempted_and_large_h_kept():
    b,t,r=example();cutoff=datetime.combine(b.sessions[-4],time(20),TZ)
    rows=evaluate_paper_execution(r,b,cutoff,horizons=(1,61))
    assert all(x['status']=='NOT_MATURED' and not x['attempted'] for x in rows)
    assert rows[-1]['holding_horizon']==61 and rows[-1]['planned_exit_date'] is None
