"""Pre-outcome support selection and frozen-family date statistics assembly."""
from collections import Counter
from statistics import mean, median


ESTIMANDS=('DECLARED_UNIVERSE_ACCOUNT','CONDITIONAL_SIGNAL','COMMON_DECIDABLE_SIGNAL','NEWLY_DECIDABLE_SIGNAL','SETUP_START','SETUP_ACCELERATION','SETUP_HIGH_LEVEL_REPAIR','SETUP_TREND_PULLBACK')


def preregistered_family(protocol):
    pairs={name+'-'+definition['parent']:[name,definition['parent']] for name,definition in protocol['groups'].items() if definition['parent']}
    left,right=protocol['primary_comparison'];pairs[left+'-'+right]=[left,right]
    return dict(contract='R1_COMPARISON_FAMILY_V2',pairs=pairs,estimands=list(ESTIMANDS),minimum_complete_blocks=2)


def assemble(results, execution, signal_execution, manifest, tape, estimator):
    protocol=manifest['effective_spec']; primary=str(protocol['primary_slippage_bps'])
    family=manifest['comparison_family']
    comparisons=family['pairs']
    sessions=tape.get('sessions',[]) if tape else []
    indexes={day:i for i,day in enumerate(sessions)}
    series={}; descriptions={}
    for name,(left,right) in comparisons.items():
        for estimand in family['estimands']:
            key=name+':'+estimand; pairs=[]; counts=Counter(); contributions=[]; eligibility_checks=[]
            for result in ([] if estimand=='DECLARED_UNIVERSE_ACCOUNT' else results):
                day=result['observation_date']; a=result['groups'].get(left);b=result['groups'].get(right)
                difference=None;delay=0
                if a and b and day in indexes:
                    common=set(a['coverage']['support']) & set(b['coverage']['support'])
                    common={iid for iid in common if a['coverage']['support'][iid]['selection_decidable'] and b['coverage']['support'][iid]['selection_decidable']}
                    newly={iid for iid,x in a['coverage']['support'].items() if x['selection_decidable'] and not b['coverage']['support'].get(iid,{}).get('selection_decidable',False)}
                    counts['common_decidable_security_days']+=len(common)
                    counts['selected_overlap']+=len(set(a['selected_ids']) & set(b['selected_ids']))
                    daily=[]
                    for group,data in ((left,a),(right,b)):
                        allowed=None
                        if estimand=='COMMON_DECIDABLE_SIGNAL':allowed=common
                        elif estimand=='NEWLY_DECIDABLE_SIGNAL':allowed=newly
                        elif estimand.startswith('SETUP_'):allowed=set(data.get('setup_selected_ids',{}).get(estimand[6:],[]))
                        records=[r for r in signal_execution.get(group,{}).get(primary,{}).get('records',[]) if r['signal_session']==day and (allowed is None or r['instrument_id'] in allowed)]
                        counts[group+'_signals']+=len(records)
                        for rec in records:
                            counts[rec['status']]+=1
                            check=delay_eligibility([rec],sessions,manifest.get('account_evaluation_window',[None,sessions[-1]])[1] or sessions[-1],protocol['block_length_sessions'])
                            eligibility_checks.append(dict(group=group,**check))
                            delay=max(delay,check['max_exit_delay_sessions'])
                            if not check['eligible']:delay=max(delay,protocol['block_length_sessions'])
                            contributions.append(dict(group=group,date=day,instrument_id=rec['instrument_id'],status=rec['status'],net_return=rec.get('net_return'),capital_occupied_cny=rec.get('capital_occupied_cny'),capital_commitment_cny=rec.get('capital_commitment_cny'),exit_delay_sessions=rec.get('exit_delay_sessions',rec.get('pending_exit_delay_sessions'))))
                        # Missing/blocked exits retained; never choose support by outcomes.
                        daily.append(mean(r['net_return'] for r in records) if records and all(r.get('net_return') is not None and r['status']=='CLOSED' for r in records) else None)
                    if all(x is not None for x in daily):difference=daily[0]-daily[1]
                pairs.append(dict(date=day,session_index=indexes.get(day),difference=difference,exit_delay=delay))
            window_diagnostics=None
            if estimand=='DECLARED_UNIVERSE_ACCOUNT':
                pairs,window_diagnostics=account_pairs(results,execution,manifest,tape,left,right)
                for group in (left,right):
                    for rec in execution.get(group,{}).get(primary,{}).get('records',[]):
                        if rec.get('actual_exit_session') and manifest['account_evaluation_window'][1] and rec['actual_exit_session']>manifest['account_evaluation_window'][1]:
                            rec=dict(rec,status='CENSORED_AT_WINDOW_END',net_return=None,exit_delay_sessions=None)
                        contributions.append(dict(group=group,date=rec['signal_session'],instrument_id=rec['instrument_id'],status=rec['status'],net_return=rec.get('net_return'),capital_occupied_cny=rec.get('capital_occupied_cny'),capital_commitment_cny=rec.get('capital_commitment_cny'),exit_delay_sessions=rec.get('exit_delay_sessions',rec.get('pending_exit_delay_sessions'))))
            series[key]=pairs
            known=[p['difference'] for p in pairs if p['difference'] is not None]
            numeric=[c for c in contributions if c['net_return'] is not None]
            by_security=Counter()
            for c in numeric:by_security[c['instrument_id']]+=abs(c['net_return'])
            total=sum(by_security.values())
            group_summaries={}
            for group in (left,right):
                records=[x for x in contributions if x['group']==group];closed=[x['net_return'] for x in records if x['net_return'] is not None]
                n=len(records);unfilled=sum(x['status'] in ('ENTRY_NOT_FILLED','NO_WHOLE_LOT_BUDGET','CASH_CONSTRAINT_NO_FILL') for x in records)
                group_summaries[group]=dict(signal_count=n,stock_count=len({x['instrument_id'] for x in records}),signal_dates=len({x['date'] for x in records}),unfilled_rate=unfilled/n if n else None,complete_return_mean=mean(closed) if closed and len(closed)==n else None,observed_closed_subset_mean=mean(closed) if closed else None,closed_subset_count=len(closed),median_closed_subset=median(closed) if closed else None,tail_closed_subset=min(closed) if closed else None,max_exit_delay_sessions=max((x['exit_delay_sessions'] for x in records if x['exit_delay_sessions'] is not None),default=None),unresolved_capital_cny=sum(float(x['capital_occupied_cny'] or 0) for x in records),policy='CLOSED_SUBSET_DESCRIPTIVE_ONLY; ALL_SIGNALS_RETAINED')
            descriptions[key]=dict(eligibility_checks=eligibility_checks,window=window_diagnostics,group_summaries=group_summaries,estimand=estimand,left=left,right=right,counts=dict(counts),sample_days=len(pairs),known_days=len(known),
                mean=mean(known) if known else None,median=median(known) if known else None,tail_min=min(known) if known else None,
                descriptive_subset_only=len(known)!=len(pairs) or any(p.get('inference_eligible') is False for p in pairs),date_contributions=pairs,security_contributions=contributions,
                max_security_absolute_contribution_share=max(by_security.values())/total if total else None,
                policy='BOTH_SIGNAL_DATES_CONDITIONAL; NO_ZERO_IMPUTATION; OVERLAPPING_SETUPS_NOT_ADDITIVE' if estimand!='DECLARED_UNIVERSE_ACCOUNT' else 'OVERLAPPING_HOLDINGS_ACCOUNT; CASH_DAYS_ONLY_IF_COMPLETE')
    effects=estimator(series,seed=protocol['seed'],block_length=protocol['block_length_sessions'],repetitions=protocol['bootstrap_repetitions'])
    for key,description in descriptions.items():
        effects[key]['signal_eligibility_checks']=description['eligibility_checks']
        if description.get('window'):
            effects[key]['eligibility']={g:d['eligibility'] for g,d in description['window']['groups'].items()}
    return dict(contract='R1_PAIRED_RESULT_STATISTICS_V3',status='ESTIMATED' if any(v['status']=='ESTIMATED' for v in effects.values()) else 'UNAVAILABLE',
        primary_slippage_bps=protocol['primary_slippage_bps'],family_size=len(series),effects=effects,descriptions=descriptions,
        independent_validation='UNAVAILABLE_NO_INDEPENDENT_HOLDOUT',market_edge='NOT_ESTABLISHED')


def freeze_windows(signal_days, tape, exit_offset, registered_end=None):
    days=sorted(signal_days); sessions=tape.get('sessions',[]) if tape else []
    last=sessions.index(days[-1]) if days[-1] in sessions else None
    planned=sessions[last+exit_offset] if last is not None and last+exit_offset<len(sessions) else None
    end=registered_end or planned
    if registered_end and (planned is None or registered_end<planned or registered_end not in sessions):raise ValueError('INVALID_REGISTERED_ACCOUNT_END')
    return dict(contract='R1_ACCOUNT_WINDOW_V3',signal_window=[days[0],days[-1]],planned_completion_end=planned,
        account_evaluation_window=[days[0],end],evidence_as_of=tape.get('as_of') if tape else None,
        tail_policy='NO_NEW_SIGNALS; COMMON_PREDECLARED_END; NO_AUTOMATIC_EXTENSION',
        calendar_hash=__import__('hashlib').sha256(str(sessions).encode()).hexdigest())


def delay_eligibility(records,sessions,end,block_length):
    issues=[];maximum=0
    for rec in records:
        if rec.get('status') in ('ENTRY_NOT_FILLED','NO_WHOLE_LOT_BUDGET','ALREADY_HELD_OR_DUPLICATE_NO_EXTENSION','CASH_CONSTRAINT_NO_FILL','LATE_SIGNAL'):continue
        planned=rec.get('planned_exit_session');actual=rec.get('actual_exit_session')
        issue=None;delay=None
        if planned not in sessions:issue='EXIT_DELAY_UNKNOWN'
        elif actual is not None and actual in sessions and actual<=end:
            delay=sessions.index(actual)-sessions.index(planned)
            if rec.get('exit_delay_sessions')!=delay:issue='EXIT_DELAY_UNKNOWN_OR_CONFLICT'
        elif rec.get('entry_price') is not None and end in sessions:
            delay=max(0,sessions.index(end)-sessions.index(planned))
            issue='PENDING_EXIT_CENSORED'
        else:issue='EXIT_DELAY_UNKNOWN'
        if delay is not None:
            maximum=max(maximum,delay)
            if delay>=block_length:issue='EXIT_DELAY_REACHES_BLOCK_LENGTH'
        if issue:issues.append(dict(trade_id=rec.get('trade_id',rec.get('instrument_id','UNKNOWN')+':'+rec.get('signal_session','UNKNOWN')),reason=issue,delay_sessions=delay,block_length_sessions=block_length))
    return dict(eligible=not issues,issues=issues,max_exit_delay_sessions=maximum,block_length_sessions=block_length)


def account_pairs(results, execution, manifest, tape, left, right):
    protocol=manifest['effective_spec'];primary=str(protocol['primary_slippage_bps'])
    sessions=tape.get('sessions',[]) if tape else []
    window=manifest.get('account_evaluation_window',[None,None]);start,end=window
    signal_window=manifest.get('signal_window',manifest.get('observed_interval',[None,None]))
    expected=[d for d in sessions if start is not None and end is not None and start<=d<=end]
    signal_days=[d for d in sessions if signal_window[0] and signal_window[0]<=d<=signal_window[1]]
    present={x['observation_date'] for x in results};missing_inputs=sorted(set(signal_days)-present)
    runs=[execution.get(g,{}).get(primary,{}) for g in (left,right)]
    diagnostics={};curves=[];initials=[];eligible=True
    for group,run in zip((left,right),runs):
        curve=run.get('account_curve',[]);points={p['session']:p for p in curve};curves.append(points)
        records=[r for r in run.get('records',[]) if end and r.get('signal_session','9999')<=end]
        observed_end=min(end,curve[-1]['session']) if end and curve else ''
        check=delay_eligibility(records,sessions,observed_end,protocol['block_length_sessions'])
        missing=[d for d in expected if d not in points or points[d].get('nav') is None]
        open_at_end=[r.get('trade_id',r.get('instrument_id')) for r in records if r.get('entry_price') is not None and (not r.get('actual_exit_session') or r['actual_exit_session']>end)]
        navs=[float(points[d]['nav']) for d in expected if d in points and points[d].get('nav') is not None]
        first_index=sessions.index(start) if start in sessions else 0
        prior=points.get(sessions[first_index-1],{}).get('nav') if first_index else protocol['initial_nav_cny']
        if start and run.get('account_start_session')==start:prior=protocol['initial_nav_cny']
        initial=float(prior) if prior is not None else None
        initials.append(initial)
        cumulative=navs[-1]/initial-1 if navs and not missing and initial else None
        peak=initial;drawdown=0.0
        if initial:
            for nav in navs:peak=max(peak,nav);drawdown=min(drawdown,nav/peak-1)
        diagnostics[group]=dict(mark_to_market_return=cumulative,account_cumulative_return=cumulative if not open_at_end and check['eligible'] and not missing_inputs and run.get('strategy_evaluation_status')=='EVALUABLE' else None,account_max_drawdown=drawdown if cumulative is not None else None,initial_nav=initial,ending_nav=navs[-1] if navs and not missing else None,eligibility=check,missing_sessions=missing,unsettled_trade_ids=open_at_end,curve_window=[curve[0]['session'],curve[-1]['session']] if curve else [None,None],observed_session_count=len(expected)-len(missing))
        eligible=eligible and check['eligible'] and not missing and not missing_inputs and run.get('strategy_evaluation_status')=='EVALUABLE'
    pairs=[]
    for day in expected:
        values=[]
        for points,initial in zip(curves,initials):
            idx=sessions.index(day);previous=points.get(sessions[idx-1],{}).get('nav') if idx else protocol['initial_nav_cny']
            if day==start:previous=initial
            current=points.get(day,{}).get('nav')
            values.append(float(current)/float(previous)-1 if current is not None and previous is not None else None)
        pairs.append(dict(date=day,session_index=sessions.index(day),difference=values[0]-values[1] if all(v is not None for v in values) else None,exit_delay=max(d['eligibility']['max_exit_delay_sessions'] for d in diagnostics.values()),inference_eligible=bool(eligible and expected)))
    return pairs,dict(signal_window=signal_window,account_window=window,planned_completion_end=manifest.get('planned_completion_end'),evidence_as_of=manifest.get('evidence_as_of'),expected_session_count=len(expected),first_session=expected[0] if expected else None,last_session=expected[-1] if expected else None,tail_session_count=sum(d>signal_window[1] for d in expected),missing_signal_sessions=missing_inputs,groups=diagnostics)
