"""Execute bounded historical rule attempts without inventing historical evidence.

Actual inputs retain original clocks, board intervals, adjustment bases and refs.
Missing PIT facts produce date/symbol audit rows, never artificially valid scores.
No training, parameter search, database writes or network calls are performed.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime,time,timezone
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from stockfish_core.yaogu.contracts import MarketInput,digest
from stockfish_core.yaogu.config import load_config
from stockfish_core.yaogu.engine import run,security_at,qualification
from stockfish_core.yaogu.events import limit_event
from stockfish_core.yaogu.evaluation import evaluate_study,export_study,StudyConfig,walk_forward_boundaries
from stockfish_core.yaogu.outcomes import evaluate_outcomes
from stockfish_core.yaogu.emotion_history import reconstructed_artifact

def write_json(path,value):
    with path.open('x',encoding='utf-8') as out:json.dump(value,out,ensure_ascii=False,indent=2,default=str,allow_nan=False)

def write_csv(path,rows,default_fields):
    fields=sorted({k for r in rows for k in r}) or default_fields
    with path.open('x',encoding='utf-8-sig',newline='') as out:
        writer=csv.DictWriter(out,fields);writer.writeheader()
        for row in rows:
            values={k:json.dumps(v,ensure_ascii=False,default=str) if isinstance(v,(dict,list,tuple)) else v for k,v in row.items()}
            writer.writerow({k:"'"+v if isinstance(v,str) and v[:1] in '=+-@' else v for k,v in values.items()})

def execute(inputs,output,*,config_path=None,warmup=60,maximum=504,allow_test_fixture=False):
    if warmup!=60 or not 312<=maximum<=504:raise ValueError('V1_STUDY_REQUIRES_60_WARMUP_AND_312_TO_504_MAX')
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=False)
    config=load_config(config_path);started=datetime.now(timezone.utc)
    runs=[];observations=[];per_day=[];scope_rows=[];raw_diagnostics=[];current_previews=[];sources=[];failures=[];emotion_artifacts=[]
    for file in inputs:
        file=Path(file).resolve();content=file.read_bytes();bundle=MarketInput.model_validate_json(content)
        if bundle.coverage_type=='TEST_FIXTURE' and not allow_test_fixture:raise ValueError('TEST_FIXTURE_REQUIRES_EXPLICIT_FLAG')
        scope_id=hashlib.sha256(content).hexdigest()[:20]
        sessions=tuple(d for d in bundle.sessions if d<=bundle.observation_date)[-maximum:]
        research=sessions[warmup:]
        if len(research)>252:research=research[:252]
        ids=sorted({s.instrument_id for s in bundle.universe.securities})
        sources.append(dict(scope_id=scope_id,input_file=file.name,input_sha256=hashlib.sha256(content).hexdigest(),coverage_type=bundle.coverage_type,
            requested_warmup_sessions=warmup,actual_calendar_sessions=len(sessions),research_session_count=len(research),
            actual_start=str(sessions[0]) if sessions else None,actual_end=str(sessions[-1]) if sessions else None,
            research_start=str(research[0]) if research else None,research_end=str(research[-1]) if research else None,
            instrument_ids=ids,universe_complete=bundle.universe.complete,master_observed_expected=bundle.universe.expected_count))
        by_key={(b.instrument_id,b.session):b for b in bundle.bars}
        scope_emotions=[]
        for day in research:
            cutoff=datetime.combine(day,time(20,30),ZoneInfo('Asia/Shanghai'))
            replay_coverage='RECONSTRUCTED_HISTORICAL' if bundle.coverage_type=='PROSPECTIVE_CAPTURED' else bundle.coverage_type
            history_mode='RECONSTRUCTED_EVALUATION_ARTIFACT' if replay_coverage in ('TEST_FIXTURE','RECONSTRUCTED_HISTORICAL') else 'PUBLISHED_CANDIDATE'
            subset=bundle.model_copy(update={'observation_date':day,'sessions':tuple(d for d in sessions if d<=day),
                'coverage_type':replay_coverage,
                'bars':tuple(b for b in bundle.bars if b.session<=day),'previous_emotions':()})
            raw_hash=digest(subset)
            subset=subset.model_copy(update={'previous_emotions':tuple(scope_emotions)})
            rid='history_'+scope_id+'_'+day.isoformat()
            try:
                result=run(subset,config,cutoff,profile='DAILY_V1',run_id=rid,market_session='HISTORICAL_EVENING_ATTEMPT',
                    history_mode=history_mode,raw_bundle_hash=raw_hash)
                runs.append(result)
                if history_mode=='RECONSTRUCTED_EVALUATION_ARTIFACT':
                    artifact=reconstructed_artifact(result,scope_id=scope_id,generated_at=datetime.now(timezone.utc))
                    scope_emotions.append(artifact)
                    emotion_artifacts.append({'scope_id':scope_id,'artifact_hash':digest(artifact),'artifact':artifact.model_dump(mode='json')})
                labels=evaluate_outcomes(result,bundle,datetime.now(timezone.utc));observations.extend(labels)
                per_day.append(dict(scope_id=scope_id,session=str(day),run_id=rid,status=result.status,coverage_type=result.coverage_type,
                    input_kind='RECONSTRUCTED_SOURCE_WITH_ORIGINAL_CLOCKS_NOT_STRICT_PIT',candidate_count=len(result.items),
                    fully_scored_count=sum(i.score is not None for i in result.items),excluded_count=len(result.excluded),unavailable_count=len(result.unavailable),
                    emotion_state=result.emotion.state,coverage=result.coverage,emotion_reasons=result.emotion.reasons))
                selected={i.instrument_id:i for i in result.items};engine_reasons={r['instrument_id']:r.get('reason_codes',[]) for r in (*result.excluded,*result.unavailable)}
                for iid in ids:
                    reasons=[]
                    if not bundle.universe.visible(cutoff,True):reasons.append('UNIVERSE_ENVELOPE_NOT_VISIBLE_AS_OF')
                    state=security_at(bundle.universe.securities,iid,day,cutoff,True)
                    reason=qualification(state,day,subset.sessions,config)
                    if reason:reasons.append(reason)
                    bar=by_key.get((iid,day))
                    if bar is None:reasons.append('RAW_BAR_NOT_ACQUIRED')
                    elif not bar.visible(cutoff,True):reasons.append('BAR_PUBLICATION_NOT_VERIFIABLE_AS_OF')
                    if not bar or not bar.limit_verified:reasons.append('HISTORICAL_LIMIT_UNVERIFIED')
                    if not bundle.themes:reasons.append('HISTORICAL_THEME_PIT_UNAVAILABLE')
                    if not bar or bar.comparable_basis is None:reasons.append('CROSS_SESSION_COMPARABLE_BASIS_UNAVAILABLE')
                    item=selected.get(iid)
                    scope_rows.append(dict(scope_id=scope_id,session=str(day),instrument_id=iid,run_id=rid,
                        engine_status=result.status,engine_reason_codes=engine_reasons.get(iid,[]),scope_diagnostic_reason_codes=sorted(set(reasons)),
                        structural_candidate=item is not None,primary_family=item.primary_family if item else None,
                        score=item.score if item else None,components=item.components if item else None,
                        selected_display=iid in result.display_ids,label_status='NO_CANDIDATE_NO_OBSERVATION' if item is None else 'SEE_OUTCOME_STORE'))
            except Exception as exc:
                failures.append(dict(scope_id=scope_id,session=str(day),error_type=type(exc).__name__,reason=str(exc)))
        for b in bundle.bars:
            raw_diagnostics.append(dict(scope_id=scope_id,instrument_id=b.instrument_id,session=str(b.session),open=b.open,high=b.high,low=b.low,close=b.close,
                reference_prev_close=b.reference_prev_close,volume_shares=b.volume_shares,amount_cny=b.amount_cny,turnover_fraction=b.turnover_fraction,
                source_refs=b.source_refs,first_seen_at=b.first_seen_at,diagnostic='RAW_OBSERVATION_NOT_PIT_FEATURE_OR_RETURN',
                comparable_basis=b.comparable_basis,score=None))
            if b.session==bundle.observation_date:
                current_previews.append(dict(scope_id=scope_id,event=limit_event(b).model_dump(mode='json'),
                    preview_scope='CURRENT_CAPTURED_EVENT_SEPARATE_FROM_HISTORICAL_252_ATTEMPTS',source_first_seen=str(b.first_seen_at)))
    completed=datetime.now(timezone.utc)
    evaluation=evaluate_study(runs,observations,StudyConfig(),completed,strategy_configs={config.config_hash:config})
    export_study(evaluation,root/'evaluation')
    all_sessions=sorted({r.observation_date for r in runs})
    folds=walk_forward_boundaries(all_sessions,observations,evaluated_at=completed)
    write_json(root/'walk-forward-boundaries.json',folds)
    write_json(root/'candidate-runs.json',[r.model_dump(mode='json') for r in runs])
    write_json(root/'reconstructed-emotion-artifacts.json',emotion_artifacts)
    write_json(root/'date-coverage.json',per_day);write_csv(root/'date-coverage.csv',per_day,['session','status'])
    write_json(root/'all-scope-status.json',scope_rows);write_csv(root/'all-scope-status.csv',scope_rows,['session','instrument_id','status'])
    write_csv(root/'raw-price-diagnostic.csv',raw_diagnostics,['session','instrument_id'])
    write_json(root/'current-event-previews.json',current_previews);write_json(root/'failures.json',failures)
    summary=dict(status='ATTEMPTS_EXECUTED_DATA_UNAVAILABLE' if not evaluation['samples'] and not failures else 'PARTIAL' if failures else evaluation['report']['status'],
        started_at=started.isoformat(),completed_at=completed.isoformat(),runtime_seconds=(completed-started).total_seconds(),source_scopes=sources,
        command=sys.argv,config_hash=config.config_hash,source_hashes=sorted({r.source_hash for r in runs}),
        run_count=len(runs),date_count=len(all_sessions),run_status_counts=dict(Counter(r.status for r in runs)),full_structural_sample_count=len(evaluation['samples']),
        scope_status_rows=len(scope_rows),actual_raw_diagnostic_rows=len(raw_diagnostics),outcome_observations=len(observations),exception_count=len(failures),
        scope_reason_counts=dict(Counter(reason for r in scope_rows for reason in r['scope_diagnostic_reason_codes'])),
        historical_validation='UNAVAILABLE' if not evaluation['samples'] else evaluation['report']['status'],execution_kind='ACTUAL_OFFLINE_RECONSTRUCTION_ATTEMPT_NOT_A_TEST_PASS',
        market_edge='NOT_ESTABLISHED',current_preview_limit_up_count=sum(r['event']['close_limit_up']=='TRUE' for r in current_previews),
        limitations=['NO_IDENTITY_BACKFILL','NO_PIT_THEME_FABRICATION','NO_CURRENT_ADJUSTMENT_BASIS_BACKFILL','NO_PRICE_ONLY_WEIGHT_CHANGE',
            'RESEARCH_EMOTION_AUTHORITY_IS_NOT_PAST_PROSPECTIVE_PUBLICATION',
            'RAW_PRICE_DIAGNOSTIC_HAS_NO_SCORES_OR_ASSUMED_EXECUTION_RETURNS','EMPTY_SAMPLE_IS_DATA_UNAVAILABLE_NOT_ZERO_SUCCESS_RATE'])
    write_json(root/'execution-summary.json',summary)
    files=[dict(path=f.relative_to(root).as_posix(),size=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sorted(root.rglob('*')) if f.is_file()]
    write_json(root/'manifest.json',dict(files=files,manifest_scope='LOCAL_CONTENT_INTEGRITY_NOT_AUTHENTICATION'))
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',action='append',required=True);p.add_argument('--output',required=True)
    p.add_argument('--config');p.add_argument('--warmup',type=int,default=60);p.add_argument('--max-sessions',type=int,default=504);p.add_argument('--allow-test-fixture',action='store_true')
    a=p.parse_args();summary=execute(a.input,a.output,config_path=a.config,warmup=a.warmup,maximum=a.max_sessions,allow_test_fixture=a.allow_test_fixture)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('source_scopes','scope_reason_counts')},ensure_ascii=True))
    return 1 if summary['exception_count'] else 0

if __name__=='__main__':raise SystemExit(main())
