"""Versioned reporting and effective execution contracts; no selection mutations."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import math

CONTRACT = 'R1_FACT_SELECTION_COVERAGE_V2'


def conjunction(states):
    states=list(states)
    return 'FAIL' if 'FAIL' in states else 'PASS' if states and all(x=='PASS' for x in states) else 'UNKNOWN'


def disjunction(states):
    states=list(states)
    return 'PASS' if 'PASS' in states else 'FAIL' if states and all(x=='FAIL' for x in states) else 'UNKNOWN'


def evaluability(row):
    """Determine support from effective dependencies, never from output labels."""
    if 'setups' in row:
        common=row['common']['status']
        core_setups=[]; full=[]
        for setup in row['setups'].values():
            parts=setup.get('conditions',{})
            core_setups.append(conjunction(v['status'] for k,v in parts.items() if k not in ('market','theme_funds','critical')))
            full.append(setup['status'])
        core=conjunction([common,disjunction(core_setups)])
        policy=conjunction([common,disjunction(full)])
        background=row.get('background',{})
    else:
        conditions=row.get('conditions',{})
        keys=('liquidity_bottom','trend_state','setup_identification','behavioral_confirmation')
        core=conjunction(conditions.get(k,{}).get('status','UNKNOWN') for k in keys)
        policy=conjunction(conditions.get(k,{}).get('status','UNKNOWN') for k in (*keys,'market_compatibility','same_theme_context','critical_counterevidence'))
        background={k:conditions.get(k,{}) for k in ('sector_context','capital_linkage')}
    missing=[];background_details=[]
    def scan(value,path):
        if isinstance(value,dict):
            if value.get('status')=='UNKNOWN' or value.get('ecosystem_state')=='UNKNOWN':
                missing.append(path);background_details.append(dict(field=path,reason_codes=value.get('reason_codes',[])))
            for key,child in value.items():
                if isinstance(child,(dict,list)):scan(child,path+':'+key)
        elif isinstance(value,list):
            if not value:missing.append(path);background_details.append(dict(field=path,reason_codes=['NO_BACKGROUND_RECORD']))
            for index,child in enumerate(value):scan(child,path+':'+str(index))
    scan(background,'background')
    return dict(contract=CONTRACT,core_facts_evaluable=core!='UNKNOWN',core_truth=core,
        selection_decidable=policy!='UNKNOWN',selection_truth=policy,
        background_coverage='INCOMPLETE' if missing else 'NO_RECORDED_GAP',background_missing=missing,background_details=background_details,
        reason_codes=['KNOWN_FAILURE_SHORT_CIRCUIT'] if policy=='FAIL' else ['COMPLETE_PASSING_PATH'] if policy=='PASS' else ['REQUIRED_FACTS_UNRESOLVED'],
        strategy_label=row.get('candidate_status',row.get('decision_status')))


def coverage(rows, qualification, universe_complete):
    support={row['instrument_id']:evaluability(row) for row in rows}
    universe=bool(universe_complete and not qualification.get('eligibility_unknown',0) and not qualification.get('identity_absent',0))
    decidable=all(x['selection_decidable'] for x in support.values())
    return dict(contract=CONTRACT,candidate_availability='PRESENT' if any(x.get('candidate_status')=='CORE_PASS' or x.get('decision_status')=='FOCUS' for x in rows) else 'ABSENT',
        universe_coverage='COMPLETE' if universe else 'INCOMPLETE',
        selection_decidability='COMPLETE' if decidable else 'INCOMPLETE',
        required_data_coverage='SUFFICIENT_FOR_DECISION' if decidable else 'INCOMPLETE',
        background_coverage='INCOMPLETE' if any(x['background_missing'] for x in support.values()) else 'NO_RECORDED_GAP',
        selection_evaluation_status='EVALUABLE' if universe and decidable else 'NOT_EVALUABLE_AS_COMPLETE_STRATEGY',support=support)


def leaves(evidence, path):
    children=evidence.get('conditions')
    if children:
        for key,value in children.items():
            if key=='common':continue  # counted once outside independent setup alternatives
            yield from leaves(value,path+':'+key)
    else:
        yield path,evidence


def funnel(rows):
    counts=Counter(); intersections=Counter(); terminal=Counter(); details=[]
    for row in rows:
        iid=row['instrument_id']; entries=[]
        if 'common' in row:
            entries.extend(leaves(row['common'],'common'))
            for setup,value in row['setups'].items():entries.extend(leaves(value,'setup:'+setup))
        else:
            for key,value in row.get('conditions',{}).items():entries.extend(leaves(value,'policy:'+key))
        keys=[]
        for key,value in entries:
            state=value.get('status','NOT_EVALUATED'); key=key+':'+state
            counts[key]+=1
            if state!='PASS':
                keys.append(key)
                details.append(dict(instrument_id=iid,condition=key,evidence=deepcopy(value)))
        intersections['|'.join(sorted(set(keys))) or 'ALL_EVALUATED_PASS']+=1
        terminal[row.get('candidate_status',row.get('decision_status','UNKNOWN'))]+=1
    assert sum(terminal.values())==len(rows)==sum(intersections.values())
    return dict(independent_condition_counts=dict(counts),condition_intersections=dict(intersections),
        terminal_partition=dict(terminal),condition_details=details,condition_count_policy='OVERLAPPING_LEAVES_DO_NOT_SUM; COMMON_ONCE_PER_SECURITY_DATE; TERMINAL_EXCLUSIVE')


def effective_spec(protocol, cost=None):
    """One validated immutable-by-value specification consumed by every engine."""
    allowed={'strategy_id','version','product_role','parameter_status','market_edge','production_promotion','seed','holding_session_intervals','entry_offset','exit_offset','order_cutoff','slippage_bps_per_side','primary_slippage_bps','initial_nav_cny','account_type','single_name_cap','theme_cap','block_length_sessions','bootstrap_repetitions','multiple_comparison','primary_comparison','data_interval','development_interval','validation_interval','holdout_interval','data_status','commission_status','groups','contract','order_construction','same_open_sale_reuse','leverage','cost_scenario','theme_unknown_policy'}
    if protocol.get('primary_comparison',['R1_FULL','V2_CONTROL'])!=['R1_FULL','V2_CONTROL']:raise ValueError('UNSUPPORTED_PRIMARY_COMPARISON')
    if set(protocol)-allowed:raise ValueError('UNSUPPORTED_EXECUTION_SPEC_FIELD')
    fixed=dict(contract='R1_EFFECTIVE_EXECUTION_SPEC_V2',order_construction='PREDECLARED_MAX_LEGAL_BUY_PRICE_WHOLE_LOT',same_open_sale_reuse=False,leverage=False,theme_unknown_policy='DISCLOSED_KNOWN_THEME_ONLY_CAP')
    if any(key in protocol and protocol[key]!=value for key,value in fixed.items()):raise ValueError('UNSUPPORTED_FIXED_EXECUTION_POLICY')
    keys=('initial_nav_cny','single_name_cap','theme_cap','entry_offset','exit_offset','holding_session_intervals','order_cutoff','slippage_bps_per_side','primary_slippage_bps','seed','block_length_sessions','bootstrap_repetitions','multiple_comparison','account_type')
    spec={k:deepcopy(protocol[k]) for k in keys}
    for key in ('initial_nav_cny','single_name_cap','theme_cap'):
        value=float(spec[key])
        if not math.isfinite(value) or value<=0:raise ValueError('INVALID_EXECUTION_SPEC:'+key)
    if not 0<spec['single_name_cap']<=spec['theme_cap']<=1:raise ValueError('INVALID_PORTFOLIO_CAPS')
    for key in ('entry_offset','exit_offset','holding_session_intervals','block_length_sessions','bootstrap_repetitions'):
        if type(spec[key]) is not int or spec[key]<=0:raise ValueError('INVALID_EXECUTION_SPEC:'+key)
    if spec['exit_offset']-spec['entry_offset']!=spec['holding_session_intervals']:raise ValueError('UNORDERED_EXECUTION_WINDOWS')
    if spec['order_cutoff']!='09:15:00 Asia/Shanghai':raise ValueError('UNSUPPORTED_ORDER_CUTOFF')
    if spec['account_type']!='HYPOTHETICAL' or spec['multiple_comparison']!='HOLM':raise ValueError('UNSUPPORTED_RESEARCH_SPEC')
    if type(spec['seed']) is not int:raise ValueError('INVALID_SEED')
    slips=spec['slippage_bps_per_side']
    if not slips or slips!=sorted(set(slips)) or any(type(x) is not int or not 0<=x<10000 for x in slips) or spec['primary_slippage_bps'] not in slips:raise ValueError('INVALID_SLIPPAGE_SCENARIOS')
    if cost is not None:
        for key in ('commission_rate','minimum_commission','transfer_rate','sell_tax_rate'):
            value=Decimal(str(cost[key]))
            if not value.is_finite() or value<0:raise ValueError('INVALID_COST:'+key)
        if cost.get('commission_includes_transfer_or_tax') is not False:raise ValueError('COST_COMPONENT_OVERLAP_UNRESOLVED')
        spec['cost_scenario']=deepcopy(cost)
    spec.update(contract='R1_EFFECTIVE_EXECUTION_SPEC_V2',theme_unknown_policy='DISCLOSED_KNOWN_THEME_ONLY_CAP',order_construction='PREDECLARED_MAX_LEGAL_BUY_PRICE_WHOLE_LOT',same_open_sale_reuse=False,leverage=False)
    return spec
