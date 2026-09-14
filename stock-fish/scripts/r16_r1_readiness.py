"""Read-only price/PV/MA availability from PIT-resolved original production facts.

This does not run selection, score, or a full strategy. Trend here means its
long-MA input availability; the production decision remains separately reported.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
import json
import hashlib
from pathlib import Path
import time
from r16_r1_evidence_assets import load_bundle
from r16_history_stability_audit import (_selected_source_view_fast, ProgressLogger,
    COMPARE_VALUE_FIELDS, _git_provenance)
from stockfish_core.yaogu.strategy_experiments import _prepare_context, _complete_pv_row
from stockfish_core.yaogu.stage_aware_momentum import _ma_value
from stockfish_core.yaogu.coverage_diagnostics import _raw_ok, _comparable_ok, exchange_of, _stats


def hydrate_selected_price_payloads(bundle,tokens,sessions):
    """Retain the exact selected full DailyBar for production PV/event calls."""
    from stockfish_core.yaogu.contracts import digest
    from stockfish_core.yaogu.history_artifacts import daily_bar_payload
    wanted = {token.row_hash:key for key,token in tokens.items()
              if key[1] in set(map(str,sessions)) and token.status=='SELECTED'}
    loaded = {}
    if bundle.history_artifact_ref:
        hashes = list(wanted)
        with bundle._history_store.store.transaction(write=False) as con:
            for offset in range(0,len(hashes),500):
                chunk = hashes[offset:offset+500]
                marks = ','.join('?' for _ in chunk)
                for stored in con.execute('SELECT row_hash,payload FROM history_evidence_rows WHERE row_hash IN ('+marks+')',chunk):
                    if hashlib.sha256(stored['payload'].encode()).hexdigest()!=stored['row_hash']:
                        raise ValueError('READINESS_SELECTED_PRICE_PAYLOAD_HASH_MISMATCH')
                    loaded[stored['row_hash']] = daily_bar_payload(json.loads(stored['payload']))
    for bar in bundle.bars:
        key = (bar.instrument_id,str(bar.session))
        token = tokens.get(key)
        if token is None or token.row_hash not in wanted or token.row_hash in loaded:
            continue
        row_hash = digest(bar)
        if row_hash==token.row_hash:
            loaded[row_hash]=daily_bar_payload(bar)
    if set(loaded)!=set(wanted):
        raise ValueError('READINESS_SELECTED_PRICE_FULL_PAYLOAD_MISSING')
    result = {}
    for row_hash,row in loaded.items():
        key=wanted[row_hash]
        if (row['instrument_id'],str(row['session']))!=key:
            raise ValueError('READINESS_SELECTED_PRICE_PAYLOAD_IDENTITY_MISMATCH')
        result[key]=row
    return result


def audit_readiness(db, input_id, cutoff, *, instrument_limit=None, checkpoint=None,budget_seconds=1200):
    logger = ProgressLogger(checkpoint,interval_seconds=10)
    original_event = logger.event
    def event(*args,**kwargs):
        if time.perf_counter()-logger.started > budget_seconds:
            raise TimeoutError('READINESS_AUDIT_BUDGET_EXCEEDED')
        original_event(*args,**kwargs)
    logger.event = event
    event('READINESS_LOAD_START',force=True)
    bundle = load_bundle(db,input_id)
    ctx = _prepare_context(bundle,datetime.fromisoformat(cutoff))
    ids = sorted(ctx.eligible_ids)
    if instrument_limit:
        ids = ids[:instrument_limit]
    sessions = tuple(str(s) for s in ctx.sessions[-255:])
    tokens,stats,rejected = _selected_source_view_fast(ctx,sessions,ids,logger,'READINESS')
    # Use the already independently PIT-resolved selected values. This avoids
    # coverage_diagnostics' older history-only aggregation and repeated scans.
    bars = defaultdict(dict)
    for (iid,session),token in tokens.items():
        if token.status == 'SELECTED':
            bars[session][iid] = {'instrument_id':iid,'session':session,
                **dict(zip(COMPARE_VALUE_FIELDS,token.values))}
    for (iid,session),row in hydrate_selected_price_payloads(bundle,tokens,ctx.sessions[-6:]).items():
        bars[session][iid]=row
    ctx = replace(ctx,bars_by_session=dict(bars),scalar_cache={})
    exchange = {iid:exchange_of(iid,ctx.securities.get(iid)) for iid in ids}
    flags = defaultdict(set)
    ledger = []
    window_specs = {f'MA{w}':w for w in (20,60,120,250)}
    requirements = {name:list(sessions[-count:]) for name,count in window_specs.items()}
    end_index = len(ctx.sessions)-1
    for index,iid in enumerate(ids,1):
        current = bars.get(str(ctx.observation_date),{}).get(iid)
        if _raw_ok(current):
            flags['current_raw'].add(iid)
        if _comparable_ok(current):
            flags['current_comparable'].add(iid)
        pv,pv_reason = _complete_pv_row(ctx,iid,ctx.sessions[-6:])
        if pv is not None:
            flags['PV'].add(iid)
        missing = {}
        for name,count in window_specs.items():
            required = requirements[name]
            values = [bars.get(day,{}).get(iid) for day in required]
            absent = [day for day,row in zip(required,values) if row is None]
            no_comparable = [day for day,row in zip(required,values) if row is not None and not _comparable_ok(row)]
            bases = {row.get('comparable_basis') for row in values if _comparable_ok(row)}
            value,reason = _ma_value(ctx,iid,end_index,count)
            if value is not None and reason is None:
                flags[name].add(iid)
            missing[name] = {'required_session_count':count,'calendar_session_count':len(required),
                'missing_source_sessions':absent,'missing_comparable_sessions':no_comparable,
                'distinct_comparable_bases':len(bases),'production_ma_reason':reason,
                'calendar_status':'COMPLETE' if len(required)==count else 'INSUFFICIENT_CALENDAR_SESSIONS'}
        long_ready = all(iid in flags[f'MA{w}'] for w in (60,120,250))
        for count in (60,120,250):
            value,reason = _ma_value(ctx,iid,end_index-5,count)
            long_ready = long_ready and value is not None and reason is None
        if long_ready:
            flags['TREND_LONG_MA_INPUTS'].add(iid)
        ledger.append({'instrument_id':iid,'exchange':exchange[iid],'pv_ready':pv is not None,
            'pv_reason':pv_reason,'trend_long_ma_inputs_ready':long_ready,'windows':missing})
        event('READINESS_WINDOWS_PROGRESS',processed=index,total=len(ids))
    flags['eligible'] = set(ids)
    report = {'readiness_version':'R16_R1_CANONICAL_PRICE_WINDOW_READINESS_V1',
        'provenance':_git_provenance(__import__('sys').argv),'input_id':input_id,
        'history_artifact_ref':bundle.history_artifact_ref,'observation_date':str(bundle.observation_date),
        'decision_cutoff':cutoff,'coverage_type':bundle.coverage_type,'instrument_limit':instrument_limit,
        'eligible_full_universe':len(ctx.eligible_ids),'scanned_eligible':len(ids),
        'coverage':{name:_stats(flags[name],ids,exchange) for name in
                    ('eligible','current_raw','current_comparable','PV','MA20','MA60','MA120','MA250','TREND_LONG_MA_INPUTS')},
        'required_sessions':requirements,'missing_session_ledger':ledger,
        'source_aggregation':stats,'rejected_source_samples':rejected,
        'production_functions':['_complete_pv_row','_ma_value'],
        'trend_decision':'NOT_VERIFIED_NOT_RUN_USE_PRODUCTION_STRATEGY_RESULT',
        'other_contexts':{name:'NOT_VERIFIED_NOT_RUN' for name in
                          ('Market','Sector','Capital','Theme','Limit','Auction','Intraday')},
        'elapsed_seconds':time.perf_counter()-logger.started,'source_access':'READ_ONLY_URI',
        'market_edge':'NOT_ESTABLISHED'}
    event('READINESS_DONE',processed=len(ids),total=len(ids),force=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--input-id',required=True)
    parser.add_argument('--cutoff',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--checkpoint-json')
    parser.add_argument('--instrument-limit',type=int)
    parser.add_argument('--budget-seconds',type=float,default=1200)
    args = parser.parse_args()
    report = audit_readiness(args.db,args.input_id,args.cutoff,instrument_limit=args.instrument_limit,
        checkpoint=args.checkpoint_json,budget_seconds=args.budget_seconds)
    output = Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'input_id':args.input_id,'coverage':report['coverage'],'elapsed_seconds':report['elapsed_seconds']},ensure_ascii=False))


if __name__ == '__main__':
    main()
