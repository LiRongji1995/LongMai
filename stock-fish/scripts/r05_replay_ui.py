"""Review saved real replay runs on an isolated loopback application instance."""
import argparse
import gzip
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.api import create_app
from stockfish_core.yaogu.candidate_service import CandidateService
from types import SimpleNamespace


def seed_derived(store, writer, replay):
    counts={'tracking_runs':0,'research_reviews':0,'episodes':0,'signals':0,'episode_observations':0,'outcome_revisions':0}
    for path in sorted(replay.glob('????-??-??/tracking.json')):
        tracking=json.loads(path.read_text(encoding='utf-8'))
        with store.transaction() as connection:
            store._put_document(connection,'strategy_tracking_runs',tracking['tracking_run_id'],tracking)
            for key, namespace, id_key, count_key in (
                ('episodes','research_episodes','episode_id','episodes'),
                ('signals','research_episode_signals','signal_record_id','signals'),
                ('observations','research_episode_observations','observation_id','episode_observations'),
            ):
                for value in tracking.get(key,[]):
                    store._put_document(connection,namespace,value[id_key],value)
                    counts[count_key]+=1
        counts['tracking_runs']+=1
    # Import full canonical revisions before compact report rows. Their IDs bind
    # session_states too, so a compact projection must never replace the original.
    seen=set()
    for path in sorted(replay.glob('????-??-??/canonical_outcomes.jsonl.gz')):
        with gzip.open(path,'rt',encoding='utf-8') as source, store.transaction() as connection:
            for line in source:
                if not line.strip():continue
                value=json.loads(line)
                store._put_document(connection,'strategy_trading_session_observations',value['observation_id'],value)
                seen.add(value['observation_id']); counts['outcome_revisions']+=1
    # Supplemental diagnostics which have no separate canonical file remain exact
    # copies of their report payload, without changing market results.
    path=replay/'outcomes.jsonl'
    if path.exists():
        with path.open(encoding='utf-8') as source, store.transaction() as connection:
            for line in source:
                if not line.strip():continue
                value=json.loads(line)
                if value['observation_id'] in seen:continue
                store._put_document(connection,'strategy_trading_session_observations',value['observation_id'],value)
                seen.add(value['observation_id'])
                counts['outcome_revisions']+=1
    path=replay/'production_feedback_5_20_60.json'
    if path.exists():
        review=json.loads(path.read_text(encoding='utf-8'))
        with store.transaction() as connection:CandidateService._write_research_review(writer,connection,review)
        counts['research_reviews']+=1
    return counts


def main():
    p=argparse.ArgumentParser();p.add_argument('--replay',type=Path,required=True);p.add_argument('--private-root',type=Path,required=True);p.add_argument('--port',type=int,default=18006);p.add_argument('--seed-only',action='store_true');p.add_argument('--serve-only',action='store_true');args=p.parse_args()
    if args.seed_only and args.serve_only:p.error('seed-only and serve-only are mutually exclusive')
    if args.serve_only and not (args.private_root/'seed-complete.json').exists():p.error('serve-only requires a completed seed manifest')
    args.private_root.mkdir(parents=True,exist_ok=True)
    if args.seed_only:
        store=SQLiteStore(args.private_root/'ui.sqlite3');writer=SimpleNamespace(store=store,_selected_strategy_rows=CandidateService._selected_strategy_rows);count=0
        for path in sorted(args.replay.glob('????-??-??/rank.json')):
            run=json.loads(path.read_text(encoding='utf-8'));store.put_document('strategy_experiment_runs',run['run_id'],run)
            with store.transaction() as connection:CandidateService._write_strategy_projection(writer,connection,run)
            count+=1
        result={'isolated_review_only':True,'seeded_count':count,'derived':seed_derived(store,writer,args.replay),'new_job_owner_created':False}
        (args.private_root/'seed-complete.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result),flush=True)
        return
    service=ApplicationService(root=args.private_root/'state',store=SQLiteStore(args.private_root/'ui.sqlite3'),data_dir=args.private_root/'no_qlib_model')
    try:
        seeded=[]
        for path in ([] if args.serve_only else sorted(args.replay.glob('????-??-??/rank.json'))):
            run=json.loads(path.read_text(encoding='utf-8'))
            service.store.put_document('strategy_experiment_runs',run['run_id'],run)
            with service.store.transaction() as connection:service.candidates._write_strategy_projection(connection,run)
            seeded.append({'observation_date':run['observation_date'],'run_id':run['run_id']})
        derived=json.loads((args.private_root/'seed-complete.json').read_text(encoding='utf-8'))['derived'] if args.serve_only else seed_derived(service.store,service.candidates,args.replay)
        print(json.dumps({'isolated_review_only':True,'seeded':seeded,'derived':derived,'port':args.port,'serve_only':args.serve_only}),flush=True)
        if args.seed_only:return
        app=create_app(service);app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False,threaded=True)
    finally:service.close()


if __name__=='__main__':main()
