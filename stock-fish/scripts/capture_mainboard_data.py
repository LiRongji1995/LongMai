"""运行/续跑默认网页同款的有界全目录DAG。

仅输出真实观测。本地操作不会激活用户输入；网页采集仍通过
CandidateService 的正常事务发布。
"""
from pathlib import Path
import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stockfish_core.yaogu.data_pipeline import MainboardPipeline
from stockfish_core.yaogu.request_queue import RequestBudget

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--cache',required=True)
p.add_argument('--output',required=True)
p.add_argument('--max-requests',type=int,default=128)
p.add_argument('--max-seconds',type=float,default=600)
p.add_argument('--rounds',type=int,default=1,help='执行多轮有界续跑，输出到 output/round-XXX；默认保持单输出目录契约。')
args=p.parse_args()
out=Path(args.output)
if not 1<=args.rounds<=20:
    raise SystemExit('rounds 必须在 1..20 范围内')
if out.exists():
    raise SystemExit('输出目录必须是新目录；请复用 cache 续跑')
def progress(value):
    print(json.dumps({k:value[k] for k in ('requested','states','network_requests_this_run','cache_hits_this_run','elapsed_seconds')},ensure_ascii=False),flush=True)
runs=[]
for index in range(args.rounds):
    target=out if args.rounds==1 else out / f'round-{index+1:03d}'
    pipeline=MainboardPipeline(args.cache,budget=RequestBudget(max_requests=args.max_requests,max_seconds=args.max_seconds))
    bundle=pipeline.capture(target,datetime.now(ZoneInfo('Asia/Shanghai')),progress=progress)
    report=pipeline.last_report
    summary={'round':index+1,'capture_report':str(target/'capture-report.json'),
        'consumer_status':report['consumer_status'],'coverage_type':bundle.coverage_type,
        'bar_rows':len(bundle.bars),'remaining_requests':len(report['remaining_requests']),
        'network_requests':report['queue']['network_requests_this_run'],
        'budget_exhausted':report['queue']['budget_exhausted']}
    runs.append(summary)
    print(json.dumps(summary|{'market_edge':'NOT_ESTABLISHED'},ensure_ascii=False),flush=True)
    if not report['remaining_requests']:
        break
    if report['queue']['network_requests_this_run']==0 and report['queue']['cache_hits_this_run']==0:
        break
print(json.dumps({'rounds':runs,'final_capture_report':runs[-1]['capture_report'] if runs else None,
                  'final_consumer_status':runs[-1]['consumer_status'] if runs else None,
                  'market_edge':'NOT_ESTABLISHED'},ensure_ascii=False))
