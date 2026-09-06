"""Exchange facts separate from strategy thresholds. No prefix-based identity."""
from datetime import date
from decimal import Decimal,ROUND_HALF_UP

SSE_2026='https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml'
SZSE_2026='https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf'
SSE_SWITCH='https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml'
SZSE_SWITCH='https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html'
REGISTRY_VERSION='SSE_SZSE_MAIN_20260706_V1'

def resolve_rule(exchange,board,session,*,st,exception_status,listing_session=None):
    """Only independently established normal/exempt days can resolve a limit.

    exception_status NORMAL_VERIFIED/IPO_REGISTERED_FIRST5/RELISTING_FIRST/
    DELISTING_FIRST/OTHER_OFFICIAL_EXEMPT. Unknown stays unsupported.
    Pre-2023 registration changes and unverified transitional issues unsupported.
    """
    day=session if isinstance(session,date) else date.fromisoformat(session)
    result={'registry_version':REGISTRY_VERSION,'exchange':exchange,'board':board,'session':day.isoformat(),
            'status':'UNSUPPORTED','rate':None,'tick':'0.01','rounding':'ROUND_HALF_UP',
            'source_url':SSE_2026 if exchange=='SSE' else SZSE_2026 if exchange=='SZSE' else None,
            'source_verified_on':'2026-09-05','exception_status':exception_status}
    if exchange not in ('SSE','SZSE') or board!='MAIN' or st not in (True,False): return result
    if day<date(2023,4,10): return result|{'reason':'PRE_REGISTERED_TRANSITION_UNSUPPORTED'}
    if exception_status in ('IPO_REGISTERED_FIRST5','RELISTING_FIRST','DELISTING_FIRST','OTHER_OFFICIAL_EXEMPT'):
        if exception_status=='IPO_REGISTERED_FIRST5' and (listing_session is None or not 1<=listing_session<=5): return result
        return result|{'status':'NOT_APPLICABLE','reason':'OFFICIAL_NO_PRICE_LIMIT'}
    if exception_status!='NORMAL_VERIFIED': return result|{'reason':'EXEMPTION_STATUS_NOT_VERIFIED'}
    switched=day>=date(2026,7,6)
    return result|{'status':'SUPPORTED','rate':'0.10' if switched or not st else '0.05',
                   'rule_id':exchange+('_20260706_MAIN' if switched else '_2023_MAIN'),
                   'effective_from':'2026-07-06' if switched else '2023-04-10',
                   'effective_to':None if switched else '2026-07-05',
                   'historical_rate_source':SSE_SWITCH if exchange=='SSE' else 'https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-04-30/4a272c2b-fe9f-4126-87bc-b4771a3a3510.PDF'}

def calculate_limits(reference_prev_close,rule,*,source_sample=None):
    if rule['status']!='SUPPORTED': return {'status':rule['status'],'limit_up':None,'limit_down':None,'rule':rule}
    base=Decimal(str(reference_prev_close));tick=Decimal(rule['tick']);rate=Decimal(rule['rate'])
    if not base.is_finite() or base<=0: raise ValueError('INVALID_REFERENCE_PRICE')
    up=(base*(1+rate)/tick).quantize(Decimal(1),rounding=ROUND_HALF_UP)*tick
    down=(base*(1-rate)/tick).quantize(Decimal(1),rounding=ROUND_HALF_UP)*tick
    if abs(up-base)<tick: up=base+tick
    if abs(down-base)<tick: down=base-tick
    down=max(tick,down)
    status='CALCULATED_REQUIRES_CROSS_VALIDATION'
    if source_sample is not None:
        if Decimal(str(source_sample['limit_up']))!=up or Decimal(str(source_sample['limit_down']))!=down:
            raise ValueError('OFFICIAL_VENDOR_LIMIT_SAMPLE_MISMATCH')
        status='CROSS_VALIDATED'
    return {'status':status,'limit_up':str(up),'limit_down':str(down),'reference_prev_close':str(base),'rule':rule}

def abnormal_deviation(stock_end,stock_start,index_end,index_start,*,basis_comparable):
    """2026 rule cumulative deviation = price ratio return minus index ratio.

    This is not the sum of daily percentage-point deviations. Reference indices:
    SSE A-share index; SZSE A-share index. Exempt sessions excluded and reset
    dates require externally verified regulatory disclosures before use.
    """
    if not basis_comparable: return None
    vals=[Decimal(str(x)) for x in (stock_end,stock_start,index_end,index_start)]
    if any(not x.is_finite() or x<=0 for x in vals): return None
    return float(vals[0]/vals[1]-vals[2]/vals[3])
