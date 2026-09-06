"""Run/resume the same bounded full-directory DAG used by the default Web page.

Outputs real observations only. This local operation does not activate user
inputs; Web capture publishes through CandidateService's normal transaction.
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
args=p.parse_args()
out=Path(args.output)
if out.exists():
    raise SystemExit('Output must be a new directory; reuse cache to resume.')
pipeline=MainboardPipeline(args.cache,budget=RequestBudget(max_requests=args.max_requests,max_seconds=args.max_seconds))
def progress(value):
    print(json.dumps({k:value[k] for k in ('requested','states','network_requests_this_run','cache_hits_this_run','elapsed_seconds')},ensure_ascii=False),flush=True)
bundle=pipeline.capture(out,datetime.now(ZoneInfo('Asia/Shanghai')),progress=progress)
print(json.dumps({'capture_report':str(out/'capture-report.json'),'consumer_status':pipeline.last_report['consumer_status'],
                  'coverage_type':bundle.coverage_type,'bar_rows':len(bundle.bars),'market_edge':'NOT_ESTABLISHED'},ensure_ascii=False))
