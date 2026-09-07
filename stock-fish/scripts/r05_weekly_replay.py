"""Execute frozen production R05 signals and independent real-data replay labels.

Raw canonical inputs stay local. The deliverable includes immutable rank files,
all logical-label rows, exact raw hashes/paths and the reproduction command.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,date,time
from fractions import Fraction
import gc
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time as timer
from zoneinfo import ZoneInfo
import csv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from stockfish_core.yaogu.contracts import MarketInput,digest
from stockfish_core.yaogu.historical_replay import HistoricalReplayAssembler,source_policy,file_hash
from stockfish_core.yaogu.strategy_experiments import run_strategy_experiments,source_identity
from stockfish_core.yaogu.strategy_tracking import evaluate_strategy_experiment_outcomes,build_strategy_tracking,build_strategy_feedback_report,select_label_revisions,OutcomeContext
from stockfish_core.yaogu.paper_execution import evaluate_paper_execution,summarize_paper,assumptions,_securities
from stockfish_core.yaogu.replay_statistics import price_statistics,statistics_table,mature_window,PV,FOCUS,BASELINE
from stockfish_core.yaogu.engine import run as daily_run

SH=ZoneInfo('Asia/Shanghai');HORIZONS=(1,2,3,4,5,10,20)
IDS=(PV,FOCUS,BASELINE,'AMOUNT_STRENGTH_TOP5_DIAGNOSTIC','AMOUNT_STRENGTH_TOP10_DIAGNOSTIC','AMOUNT_STRENGTH_TOP20_DIAGNOSTIC','DAILY_V1','DNA_RERANK_V1','THEME_CONTEXT_V1','STAGE_AWARE_MOMENTUM_V2')


def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():raise FileExistsError('IMMUTABLE_REPLAY_ARTIFACT_EXISTS:'+str(path))
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')


def jsonl(path,rows):
    with Path(path).open('x',encoding='utf-8') as out:
        for r in rows:out.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),default=str,allow_nan=False)+'\n')


def csv_file(path,rows,fields):
    with Path(path).open('x',encoding='utf-8-sig',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)


def code_manifest():
    paths=list((ROOT/'stockfish_core').rglob('*.py'))+list((ROOT/'config').glob('*.yaml'))+list((ROOT/'config').glob('r05*.json'))
    paths.append(ROOT/'scripts'/'r05_weekly_replay.py')
    return {p.relative_to(ROOT).as_posix():file_hash(p) for p in sorted(paths)}


def freeze(output,supply,sources):
    if (output/'replay_plan.json').exists():return json.loads((output/'replay_plan.json').read_text(encoding='utf-8'))
    acquisition=json.loads((supply/'acquisition_plan.json').read_text(encoding='utf-8'))
    policy=source_policy(sources);a=HistoricalReplayAssembler(supply,policy)
    cutoff=datetime.fromisoformat(acquisition['frozen_execution_cutoff'])
    completed=[str(d) for d in a.calendar if datetime.combine(d,time(15),SH)<=cutoff]
    if completed[-5:]!=acquisition['primary_sessions'] or completed[-20:]!=acquisition['attempt_sessions']:raise ValueError('ACTUAL_CALENDAR_PLAN_MISMATCH')
    manifest=code_manifest()
    plan={'task_id':'R05','plan_created_at':datetime.now(SH).isoformat(),'execution_cutoff':cutoff.isoformat(),
          'cutoff_semantics':'COMMON_MARKET_INFORMATION_CUTOFF_ACTUAL_LATE_ACQUISITION_TIMES_RETAINED',
          'primary_sessions':completed[-5:],'attempt_sessions':completed[-20:],
          'selection_identity':source_identity(),'source_tree_manifest':manifest,'source_tree_hash':digest(manifest),
          'historical_source_policy':policy,'source_policy_hash':digest(policy),
          'calendar':{'provider':'baostock_query_trade_dates','sessions':[str(d) for d in a.calendar],
                      'official_crosschecks':{k:v for k,v in policy['source_documents'].items() if 'calendar' in k}},
          'signal_input':{'prior_sessions':26,'maximum_business_day':'T','cutoff':'T 20:00 Asia/Shanghai','current_membership_used':False},
          'outcome_input':{'last_session':completed[-1],'prior_sessions':45,'separate_from_signal':True},
          'horizons':list(HORIZONS),'price_label':'T_CLOSE_TO_T_PLUS_H_COMPARABLE_CLOSE_EXACT_DECIMAL',
          'denominator':'MATURE_VALID_LOGICAL_SIGNAL_PRICE_LABELS_ZERO_INCLUDED_REVISIONS_NOT_COUNTED',
          'episode_policy':'FORMAL_TRACKING_POLICY_PILOT_V0.1_ONLY_NO_SYNTHETIC_NONFOCUS_EPISODES',
          'execution_assumptions':assumptions(),'strategies_and_diagnostics':list(IDS),
          'common_pool':'SAME_DATE_PRODUCTION_QUALIFIED_SECURITIES_WITH_ALL_SIX_PV_PRICE_AMOUNT_FIELDS_BEFORE_PV_GATES',
          'single_factor_diagnostic':{'factor':'A_CURRENT_AMOUNT_OVER_PRIOR5_MEAN','direction':'DESC_EXACT_FRACTION','tie_break':'INSTRUMENT_ID_ASC','top':[5,10,20],'quota_on_production_candidates':False},
          'mature_supplement_rule':'FOR_EACH_H_LAST5_OF_ATTEMPT_DATES_WITH_CALENDAR_T_PLUS_H_AT_OR_BEFORE_CUTOFF_WITHOUT_RETURN_OR_QUALITY_SELECTION',
          'budget':{**acquisition['budget'],'actual_provider_concurrency':1,'retries':'RECOVER_FAILED_CHECKPOINTS_REUSE_SUCCESSES','http_accounting':'PRECHARGED_SHARED_PROVIDER_BUDGET_HTTP_DISTINCT_FROM_SDK_OPERATIONS'},
          'computation_workers':2,
          'raw_captures':a.integrity,'capture_checkpoint_hashes':{p.name:file_hash(p) for p in sorted((supply/'checkpoints').glob('*.json'))},
          'market_edge':'NOT_ESTABLISHED','archived_point_in_time':False}
    dump(output/'replay_plan.json',plan)
    # JSON is valid YAML; use one serializer to avoid changing Decimal/cost policy.
    dump(output/'execution_assumptions.yaml',assumptions())
    return plan


def baseline_results(run):
    items=run['results'][PV]['eligible_feature_items']
    ordered=sorted(items,key=lambda r:r['instrument_id'])
    result={BASELINE:{'strategy_id':BASELINE,'status':'LIMITED_SCOPE' if items else 'UNAVAILABLE','items':[{**r,'rank':i} for i,r in enumerate(ordered,1)],'qualified_ids':[r['instrument_id'] for r in ordered]}}
    factor=lambda r:Fraction(r['exact_factors']['A']['numerator'],r['exact_factors']['A']['denominator'])
    ordered=sorted(items,key=lambda r:(-factor(r),r['instrument_id']))
    for n in (5,10,20):
        sid=f'AMOUNT_STRENGTH_TOP{n}_DIAGNOSTIC';result[sid]={'strategy_id':sid,'status':'LIMITED_SCOPE' if items else 'UNAVAILABLE',
            'diagnostic_only':True,'items':[{**r,'rank':i,'ranking_factor':'A'} for i,r in enumerate(ordered[:n],1)],'qualified_ids':[r['instrument_id'] for r in items]}
    return result


def save_input(path,bundle):
    path=path.with_name(path.name.removesuffix('.json.gz')+'-'+digest(bundle)[:16]+'.json.gz')
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        with gzip.open(path,'rt',encoding='utf-8') as f:
            if digest(json.load(f))!=digest(bundle):raise ValueError('IMMUTABLE_CANONICAL_INPUT_COLLISION')
    else:
        with gzip.open(path,'xt',encoding='utf-8') as f:f.write(bundle.model_dump_json())
    return {'path':str(path.resolve()),'sha256':file_hash(path),'canonical_input_hash':digest(bundle),'distribution':'LOCAL_ONLY_RAW_MARKET_INPUT_NOT_IN_SHAREABLE_PACKAGE'}


def signals(output,supply,plan,*,dates=None,write_attempts=True):
    if code_manifest()!=plan['source_tree_manifest']:raise ValueError('FROZEN_REPLAY_SOURCE_TREE_CHANGED')
    a=HistoricalReplayAssembler(supply,plan['historical_source_policy']);attempts=[]
    for d in dates or plan['attempt_sessions']:
        target=output/d
        if (target/'rank.json').exists():
            if not(target/'attempt.json').exists():
                saved=json.loads((target/'rank.json').read_text(encoding='utf-8'))
                if saved.get('replay_plan_hash')!=digest(plan):raise ValueError('FROZEN_REPLAY_PLAN_MISMATCH')
                dump(target/'attempt.json',{'observation_date':d,'status':saved['results'][PV]['status'],'run_id':saved['run_id'],
                    'rank_file_sha256':file_hash(target/'rank.json'),'primary_window':d in plan['primary_sessions'],
                    'recovered_completed_run_without_attempt_record':True,'recovered_at':datetime.now(SH).isoformat(),
                    'pv_count':len(saved['results'][PV]['items']),'focus_count':len(saved['selection_results'][FOCUS]['selected_ids']),
                    'eligible_complete':len(saved['results'][PV]['eligible_feature_items'])})
            attempts.append(json.loads((target/'attempt.json').read_text(encoding='utf-8')));continue
        started=datetime.now(SH);tick=timer.perf_counter();target.mkdir(parents=True,exist_ok=True)
        try:
            bundle,audit=a.build(d,history_sessions=plan['signal_input']['prior_sessions'])
            reference=save_input(supply/'canonical-inputs'/(d+'.json.gz'),bundle)
            cutoff=datetime.combine(date.fromisoformat(d),time(20),SH)
            if source_identity()!=plan['selection_identity']:raise ValueError('FROZEN_SELECTION_IMPLEMENTATION_CHANGED')
            run=run_strategy_experiments(bundle,cutoff=cutoff)
            actual_daily=daily_run(bundle,cutoff=cutoff,profile='DAILY_V1',generated_at=datetime.now(SH))
            daily_summary={'strategy_id':'DAILY_V1','status':actual_daily.status,'run_id':actual_daily.run_id,
                           'source_hash':actual_daily.source_hash,'config_hash':actual_daily.config_hash,
                           'coverage':actual_daily.coverage,'items':[x.model_dump(mode='json') for x in actual_daily.items],
                           'reason':'FROZEN_DAILY_REQUIRES_VERIFIED_LIMIT_EVENTS_EMOTION_THEME_AND_LONGER_HISTORY',
                           'original_candidate_run_hash':digest(actual_daily)}
            dump(target/'daily_attempt.json',daily_summary)
            run['results']['DAILY_V1']={**daily_summary,'items':[{**i,'rank':i.get('family_rank')} for i in daily_summary['items'] if i.get('daily_score') is not None]}
            run['evaluation_baselines']=baseline_results(run)
            run.update(generated_at=datetime.now(SH).isoformat(),replay_plan_hash=digest(plan),replay_scope=plan['historical_source_policy']['scope_status'])
            dump(target/'input_reference.json',{**reference,'normalization_audit':audit})
            dump(target/'qualification_audit.json',run['qualification'])
            dump(target/'rank.json',run)
            dump(target/'data_gaps.json',{'source_gaps':audit['gaps'],'pv_missing':run['results'][PV]['missing'],
                 'unknown_scope':plan['historical_source_policy']['unknown_scope'],
                 'strategy_states':{sid:{'status':r.get('status'),'reason':r.get('reason'),'counts':r.get('counts')} for sid,r in run['results'].items() if not r.get('compatibility_alias')}})
            csv_file(target/'focus.csv',run['selection_results'][FOCUS]['selected_items'],['instrument_id','name','rank','S_PV','A','R','H','close_location','close','risk_review_status','board_state'])
            csv_file(target/'pv_full.csv',run['results'][PV]['items'],['instrument_id','name','rank','S_PV','A','R','H','close','comparable_close','board_state'])
            attempt={'observation_date':d,'status':run['results'][PV]['status'],'run_id':run['run_id'],'rank_file_sha256':file_hash(target/'rank.json'),
                     'primary_window':d in plan['primary_sessions'],'started_at':started.isoformat(),'ended_at':datetime.now(SH).isoformat(),
                     'elapsed_seconds':timer.perf_counter()-tick,'pv_count':len(run['results'][PV]['items']),
                     'focus_count':len(run['selection_results'][FOCUS]['selected_ids']),'eligible_complete':len(run['results'][PV]['eligible_feature_items']),
                     'future_price_rows':audit['future_price_rows_in_signal']}
            dump(target/'attempt.json',attempt);attempts.append(attempt)
            print(json.dumps(attempt,ensure_ascii=False),flush=True)
            del bundle,actual_daily,run;gc.collect()
        except Exception as exc:
            failure={'observation_date':d,'status':'FAILED','started_at':started.isoformat(),'ended_at':datetime.now(SH).isoformat(),'error_type':type(exc).__name__,'error':str(exc)}
            dump(target/'failure.json',failure);attempts.append(failure)
            print(json.dumps(failure,ensure_ascii=False),flush=True)
            if 'FROZEN_' in str(exc):raise
    if write_attempts and not(output/'historical_attempts_20_sessions.json').exists():dump(output/'historical_attempts_20_sessions.json',attempts)
    return attempts


def _signal_worker(args):
    output,supply,plan,d=args
    return signals(output,supply,plan,dates=[d],write_attempts=False)[0]


def signals_parallel(output,supply,plan):
    attempts=[]
    with ProcessPoolExecutor(max_workers=plan['computation_workers']) as pool:
        futures=[pool.submit(_signal_worker,(output,supply,plan,d)) for d in plan['attempt_sessions']]
        for future in as_completed(futures):attempts.append(future.result())
    attempts.sort(key=lambda r:r['observation_date'])
    if not(output/'historical_attempts_20_sessions.json').exists():dump(output/'historical_attempts_20_sessions.json',attempts)


def freeze_outcome_execution(output,plan):
    """Preserve signal provenance while freezing the actual label implementation.

    The permitted changes are mechanical evaluation-context reuse, explicit
    run/policy label identity, archive persistence and export consistency. Selection code and all registered
    horizons/cost/denominator assumptions must still match the signal plan.
    """
    if source_identity()!=plan['selection_identity']:raise ValueError('FROZEN_SELECTION_IMPLEMENTATION_CHANGED')
    manifest=code_manifest()
    differences={p:{'signal_sha256':plan['source_tree_manifest'].get(p),'outcome_sha256':manifest.get(p)}
                 for p in set(manifest)|set(plan['source_tree_manifest']) if manifest.get(p)!=plan['source_tree_manifest'].get(p)}
    permitted={'stockfish_core/yaogu/strategy_tracking.py','stockfish_core/yaogu/candidate_service.py','stockfish_core/yaogu/replay_statistics.py','scripts/r05_weekly_replay.py'}
    if set(differences)-permitted:raise ValueError('UNREGISTERED_OUTCOME_IMPLEMENTATION_CHANGE:'+','.join(sorted(set(differences)-permitted)))
    path=output/'outcome_execution_plan.json'
    if path.exists():
        frozen=json.loads(path.read_text(encoding='utf-8'))
        if frozen['source_tree_manifest']!=manifest or frozen['parent_signal_plan_hash']!=digest(plan):raise ValueError('FROZEN_OUTCOME_EXECUTION_CHANGED')
        return frozen
    frozen={'task_id':'R05','frozen_at':datetime.now(SH).isoformat(),'parent_signal_plan_hash':digest(plan),
            'selection_identity':source_identity(),'source_tree_manifest':manifest,'source_tree_hash':digest(manifest),
            'changes_from_signal_execution':differences,'selection_rules_changed':False,
            'execution_cutoff':plan['execution_cutoff'],'horizons':plan['horizons'],
            'execution_assumptions':plan['execution_assumptions'],'denominator':plan['denominator'],
            'reason':'INITIAL_OUTCOME_EXECUTION_SAME_FROZEN_SOURCE_AS_CANONICAL_SIGNAL_RUNS' if not differences else 'REUSE_EXACT_BUNDLE_AND_CUTOFF_VALIDATION_CONTEXT; EXPLICIT_RUN_AND_METRIC_POLICY_LABEL_IDENTITY; PERSIST_PREVIOUSLY_ARCHIVED_EPISODES; DISTINGUISH_TRUE_EPISODE_FIRST_FROM_WINDOW_FIRST_SIGNAL; EXPORT_CANONICAL_VIEW_CONSISTENCY',
            'prior_interrupted_attempt':'../weekly-outcomes-command-01/execution.json',
            'original_runtime_source':'../provenance/replay-runtime-original',
            'prior_signal_attempt_manifest':'../preserved_prior_replay_manifest.json',
            'original_signal_artifacts_unchanged':True}
    dump(path,frozen)
    return frozen


def outcomes(output,supply,plan):
    execution=freeze_outcome_execution(output,plan)
    a=HistoricalReplayAssembler(supply,plan['historical_source_policy']);cutoff=datetime.fromisoformat(plan['execution_cutoff'])
    bundle,audit=a.build(plan['primary_sessions'][-1],history_sessions=45,include_security_history=True)
    reference=save_input(supply/'canonical-inputs'/'outcome-through-last-session.json.gz',bundle)
    dump(output/'outcome_input_reference.json',{**reference,'normalization_audit':audit,'actual_execution_source_manifest':code_manifest(),'outcome_execution_plan_hash':digest(execution)})
    context=OutcomeContext(bundle,cutoff)
    prepared=(context.bars,_securities(bundle,cutoff))
    runs=[];compact_labels=[];tracking_runs=[];episodes={};prior_observations=[];all_paper=[]
    for d in plan['attempt_sessions']:
        target=output/d
        if not(target/'rank.json').exists():continue
        run=json.loads((target/'rank.json').read_text(encoding='utf-8'));runs.append(run)
        if run['replay_plan_hash']!=digest(plan) or file_hash(target/'rank.json')!=json.loads((target/'attempt.json').read_text(encoding='utf-8'))['rank_file_sha256']:raise ValueError('IMMUTABLE_SIGNAL_PROVENANCE_MISMATCH')
        values=evaluate_strategy_experiment_outcomes(run,bundle,cutoff,horizons=HORIZONS,_context=context)
        # Full canonical observations are separately compressed; tabular rows
        # retain logical/revision IDs, exact returns and every metric state.
        with gzip.open(target/'canonical_outcomes.jsonl.gz','xt',encoding='utf-8') as out:
            for value in values:out.write(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
        compact_labels.extend({k:v for k,v in r.items() if k!='session_states'} for r in values)
        tracking=build_strategy_tracking(run,bundle,cutoff,horizons=HORIZONS,existing_episodes=list(episodes.values()),prior_observations=prior_observations,_context=context)
        tracking_runs.append(tracking)
        compact_labels.extend({k:v for k,v in r.items() if k!='session_states'} for r in tracking.get('shadow_outcomes',[]))
        for e in tracking['episodes']:episodes[e['episode_id']]=e
        prior_observations.extend(tracking['observations'])
        dump(target/'tracking.json',tracking)
        paper=evaluate_paper_execution(run,bundle,cutoff,horizons=HORIZONS,prepared=prepared)
        jsonl(target/'paper_trades.jsonl',paper);all_paper.extend(paper)
        print(json.dumps({'outcome_date':d,'labels':len(values),'paper':len(paper),'focus_episodes':len(tracking['episodes'])}),flush=True)
        del values;gc.collect()
    selected=select_label_revisions(compact_labels,cutoff)
    jsonl(output/'post_exit_shadow.jsonl',[r for r in selected if r.get('label_kind')=='POST_EXIT_SHADOW'])
    jsonl(output/'outcomes.jsonl',selected)
    jsonl(output/'revision_selection.jsonl',[{'logical_label_id':r['logical_label_id'],'observation_id':r['observation_id'],'policy':'LATEST_VALID_AS_OF_COMMON_CUTOFF_ONE_PER_LOGICAL_LABEL'} for r in selected])
    csv_file(output/'outcomes.csv',selected,['strategy_id','instrument_id','observation_date','horizon','label_end','price_label_status','price_direction','return_exact','selected_focus','logical_label_id','observation_id','run_id'])
    matrix=[];per_date=[]
    for run in runs:
        d=run['observation_date']
        table=statistics_table(selected,runs,tracking_runs,[d],HORIZONS,IDS)
        for row in table:
            p=row['pooled_signals'];target=str(a.calendar[a.calendar.index(date.fromisoformat(d))+row['horizon']])
            matrix.append({'observation_date':d,'primary_window':d in plan['primary_sessions'],'strategy_id':row['strategy_id'],'horizon':row['horizon'],
                           'target_session':target,'calendar_mature':target<=plan['primary_sessions'][-1],**p})
        per_date.append({'observation_date':d,'statistics':table})
    csv_file(output/'maturity_matrix.csv',matrix,['observation_date','primary_window','strategy_id','horizon','target_session','calendar_mature','signals','mature_valid','not_matured','missing_or_incomparable','positive','zero','negative','up_rate','mean_return','median_return','p10_return'])
    dump(output/'statistics_by_date.json',per_date)
    primary=statistics_table(selected,runs,tracking_runs,plan['primary_sessions'],HORIZONS,IDS)
    dump(output/'statistics_primary_5_sessions.json',primary)
    dump(output/'statistics_attempt_20_sessions.json',statistics_table(selected,runs,tracking_runs,plan['attempt_sessions'],HORIZONS,IDS))
    supplement={str(h):{'dates':mature_window(a.calendar,plan['attempt_sessions'],plan['primary_sessions'][-1],h),'statistics':statistics_table(selected,runs,tracking_runs,mature_window(a.calendar,plan['attempt_sessions'],plan['primary_sessions'][-1],h),(h,),IDS)} for h in HORIZONS}
    dump(output/'statistics_mature_supplement.json',supplement)
    paper_summary=[]
    for sid in IDS:
        for h in HORIZONS:
            for name,days in [('PRIMARY_5',plan['primary_sessions']),('ATTEMPT_20',plan['attempt_sessions'])]:
                paper_summary.append({'window':name,'strategy_id':sid,'horizon':h,**summarize_paper([r for r in all_paper if r['strategy_id']==sid and r['holding_horizon']==h and r['signal_date'] in days])})
    dump(output/'paper_execution_summary.json',paper_summary)
    feedback=build_strategy_feedback_report(runs[-1],selected,tracking_runs,cutoff,runs=runs,sessions=a.calendar)
    dump(output/'production_feedback_5_20_60.json',feedback)
    dump(output/'scope_and_comparators.json',{'common_pool_policy':plan['common_pool'],'selected_intersection_used':False,
        'dates':[{'observation_date':r['observation_date'],'common_ids':r['results'][PV]['complete_feature_ids'],
                  'actual_coverage':{sid:{'status':v.get('status'),'qualified_ids':v.get('qualified_ids'),'ranked_count':sum(i.get('rank') is not None for i in v.get('items',[]))} for sid,v in r['results'].items() if not v.get('compatibility_alias')}} for r in runs],
        'scope_status':'LIMITED_SCOPE','market_edge':'NOT_ESTABLISHED'})
    print(json.dumps({'completed_signal_dates':len(runs),'logical_labels':len(selected),'paper_rows':len(all_paper),'market_edge':'NOT_ESTABLISHED'}),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--supply',type=Path,required=True);p.add_argument('--sources',type=Path,required=True)
    p.add_argument('--phase',choices=['plan','signals','outcomes','all'],default='all');args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);plan=freeze(args.output,args.supply,args.sources)
    if args.phase in ('signals','all'):signals_parallel(args.output,args.supply,plan)
    if args.phase in ('outcomes','all'):outcomes(args.output,args.supply,plan)


if __name__=='__main__':main()
