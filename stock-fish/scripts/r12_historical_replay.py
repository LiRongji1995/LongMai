"""Attempt source-contract reconstruction for the latest bounded exchange days.

Reads the immutable provider archive. Artifact writes use only an explicitly
named existing project DB; reconstructed runs cannot activate prospective input.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time
import json
from pathlib import Path
import sys
from time import perf_counter
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.historical_replay import HistoricalReplayAssembler, source_policy
from stockfish_core.yaogu.data_pipeline import stage_aware_history_sessions_required

SH = ZoneInfo('Asia/Shanghai')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding='utf-8')


def source_contract(assembler, session, history_sessions):
    required=[d for d in assembler.calendar if d<=session][-history_sessions:]
    absent=[str(d) for d in required if d not in assembler.raw]
    empty=[str(d) for d in required if d in assembler.raw and not assembler.raw[d].get('rows')]
    master_present=session in assembler.masters
    raw_present=session in assembler.raw
    master_nonempty=master_present and bool(assembler.masters[session].get('rows'))
    raw_nonempty=raw_present and bool(assembler.raw[session].get('rows'))
    response_sessions=len(required)-len(absent)
    nonempty_sessions=response_sessions-len(empty)
    blockers=[]
    if not master_present:blockers.append('NO_SUCCESSFUL_DATED_MASTER_FOR_SIGNAL_SESSION')
    elif not master_nonempty:blockers.append('EMPTY_DATED_MASTER_RESPONSE_DOES_NOT_PROVE_ELIGIBLE_UNIVERSE')
    if not raw_present:blockers.append('NO_SUCCESSFUL_RAW_DAILY_FOR_SIGNAL_SESSION')
    elif not raw_nonempty:blockers.append('EMPTY_RAW_DAILY_RESPONSE_DOES_NOT_PROVE_CURRENT_PRICE')
    if len(required)<history_sessions:blockers.append('AUTHORITATIVE_CALENDAR_WARMUP_TOO_SHORT')
    if absent:blockers.append('CORE_PRICE_HISTORY_HAS_MISSING_EXCHANGE_SESSIONS')
    if empty:blockers.append('CORE_PRICE_HISTORY_HAS_EMPTY_EXCHANGE_SESSION_RESPONSES')
    if not assembler.factors:blockers.append('NO_VERIFIED_ADJUSTMENT_FACTOR_CAPTURE')
    return {'source_contract_blockers':blockers, 'required_core_history_sessions':history_sessions,
        'required_calendar_sessions_available':len(required),
        'successful_core_price_response_sessions':response_sessions,
        'nonempty_core_price_sessions':nonempty_sessions,
        'captured_core_price_sessions':nonempty_sessions,
        'missing_core_price_sessions':absent,'empty_core_price_sessions':empty,
        'unavailable_core_price_sessions':sorted(absent+empty),
        'dated_master_capture_present':master_present,
        'dated_master_available_nonempty':master_nonempty,'dated_master_available':master_nonempty,
        'current_raw_price_capture_present':raw_present,
        'current_raw_price_available_nonempty':raw_nonempty,'current_raw_price_available':raw_nonempty,
        'availability_semantics':'NONEMPTY_RESPONSE_ONLY; NOT_FULL_UNIVERSE_OR_PER_INSTRUMENT_WINDOW_CERTIFICATION',
        'registered_original_identity_fact_count':len(assembler.basic),
        'conflicting_original_identity_fact_count':len(assembler.identity_conflicts),
        'factor_security_count':len(assembler.factors)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--source-docs',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--db',type=Path)
    parser.add_argument('--observation-date',type=date.fromisoformat)
    parser.add_argument('--sessions',type=int,default=20)
    parser.add_argument('--audit-only',action='store_true',help='Read source contracts and write private diagnostics; no DB writes or signal run.')
    args=parser.parse_args()
    if not 1<=args.sessions<=60:parser.error('--sessions must be 1..60')
    if args.output.exists():parser.error('--output must be a new directory')
    if not args.audit_only and (args.db is None or not args.db.is_file()):
        parser.error('--db must name the existing authoritative project DB')
    started=perf_counter();args.output.mkdir(parents=True)
    policy=source_policy(args.source_docs)
    assembler=HistoricalReplayAssembler(args.cache,policy)
    day=args.observation_date or min(datetime.now(SH).date(),max(assembler.calendar))
    sessions=[d for d in assembler.calendar if d<=day][-args.sessions:]
    warmup=stage_aware_history_sessions_required(planned_replay_signal_sessions=0)
    store=owner=None
    if not args.audit_only:
        from stockfish_core.storage import SQLiteStore
        from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
        store=SQLiteStore(args.db);owner=HistoryArtifactStore(store)
    attempts=[]
    for session in sessions:
        row={'observation_date':str(session),**source_contract(assembler,session,warmup),
            'coverage_type':'RECONSTRUCTED_HISTORICAL','official_primary_capture':False,
            'official_validation_signal_countable':False, 'source_constructed':False,
            'price_trend_replay_scope_status':'SOURCE_CONTRACT_BLOCKED',
            'full_context_replay_scope_status':'UNKNOWN_OR_PARTIAL_CONTEXT'}
        if args.audit_only:
            row['status']='SOURCE_CONTRACT_AUDIT_ONLY'
        elif not row['dated_master_capture_present'] or not row['current_raw_price_capture_present']:
            row['status']='SOURCE_CONTRACT_BLOCKED'
        else:
            # A verified empty capture can still produce an explicit diagnostic
            # input. Construction never promotes empty scope into availability.
            try:
                from stockfish_core.yaogu.stage_aware_momentum import run_stage_aware_momentum
                bundle,audit=assembler.build(session,history_sessions=warmup,history_store=owner)
                cutoff=datetime.combine(session,time(20,30),SH)
                result=run_stage_aware_momentum(bundle,cutoff=cutoff)
                counts=result.get('counts',{});conditions=counts.get('condition_counts',{})
                trend=conditions.get('trend_state',{})
                evaluable_trend=trend.get('PASS',0)+trend.get('FAIL',0)
                input_id='market_'+digest(bundle)
                identity={'input_id':input_id,'history_artifact_hash':bundle.history_artifact_hash,
                    'decision_cutoff':cutoff.isoformat(),'strategy_id':result['strategy_id'],
                    'implementation_version':result['implementation_version'],'spec_revision':result['spec_revision'],
                    'source_identity':{k:v for k,v in result.items() if k.endswith(('_hash','_hashes'))}}
                result.update(run_id='historical_replay_'+digest(identity),input_id=input_id,
                    history_artifact_ref=bundle.history_artifact_ref,history_artifact_hash=bundle.history_artifact_hash,
                    coverage_type='RECONSTRUCTED_HISTORICAL',validation_role='RECONSTRUCTED_HISTORICAL_RESEARCH',
                    official_primary_capture=False,official_validation_signal_countable=False,
                    source_identity=identity)
                store.put_document('candidate_inputs',input_id,bundle.model_dump(mode='json'))
                store.put_document('historical_replay_runs',result['run_id'],result)
                write_json(args.output/str(session)/'reconstruction-audit.json',audit)
                write_json(args.output/str(session)/'market-input.json',bundle.model_dump(mode='json'))
                write_json(args.output/str(session)/'signal-run.json',result)
                row.update(status='EVALUATED' if evaluable_trend else 'INSUFFICIENT_IDENTITY' if not counts.get('eligible') else 'INSUFFICIENT_HISTORY',
                    source_constructed=True, input_id=input_id,run_id=result['run_id'],
                    history_artifact_ref=bundle.history_artifact_ref,history_artifact_hash=bundle.history_artifact_hash,
                    input_bytes=(args.output/str(session)/'market-input.json').stat().st_size,
                    focus=counts.get('focus',0),watch=counts.get('watch',0),rejected=counts.get('rejected',0),
                    unknown=counts.get('unknown',0),eligible=counts.get('eligible',0),
                    evaluable_trend_instruments=evaluable_trend,condition_counts=conditions,
                    price_trend_replay_scope_status='EVALUATED_LIMITED_SCOPE' if evaluable_trend else 'INSUFFICIENT_HISTORY',
                    normalization_gap_counts=dict(Counter(g['reason'] for g in audit['gaps'])))
                if not evaluable_trend and 'CORE_PRICE_HISTORY_HAS_MISSING_EXCHANGE_SESSIONS' not in row['source_contract_blockers']:
                    row['source_contract_blockers'].append('NO_INSTRUMENT_WITH_EVALUABLE_V2_TREND_CONTRACT')
            except Exception as exc:
                row.update(status='FAILED',error_type=type(exc).__name__,error_code=str(exc).split(':')[0][:160])
        attempts.append(row)
        write_json(args.output/'attempts.json',attempts)
        print(json.dumps({k:row.get(k) for k in ('observation_date','status','source_constructed','eligible','evaluable_trend_instruments')},sort_keys=True),flush=True)
    report={'report_version':'R12_SOURCE_CONTRACT_REPLAY_V2','generated_at':datetime.now(SH).isoformat(),
        'mode':'READ_ONLY_SOURCE_AUDIT' if args.audit_only else 'RECONSTRUCTED_HISTORICAL_ARTIFACT_REPLAY',
        'source_cache':str(args.cache.resolve()),'source_policy_hash':assembler.policy_hash,
        'authoritative_db':str(args.db.resolve()) if args.db else None,
        'attempt_count':len(attempts),'source_constructed_count':sum(r['source_constructed'] for r in attempts),
        'evaluable_signal_day_count':sum(r.get('evaluable_trend_instruments',0)>0 for r in attempts),
        'full_context_evaluable_day_count':0,'failed_count':sum(r['status']=='FAILED' for r in attempts),
        'status_counts':dict(Counter(r['status'] for r in attempts)),
        'source_contract_blocker_counts':dict(Counter(b for r in attempts for b in r['source_contract_blockers'])),
        'elapsed_seconds':round(perf_counter()-started,3),'attempts':attempts,
        'market_edge':'NOT_ESTABLISHED'}
    if not report['evaluable_signal_day_count']:
        report['objective_blocker']='No source-contract-complete V2 trend signal day; inspect dated master/raw and contiguous warmup/comparability blockers per session.'
    write_json(args.output/'source-policy.json',policy)
    write_json(args.output/'V2_HISTORICAL_REPLAY_REPORT.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('attempts',)},sort_keys=True),flush=True)


if __name__=='__main__':main()
