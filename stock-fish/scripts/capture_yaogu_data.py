"""Bounded actual public-source probe; no account, model, live database or orders.

Usage: .venv/Scripts/python scripts/capture_yaogu_data.py --output DIR
Writes immutable receipts, actual RAW history, honest incomplete MarketInput.
No fabricated price limits, historical board identity or PIT theme membership.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from datetime import date,datetime,timedelta,timezone
import json
import subprocess
from stockfish_core.yaogu.providers import archive_receipt,capture_akshare

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--end',default='2026-09-04');p.add_argument('--sessions',type=int,default=312);p.add_argument('--codes',default='sh.600000,sz.000001,sh.600519,sz.002261,sh.600410');args=p.parse_args()
    if not 60<=args.sessions<=504: raise ValueError('60_TO_504_SESSION_BUDGET')
    dest=Path(args.output);dest.mkdir(parents=True,exist_ok=True); raw=dest/'raw'
    end=date.fromisoformat(args.end);start=end-timedelta(days=800)
    request={'provider':'baostock','start':start.isoformat(),'end':end.isoformat(),'max_sessions':args.sessions,'codes':args.codes.split(',')}
    if len(request['codes'])>10: raise ValueError('10_SYMBOL_PROBE_LIMIT')
    probes=[]; payload=None
    try:
        r=subprocess.run([sys.executable,'-m','stockfish_core.yaogu.provider_worker'],input=json.dumps(request),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=110)
        if r.returncode: raise ValueError('BAOSTOCK_FAILED')
        payload=json.loads(r.stdout)
        receipt=archive_receipt(raw,'Baostock','RAW_DAILY_HISTORY',request,payload,pagination_complete=True,field_version=payload['version'],source_url='https://www.baostock.com/')
    except (ValueError,subprocess.TimeoutExpired): receipt=archive_receipt(raw,'Baostock','RAW_DAILY_HISTORY',request,{'error':'FETCH_FAILED'},status='FETCH_FAILED')
    probes.append(receipt)
    # Current exchange membership is for current coverage only, never backfilled.
    exchange_rows={}
    for endpoint,parameters in [('stock_info_sh_name_code',{'symbol':'主板A股'}),('stock_info_sz_name_code',{'symbol':'A股列表'}),('stock_zt_pool_em',{'date':args.end.replace('-','')}),('stock_lhb_detail_em',{'start_date':args.end.replace('-',''),'end_date':args.end.replace('-','')}),('stock_board_concept_name_em',{})]:
        rec,values=capture_akshare(endpoint,parameters,raw,timeout=35);probes.append(rec);exchange_rows[endpoint]=values
        print(json.dumps({'endpoint':endpoint,'status':rec['status'],'rows':len(values)}),flush=True)
    summary={'captured_at':datetime.now(timezone.utc).isoformat(),'coverage_type':'RECONSTRUCTED_HISTORICAL','probes':probes,
             'requested_history_sessions':args.sessions,'requested_instruments':request['codes'],'whole_mainboard_coverage':False,
             'historical_pit_theme':'UNAVAILABLE','verified_daily_limits':'UNAVAILABLE','auction_0925':'UNAVAILABLE',
             'rights':'Existing public SDK access used for bounded local research; commercial redistribution NOT_VERIFIED; SDK license is not underlying data license.'}
    if payload:
        from stockfish_core.yaogu.contracts import MarketInput,UniverseSnapshot,SecurityRecord,DailyBar,SourceReceipt,digest
        seen=receipt['first_seen_at'];clock=dict(event_time=seen,source_published_at=seen,first_seen_at=seen,ingested_at=seen)
        securities=[];bars=[]
        for record in payload['records']:
            if not record['basic']: continue
            basic=record['basic'][0];iid=record['code'].split('.')[1]+'.'+record['code'].split('.')[0].upper()
            latest=record['daily'][-1] if record['daily'] else {}
            # No prefix-based final board evidence. A current exchange list can
            # verify today's board, but cannot create historical effective dates.
            securities.append(SecurityRecord(instrument_id=iid,exchange='SSE' if iid.endswith('.SH') else 'SZSE',board='UNKNOWN',security_type='A_COMMON' if basic.get('type')=='1' else 'UNKNOWN',listed_on=basic['ipoDate'],effective_from=end,st=latest.get('isST')=='1' if latest else None,suspended=latest.get('tradestatus')=='0' if latest else None,name=basic.get('code_name',''),source_refs=(receipt['receipt_id'],),**clock))
            for row in record['daily']:
                values={name:row.get(source) or None for name,source in [('open','open'),('high','high'),('low','low'),('close','close'),('reference_prev_close','preclose'),('volume_shares','volume'),('amount_cny','amount')]}
                for name in ('open','high','low','close','reference_prev_close'):
                    if values[name] is not None and float(values[name])<=0: values[name]=None
                bars.append(DailyBar(instrument_id=iid,session=row['date'],turnover_fraction=float(row['turn'])/100 if row.get('turn') else None,turnover_basis='BAOSTOCK_TURN_PERCENT_FLOAT_SHARES',source_refs=(receipt['receipt_id'],),quality='PARTIAL',**values,**clock))
        universe=UniverseSnapshot(snapshot_id='ACTUAL_PARTIAL_'+digest(payload)[:20],securities=tuple(securities),complete=False,expected_count=None,source_refs=(receipt['receipt_id'],),**clock)
        src=SourceReceipt(receipt_id=receipt['receipt_id'],provider='Baostock',dataset='RAW_DAILY_HISTORY',content_hash=receipt['content_hash'],field_version=receipt['field_version'],row_count=sum(len(r['daily']) for r in payload['records']),pagination_complete=True,status='PARTIAL',source_url=receipt['source_url'],**clock)
        bundle=MarketInput(observation_date=end,sessions=tuple(payload['sessions']),universe=universe,bars=tuple(bars),receipts=(src,),coverage_type='RECONSTRUCTED_HISTORICAL')
        filename=dest/('market-input-'+digest(bundle)[:20]+'.json');filename.write_text(bundle.model_dump_json(indent=2),encoding='utf-8')
        summary.update(actual_sessions=len(payload['sessions']),actual_start=payload['sessions'][0],actual_end=payload['sessions'][-1],actual_raw_rows=len(bars),market_input=filename.name,historical_identity='UNKNOWN_NO_BACKFILL',raw_symbol_rows={r['code']:len(r['daily']) for r in payload['records']})
    (dest/'coverage.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='probes'},ensure_ascii=False))

if __name__=='__main__':main()
