"""R03 bounded public-provider requests, archived receipts and explicit scope.

No endpoint silently truncates the requested securities. Queue-wide rate limits,
budgets, retries and caching belong to the persistent data pipeline caller.
"""
from __future__ import annotations
from datetime import date,datetime,timezone
from pathlib import Path
import hashlib,json,re,subprocess,sys
from .providers import archive_receipt

ENDPOINTS={
    'delisting_sse':{'provider':'sse','params':(),'required':()},
    'calendar':{'provider':'baostock','params':('start_date','end_date'),'required':('start_date','end_date')},
    'master_all':{'provider':'baostock','params':('date',),'required':('date',)},
    'market_daily':{'provider':'baostock','params':('date',),'required':('date',)},
    'adjustment_factors':{'provider':'baostock','params':('codes','start_date','end_date'),'required':('codes','start_date','end_date')},
    'stock_basic':{'provider':'baostock','params':('codes',),'required':('codes',)},
    'history':{'provider':'baostock','params':('codes','start_date','end_date'),'required':('codes','start_date','end_date')},
    'history_adjusted':{'provider':'baostock','params':('codes','start_date','end_date'),'required':('codes','start_date','end_date')},
    'mainboard_sse':{'provider':'akshare','function':'stock_info_sh_name_code','params':(),'required':()},
    'master_szse':{'provider':'akshare','function':'stock_info_sz_name_code','params':(),'required':()},
    'spot_all':{'provider':'akshare','function':'stock_zh_a_spot_em','params':(),'required':()},
    'spot_sina':{'provider':'akshare','function':'stock_zh_a_spot','params':(),'required':()},
    'concept_list':{'provider':'akshare','function':'stock_board_concept_name_em','params':(),'required':()},
    'concept_members':{'provider':'akshare','function':'stock_board_concept_cons_em','params':('symbol',),'required':('symbol',)},
    'concept_list_sina':{'provider':'akshare','function':'stock_sector_spot','params':(),'required':()},
    'concept_members_sina':{'provider':'akshare','function':'stock_sector_detail','params':('symbol',),'required':('symbol',)},
    'industry_list':{'provider':'akshare','function':'stock_board_industry_name_em','params':(),'required':()},
    'industry_members':{'provider':'akshare','function':'stock_board_industry_cons_em','params':('symbol',),'required':('symbol',)},
    'money_flow':{'provider':'akshare','function':'stock_individual_fund_flow','params':('stock','market'),'required':('stock','market')},
    'lhb_summary':{'provider':'akshare','function':'stock_lhb_detail_em','params':('start_date','end_date'),'required':('start_date','end_date')},
    'limit_up_pool':{'provider':'akshare','function':'stock_zt_pool_em','params':('date',),'required':('date',)},
    'daily_limits_eastmoney':{'provider':'eastmoney','params':('date','codes'),'required':('date','codes')},
}
FIELD_VERSION='R03_PROVIDER_ROWS_V1'

def validate_request(endpoint,parameters):
    if endpoint not in ENDPOINTS:raise ValueError('UNSUPPORTED_PROVIDER_ENDPOINT')
    definition=ENDPOINTS[endpoint];parameters=dict(parameters)
    if set(parameters)-set(definition['params']) or set(definition['required'])-set(parameters):raise ValueError('INVALID_REQUEST_FIELDS')
    for field in ('start_date','end_date','date'):
        if field in parameters:parameters[field]=date.fromisoformat(str(parameters[field])).isoformat()
    if 'start_date' in parameters and parameters['start_date']>parameters['end_date']:raise ValueError('INVALID_REQUEST_WINDOW')
    if 'codes' in parameters:
        codes=parameters['codes']
        if not isinstance(codes,(list,tuple)) or not 1<=len(codes)<=100:raise ValueError('EXPLICIT_BATCH_REQUIRES_1_TO_100_CODES')
        normalized=[]
        for code in codes:
            code=str(code)
            if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',code):code=code[-2:].lower()+'.'+code[:6]
            if not re.fullmatch(r'(sh|sz|bj)\.\d{6}',code):raise ValueError('INVALID_PROVIDER_SECURITY_ID')
            normalized.append(code)
        if len(normalized)!=len(set(normalized)):raise ValueError('DUPLICATE_REQUEST_SECURITY')
        parameters['codes']=normalized
    if 'symbol' in parameters and (not isinstance(parameters['symbol'],str) or not 1<=len(parameters['symbol'])<=80):raise ValueError('INVALID_THEME_ID')
    if endpoint=='money_flow' and (not re.fullmatch(r'\d{6}',parameters['stock']) or parameters['market'] not in ('sh','sz')):raise ValueError('INVALID_MONEY_SECURITY')
    return parameters

def execute_request(endpoint,params,output_dir,timeout=45):
    params=validate_request(endpoint,params)
    if not 0<timeout<=90:raise ValueError('PROVIDER_TIMEOUT_MUST_BE_0_TO_90_SECONDS')
    request={'endpoint':endpoint,'parameters':params,'provider':ENDPOINTS[endpoint]['provider'],'max_http_requests':128}
    rid=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest();started=datetime.now(timezone.utc)
    try:
        process=subprocess.run([sys.executable,'-m','stockfish_core.yaogu.provider_requests_worker'],input=json.dumps(request),
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        result=json.loads(process.stdout)
        if process.returncode or not isinstance(result,dict):raise ValueError('WORKER_INVALID_RESULT')
    except subprocess.TimeoutExpired:
        result={'status':'FETCH_FAILED','failure_category':'NETWORK','error_code':'REQUEST_TIMEOUT','rows':[],'pagination_complete':False,'http_responses':[]}
    except (ValueError,json.JSONDecodeError):
        result={'status':'FETCH_FAILED','failure_category':'IMPLEMENTATION','error_code':'WORKER_INVALID_RESULT','rows':[],'pagination_complete':False,'http_responses':[]}
    root=Path(output_dir);receipts=[]
    for response in result.get('http_responses',[]):
        receipts.append(archive_receipt(root/'upstream',request['provider'],endpoint,response['parameters'],response['body'],
            status='COMPLETE' if response['status_code']==200 else 'FETCH_FAILED',source_url=response['url'],
            field_version='UPSTREAM_HTTP_BODY_V1',first_seen_at=response['received_at']))
    payload={k:v for k,v in result.items() if k!='http_responses'}
    receipt=archive_receipt(root/'raw',request['provider'],endpoint,params,payload,status=result['status'],
        pagination_complete=result.get('pagination_complete',False),field_version=result.get('field_version',FIELD_VERSION),
        source_url=result.get('source_url'),first_seen_at=result.get('first_seen_at'))
    receipts.append(receipt)
    answer=dict(request_id=rid,status=result['status'],failure_category=result.get('failure_category'),error_code=result.get('error_code'),
        provider=request['provider'],endpoint=endpoint,parameters=params,field_version=result.get('field_version',FIELD_VERSION),
        receipts=receipts,rows=result.get('rows',[]),coverage=result.get('coverage',{}),gaps=result.get('gaps',[]),
        started_at=started.isoformat(),completed_at=datetime.now(timezone.utc).isoformat(),request_receipt_id=receipt['receipt_id'],
        pagination_complete=result.get('pagination_complete',False),diagnostics=result.get('diagnostics',{}))
    root.mkdir(parents=True,exist_ok=True)
    path=root/('request-'+rid+'-'+receipt['receipt_id'][:16]+'.json')
    if not path.exists():path.write_text(json.dumps(answer,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return answer
