"""R03 isolated SDK worker with bounded diagnostics; never prints credentials."""
import contextlib,json,sys,threading,re
from datetime import datetime,timezone

def classify_error(exc):
    names=[];current=exc
    for _ in range(5):
        if current is None:break
        names.append(type(current).__name__);current=current.__cause__ or current.__context__
    label=' '.join(names)+' '+str(exc)
    if 'NameResolution' in label or 'gaierror' in label:return 'NETWORK','DNS_RESOLUTION_FAILED',names
    if 'ProxyError' in label:return 'NETWORK','PROXY_CONNECTION_FAILED',names
    if 'Timeout' in label or 'timed out' in label:return 'NETWORK','UPSTREAM_TIMEOUT',names
    if any(x in label for x in ('ConnectionError','RemoteDisconnected','ConnectionReset','ConnectionAborted')):return 'NETWORK','UPSTREAM_CONNECTION_CLOSED',names
    if isinstance(exc,(KeyError,TypeError,AttributeError,IndexError)) or 'JSONDecodeError' in label:return 'UPSTREAM_SCHEMA','RESPONSE_SCHEMA_OR_ENCODING',names
    if '401' in label or '403' in label:return 'PERMISSION','HTTP_ACCESS_DENIED',names
    if '429' in label:return 'NETWORK','HTTP_RATE_LIMIT',names
    if (code:=re.search(r'BAOSTOCK_QUERY_([\d-]+)',label)):return 'PROVIDER','BAOSTOCK_ERROR_'+code.group(1),names
    return 'PROVIDER','PROVIDER_QUERY_FAILED',names

def bs_rows(result,maximum=30000):
    if result.error_code!='0':raise ValueError('BAOSTOCK_QUERY_'+str(result.error_code))
    out=[]
    while result.next():
        if result.error_code!='0':raise ValueError('BAOSTOCK_CURSOR_FAILED')
        if len(out)>=maximum:raise ValueError('EXPLICIT_PROVIDER_ROW_BOUND')
        out.append(dict(zip(result.fields,result.get_row_data())))
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

def baostock_request(endpoint,params):
    import baostock as bs
    if bs.login().error_code!='0':raise ValueError('BAOSTOCK_PUBLIC_LOGIN_FAILED')
    try:
        security_statuses=[]
        if endpoint=='calendar':rows=bs_rows(bs.query_trade_dates(start_date=params['start_date'],end_date=params['end_date']))
        elif endpoint=='master_all':rows=bs_rows(bs.query_all_stock(day=params['date']))
        elif endpoint=='market_daily':rows=bs_rows(bs.query_daily_history_k_AStock(date=params['date']))
        elif endpoint=='adjustment_factors':
            rows=[]
            for code in params['codes']:
                # One bad instrument does not discard successful neighbors.
                # Each code is attempted once here; durable retry budgets are
                # owned by RequestQueue, not a hidden SDK retry loop.
                try:
                    part=bs_rows(bs.query_adjust_factor(code=code,start_date=params['start_date'],end_date=params['end_date']))
                    if any(row.get('code')!=code for row in part):raise ValueError('FACTOR_RESPONSE_SECURITY_MISMATCH')
                    rows.extend(part)
                    security_statuses.append(dict(code=code,status='COMPLETE',row_count=len(part),cursor_exhausted=True,captured_at=datetime.now(timezone.utc).isoformat()))
                except Exception as exc:
                    category,error,classes=classify_error(exc)
                    security_statuses.append(dict(code=code,status='FETCH_FAILED',row_count=0,cursor_exhausted=False,
                        error_code=error,failure_category=category,exception_classes=classes,captured_at=datetime.now(timezone.utc).isoformat()))
        elif endpoint=='stock_basic':
            rows=[]
            for code in params['codes']:rows.extend(bs_rows(bs.query_stock_basic(code=code)))
        else:
            rows=[]
            fields='date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,isST'
            flag='1' if endpoint=='history_adjusted' else '3'
            for code in params['codes']:
                rows.extend(bs_rows(bs.query_history_k_data_plus(code,fields,start_date=params['start_date'],
                    end_date=params['end_date'],frequency='d',adjustflag=flag),maximum=505))
        failed=[row for row in security_statuses if row['status']!='COMPLETE']
        status='PARTIAL' if failed and any(row['status']=='COMPLETE' for row in security_statuses) else 'FETCH_FAILED' if failed else 'COMPLETE'
        return dict(status=status,rows=rows,pagination_complete=not failed,field_version='BAOSTOCK_'+getattr(bs,'__version__','unknown')+'_R03_V1',
            source_url='https://baostock.com/',coverage={'returned_rows':len(rows),'requested_codes':params.get('codes'),
                'returned_codes':sorted({r.get('code') for r in rows if r.get('code')}),'cursor_exhausted':not failed,'security_statuses':security_statuses},
            gaps=[{'code':row['code'],'status':row['status'],'error_code':row['error_code']} for row in failed],
            failure_category=failed[0]['failure_category'] if failed else None,error_code='BATCH_SECURITY_FAILURES' if failed else None,
            diagnostics={'public_sdk_session':True,'adjustflag':'1' if endpoint=='history_adjusted' else '3' if endpoint=='history' else None})
    finally:bs.logout()

def akshare_request(endpoint,params,captured,max_requests):
    import akshare as ak
    import requests
    from .provider_requests import ENDPOINTS
    original=requests.Session.request;lock=threading.Lock();attempted=[0]
    def trace(session,method,url,**kwargs):
        with lock:
            attempted[0]+=1
            if attempted[0]>max_requests:raise ValueError('EXPLICIT_HTTP_REQUEST_BUDGET_EXHAUSTED')
        # Socket timeout is subordinate to the parent process wall-clock bound.
        kwargs['timeout']=min(float(kwargs.get('timeout') or 12),12) if not isinstance(kwargs.get('timeout'),tuple) else (8,12)
        response=original(session,method,url,**kwargs)
        if len(response.content)>16*1024*1024:raise ValueError('UPSTREAM_RESPONSE_SIZE_LIMIT')
        try:body=response.json()
        except ValueError:
            import base64
            body={'encoding':'base64','data':base64.b64encode(response.content).decode('ascii')}
        values=kwargs.get('params') or {};values=values if isinstance(values,dict) else {}
        clean={str(k):v for k,v in values.items() if not any(s in str(k).lower() for s in ('token','secret','password','cookie','authorization','api_key','ut'))}
        with lock:captured.append(dict(url=url.split('?')[0],parameters=clean,body=body,status_code=response.status_code,received_at=datetime.now(timezone.utc).isoformat()))
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
        coverage={'returned_rows':len(rows),'upstream_total_values':expected,'http_requests':attempted[0],'page_sets':{k:sorted(v) for k,v in page_sets.items()}},
        gaps=[] if complete else ['PAGINATION_OR_EMPTY_RESULT_NOT_VERIFIED'],diagnostics={'schema_columns':list(result.columns),'endpoint_function':ENDPOINTS[endpoint]['function']})

def main():
    request=json.load(sys.stdin);captured=[]
    from .provider_requests import validate_request
    parameters=validate_request(request['endpoint'],request['parameters'])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if request['provider']=='baostock':result=baostock_request(request['endpoint'],parameters)
            elif request['provider']=='sse' and request['endpoint']=='delisting_sse':result=official_sse_delisting(captured)
            else:result=akshare_request(request['endpoint'],parameters,captured,request['max_http_requests'])
    except Exception as exc:
        category,code,classes=classify_error(exc)
        result=dict(status='FETCH_FAILED',failure_category=category,error_code=code,rows=[],pagination_complete=False,
            coverage={'http_responses':len(captured)},diagnostics={'exception_classes':classes},gaps=[code])
    result['http_responses']=captured;result['first_seen_at']=datetime.now(timezone.utc).isoformat()
    print(json.dumps(result,ensure_ascii=True,allow_nan=False))

if __name__=='__main__':main()
