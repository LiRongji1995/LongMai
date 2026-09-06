"""R03 bounded public-provider requests, archived receipts and explicit scope.

No endpoint silently truncates the requested securities. Queue-wide rate limits,
budgets, retries and caching belong to the persistent data pipeline caller.
"""
from __future__ import annotations
from datetime import date,datetime,timezone
from pathlib import Path
import hashlib,json,re,subprocess,sys
from .providers import archive_receipt

TUSHARE_ENDPOINTS={
    'tushare_stock_basic':{
        'provider':'tushare','api_name':'stock_basic','params':('ts_code','name','market','list_status','exchange','is_hs'),
        'required':(),'fields':'ts_code,symbol,name,area,industry,market,list_date,act_name,act_ent_type,list_status,delist_date,is_hs',
        'doc_url':'https://tushare.pro/document/2?doc_id=25'},
    'tushare_trade_cal':{
        'provider':'tushare','api_name':'trade_cal','params':('exchange','start_date','end_date','is_open'),
        'required':('start_date','end_date'),'fields':'exchange,cal_date,is_open,pretrade_date',
        'doc_url':'https://tushare.pro/document/2?doc_id=26'},
    'tushare_daily':{
        'provider':'tushare','api_name':'daily','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount',
        'doc_url':'https://tushare.pro/document/2?doc_id=27'},
    'tushare_daily_basic':{
        'provider':'tushare','api_name':'daily_basic','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe,pb,total_share,float_share,free_share,total_mv,circ_mv',
        'doc_url':'https://tushare.pro/document/2?doc_id=32'},
    'tushare_adj_factor':{
        'provider':'tushare','api_name':'adj_factor','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'ts_code,trade_date,adj_factor',
        'doc_url':'https://tushare.pro/document/2?doc_id=28'},
    'tushare_stk_limit':{
        'provider':'tushare','api_name':'stk_limit','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'trade_date,ts_code,up_limit,down_limit',
        'doc_url':'https://tushare.pro/document/2?doc_id=183'},
    'tushare_limit_list_d':{
        'provider':'tushare','api_name':'limit_list_d','params':('ts_code','trade_date','start_date','end_date','limit_type','exchange'),
        'required':(),'fields':'trade_date,ts_code,industry,name,close,pct_chg,amount,limit_amount,float_mv,total_mv,turnover_ratio,fd_amount,first_time,last_time,open_times,up_stat,limit_times,limit,limit_type',
        'doc_url':'https://tushare.pro/document/2?doc_id=298'},
    'tushare_suspend_d':{
        'provider':'tushare','api_name':'suspend_d','params':('ts_code','suspend_date','resume_date'),
        'required':(),'fields':'ts_code,suspend_date,resume_date,ann_date,suspend_reason,reason_type',
        'doc_url':'https://tushare.pro/document/2?doc_id=31'},
    'tushare_stock_st':{
        'provider':'tushare','api_name':'stock_st','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'ts_code,name,trade_date,reason,st_type',
        'doc_url':'https://tushare.pro/document/2?doc_id=397'},
    'tushare_top_list':{
        'provider':'tushare','api_name':'top_list','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason',
        'doc_url':'https://tushare.pro/document/2?doc_id=106'},
    'tushare_top_inst':{
        'provider':'tushare','api_name':'top_inst','params':('ts_code','trade_date'),
        'required':('trade_date',),'fields':'trade_date,ts_code,exalter,buy,buy_rate,sell,sell_rate,net_buy,side,reason',
        'doc_url':'https://tushare.pro/document/2?doc_id=107'},
    'tushare_moneyflow':{
        'provider':'tushare','api_name':'moneyflow','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount',
        'doc_url':'https://tushare.pro/document/2?doc_id=170'},
    'tushare_dc_index':{
        'provider':'tushare','api_name':'dc_index','params':('ts_code','name','trade_date'),
        'required':(),'fields':'ts_code,name,trade_date,close,open,high,low,pre_close,pct_change,vol,amount',
        'doc_url':'https://tushare.pro/document/2?doc_id=362'},
    'tushare_dc_member':{
        'provider':'tushare','api_name':'dc_member','params':('ts_code','trade_date'),
        'required':(),'fields':'ts_code,con_code,con_name,trade_date,weight',
        'doc_url':'https://tushare.pro/document/2?doc_id=363'},
    'tushare_stk_auction':{
        'provider':'tushare','api_name':'stk_auction','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'ts_code,trade_date,vol,price,amount,pre_close,turnover_rate,volume_ratio,float_share',
        'doc_url':'https://tushare.pro/document/2?doc_id=369'},
    'tushare_stk_auction_o':{
        'provider':'tushare','api_name':'stk_auction_o','params':('ts_code','trade_date','start_date','end_date'),
        'required':(),'fields':'ts_code,trade_date,vol,price,amount,pre_close,turnover_rate,volume_ratio,float_share',
        'doc_url':'https://tushare.pro/document/2?doc_id=370'},
}

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
}|TUSHARE_ENDPOINTS
FIELD_VERSION='R03_PROVIDER_ROWS_V1'

def _date_iso(value):
    text=str(value)
    if re.fullmatch(r'\d{8}',text):
        return date.fromisoformat(text[:4]+'-'+text[4:6]+'-'+text[6:]).isoformat()
    return date.fromisoformat(text[:10]).isoformat()

def validate_request(endpoint,parameters):
    if endpoint not in ENDPOINTS:raise ValueError('UNSUPPORTED_PROVIDER_ENDPOINT')
    definition=ENDPOINTS[endpoint];parameters=dict(parameters)
    if set(parameters)-set(definition['params']) or set(definition['required'])-set(parameters):raise ValueError('INVALID_REQUEST_FIELDS')
    for field in ('start_date','end_date','date','trade_date','suspend_date','resume_date','ann_date'):
        if field in parameters and parameters[field] not in (None,''):parameters[field]=_date_iso(parameters[field])
    if 'start_date' in parameters and parameters['start_date']>parameters['end_date']:raise ValueError('INVALID_REQUEST_WINDOW')
    if 'ts_code' in parameters and parameters.get('ts_code') not in (None,'') and not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',str(parameters['ts_code'])):
        raise ValueError('INVALID_TUSHARE_TS_CODE')
    if 'list_status' in parameters and parameters.get('list_status') not in (None,'','L','D','P'):
        raise ValueError('INVALID_TUSHARE_LIST_STATUS')
    if 'is_open' in parameters and parameters.get('is_open') not in (None,'',0,1,'0','1'):
        raise ValueError('INVALID_TUSHARE_IS_OPEN')
    if 'limit_type' in parameters and parameters.get('limit_type') not in (None,'','U','D','Z'):
        raise ValueError('INVALID_TUSHARE_LIMIT_TYPE')
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

def _partial_timeout_result(partial_path):
    try:
        value=json.loads(Path(partial_path).read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError,TypeError):
        return None
    if not isinstance(value,dict) or not value.get('rows'):
        return None
    return {**value,'status':'PARTIAL','failure_category':'NETWORK',
        'error_code':'REQUEST_TIMEOUT_AFTER_PARTIAL','pagination_complete':False,
        'gaps':value.get('gaps',[])+['REQUEST_TIMEOUT_AFTER_PARTIAL']}

def execute_request(endpoint,params,output_dir,timeout=45):
    params=validate_request(endpoint,params)
    if not 0<timeout<=90:raise ValueError('PROVIDER_TIMEOUT_MUST_BE_0_TO_90_SECONDS')
    root=Path(output_dir)
    root.mkdir(parents=True,exist_ok=True)
    partial_path=root/'worker-partial.json'
    request={'endpoint':endpoint,'parameters':params,'provider':ENDPOINTS[endpoint]['provider'],
        'max_http_requests':128,'partial_path':str(partial_path)}
    identity={k:v for k,v in request.items() if k!='partial_path'}
    rid=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();started=datetime.now(timezone.utc)
    try:
        process=subprocess.run([sys.executable,'-m','stockfish_core.yaogu.provider_requests_worker'],input=json.dumps(request),
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        result=json.loads(process.stdout)
        if process.returncode or not isinstance(result,dict):raise ValueError('WORKER_INVALID_RESULT')
    except subprocess.TimeoutExpired:
        result=_partial_timeout_result(partial_path) or {'status':'FETCH_FAILED','failure_category':'NETWORK','error_code':'REQUEST_TIMEOUT','rows':[],'pagination_complete':False,'http_responses':[]}
    except (ValueError,json.JSONDecodeError):
        result={'status':'FETCH_FAILED','failure_category':'IMPLEMENTATION','error_code':'WORKER_INVALID_RESULT','rows':[],'pagination_complete':False,'http_responses':[]}
    receipts=[]
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
