"""Bounded per-endpoint real authorization probe; never emits token values."""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stockfish_core.yaogu.data_pipeline import tushare_request_plan
from stockfish_core.yaogu.provider_requests import execute_request,TUSHARE_ENDPOINTS
from stockfish_core.yaogu.provider_budget import initialize,counts


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--date',required=True,type=date.fromisoformat)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    budget_path=args.output/'http-budget.sqlite3';budget_id=initialize(budget_path,32,minimum_interval=.5)
    requests=list({ep:(ep,params) for ep,params in tushare_request_plan(args.date,(args.date,))}.values())
    report={'probe_kind':'ACTUAL_ENVIRONMENT_AUTHORIZATION_CHECK','started_at':datetime.now(timezone.utc).isoformat(),
        'token_configured':bool(os.environ.get('TUSHARE_TOKEN') or os.environ.get('TUSHARE_PRO_TOKEN')),'endpoints':[]}
    for endpoint,params in requests:
        value=execute_request(endpoint,params,args.output/'raw'/endpoint,timeout=40,max_http_requests=2,
            budget_path=str(budget_path),budget_id=budget_id,queue_request_id=endpoint)
        row={'endpoint':endpoint,'parameters':params,'official_document':TUSHARE_ENDPOINTS[endpoint]['doc_url'],
            'document_review_date':'2026-09-06','status':value.get('error_code') or value['status'],
            'http_attempts':value.get('coverage',{}).get('http_attempts',0),'rows':len(value['rows']),
            'request_receipt_id':value['request_receipt_id'],'source_hashes':value['implementation_source_hashes']}
        report['endpoints'].append(row)
        (args.output/'endpoint-status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report.update(completed_at=datetime.now(timezone.utc).isoformat(),transport_counts=counts(budget_path,budget_id),
        paid_source_status='AUTH_REQUIRED_REAL_VENDOR_DATA_NOT_VERIFIED' if not report['token_configured'] else 'PER_ENDPOINT_RESULTS_ONLY_NOT_ALL_PERMISSIONS_CERTIFIED')
    (args.output/'endpoint-status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'token_configured':report['token_configured'],'endpoint_count':len(requests),'transport_counts':report['transport_counts'],
        'statuses':{r['status']:sum(x['status']==r['status'] for x in report['endpoints']) for r in report['endpoints']}}))


if __name__=='__main__':main()
