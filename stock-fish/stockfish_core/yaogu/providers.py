"""Bounded public-source adapters. Cached responses are not PIT authorizations."""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import json
import subprocess
import sys
from zoneinfo import ZoneInfo

def archive_receipt(root,provider,dataset,parameters,payload,*,status='COMPLETE',pagination_complete=False,field_version='1',source_url=None,first_seen_at=None):
    """Immutable raw archive separated from normalized feature and outcome stores."""
    blocked=('token','secret','password','authorization','cookie','api_key','account')
    if any(any(s in k.lower() for s in blocked) for k in parameters): raise ValueError('SENSITIVE_REQUEST_PARAMETER')
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
    h=hashlib.sha256(raw).hexdigest(); seen=first_seen_at or datetime.now(timezone.utc).isoformat()
    receipt={'provider':provider,'dataset':dataset,'parameters':parameters,'field_version':field_version,
             'row_count':len(payload) if isinstance(payload,list) else None,'pagination_complete':pagination_complete,
             'status':status,'first_seen_at':seen,'ingested_at':datetime.now(timezone.utc).isoformat(),'content_hash':h,
             'source_url':source_url,'integrity_scope':'LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION'}
    rid=hashlib.sha256(json.dumps(receipt,sort_keys=True).encode()).hexdigest(); receipt['receipt_id']=rid
    for path,content in [(root/(h+'.raw.json'),raw),(root/(rid+'.receipt.json'),json.dumps(receipt,ensure_ascii=False,indent=2).encode())]:
        if path.exists():
            if path.read_bytes()!=content: raise ValueError('IMMUTABLE_RECEIPT_COLLISION')
        else: path.write_bytes(content)
    return receipt

AKSHARE_CAPABILITIES={
    'stock_info_sh_name_code':{'symbol':'主板A股'},
    'stock_info_sz_name_code':{'symbol':'A股列表'},
    'stock_zt_pool_em':{'date':'SESSION'},
    'stock_zt_pool_zbgc_em':{'date':'SESSION'},
    'stock_lhb_detail_em':{'start_date':'SESSION','end_date':'SESSION'},
    'stock_lhb_stock_detail_em':{'symbol':'SYMBOL','date':'SESSION','flag':'BUY_OR_SELL'},
    'stock_board_concept_name_em':{},
    'stock_board_concept_cons_em':{'symbol':'THEME'},
    'stock_individual_fund_flow':{'stock':'SYMBOL','market':'MARKET'},
}

def capture_akshare(endpoint,parameters,archive_root,timeout=40):
    if endpoint not in AKSHARE_CAPABILITIES or not 0<timeout<=60: raise ValueError('UNSUPPORTED_PROVIDER_REQUEST')
    request={'provider':'akshare','endpoint':endpoint,'parameters':parameters}
    try:
        completed=subprocess.run([sys.executable,'-m','stockfish_core.yaogu.provider_worker'],input=json.dumps(request),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        if completed.returncode: raise ValueError('SDK_REQUEST_FAILED')
        payload=json.loads(completed.stdout)
        if payload.get('status')!='COMPLETE': raise ValueError('SDK_RESPONSE_FAILED')
        for response in payload.get('raw_responses',[]):
            archive_receipt(Path(archive_root)/'upstream','AKShare_upstream',endpoint,
                response['parameters'],response['body'],status='COMPLETE' if response['status_code']==200 else 'FETCH_FAILED',
                pagination_complete=False,field_version=response['kind'],source_url=response['url'],first_seen_at=response['observed_at'])
        return archive_receipt(archive_root,'AKShare',endpoint,parameters,payload['rows'],status='COMPLETE',
            pagination_complete=payload.get('pagination_complete',False),field_version=payload['version'],source_url=payload.get('source_url')),payload['rows']
    except (subprocess.TimeoutExpired,ValueError,json.JSONDecodeError):
        receipt=archive_receipt(archive_root,'AKShare',endpoint,parameters,{'error':'FETCH_FAILED'},status='FETCH_FAILED')
        return receipt,[]

class AkshareLhbAdapter:
    """Summary disclosures are whitelisted; post-listing returns isolated."""
    mapping={'代码':'instrument_code','名称':'name','上榜日':'session','解读':'reason','上榜原因':'reason',
             '龙虎榜净买额':'net_cny','龙虎榜买入额':'buy_cny','龙虎榜卖出额':'sell_cny','市场总成交额':'amount_cny'}
    def capture(self,day,archive_root):
        value=day.isoformat().replace('-','') if hasattr(day,'isoformat') else str(day).replace('-','')
        receipt,rows=capture_akshare('stock_lhb_detail_em',{'start_date':value,'end_date':value},archive_root)
        features=[{self.mapping[k]:v for k,v in row.items() if k in self.mapping} for row in rows]
        # Persist only in a separate outcome directory, never joined by feature API.
        outcomes=[{k:v for k,v in row.items() if k in ('代码','上榜日') or '上榜后' in k} for row in rows]
        if outcomes: archive_receipt(Path(archive_root)/'outcome_store','AKShare','LHB_OUTCOME_ONLY',{'date':value},outcomes)
        return {'status':receipt['status'],'receipt':receipt,'features':features,
                'score_ready':False,'reason':'SEAT_WINDOWS_AND_DISCLOSURE_COMPLETENESS_REQUIRE_SEPARATE_VERIFICATION'}

    @staticmethod
    def normalize_seats(rows,*,instrument_id,receipt,windows_by_reason):
        """An explicit verified reason->window map is required for scoring.

        The SDK's 类型/交易日 is not enough to infer a three-session start.
        Preserve unmapped rows as window-unknown evidence, never guess a date.
        """
        features=[]
        for position,row in enumerate(rows):
            reason=str(row.get('类型',''))
            name=row.get('交易营业部名称')
            if not name: raise ValueError('LHB_SEAT_NAME_MISSING')
            buy,sell=row.get('买入金额'),row.get('卖出金额')
            from .funds import _num
            buy,sell=_num(buy) if buy is not None else None,_num(sell) if sell is not None else None
            if any(v is not None and v<0 for v in (buy,sell)): raise ValueError('LHB_SEAT_AMOUNT_INVALID')
            window=windows_by_reason.get(reason)
            provider_seat_id=row.get('席位代码') or row.get('营业部代码')
            ambiguous=name in ('机构专用','沪股通专用','深股通专用') and not provider_seat_id
            entity_key=str(provider_seat_id) if provider_seat_id else name if not ambiguous else f'UNRESOLVED:{receipt["receipt_id"]}:{position}:{name}'
            features.append(dict(instrument_id=instrument_id,provider='AKSHARE_EASTMONEY',seat_name=name,
                seat_id=hashlib.sha256(('AKSHARE_EASTMONEY:'+entity_key).encode()).hexdigest()[:24],
                seat_identity_status='UNRESOLVED_GROUP_LABEL' if ambiguous else 'PROVIDER_ID' if provider_seat_id else 'VENUE_NAME_LABEL',
                buy_cny=buy,sell_cny=sell,reason=reason,
                window_start=window[0] if window else None,window_end=window[1] if window else None,
                window_status='VERIFIED' if window else 'UNAVAILABLE',amount_status='COMPLETE' if buy is not None and sell is not None else 'PARTIAL',source_refs=[receipt['receipt_id']],
                event_time=receipt['first_seen_at'],source_published_at=receipt['first_seen_at'],
                first_seen_at=receipt['first_seen_at'],ingested_at=receipt['ingested_at']))
        return features

    def capture_seats(self,instrument_id,day,archive_root,*,windows_by_reason=None):
        value=day.isoformat().replace('-','') if hasattr(day,'isoformat') else str(day).replace('-','')
        results=[];receipts=[]
        for side in ('买入','卖出'):
            receipt,rows=capture_akshare('stock_lhb_stock_detail_em',{'symbol':instrument_id[:6],'date':value,'flag':side},archive_root)
            receipts.append(receipt)
            if receipt['status']=='COMPLETE':results.extend(self.normalize_seats(rows,instrument_id=instrument_id,receipt=receipt,windows_by_reason=windows_by_reason or {}))
        return dict(receipts=receipts,features=results,score_ready=bool(results) and all(r['window_status']=='VERIFIED' and r['amount_status']=='COMPLETE' and r['seat_identity_status']!='UNRESOLVED_GROUP_LABEL' for r in results) and all(r['pagination_complete'] for r in receipts),
                    identity_note='SEAT_ALIAS_IS_NOT_CONFIRMED_NATURAL_PERSON')

def capture_diagnostic_max6(output_dir,now):
    """Actual bounded post-close capture for a service-owned local scheduler.

    This is explicitly a six-symbol current strong-pool coverage probe, not a
    whole-market data service. Historical state/limits/PIT themes remain unknown.
    Never returns stale inputs after a failed capture. Caller must use a cutoff
    at/after the latest returned receipt, never the planned schedule timestamp.
    """
    from datetime import timedelta
    from decimal import Decimal
    from .contracts import MarketInput,UniverseSnapshot,SecurityRecord,DailyBar,SourceReceipt,digest
    from .rules import resolve_rule,calculate_limits
    if now.tzinfo is None: raise ValueError('TIMEZONE_REQUIRED')
    local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    if local.hour<15: raise ValueError('POST_CLOSE_DATA_NOT_READY')
    day=local.date();root=Path(output_dir)/('capture-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    root.mkdir(parents=True,exist_ok=False)
    receipts=[];datasets={}
    for endpoint,parameters in [('stock_info_sh_name_code',{'symbol':'主板A股'}),('stock_info_sz_name_code',{'symbol':'A股列表'}),('stock_zt_pool_em',{'date':day.strftime('%Y%m%d')})]:
        receipt,rows=capture_akshare(endpoint,parameters,root/'raw',timeout=30)
        receipts.append(receipt);datasets[endpoint]=rows
        if receipt['status']!='COMPLETE': raise ValueError('CURRENT_MARKET_CAPTURE_UNAVAILABLE:'+endpoint)
    master={}
    for r in datasets['stock_info_sh_name_code']:
        master[r['证券代码']]=dict(instrument_id=r['证券代码']+'.SH',exchange='SSE',name=r['证券简称'],listed_on=r['上市日期'][:10],source=receipts[0]['receipt_id'])
    for r in datasets['stock_info_sz_name_code']:
        if r.get('板块')=='主板':master[r['A股代码']]=dict(instrument_id=r['A股代码']+'.SZ',exchange='SZSE',name=r['A股简称'],listed_on=r['A股上市日期'][:10],source=receipts[1]['receipt_id'])
    # Coarse preselection is not final 60-exchange-day qualification; engine
    # applies the exact observed exchange calendar after data acquisition.
    pool={r['代码']:r for r in datasets['stock_zt_pool_em'] if r.get('代码') in master}
    selected=[code for code in sorted(pool) if 'ST' not in master[code]['name'].upper() and master[code]['listed_on']<=(day-timedelta(days=60)).isoformat()][:6]
    request={'provider':'baostock','start':(day-timedelta(days=800)).isoformat(),'end':day.isoformat(),'max_sessions':312,
             'codes':[master[c]['instrument_id'][-2:].lower()+'.'+c for c in selected]}
    try:
        r=subprocess.run([sys.executable,'-m','stockfish_core.yaogu.provider_worker'],input=json.dumps(request),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
        if r.returncode: raise ValueError('BAOSTOCK_CURRENT_CAPTURE_FAILED')
        payload=json.loads(r.stdout)
    except (subprocess.TimeoutExpired,ValueError):
        archive_receipt(root/'raw','Baostock','CURRENT_STRONG_RAW312',request,{'error':'FETCH_FAILED'},status='FETCH_FAILED')
        raise ValueError('CURRENT_RAW_DATA_UNAVAILABLE') from None
    if not payload['sessions'] or payload['sessions'][-1]!=day.isoformat(): raise ValueError('NO_CURRENT_TRADING_SESSION')
    receipt=archive_receipt(root/'raw','Baostock','CURRENT_STRONG_RAW312',request,payload,pagination_complete=True,field_version=payload['version'],source_url='https://www.baostock.com/')
    receipts.append(receipt)
    clock=dict(event_time=receipt['first_seen_at'],source_published_at=receipt['first_seen_at'],first_seen_at=receipt['first_seen_at'],ingested_at=receipt['ingested_at'])
    refs=tuple(r['receipt_id'] for r in receipts)
    securities=[];bars=[]
    for record in payload['records']:
        code=record['code'].split('.')[1];m=master[code]
        latest=next((r for r in record['daily'] if r['date']==day.isoformat()),None)
        if not latest: continue
        st=latest.get('isST')=='1' if latest.get('isST') in ('0','1') else None
        suspended=latest.get('tradestatus')=='0' if latest.get('tradestatus') in ('0','1') else None
        securities.append(SecurityRecord(instrument_id=m['instrument_id'],exchange=m['exchange'],board='MAIN',security_type='A_COMMON',listed_on=m['listed_on'],effective_from=day,st=st,suspended=suspended,name=m['name'],source_refs=refs,**clock))
        rule=resolve_rule(m['exchange'],'MAIN',day,st=st,exception_status='NORMAL_VERIFIED')
        limits=calculate_limits(latest['preclose'],rule) if latest.get('preclose') else {}
        matched=bool(limits.get('limit_up')) and Decimal(limits['limit_up'])==Decimal(str(pool[code].get('最新价')))==Decimal(latest['close'])
        for row in record['daily']:
            values={n:row.get(s) or None for n,s in [('open','open'),('high','high'),('low','low'),('close','close'),('reference_prev_close','preclose'),('volume_shares','volume'),('amount_cny','amount')]}
            for n in ('open','high','low','close','reference_prev_close'):
                if values[n] is not None and Decimal(values[n])<=0: values[n]=None
            current=row['date']==day.isoformat() and matched
            bars.append(DailyBar(instrument_id=m['instrument_id'],session=row['date'],**values,
                limit_up=limits['limit_up'] if current else None,limit_down=limits['limit_down'] if current else None,
                limit_applicable=True if current else None,limit_verified=current,rule_id=rule.get('rule_id') if current else None,
                turnover_fraction=float(row['turn'])/100 if row.get('turn') else None,turnover_basis='BAOSTOCK_FLOAT_SHARE_TURN_PERCENT',
                source_refs=refs,quality='VALID' if current else 'PARTIAL',**clock))
    universe=UniverseSnapshot(snapshot_id='CURRENT_STRONG_SCOPE_'+digest(securities)[:20],securities=tuple(securities),complete=False,expected_count=len(master),source_refs=refs,**clock)
    sources=tuple(SourceReceipt(receipt_id=r['receipt_id'],provider=r['provider'],dataset=r['dataset'],content_hash=r['content_hash'],field_version=r['field_version'],row_count=r['row_count'] or 0,pagination_complete=r['pagination_complete'],status='PARTIAL',source_url=r['source_url'],event_time=r['first_seen_at'],source_published_at=r['first_seen_at'],first_seen_at=r['first_seen_at'],ingested_at=r['ingested_at']) for r in receipts)
    bundle=MarketInput(observation_date=day,sessions=tuple(payload['sessions']),universe=universe,bars=tuple(bars),receipts=sources,coverage_type='RECONSTRUCTED_HISTORICAL')
    (root/'market-input.json').write_text(bundle.model_dump_json(indent=2),encoding='utf-8')
    (root/'coverage.json').write_text(json.dumps(dict(status='PARTIAL',scope='DIAGNOSTIC_MAX6',master_observed=len(master),current_symbols=len(securities),history_rows=len(bars),historical_identity='UNAVAILABLE',theme_pit='UNAVAILABLE',whole_market_emotion='UNAVAILABLE',cutoff_must_be_at_or_after=clock['ingested_at']),indent=2),encoding='utf-8')
    return bundle

# Legacy callers remain loadable; the R03 default scheduler uses the full-pool
# request DAG and must never select this alias as a production provider.
capture_current_market = capture_diagnostic_max6
