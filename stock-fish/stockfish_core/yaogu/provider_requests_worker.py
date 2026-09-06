"""R03 isolated SDK worker with bounded diagnostics; never prints credentials."""
import contextlib,json,os,sys,threading,re
import hashlib
from datetime import date,datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_transport = {}
_attempt_counts = {'HTTP':0,'SDK':0}

def reserve_attempt(kind='HTTP'):
    from .provider_budget import charge, HttpBudgetExhausted
    if kind=='HTTP' and _attempt_counts[kind]>=_transport.get('max_http_requests',10000):
        raise HttpBudgetExhausted('EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED')
    charge(_transport.get('budget_path'),_transport.get('budget_id'),_transport.get('queue_request_id','standalone'),
           _transport.get('provider','standalone'),_transport.get('endpoint','unknown'),kind=kind)
    _attempt_counts[kind]+=1

def read_partial(path):
    if not path:return None
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        expected=value.pop('checkpoint_hash',None)
        if expected is None:
            # Prior Eastmoney partial files had no digest. They remain partial
            # transport evidence; never used to resume a Tushare page cursor.
            value.setdefault('diagnostics',{})['legacy_checkpoint_without_hash']=True
            return value
        if hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False).encode()).hexdigest()!=expected:
            raise ValueError('CHECKPOINT_CONTENT_CORRUPT')
        return value
    except FileNotFoundError:return None

def classify_error(exc):
    names=[];current=exc
    for _ in range(5):
        if current is None:break
        names.append(type(current).__name__);current=current.__cause__ or current.__context__
    label=' '.join(names)+' '+str(exc)
    if 'EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED' in label:return 'BUDGET','EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED',names
    if 'NameResolution' in label or 'gaierror' in label:return 'NETWORK','DNS_RESOLUTION_FAILED',names
    if 'ProxyError' in label:return 'NETWORK','PROXY_CONNECTION_FAILED',names
    if 'Timeout' in label or 'timed out' in label:return 'NETWORK','UPSTREAM_TIMEOUT',names
    if any(x in label for x in ('ConnectionError','RemoteDisconnected','ConnectionReset','ConnectionAborted')):return 'NETWORK','UPSTREAM_CONNECTION_CLOSED',names
    if isinstance(exc,(KeyError,TypeError,AttributeError,IndexError)) or 'JSONDecodeError' in label:return 'UPSTREAM_SCHEMA','RESPONSE_SCHEMA_OR_ENCODING',names
    if '401' in label or '403' in label:return 'PERMISSION','HTTP_ACCESS_DENIED',names
    if '429' in label:return 'NETWORK','HTTP_RATE_LIMIT',names
    if (code:=re.search(r'BAOSTOCK_QUERY_([\d-]+)',label)):return 'PROVIDER','BAOSTOCK_ERROR_'+code.group(1),names
    return 'PROVIDER','PROVIDER_QUERY_FAILED',names

def write_partial(path,result):
    if not path:
        return
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix(target.suffix+'.tmp')
    value=dict(result)
    value['checkpoint_hash']=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False).encode()).hexdigest()
    with tmp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=True,allow_nan=False)
        stream.flush();os.fsync(stream.fileno())
    tmp.replace(target)

def bs_rows(result,maximum=30000):
    if result.error_code!='0':raise ValueError('BAOSTOCK_QUERY_'+str(result.error_code))
    out=[]
    while result.next():
        if result.error_code!='0':raise ValueError('BAOSTOCK_CURSOR_FAILED')
        if len(out)>=maximum:raise ValueError('EXPLICIT_PROVIDER_ROW_BOUND')
        out.append(dict(zip(result.fields,result.get_row_data())))
    if result.error_code!='0':raise ValueError('BAOSTOCK_QUERY_'+str(result.error_code))
    return out

def official_sse_delisting(captured):
    """Exact public request used by the exchange risk-plate page.

    Page JS search_listedCompanyInfo_2021.js, lines 2776 and 2794-2796,
    labels domesticIndicator=P, productType=0 as the main-board exit period.
    An explicit complete empty response is a known empty exclusion set.
    """
    import requests
    url='https://query.sse.com.cn/commonSoaQuery.do'
    params={'sqlId':'PL_SSGSXX_FXJSBGPLB','domesticIndicator':'P','productType':'0'}
    reserve_attempt()
    response=requests.get(url,params=params,headers={'Referer':'https://www.sse.com.cn/disclosure/listedinfo/riskplate/'},timeout=12)
    response.raise_for_status();body=response.json()
    captured.append(dict(url=url,parameters=params,body=body,status_code=response.status_code,received_at=datetime.now(timezone.utc).isoformat()))
    rows=body.get('result')
    complete=(isinstance(rows,list) and body.get('sqlId')==params['sqlId'] and str(body.get('isPagination')).lower()=='false'
              and not body.get('actionErrors') and not body.get('fieldErrors') and body.get('pageHelp',{}).get('total')==len(rows))
    return dict(status='COMPLETE' if complete else 'PARTIAL',rows=rows if isinstance(rows,list) else [],pagination_complete=complete,
        field_version='SSE_RISK_PLATE_MAIN_P0_R03_V1',source_url='https://www.sse.com.cn/disclosure/listedinfo/riskplate/',
        coverage={'complete_current_mainboard_delisting_set':complete,'upstream_total':body.get('pageHelp',{}).get('total')},
        diagnostics={'definition_source':'https://www.sse.com.cn/xhtml/home/2021public/querySearch/search_listedCompanyInfo_2021.js',
                     'definition_sha256':'e2685c1e237664ca9b4e792d2c5d95c59e2914deb51e0acd9c1c8b54c94e4227'},gaps=[] if complete else ['EXCLUSION_SET_NOT_COMPLETE'])

def baostock_request(endpoint,params,partial_path=None):
    import tempfile,time
    from ..jobs import _ServiceLock
    from ..storage import StorageError
    lock_path=Path(tempfile.gettempdir())/'stockfish-baostock-public-session.lock'
    start=time.monotonic()
    while True:
        try:
            lock=_ServiceLock(lock_path);break
        except StorageError:
            if time.monotonic()-start>=55:raise TimeoutError('BAOSTOCK_SESSION_LOCK_TIMEOUT')
            time.sleep(.1)
    try:return _baostock_session_request(endpoint,params,partial_path)
    finally:lock.close()


def _baostock_session_request(endpoint,params,partial_path=None):
    import baostock as bs
    reserve_attempt('SDK')
    if bs.login().error_code!='0':raise ValueError('BAOSTOCK_PUBLIC_LOGIN_FAILED')
    try:
        security_statuses=[]
        if endpoint=='calendar':
            reserve_attempt('SDK');rows=bs_rows(bs.query_trade_dates(start_date=params['start_date'],end_date=params['end_date']))
        elif endpoint=='master_all':
            reserve_attempt('SDK');rows=bs_rows(bs.query_all_stock(day=params['date']))
        elif endpoint=='market_daily':
            reserve_attempt('SDK');rows=bs_rows(bs.query_daily_history_k_AStock(date=params['date']))
        elif endpoint=='stock_basic_all':
            reserve_attempt('SDK');rows=bs_rows(bs.query_stock_basic(code=''))
        elif endpoint=='adjustment_factors':
            recovered=read_partial(partial_path) or {}
            rows=list(recovered.get('rows',[]))
            security_statuses=[r for r in recovered.get('coverage',{}).get('security_statuses',[]) if r.get('status')=='COMPLETE']
            done={r['code'] for r in security_statuses}
            for code in params['codes']:
                if code in done:continue
                # One bad instrument does not discard successful neighbors.
                # Each code is attempted once here; durable retry budgets are
                # owned by RequestQueue, not a hidden SDK retry loop.
                try:
                    reserve_attempt('SDK')
                    part=bs_rows(bs.query_adjust_factor(code=code,start_date=params['start_date'],end_date=params['end_date']))
                    if any(row.get('code')!=code for row in part):raise ValueError('FACTOR_RESPONSE_SECURITY_MISMATCH')
                    rows.extend(part)
                    security_statuses.append(dict(code=code,status='COMPLETE',row_count=len(part),cursor_exhausted=True,captured_at=datetime.now(timezone.utc).isoformat()))
                except Exception as exc:
                    category,error,classes=classify_error(exc)
                    security_statuses.append(dict(code=code,status='FETCH_FAILED',row_count=0,cursor_exhausted=False,
                        error_code=error,failure_category=category,exception_classes=classes,captured_at=datetime.now(timezone.utc).isoformat()))
                write_partial(partial_path,dict(status='PARTIAL',rows=rows,pagination_complete=False,
                    field_version='BAOSTOCK_'+getattr(bs,'__version__','unknown')+'_R03_V1',
                    source_url='https://baostock.com/',coverage={'security_statuses':security_statuses},http_responses=[]))
        elif endpoint=='stock_basic':
            rows=[]
            for code in params['codes']:
                reserve_attempt('SDK');rows.extend(bs_rows(bs.query_stock_basic(code=code)))
        else:
            rows=[]
            fields='date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,isST'
            flag='1' if endpoint=='history_adjusted' else '3'
            for code in params['codes']:
                reserve_attempt('SDK')
                rows.extend(bs_rows(bs.query_history_k_data_plus(code,fields,start_date=params['start_date'],
                    end_date=params['end_date'],frequency='d',adjustflag=flag),maximum=505))
                write_partial(partial_path,dict(status='PARTIAL',rows=rows,pagination_complete=False,
                    field_version='BAOSTOCK_'+getattr(bs,'__version__','unknown')+'_R03_V1',
                    source_url='https://baostock.com/',coverage={'returned_rows':len(rows)},http_responses=[]))
        failed=[row for row in security_statuses if row['status']!='COMPLETE']
        status='PARTIAL' if failed and any(row['status']=='COMPLETE' for row in security_statuses) else 'FETCH_FAILED' if failed else 'COMPLETE'
        return dict(status=status,rows=rows,pagination_complete=not failed,field_version='BAOSTOCK_'+getattr(bs,'__version__','unknown')+'_R03_V1',
            source_url='https://baostock.com/',coverage={'returned_rows':len(rows),'requested_codes':params.get('codes'),
                'returned_codes':sorted({r.get('code') for r in rows if r.get('code')}),'cursor_exhausted':not failed,'security_statuses':security_statuses},
            gaps=[{'code':row['code'],'status':row['status'],'error_code':row['error_code']} for row in failed],
            failure_category=failed[0]['failure_category'] if failed else None,error_code='BATCH_SECURITY_FAILURES' if failed else None,
            diagnostics={'public_sdk_session':True,'adjustflag':'1' if endpoint=='history_adjusted' else '3' if endpoint=='history' else None})
    finally:
        reserve_attempt('SDK');bs.logout()

def akshare_request(endpoint,params,captured,max_requests):
    import akshare as ak
    import requests
    from .provider_requests import ENDPOINTS
    original=requests.Session.request;lock=threading.Lock();attempted=[0];reused=[0]
    checkpoint_path=_transport.get('partial_path')
    prior=read_partial(checkpoint_path) or {}
    cached=prior.get('http_responses',[]) if prior.get('checkpoint_endpoint')==endpoint else []
    def trace(session,method,url,**kwargs):
        values=kwargs.get('params') or {};values=values if isinstance(values,dict) else {}
        clean={str(k):v for k,v in values.items() if not any(s in str(k).lower() for s in ('token','secret','password','cookie','authorization','api_key','ut'))}
        for saved in cached:
            if saved.get('status_code')==200 and saved.get('url')==url.split('?')[0] and saved.get('parameters')==clean:
                import base64
                response=requests.Response();response.status_code=200;response.url=url
                body=saved['body']
                response._content=base64.b64decode(body['data']) if isinstance(body,dict) and body.get('encoding')=='base64' else json.dumps(body,ensure_ascii=False).encode('utf-8')
                response.encoding='utf-8';captured.append(saved);reused[0]+=1
                return response
        with lock:
            if attempted[0]>=max_requests:raise ValueError('EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED')
            reserve_attempt();attempted[0]+=1
        # Socket timeout is subordinate to the parent process wall-clock bound.
        kwargs['timeout']=min(float(kwargs.get('timeout') or 12),12) if not isinstance(kwargs.get('timeout'),tuple) else (8,12)
        response=original(session,method,url,**kwargs)
        if len(response.content)>16*1024*1024:raise ValueError('UPSTREAM_RESPONSE_SIZE_LIMIT')
        try:body=response.json()
        except ValueError:
            import base64
            body={'encoding':'base64','data':base64.b64encode(response.content).decode('ascii')}
        with lock:captured.append(dict(url=url.split('?')[0],parameters=clean,body=body,status_code=response.status_code,received_at=datetime.now(timezone.utc).isoformat()))
        write_partial(checkpoint_path,dict(status='PARTIAL',rows=[],pagination_complete=False,http_responses=captured,
            checkpoint_endpoint=endpoint,field_version='AKSHARE_RAW_PAGE_CHECKPOINT_R05_V1',coverage={'http_requests':attempted[0]}))
        if response.status_code in (401,403,429):raise ValueError('HTTP_'+str(response.status_code))
        return response
    requests.Session.request=trace
    kwargs=dict(params)
    if endpoint=='mainboard_sse':kwargs={'symbol':'主板A股'}
    elif endpoint=='master_szse':kwargs={'symbol':'A股列表'}
    elif endpoint=='concept_list_sina':kwargs={'indicator':'概念'}
    elif endpoint=='concept_members_sina':kwargs={'sector':params['symbol']}
    elif endpoint=='lhb_summary':kwargs={k:v.replace('-','') for k,v in kwargs.items()}
    elif endpoint=='limit_up_pool':kwargs={'date':params['date'].replace('-','')}
    result=getattr(ak,ENDPOINTS[endpoint]['function'])(**kwargs)
    rows=json.loads(result.to_json(orient='records',date_format='iso'))
    complete=False;expected=[];page_sets={};page_counts={}
    for response in captured:
        body=response['body'];data=body.get('data') if isinstance(body,dict) else None;rs=body.get('result') if isinstance(body,dict) else None
        if isinstance(data,dict) and isinstance(data.get('total'),int):expected.append(data['total'])
        if isinstance(rs,dict) and isinstance(rs.get('pages'),int):
            url=response['url'];page_counts[url]=rs['pages'];page_sets.setdefault(url,set()).add(int(response['parameters'].get('pageNumber',1)))
    if expected:complete=len(rows)>=max(expected)
    if page_counts:complete=all(page_sets[u]==set(range(1,n+1)) for u,n in page_counts.items())
    if endpoint=='mainboard_sse':
        totals=[r['body'].get('pageHelp',{}).get('total') for r in captured if isinstance(r['body'],dict)]
        complete=bool(rows) and bool(totals) and all(isinstance(t,int) and t==len(rows) for t in totals)
    if endpoint=='master_szse':
        complete=bool(rows) and bool(captured) and all(r['status_code']==200 for r in captured)
    if endpoint=='money_flow':complete=bool(rows) and bool(captured) # Endpoint is documented bounded historical series, not all-market coverage.
    if endpoint=='concept_list_sina':complete=bool(rows) and len(captured)==1 and captured[0]['status_code']==200
    if endpoint=='concept_members_sina':
        totals=[int(r['body']) for r in captured if isinstance(r['body'],(int,str)) and str(r['body']).isdigit()]
        complete=bool(totals) and len(rows)==totals[0]
    if endpoint=='limit_up_pool':
        complete=bool(rows) and any(isinstance(r['body'],dict) and isinstance(r['body'].get('data'),dict) and isinstance(r['body']['data'].get('pool'),list) and len(r['body']['data']['pool'])==len(rows) for r in captured)
    if endpoint=='spot_sina':complete=False # Explicit count/page proof remains necessary; successful quotes are still retained.
    return dict(status='COMPLETE' if rows and complete else 'PARTIAL',rows=rows,pagination_complete=complete,
        field_version='AKSHARE_'+ak.__version__+'_R03_V1',source_url=captured[0]['url'] if captured else None,
        coverage={'returned_rows':len(rows),'upstream_total_values':expected,'http_requests':attempted[0],'cache_hits':reused[0],'pages_this_run':attempted[0],'page_sets':{k:sorted(v) for k,v in page_sets.items()}},
        gaps=[] if complete else ['PAGINATION_OR_EMPTY_RESULT_NOT_VERIFIED'],diagnostics={'schema_columns':list(result.columns),'endpoint_function':ENDPOINTS[endpoint]['function']})

def _eastmoney_limit_code(code):
    raw=str(code).strip()
    match=re.fullmatch(r'(?i)(sh|sz)\.(\d{6})',raw)
    if match:
        exchange=match.group(1).lower();symbol=match.group(2)
    else:
        match=re.fullmatch(r'(?i)(\d{6})\.(SH|SZ)',raw)
        if not match:raise ValueError('EASTMONEY_LIMIT_CODE_FORMAT_UNSUPPORTED')
        symbol=match.group(1);exchange=match.group(2).lower()
    return raw,symbol+'.'+exchange.upper(),('1' if exchange=='sh' else '0')+'.'+symbol,symbol

def eastmoney_daily_limits(params,captured,max_requests,partial_path=None):
    """从逐证券行情记录直接读取供应商 f51/f52 涨跌停价。

    clist 端点会把 f51/f52 用作其他含义，因此此连接器只消费
    qt/stock/get，并按请求交易日校验供应商时间戳。它是直接市场数据
    观测，不是普通规则推导，也不是历史 PIT 数据源。
    """
    import requests
    day=date.fromisoformat(params['date'])
    rows=[];statuses=[];attempts=0
    recovered=read_partial(partial_path) or {}
    if recovered.get('field_version')=='EASTMONEY_STOCK_GET_F51_F52_R04_V1' and recovered.get('coverage',{}).get('requested_codes')==params['codes']:
        statuses=[s for s in recovered.get('coverage',{}).get('security_statuses',[]) if s.get('status')=='COMPLETE']
        done={s['code'] for s in statuses}
        rows=[r for r in recovered.get('rows',[]) if r.get('code') in done and r.get('date')==params['date']]
        captured.extend(recovered.get('http_responses',[]))
    else:done=set()
    for code in params['codes']:
        if code in done:continue
        if attempts>=max_requests:raise ValueError('EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED')
        raw_code=str(code);iid=raw_code
        try:
            raw_code,iid,secid,symbol=_eastmoney_limit_code(code)
            query={'fltt':'2','invt':'2','secid':secid,'fields':'f43,f51,f52,f57,f59,f86'}
            reserve_attempt();attempts+=1
            response=requests.get('https://push2.eastmoney.com/api/qt/stock/get',params=query,timeout=(5,12))
            try:body=response.json()
            except ValueError:body={'text_prefix':response.text[:200]}
            captured.append(dict(url='https://push2.eastmoney.com/api/qt/stock/get',
                parameters={k:v for k,v in query.items() if k!='ut'},body=body,
                status_code=response.status_code,received_at=datetime.now(timezone.utc).isoformat()))
            if response.status_code in (401,403,429):raise ValueError('HTTP_'+str(response.status_code))
            response.raise_for_status()
            data=body.get('data') if isinstance(body,dict) else None
            if not isinstance(data,dict) or body.get('rc')!=0 or str(data.get('f57'))!=symbol:
                raise ValueError('EASTMONEY_LIMIT_IDENTITY_MISMATCH')
            try:
                source_time=datetime.fromtimestamp(float(data['f86']),timezone.utc)
                close=float(data['f43']);limit_up=float(data['f51']);limit_down=float(data['f52'])
            except (KeyError,TypeError,ValueError,OSError,OverflowError):
                raise ValueError('EASTMONEY_LIMIT_FIELDS_MISSING')
            source_business_date=source_time.astimezone(ZoneInfo('Asia/Shanghai')).date()
            if source_business_date!=day:
                raise ValueError('EASTMONEY_LIMIT_DATE_MISMATCH')
            if not (0<limit_down<limit_up and limit_down<=close<=limit_up):
                raise ValueError('EASTMONEY_LIMIT_VALUES_INVALID')
            rows.append({'code':raw_code,'instrument_id':iid,'date':day.isoformat(),
                'close':close,'limit_up':limit_up,'limit_down':limit_down,
                'source_timestamp':source_time.isoformat(),
                'source_business_date':source_business_date.isoformat(),
                'field_mapping':'EASTMONEY_STOCK_GET_F51_F52_R04_V1'})
            statuses.append({'code':raw_code,'instrument_id':iid,'status':'COMPLETE',
                'captured_at':datetime.now(timezone.utc).isoformat()})
        except Exception as exc:
            category,error,classes=classify_error(exc)
            if error=='PROVIDER_QUERY_FAILED' and str(exc).startswith('EASTMONEY_'):error=str(exc)
            statuses.append({'code':raw_code,'instrument_id':iid,'status':'FETCH_FAILED',
                'failure_category':category,'error_code':error,'exception_classes':classes,
                'captured_at':datetime.now(timezone.utc).isoformat()})
        partial_failed=[s for s in statuses if s['status']!='COMPLETE']
        partial_status='COMPLETE' if rows and not partial_failed else 'PARTIAL' if rows else 'FETCH_FAILED'
        write_partial(partial_path,dict(status=partial_status,rows=rows,pagination_complete=False,
            http_responses=captured,field_version='EASTMONEY_STOCK_GET_F51_F52_R04_V1',
            source_url='https://push2.eastmoney.com/api/qt/stock/get',
            coverage={'returned_rows':len(rows),'requested_codes':params['codes'],
                'returned_instrument_ids':sorted({r['instrument_id'] for r in rows}),
                'business_sessions':[day.isoformat()],'http_requests':attempts,
                'security_statuses':statuses},
            gaps=partial_failed,diagnostics={'field_mapping':'EASTMONEY_STOCK_GET_F51_F52_R04_V1'},
            first_seen_at=datetime.now(timezone.utc).isoformat()))
    failed=[s for s in statuses if s['status']!='COMPLETE']
    status='COMPLETE' if rows and not failed else 'PARTIAL' if rows else 'FETCH_FAILED'
    return dict(status=status,rows=rows,pagination_complete=not failed,
        field_version='EASTMONEY_STOCK_GET_F51_F52_R04_V1',
        source_url='https://push2.eastmoney.com/api/qt/stock/get',
        coverage={'returned_rows':len(rows),'requested_codes':params['codes'],
            'returned_instrument_ids':sorted({r['instrument_id'] for r in rows}),
            'business_sessions':[day.isoformat()],'http_requests':attempts,
            'security_statuses':statuses},
        gaps=[s for s in failed],failure_category=failed[0].get('failure_category') if failed else None,
        error_code='BATCH_SECURITY_FAILURES' if failed else None,
        diagnostics={'field_mapping':'EASTMONEY_STOCK_GET_F51_F52_R04_V1'})

def _compact_date(value):
    if value in (None,''):
        return value
    text=str(value)
    return text.replace('-','') if re.fullmatch(r'\d{4}-\d{2}-\d{2}',text) else text

def tushare_request(endpoint,params,captured,max_requests,partial_path=None):
    from .provider_requests import TUSHARE_ENDPOINTS
    import requests
    definition=TUSHARE_ENDPOINTS[endpoint]
    token=os.environ.get('TUSHARE_TOKEN') or os.environ.get('TUSHARE_PRO_TOKEN')
    if not token or token.strip().lower() in {'your-tushare-token','changeme','test'}:
        return dict(status='FETCH_FAILED',failure_category='AUTH',error_code='AUTH_REQUIRED',rows=[],
            pagination_complete=False,field_version='TUSHARE_HTTP_PRO_R05_V1',
            source_url=definition['doc_url'],coverage={'auth_configured':False,'requested_endpoint':definition['api_name']},
            gaps=['TUSHARE_TOKEN_REQUIRED'],diagnostics={'transport':'HTTP_POST','token_env_names':['TUSHARE_TOKEN','TUSHARE_PRO_TOKEN']})
    clean_params={k:_compact_date(v) for k,v in params.items() if v not in (None,'')}
    limit=int(definition.get('page_limit',5000));offset=0;rows=[];field_names=[];attempts=0;first_error=None;first_category=None
    identity={'endpoint':endpoint,'parameters':clean_params,'limit':limit,'field_version':'TUSHARE_HTTP_PRO_R05_V2'}
    recovered=read_partial(partial_path)
    recovered_pages=0;pages=0
    if recovered and recovered.get('checkpoint_identity')==identity:
        rows=list(recovered.get('rows',[]));offset=recovered.get('next_offset',0)
        field_names=recovered.get('coverage',{}).get('fields',[])
        recovered_pages=recovered.get('coverage',{}).get('pages_total',0)
        captured.extend(recovered.get('http_responses',[]))
        if recovered.get('pagination_complete'):
            recovered['coverage'].update(http_requests=0,pages_this_run=0,recovered_pages=recovered_pages,cache_hit=True)
            return recovered
    def checkpoint(complete=False):
        value=dict(status='COMPLETE' if complete else 'PARTIAL',rows=rows,pagination_complete=complete,
            field_version='TUSHARE_HTTP_PRO_R05_V2',source_url=definition['doc_url'],
            checkpoint_identity=identity,next_offset=offset,http_responses=captured,
            coverage={'auth_configured':True,'requested_endpoint':definition['api_name'],'api_name':definition['api_name'],
                'returned_rows':len(rows),'fields':field_names,'http_requests':attempts,'limit':limit,
                'pages_this_run':pages,'pages_total':recovered_pages+pages,'recovered_pages':recovered_pages,
                'offset_exhausted':complete},gaps=[],diagnostics={'transport':'HTTP_POST','token_redacted':True})
        write_partial(partial_path,value)
        return value
    while True:
        if attempts>=max_requests:
            first_error=first_error or 'EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED'
            first_category=first_category or 'BUDGET'
            break
        page_params={**clean_params,'limit':limit,'offset':offset}
        payload={'api_name':definition['api_name'],'token':token,'params':page_params,'fields':definition['fields']}
        try:
            reserve_attempt();attempts+=1
            response=requests.post('http://api.tushare.pro',json=payload,timeout=(5,20))
            try:
                body=response.json()
            except ValueError:
                body={'text_prefix':response.text[:200]}
            # Even an upstream error echo cannot persist the supplied secret.
            body=json.loads(json.dumps(body,ensure_ascii=True).replace(token,'[REDACTED]'))
            captured.append(dict(url='http://api.tushare.pro',parameters={k:v for k,v in page_params.items() if k!='token'},
                body=body,status_code=response.status_code,received_at=datetime.now(timezone.utc).isoformat()))
            if response.status_code in (401,403,429):
                first_error='HTTP_'+str(response.status_code);first_category='PERMISSION' if response.status_code in (401,403) else 'NETWORK'
                break
            response.raise_for_status()
            if not isinstance(body,dict) or body.get('code') not in (0,'0'):
                msg=str(body.get('msg',''))
                code='TUSHARE_API_'+str(body.get('code','UNKNOWN'))
                if '权限' in msg or '积分' in msg or 'token' in msg.lower() or body.get('code') in (-2001,-2002,'-2001','-2002'):
                    category='PERMISSION'
                elif '频' in msg or '每分钟' in msg or body.get('code') in (-2003,'-2003'):
                    category='NETWORK'
                else:
                    category='PROVIDER'
                first_error=code;first_category=category
                break
            data=body.get('data') if isinstance(body,dict) else None
            fields=data.get('fields') if isinstance(data,dict) else None
            items=data.get('items') if isinstance(data,dict) else None
            if not isinstance(fields,list) or not isinstance(items,list):
                first_error='TUSHARE_RESPONSE_SCHEMA_OR_ENCODING';first_category='UPSTREAM_SCHEMA'
                break
        except Exception as exc:
            first_category,first_error,_classes=classify_error(exc)
            break
        field_names=fields
        rows.extend(dict(zip(fields,item)) for item in items)
        pages+=1;offset+=len(items)
        # Durable successful page before the next admission/network request.
        checkpoint(len(items)<limit)
        if len(items)<limit:
            first_error=None
            first_category=None
            break
    complete=first_error is None
    result=checkpoint(complete)
    result.update(status='COMPLETE' if complete else 'PARTIAL' if rows else 'FETCH_FAILED',
        failure_category=first_category if first_error else None,error_code=first_error,gaps=[] if complete else [first_error])
    if complete and not rows:result['coverage']['empty_status']='EMPTY_VALID'
    return result

def main():
    request=json.load(sys.stdin);captured=[]
    global _transport, _attempt_counts
    _transport=request;_attempt_counts={'HTTP':0,'SDK':0}
    from .provider_requests import validate_request
    parameters=validate_request(request['endpoint'],request['parameters'])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if request['provider']=='baostock':result=baostock_request(request['endpoint'],parameters,request.get('partial_path'))
            elif request['provider']=='sse' and request['endpoint']=='delisting_sse':result=official_sse_delisting(captured)
            elif request['provider']=='eastmoney' and request['endpoint']=='daily_limits_eastmoney':result=eastmoney_daily_limits(parameters,captured,request['max_http_requests'],request.get('partial_path'))
            elif request['provider']=='tushare':result=tushare_request(request['endpoint'],parameters,captured,request['max_http_requests'],request.get('partial_path'))
            else:result=akshare_request(request['endpoint'],parameters,captured,request['max_http_requests'])
    except Exception as exc:
        category,code,classes=classify_error(exc)
        result=dict(status='FETCH_FAILED',failure_category=category,error_code=code,rows=[],pagination_complete=False,
            coverage={'http_responses':len(captured)},diagnostics={'exception_classes':classes},gaps=[code])
    result['http_responses']=captured;result['first_seen_at']=datetime.now(timezone.utc).isoformat()
    result.setdefault('coverage',{}).update(http_attempts=_attempt_counts['HTTP'],sdk_operations=_attempt_counts['SDK'])
    _transport={}
    print(json.dumps(result,ensure_ascii=True,allow_nan=False))

if __name__=='__main__':main()
