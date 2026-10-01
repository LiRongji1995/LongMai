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
            key=name+':'+estimand; pairs=[]; counts=Counter(); contributions=[]
            for result in results:
                day=result['observation_date']; a=result['groups'].get(left);b=result['groups'].get(right)
                difference=None;delay=0
                if a and b and day in indexes:
                    common=set(a['coverage']['support']) & set(b['coverage']['support'])
                    common={iid for iid in common if a['coverage']['support'][iid]['selection_decidable'] and b['coverage']['support'][iid]['selection_decidable']}
                    newly={iid for iid,x in a['coverage']['support'].items() if x['selection_decidable'] and not b['coverage']['support'].get(iid,{}).get('selection_decidable',False)}
                    counts['common_decidable_security_days']+=len(common)
                    counts['selected_overlap']+=len(set(a['selected_ids']) & set(b['selected_ids']))
                    if estimand=='DECLARED_UNIVERSE_ACCOUNT':
                        daily=[]
                        for group in (left,right):
                            run=execution.get(group,{}).get(primary,{})
                            curve=run.get('account_curve',[]);position=next((i for i,x in enumerate(curve) if x['session']==day),None)
                            value=None
                            if run.get('strategy_evaluation_status')=='EVALUABLE' and position is not None:
                                current=curve[position]['nav'];prev=curve[position-1]['nav'] if position else protocol['initial_nav_cny']
                                if current is not None and prev is not None:value=float(current)/float(prev)-1
                            daily.append(value)
                        if all(x is not None for x in daily):difference=daily[0]-daily[1]
                    else:
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
                                delay=max(delay,rec.get('exit_delay_sessions',0))
                                contributions.append(dict(group=group,date=day,instrument_id=rec['instrument_id'],status=rec['status'],net_return=rec.get('net_return'),capital_occupied_cny=rec.get('capital_occupied_cny'),capital_commitment_cny=rec.get('capital_commitment_cny'),exit_delay_sessions=rec.get('exit_delay_sessions',rec.get('pending_exit_delay_sessions',0))))
                            # Missing/blocked exits retained; never choose support by outcomes.
                            daily.append(mean(r['net_return'] for r in records) if records and all(r.get('net_return') is not None and r['status']=='CLOSED' for r in records) else None)
                        if all(x is not None for x in daily):difference=daily[0]-daily[1]
                pairs.append(dict(date=day,session_index=indexes.get(day),difference=difference,exit_delay=delay))
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
                group_summaries[group]=dict(signal_count=n,stock_count=len({x['instrument_id'] for x in records}),signal_dates=len({x['date'] for x in records}),unfilled_rate=unfilled/n if n else None,complete_return_mean=mean(closed) if closed and len(closed)==n else None,observed_closed_subset_mean=mean(closed) if closed else None,closed_subset_count=len(closed),median_closed_subset=median(closed) if closed else None,tail_closed_subset=min(closed) if closed else None,max_exit_delay_sessions=max((x['exit_delay_sessions'] for x in records),default=None),unresolved_capital_cny=sum(float(x['capital_occupied_cny'] or 0) for x in records),policy='CLOSED_SUBSET_DESCRIPTIVE_ONLY; ALL_SIGNALS_RETAINED')
            descriptions[key]=dict(group_summaries=group_summaries,estimand=estimand,left=left,right=right,counts=dict(counts),sample_days=len(pairs),known_days=len(known),
                mean=mean(known) if known else None,median=median(known) if known else None,tail_min=min(known) if known else None,
                descriptive_subset_only=len(known)!=len(pairs),date_contributions=pairs,security_contributions=contributions,
                max_security_absolute_contribution_share=max(by_security.values())/total if total else None,
                policy='BOTH_SIGNAL_DATES_CONDITIONAL; NO_ZERO_IMPUTATION; OVERLAPPING_SETUPS_NOT_ADDITIVE' if estimand!='DECLARED_UNIVERSE_ACCOUNT' else 'OVERLAPPING_HOLDINGS_ACCOUNT; CASH_DAYS_ONLY_IF_COMPLETE')
    effects=estimator(series,seed=protocol['seed'],block_length=protocol['block_length_sessions'],repetitions=protocol['bootstrap_repetitions'])
    return dict(contract='R1_PAIRED_RESULT_STATISTICS_V2',status='ESTIMATED' if any(v['status']=='ESTIMATED' for v in effects.values()) else 'UNAVAILABLE',
        primary_slippage_bps=protocol['primary_slippage_bps'],family_size=len(series),effects=effects,descriptions=descriptions,
        independent_validation='UNAVAILABLE_NO_INDEPENDENT_HOLDOUT',market_edge='NOT_ESTABLISHED')
