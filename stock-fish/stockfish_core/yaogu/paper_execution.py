"""Explicit R05 next-open paper scenario; independent from price labels/accounts."""
from __future__ import annotations
from datetime import datetime, time
from bisect import bisect_left
from decimal import Decimal
from zoneinfo import ZoneInfo
from .contracts import digest
from .events import limit_event
from .strategy_tracking import _visible_bars, authoritative_strategy_items, validate_horizons
from .visibility import EvidenceResolver

POLICY_ID='NEXT_OPEN_HOLD_H_PAPER_V1'
SH=ZoneInfo('Asia/Shanghai')
COSTS=(0,30,60)
PV_FOCUS='PV_FOCUS_PREVIEW_V0.1'
STAGE_AWARE='STAGE_AWARE_MOMENTUM_V2'
STAGE_AWARE_FOCUS='STAGE_AWARE_MOMENTUM_V2_FOCUS'


def assumptions():
    return {'policy_id':POLICY_ID,'parameter_status':'EXPLICIT_UNVALIDATED_RESEARCH_ASSUMPTION',
            'buy_attempt':'SIGNAL_T_PLUS_1_OPEN','planned_exit':'ENTRY_E_PLUS_H_CLOSE',
            'minimum_holding_sessions':1,'unsuccessful_buy':'DO_NOT_CHASE',
            'round_trip_cost_bps':list(COSTS),'primary_cost_bps':30,'cost_allocation':'HALF_EACH_SIDE',
            'cost_semantics':'PURE_RESEARCH_SCENARIO_NOT_CURRENT_REAL_TAX_OR_COMMISSION',
            'slippage':'INCLUDED_IN_ASSUMED_ALL_IN_COST_NO_SEPARATE_DEDUCTION',
            'net_return_formula':'exit_comparable_close*(1-bps/20000)/(entry_comparable_open*(1+bps/20000))-1',
            'fill_evidence':'DAILY_NON_BOUNDARY_PAPER_MODEL_ONLY_NOT_PROOF_OF_ACTUAL_FILL',
            'unverified_limit':'FILL_UNVERIFIED','blocked_exit':'RETAIN_OPEN_MARK_AND_TRY_LATER_VERIFIED_CLOSE',
            'actual_order_instruction':False,'account_required':False}


def _complete(day,cutoff):
    return day is not None and datetime.combine(day,time(15),SH)<=cutoff.astimezone(SH)


def _securities(bundle,cutoff):
    resolver=EvidenceResolver(bundle.receipts,cutoff=cutoff,coverage_type=bundle.coverage_type,sessions=bundle.sessions)
    groups={};days=tuple(bundle.sessions)
    for row in bundle.universe.securities:
        if not resolver.is_visible(row,instrument_id=row.instrument_id):continue
        start=bisect_left(days,row.effective_from);end=bisect_left(days,row.effective_to) if row.effective_to else len(days)
        for day in days[start:end]:groups.setdefault((row.instrument_id,day),[]).append(row)
    result={}
    priority=lambda r:(r.source_published_at,r.ingested_at,r.first_seen_at)
    for key,rows in groups.items():
        latest=max(priority(r) for r in rows);choices=[r for r in rows if priority(r)==latest]
        if len({digest(r) for r in choices})==1:result[key]=choices[0]
    return result


def evaluate_paper_execution(run,bundle,cutoff,*,horizons=(1,2,3,4,5,10,20),prepared=None):
    horizons=validate_horizons(horizons)
    days=sorted(set(bundle.sessions));signal=__import__('datetime').date.fromisoformat(run['observation_date'])
    start=days.index(signal);entry_day=days[start+1] if start+1<len(days) else None
    bars,securities=prepared if prepared is not None else (_visible_bars(bundle,cutoff),_securities(bundle,cutoff))
    strategies=list(authoritative_strategy_items(run))
    pv=dict((x['instrument_id'],x) for sid,items in strategies if sid=='PV_OBSERVATION_V1' for x in items)
    selections=run.get('selection_results',{})
    if selections.get(STAGE_AWARE_FOCUS) is not None:
        stage=dict((x['instrument_id'],x) for sid,items in strategies if sid==STAGE_AWARE for x in items)
        focus=(selections.get(STAGE_AWARE_FOCUS) or {}).get('selected_ids',[])
        strategies.append((STAGE_AWARE_FOCUS,[stage[iid] for iid in focus if iid in stage]))
    else:
        focus=(selections.get(PV_FOCUS) or {}).get('selected_ids',[])
        strategies.append((PV_FOCUS,[pv[iid] for iid in focus if iid in pv]))
    output=[]
    for sid,items in strategies:
        for item in items:
            iid=item['instrument_id'];entry=bars.get((iid,entry_day));state=securities.get((iid,entry_day))
            for h in horizons:
                target_index=start+1+h;target=days[target_index] if target_index<len(days) else None
                price_target=days[start+h] if start+h<len(days) else None
                row={'policy_id':POLICY_ID,'strategy_id':sid,'run_id':run['run_id'],'instrument_id':iid,
                     'signal_date':str(signal),'entry_attempt_date':str(entry_day) if entry_day else None,
                     'holding_horizon':h,'signal_price_target_date':str(price_target) if price_target else None,
                     'planned_exit_date':str(target) if target else None,'evaluated_at':cutoff.isoformat(),
                     'attempted':_complete(entry_day,cutoff),'entry_confirmed':False,'closed':False,
                     'status':'NOT_MATURED','reason_codes':[],'exit_date':None,'delayed_exit':False,
                     'gross_return':None,'net_return_by_cost_bps':{str(bps):None for bps in COSTS},
                     'mark_to_market_return':None,'blocked_exit_sessions':[],
                     'evidence_grade':'DAILY_BAR_RESEARCH_MODEL','market_edge':'NOT_ESTABLISHED'}
                if not row['attempted']:
                    row['reason_codes']=['ENTRY_SESSION_NOT_MATURED_OR_CALENDAR_PENDING']
                elif state is not None and state.suspended is True:
                    row.update(status='NO_FILL',reason_codes=['ENTRY_SUSPENDED'])
                elif entry is None or state is None or state.suspended is None or entry.comparable_open is None:
                    row.update(status='FILL_UNVERIFIED',reason_codes=['ENTRY_PRICE_OR_DATED_TRADING_STATE_UNAVAILABLE'])
                elif not entry.limit_verified or entry.limit_up is None or entry.limit_down is None or entry.limit_applicable is not True:
                    row.update(status='FILL_UNVERIFIED',reason_codes=['ENTRY_LIMIT_EVIDENCE_UNAVAILABLE'])
                elif limit_event(entry).quality!='VALID' or entry.open is None or entry.open>=entry.limit_up or entry.open<=entry.limit_down:
                    row.update(status='FILL_UNVERIFIED',reason_codes=['ENTRY_AT_BOUNDARY_OR_INVALID_DAILY_EVIDENCE'])
                else:
                    row.update(entry_confirmed=True,entry_comparable_open=str(entry.comparable_open),entry_raw_open=str(entry.open),
                               status='OPEN_NOT_MATURED')
                    if _complete(target,cutoff):
                        row['status']='OPEN_BLOCKED'
                        for exit_day in days[target_index:]:
                            if not _complete(exit_day,cutoff):break
                            bar=bars.get((iid,exit_day));security=securities.get((iid,exit_day))
                            if bar is None or bar.comparable_close is None or bar.comparable_basis!=entry.comparable_basis:
                                reason='EXIT_COMPARABLE_PRICE_UNAVAILABLE'
                            elif security is None or security.suspended is None:
                                reason='EXIT_DATED_TRADING_STATE_UNKNOWN'
                            elif security.suspended:
                                reason='EXIT_SUSPENDED'
                            elif not bar.limit_verified or bar.limit_down is None or bar.limit_applicable is not True:
                                reason='EXIT_LIMIT_EVIDENCE_UNAVAILABLE'
                            elif limit_event(bar).quality!='VALID' or bar.close<=bar.limit_down:
                                reason='EXIT_LOWER_LIMIT_BLOCKED_OR_INVALID'
                            else:
                                ratio=bar.comparable_close/entry.comparable_open
                                row.update(status='CLOSED',closed=True,exit_date=str(exit_day),delayed_exit=exit_day!=target,
                                           exit_raw_close=str(bar.close),gross_return=str(ratio-1))
                                row['net_return_by_cost_bps']={str(bps):str(ratio*(1-Decimal(bps)/20000)/(1+Decimal(bps)/20000)-1) for bps in COSTS}
                                break
                            row['blocked_exit_sessions'].append({'session':str(exit_day),'reason':reason})
                        row['reason_codes']=sorted({r['reason'] for r in row['blocked_exit_sessions']})
                    visible_marks=[bars[(iid,d)] for d in days[start+1:] if _complete(d,cutoff) and (iid,d) in bars
                                   and (not row['closed'] or str(d)<=row['exit_date'])
                                   and bars[(iid,d)].comparable_basis==entry.comparable_basis and bars[(iid,d)].comparable_close is not None]
                    if visible_marks:
                        row['mark_to_market_return']=str(visible_marks[-1].comparable_close/entry.comparable_open-1)
                        row['mark_session']=str(visible_marks[-1].session)
                row['trade_id']='paper_'+digest({k:row[k] for k in ('policy_id','strategy_id','run_id','instrument_id','holding_horizon')})
                output.append(row)
    return output


def summarize_paper(rows):
    closed=[r for r in rows if r['closed']]
    attempted=sum(r['attempted'] for r in rows)
    states={state:sum(r['status']==state for r in rows) for state in sorted({r['status'] for r in rows})}
    result={'total_signals':len(rows),'attempted':attempted,'evaluable':sum(r['entry_confirmed'] for r in rows),
            'closed':len(closed),'states':states,'coverage':len(closed)/attempted if attempted else None,'cost_scenarios':{}}
    for bps in COSTS:
        values=[Decimal(r['net_return_by_cost_bps'][str(bps)]) for r in closed]
        result['cost_scenarios'][str(bps)]={'denominator_closed':len(values),
            'win_rate':sum(v>0 for v in values)/len(values) if values else None,
            'mean_net_return':float(sum(values)/len(values)) if values else None}
    return result
