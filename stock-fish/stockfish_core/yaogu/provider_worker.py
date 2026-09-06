"""Isolated installed SDK read-only calls; parent bounds runtime and sanitizes errors."""
import contextlib
import inspect
import json
import sys
from datetime import datetime,timezone

def rows(result,maximum=20000):
    if result.error_code!='0': raise ValueError('PROVIDER_QUERY_FAILED')
    values=[]
    while result.next():
        if result.error_code!='0' or len(values)>=maximum: raise ValueError('PROVIDER_ROW_BOUND')
        values.append(dict(zip(result.fields,result.get_row_data())))
    return values

def main():
    request=json.load(sys.stdin)
    if request['provider']=='akshare':
        from .providers import AKSHARE_CAPABILITIES
        if request['endpoint'] not in AKSHARE_CAPABILITIES: raise ValueError('UNSUPPORTED_ENDPOINT')
        import akshare as ak
        import requests
        captured=[]
        original=requests.Session.request
        def trace(session,method,url,**kwargs):
            response=original(session,method,url,**kwargs)
            parameters={k:v for k,v in (kwargs.get('params') or {}).items() if not any(s in str(k).lower() for s in ('token','secret','password','cookie','authorization','api_key'))}
            try: body=response.json();kind='JSON_UPSTREAM_RESPONSE'
            except ValueError:
                import base64
                body={'base64':base64.b64encode(response.content).decode('ascii')};kind='BINARY_UPSTREAM_RESPONSE'
            if len(response.content)>12*1024*1024: raise ValueError('UPSTREAM_RESPONSE_SIZE_LIMIT')
            captured.append(dict(url=url.split('?')[0],parameters=parameters,status_code=response.status_code,
                observed_at=datetime.now(timezone.utc).isoformat(),kind=kind,body=body))
            if len(captured)>12:raise ValueError('UPSTREAM_PAGE_LIMIT')
            return response
        requests.Session.request=trace
        with contextlib.redirect_stdout(sys.stderr):
            result=getattr(ak,request['endpoint'])(**request['parameters'])
        # SDK field values serialized by pandas; NaN becomes null.
        page_sets={};needed={}
        for item in captured:
            body=item['body'];rs=body.get('result') if isinstance(body,dict) else None
            if isinstance(rs,dict) and isinstance(rs.get('pages'),int):
                needed[item['url']]=rs['pages'];page_sets.setdefault(item['url'],set()).add(int(item['parameters'].get('pageNumber',1)))
        complete=bool(needed) and all(page_sets[u]==set(range(1,n+1)) for u,n in needed.items())
        value={'status':'COMPLETE','rows':json.loads(result.to_json(orient='records',date_format='iso')),
               'version':ak.__version__,'pagination_complete':complete,'source_url':captured[0]['url'] if captured else None,
               'raw_responses':captured}
    elif request['provider']=='baostock':
        import baostock as bs
        with contextlib.redirect_stdout(sys.stderr):
            if bs.login().error_code!='0': raise ValueError('LOGIN_FAILED')
            try:
                start,end=request['start'],request['end']
                calendar=rows(bs.query_trade_dates(start_date=start,end_date=end))
                sessions=[r['calendar_date'] for r in calendar if r['is_trading_day']=='1'][-min(504,request.get('max_sessions',312)):]
                if not sessions: raise ValueError('NO_EXCHANGE_SESSIONS')
                records=[]
                if not 1<=len(request['codes'])<=10:raise ValueError('DIAGNOSTIC_EXPLICIT_MAX10_BATCH_REQUIRED')
                for code in request['codes']:
                    records.append({'code':code,'basic':rows(bs.query_stock_basic(code=code)),
                        'daily':rows(bs.query_history_k_data_plus(code,'date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,isST',start_date=sessions[0],end_date=sessions[-1],frequency='d',adjustflag='3'),maximum=505),
                        'industry':rows(bs.query_stock_industry(code=code,date=end))})
            finally: bs.logout()
        value={'status':'COMPLETE','sessions':sessions,'records':records,'calendar':calendar,'pagination_complete':True,'version':getattr(bs,'__version__','unknown')}
    else: raise ValueError('UNSUPPORTED_PROVIDER')
    print(json.dumps(value,ensure_ascii=True,allow_nan=False))

if __name__=='__main__':main()
