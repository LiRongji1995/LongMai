"""Summarize real request records; missing legacy counters remain unknown."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root,evidence):
    requests=[json.loads(p.read_text(encoding='utf-8')) for p in (root/'cache').glob('request-*.json')]
    requests={r['request_receipt_id']:r for r in requests}.values()
    groups=defaultdict(list)
    for r in requests:groups[r['endpoint']].append(r)
    rows=[]
    for endpoint,values in sorted(groups.items()):
        tasks=Counter(json.dumps(r['parameters'],sort_keys=True) for r in values)
        def counts(key):
            known=[r['coverage'][key] for r in values if key in r.get('coverage',{})]
            return {'known_total':sum(known),'requests_with_known_counter':len(known),'requests_counter_unknown':len(values)-len(known)}
        rows.append({'endpoint':endpoint,'actual_request_executions':len(values),'unique_parameter_tasks':len(tasks),
                     'repeat_executions_after_first':sum(n-1 for n in tasks.values()),
                     'states':dict(Counter(r['status'] for r in values)),
                     'http_attempts':counts('http_attempts'),'sdk_operations':counts('sdk_operations'),
                     'actual_upstream_http_receipt_count':sum(sum(x.get('field_version')=='UPSTREAM_HTTP_BODY_V1' for x in r['receipts']) for r in values)})
    cache_hits=0
    for path in evidence.glob('capture-*/stdout.log'):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:record=json.loads(line)
            except (ValueError,TypeError):continue
            if record.get('cache_hit') is True:cache_hits+=1
    latest=[json.loads(p.read_text(encoding='utf-8')) for p in (root/'checkpoints').glob('*.json')]
    final=[{'endpoint':r['endpoint'],'parameters':r['parameters'],'status':r['status'],'rows':len(r.get('rows',[])),
            'pagination_complete':r.get('pagination_complete'),'original_receipt_id':r['request_receipt_id']} for r in latest]
    manifest=[{'path':str(p.resolve()),'sha256':sha(p),'bytes':p.stat().st_size,'distribution':'LOCAL_ONLY_NOT_REDISTRIBUTED'}
              for p in sorted(root.rglob('*')) if p.is_file() and p.suffix.lower() in ('.json','.gz')]
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'evidence_class':'ACTUAL_PUBLIC_SOURCE_REQUESTS',
            'by_endpoint':rows,'successful_cache_reuses_in_recorded_capture_commands':cache_hits,
            'unique_parameter_tasks':sum(r['unique_parameter_tasks'] for r in rows),
            'actual_request_executions_including_retries':sum(r['actual_request_executions'] for r in rows),
            'budget_interpretation':'150_PARAMETERIZED_TASKS_PER_CAPTURE_PHASE_REPEATS_COUNTED_SEPARATELY_NOT_A_GLOBAL_150_HTTP_LIMIT',
            'legacy_measurement_limits':'Missing counters are UNKNOWN; BaoStock socket SDK operations are not HTTP attempts or HTTP pages.',
            'final_checkpoints':final,'raw_artifact_manifest':manifest,
            'privacy':'Only paths/hashes/acquisition commands supplied; no full-market raw cache, canonical input or private DB in shareable package.',
            'original_review_snapshot':'4599_IDENTIFIER_27587_ROW_PRIOR_SNAPSHOT_NOT_PRESENT_LOCALLY_NOT_FABRICATED',
            'acquisition_commands':['python scripts/r05_capture_replay_inputs.py --output LOCAL_CAPTURE_DIR --phase identity',
                                    'python scripts/r05_capture_replay_inputs.py --output LOCAL_CAPTURE_DIR --phase daily',
                                    'python scripts/r05_capture_replay_inputs.py --output LOCAL_CAPTURE_DIR --phase factors'],
            'optional_supply':{'TUSHARE':'AUTH_REQUIRED_HTTP_ZERO_SEPARATE_REAL_AUTH_PROBE','VERIFIED_HISTORICAL_LIMITS':'UNAVAILABLE',
                'DATED_THEME_MEMBERSHIP':'UNAVAILABLE','FUNDS_AND_DRAGON_TIGER_HISTORY':'NOT_AVAILABLE_FOR_COMPARABLE_WINDOW'},
            'market_edge':'NOT_ESTABLISHED'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--supply',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=audit(args.supply,args.evidence)
    with args.output.open('x',encoding='utf-8') as out:json.dump(result,out,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ('unique_parameter_tasks','actual_request_executions_including_retries','successful_cache_reuses_in_recorded_capture_commands')},ensure_ascii=False))


if __name__=='__main__':main()
