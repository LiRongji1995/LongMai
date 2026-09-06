"""Transparent R05 price-label summaries; counts never include revisions twice."""
from collections import defaultdict
from decimal import Decimal
from statistics import median
from .strategy_tracking import _episode_signal_index, _episode_window_cohorts

PV='PV_OBSERVATION_V1'
FOCUS='PV_FOCUS_PREVIEW_V0.1'
BASELINE='ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC'


def price_statistics(rows,*,expected_dates=()):
    unique={r['logical_label_id']:r for r in rows}
    if len(unique)!=len(rows):raise ValueError('SELECT_REVISIONS_BEFORE_STATISTICS')
    values=sorted(Decimal(r['return_exact']) for r in rows if r.get('price_label_status')=='MATURE_VALID' and r.get('return_exact') is not None)
    pending=sum(r.get('price_label_status')=='NOT_MATURED' for r in rows)
    by_date=defaultdict(list)
    for r in rows:
        if r.get('price_label_status')=='MATURE_VALID' and r.get('return_exact') is not None:by_date[r['observation_date']].append(Decimal(r['return_exact']))
    date_means=[sum(v)/len(v) for v in by_date.values()]
    date_up=[Decimal(sum(x>0 for x in v))/len(v) for v in by_date.values()]
    return {'signals':len(rows),'unique_instruments':len({r['instrument_id'] for r in rows}),
            'signal_dates':len({r['observation_date'] for r in rows}),'expected_observation_dates':len(expected_dates),
            'mature_valid':len(values),'not_matured':pending,'missing_or_incomparable':len(rows)-len(values)-pending,
            'positive':sum(v>0 for v in values),'zero':sum(v==0 for v in values),'negative':sum(v<0 for v in values),
            'up_rate':sum(v>0 for v in values)/len(values) if values else None,
            'mean_return':float(sum(values)/len(values)) if values else None,
            'median_return':float(median(values)) if values else None,
            'p10_return':float(values[int((len(values)-1)*.1)]) if values else None,
            'p90_return':float(values[int((len(values)-1)*.9)]) if values else None,
            'date_equal_up_rate':float(sum(date_up)/len(date_up)) if date_up else None,
            'date_equal_mean_return':float(sum(date_means)/len(date_means)) if date_means else None,
            'valid_result_dates':sorted(by_date),'dates_without_valid_labels':sorted(set(expected_dates)-set(by_date)),
            'date_mean_range':[float(min(date_means)),float(max(date_means))] if date_means else None,
            'uncertainty':'INSUFFICIENT_DATES_FOR_STABLE_INFERENCE_DATE_AND_EPISODE_DEPENDENCE'}


def statistics_table(labels,runs,tracking,dates,horizons,strategy_ids):
    dates=list(dates);selected=[r for r in labels if r['observation_date'] in dates and r.get('label_kind','SIGNAL')=='SIGNAL']
    groups=defaultdict(list)
    for r in selected:
        groups[(r['strategy_id'],r['horizon'])].append(r)
        if r['strategy_id']==PV and r.get('selected_focus'):groups[(FOCUS,r['horizon'])].append(r)
    episode_index=_episode_signal_index(labels,tracking,runs)
    output=[]
    for sid in strategy_ids:
        for h in horizons:
            rows=groups[(sid,h)]
            cohorts=_episode_window_cohorts(rows,sid,h,dates,episode_index)
            pooled=price_statistics(rows,expected_dates=dates)
            relative=[]
            for d in dates:
                ours=price_statistics([r for r in rows if r['observation_date']==d])
                ref=price_statistics([r for r in groups[(BASELINE,h)] if r['observation_date']==d])
                if ours['mean_return'] is not None and ref['mean_return'] is not None:
                    relative.append({'date':d,'strategy_mature':ours['mature_valid'],'baseline_mature':ref['mature_valid'],
                                     'strategy_mean':ours['mean_return'],'baseline_mean':ref['mean_return'],
                                     'excess_mean':ours['mean_return']-ref['mean_return']})
            output.append({'strategy_id':sid,'horizon':h,'pooled_signals':pooled,
                           'episode_first_signal':{**price_statistics(cohorts['first'],expected_dates=dates),
                                                   'scope':'TRUE_EPISODE_FIRST_SIGNAL_DATE_WITHIN_WINDOW'},
                           'episode_window_first_signal':{**price_statistics(cohorts['window_first'],expected_dates=dates),
                                                   'scope':'FIRST_SIGNAL_OBSERVED_INSIDE_THIS_WINDOW_INCLUDES_CONTINUING_EPISODES'},
                           **{key:value for key,value in cohorts.items() if key not in {'first','window_first'}},
                           'episode_scope':'FORMAL_FOCUS_EPISODE_LINKED_SUBSET' if cohorts['episode_count'] else 'UNAVAILABLE_NO_FORMAL_EPISODE',
                           'episode_full_strategy_coverage':len(rows)>0 and cohorts['signals_without_episode']==0,
                           'same_date_eligible_benchmark':relative,
                           'date_equal_excess_mean':sum(r['excess_mean'] for r in relative)/len(relative) if relative else None})
    return output


def mature_window(calendar,attempt_dates,cutoff_date,horizon):
    """Calendar rule fixed before returns; never choose dates by field quality."""
    days=[str(d) for d in calendar]
    return [d for d in attempt_dates if days.index(d)+horizon<len(days) and days[days.index(d)+horizon]<=str(cutoff_date)][-5:]
