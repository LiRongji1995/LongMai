"""Independent small arithmetic goldens for R05 replay reporting, TEST_FIXTURE only."""
from datetime import date,datetime,timedelta
from decimal import Decimal
import pytest
from stockfish_core.yaogu.replay_statistics import price_statistics,statistics_table,mature_window,PV,FOCUS,BASELINE


def row(i,day='2026-08-31',value='0',status='MATURE_VALID',sid=PV):
    return {'logical_label_id':f'TEST_LABEL_{i}','observation_id':f'TEST_REV_{i}','instrument_id':f'TEST{i:03d}',
        'observation_date':day,'return_exact':value,'price_label_status':status,'strategy_id':sid,'horizon':1,
        'run_id':'TEST_RUN_'+day,'label_kind':'SIGNAL','selected_focus':False}


def test_zero_return_is_valid_denominator_but_not_up():
    result=price_statistics([row(0,value='0'),row(1,value='.10'),row(2,value='-.10')])
    assert result['mature_valid']==3 and result['zero']==1 and result['positive']==1 and result['negative']==1
    assert result['up_rate']==pytest.approx(1/3) and result['mean_return']==0
    assert result['median_return']==0


def test_date_equal_is_distinct_from_signal_pooled_when_counts_differ():
    rows=[row(0,'2026-08-31','.1')]+[row(i,'2026-09-01','-.1') for i in range(1,10)]
    result=price_statistics(rows,expected_dates=['2026-08-31','2026-09-01'])
    assert result['up_rate']==pytest.approx(.1) and result['date_equal_up_rate']==pytest.approx(.5)
    assert result['mean_return']==pytest.approx(-.08) and result['date_equal_mean_return']==0
    assert result['date_mean_range']==[-.1,.1]


def test_duplicate_label_revisions_must_be_selected_before_statistics():
    first=row(0,value='.1');revised={**first,'observation_id':'NEW_REVISION','return_exact':'.2'}
    with pytest.raises(ValueError,match='SELECT_REVISIONS_BEFORE_STATISTICS'):
        price_statistics([first,revised])


def test_unmatured_and_zero_signal_dates_remain_visible_without_fake_zero_return():
    dates=['2026-08-31','2026-09-01','2026-09-02']
    pending=[row(0,dates[0],None,'NOT_MATURED')]
    rows=statistics_table(pending,[],[],dates,[1],[PV,FOCUS,BASELINE]);table={r['strategy_id']:r for r in rows}
    pv=table[PV]['pooled_signals']
    assert pv['signals']==1 and pv['not_matured']==1 and pv['mature_valid']==0
    assert pv['up_rate'] is None and pv['mean_return'] is None
    assert pv['expected_observation_dates']==3 and pv['dates_without_valid_labels']==dates
    empty=table[FOCUS]['pooled_signals']
    assert empty['signals']==0 and empty['expected_observation_dates']==3 and empty['up_rate'] is None


def test_supplement_mature_window_uses_calendar_only_not_returns_or_coverage():
    calendar=[date(2026,8,3)+timedelta(days=i) for i in range(10)] # synthetic explicit exchange sessions
    attempts=[str(d) for d in calendar[:8]]
    expected=[str(d) for d in calendar[2:7]]
    selected=mature_window(calendar,attempts,str(calendar[-1]),3)
    assert selected==expected
    # The later missing/losing dates cannot be swapped for earlier winners.
    labels=[row(i,day,None if i%2 else '-.9','UNAVAILABLE' if i%2 else 'MATURE_VALID') for i,day in enumerate(attempts)]
    assert mature_window(calendar,attempts,str(calendar[-1]),3)==selected
    assert labels and selected[-1]==attempts[6]
    assert mature_window(calendar,attempts,str(calendar[-1]),20)==[]


def test_true_episode_first_excludes_continuing_episode_but_window_first_is_disclosed():
    from stockfish_core.yaogu.strategy_tracking import build_strategy_feedback_report
    cutoff = datetime.fromisoformat('2026-09-04T20:00:00+08:00')
    days = ['2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04']
    first = row(0, '2026-08-20', '-.10')
    repeat = {**row(1, '2026-09-01', '.10'), 'instrument_id': first['instrument_id']}
    new = row(2, '2026-09-02', '.20')
    labels = [{**value, 'selected_focus': True, 'evaluated_at': cutoff.isoformat(), 'status': 'MATURE',
               'metrics': {'close_reference_change': float(value['return_exact'])}} for value in [first, repeat, new]]
    runs = [{'run_id': label['run_id'], 'observation_date': label['observation_date']} for label in labels]
    tracking = [{'episodes': [{'episode_id': 'TEST_OLD_EPISODE', 'observation_date': '2026-08-20',
                               'run_id': first['run_id'], 'instrument_id': first['instrument_id']},
                               {'episode_id': 'TEST_NEW_EPISODE', 'observation_date': '2026-09-02'}],
                 'signals': [{'run_id': label['run_id'], 'instrument_id': label['instrument_id'],
                              'observation_date': label['observation_date'],
                              'episode_id': 'TEST_NEW_EPISODE' if label['instrument_id'] == new['instrument_id'] else 'TEST_OLD_EPISODE'}
                             for label in labels]}]
    table = statistics_table(labels, runs, tracking, days, [1], [PV, FOCUS])
    report = build_strategy_feedback_report(runs[-1], labels, tracking, cutoff, runs=runs,
                                             sessions=['2026-08-20', *days])
    feedback = report['rolling_windows']['5']['statistics']
    for result in [*table, *feedback]:
        assert result['pooled_signals']['signals'] == 2
        assert result['episode_count'] == 2
        assert result['continuing_episode_count'] == result['started_before_window_episode_count'] == 1
        assert result['started_in_window_episode_count'] == 1
        assert result['episode_first_signal']['signals'] == 1
        assert result['episode_first_signal']['mean_return'] == pytest.approx(.20)
        assert result['episode_window_first_signal']['signals'] == 2
        assert result['episode_window_first_signal']['mean_return'] == pytest.approx(.15)
    # Missing an older label never licenses replacing it with a profitable repeat.
    without_first = statistics_table(labels[1:], runs, tracking, days, [1], [PV])[0]
    assert without_first['episode_first_signal']['signals'] == 1
    assert without_first['episode_first_label_unavailable_count'] == 1
    all_history = statistics_table(labels, runs, tracking, ['2026-08-20', *days], [1], [PV])[0]
    assert all_history['episode_first_signal']['signals'] == 2
    assert all_history['episode_first_signal']['mean_return'] == pytest.approx(.05)
    # A surviving formal episode also preserves its original run association.
    tracking[0]['signals'] = tracking[0]['signals'][1:]
    retained_episode = statistics_table(labels, runs, tracking, ['2026-08-20', *days], [1], [PV])[0]
    assert retained_episode['episode_first_signal']['signals'] == 2
