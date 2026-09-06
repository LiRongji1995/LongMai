"""R05 bounded, resumable public source collection for the frozen replay window."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.observed_data import exchange_calendar_2026
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.provider_requests import execute_request

SH=ZoneInfo('Asia/Shanghai')


def capture(root, endpoint, params, *, force=False):
    key=digest({'endpoint':endpoint,'parameters':params})
    path=root/'checkpoints'/(key+'.json')
    if path.exists() and not force:
        prior=json.loads(path.read_text(encoding='utf-8'))
        if prior['status']=='COMPLETE' and prior.get('pagination_complete'):
            print(json.dumps({'endpoint':endpoint,'parameters':params,'cache_hit':True,'rows':len(prior.get('rows',[]))}),flush=True)
            return prior
    result=execute_request(endpoint,params,root/'cache',timeout=90)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)
    print(json.dumps({'endpoint':endpoint,'parameters':params,'status':result['status'],
                      'rows':len(result.get('rows',[])),'error_code':result.get('error_code')},ensure_ascii=False),flush=True)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--phase',choices=['identity','daily','factors'],default='identity')
    args=p.parse_args(); root=args.output;root.mkdir(parents=True,exist_ok=True)
    plan_path=root/'acquisition_plan.json'
    if plan_path.exists():plan=json.loads(plan_path.read_text(encoding='utf-8'))
    else:
        now=datetime.now(SH)
        completed=[d for d in sorted(exchange_calendar_2026().sessions) if datetime.combine(d,time(15),SH)<=now]
        plan={'task_id':'R05','frozen_execution_cutoff':now.isoformat(),'primary_sessions':[str(d) for d in completed[-5:]],
              'attempt_sessions':[str(d) for d in completed[-20:]],'price_sessions':[str(d) for d in completed[-45:]],
              'budget':{'maximum_tasks':150,'max_concurrency':2,'per_task_timeout_seconds':90},
              'selection':'ALL_AVAILABLE_DATED_ELIGIBLE_SECURITIES_NO_PERFORMANCE_FILTER',
              'raw_market_data_distribution':'LOCAL_ONLY_PATH_HASH_MANIFEST',
              'calendar_source':'stockfish_core/exchange_calendar_2026.json',
              'calendar_sha256':__import__('hashlib').sha256(Path('stockfish_core/exchange_calendar_2026.json').read_bytes()).hexdigest()}
        plan_path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    last=plan['primary_sessions'][-1]
    if args.phase=='identity':
        tasks=[('mainboard_sse',{}),('master_szse',{}),('delisting_sse',{}),
               ('calendar',{'start_date':'2024-01-01','end_date':'2026-12-31'})]
    elif args.phase=='daily':
        tasks=[('market_daily',{'date':d}) for d in plan['price_sessions']]
        tasks += [('master_all',{'date':d}) for d in plan['attempt_sessions']]
    else:
        results=[json.loads(p.read_text(encoding='utf-8')) for p in (root/'checkpoints').glob('*.json')]
        codes=set()
        for r in results:
            if r['endpoint']=='mainboard_sse':codes.update('sh.'+str(x['证券代码']) for x in r.get('rows',[]))
            if r['endpoint']=='master_szse':codes.update('sz.'+str(x['A股代码']) for x in r.get('rows',[]) if x.get('板块')=='主板')
        codes=sorted(codes)
        tasks=[('adjustment_factors',{'codes':codes[i:i+50],'start_date':'1990-01-01','end_date':last}) for i in range(0,len(codes),50)]
    if len(tasks)>plan['budget']['maximum_tasks']:raise ValueError('TASK_BUDGET_EXCEEDED')
    # The public anonymous BaoStock session is serialized. The provider also
    # holds a cross-process lock so concurrent callers cannot log each other out.
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures=[pool.submit(capture,root,endpoint,params) for endpoint,params in tasks]
        results=[f.result() for f in as_completed(futures)]
    summary={'phase':args.phase,'completed_at':datetime.now(SH).isoformat(),'task_count':len(tasks),
             'states':{state:sum(r['status']==state for r in results) for state in sorted({r['status'] for r in results})},
             'rows':sum(len(r.get('rows',[])) for r in results)}
    (root/(args.phase+'-summary.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
