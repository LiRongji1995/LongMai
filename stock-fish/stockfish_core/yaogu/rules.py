"""Exchange facts separate from strategy thresholds. No prefix-based identity."""
from datetime import date
from decimal import Decimal,ROUND_HALF_UP
import re

SSE_2026='https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml'
SZSE_2026='https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf'
SSE_SWITCH='https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml'
SZSE_SWITCH='https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html'
REGISTRY_VERSION='SSE_SZSE_MAIN_20260706_V1'
RULE_DERIVED_LIMIT_VERSION='RULE_DERIVED_LIMIT_EVIDENCE_R04_V1'

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

def derive_verified_limits(reference_prev_close,*,exchange,board,session,st,normal_or_exempt_proof,listing_session=None,source_sample=None):
    """只有普通/豁免分支证据齐全时，才推导精确涨跌停价。

    这是 R04 允许的 RULE_DERIVED_VERIFIED 路径。它刻意不是
    close/preclose 百分比计算的便捷封装：缺少证据、规则不支持或分支
    样本未校验时，结果仍保持不可用。
    """
    required=('proof_status','source_receipt_ids','source_fact_ids','official_source_hash',
              'reference_price_semantics','reference_price_receipt','validation_suite_id',
              'sample_evidence_refs','definition_version')
    if not isinstance(normal_or_exempt_proof,dict) or any(not normal_or_exempt_proof.get(k) for k in required):
        return {'status':'UNKNOWN','reason':'NORMAL_OR_EXEMPT_PROOF_REQUIRED','derivation_version':RULE_DERIVED_LIMIT_VERSION}
    if normal_or_exempt_proof['proof_status']!='NORMAL_VERIFIED':
        return {'status':'UNKNOWN','reason':'ONLY_NORMAL_VERIFIED_BRANCH_SUPPORTED_FOR_DERIVATION',
                'derivation_version':RULE_DERIVED_LIMIT_VERSION}
    if normal_or_exempt_proof['reference_price_semantics'] not in ('RAW_PREVIOUS_CLOSE','OFFICIAL_EX_RIGHT_REFERENCE_PRICE'):
        return {'status':'UNKNOWN','reason':'REFERENCE_PRICE_SEMANTICS_UNSUPPORTED',
                'derivation_version':RULE_DERIVED_LIMIT_VERSION}
    if not re.fullmatch(r'[0-9a-f]{64}',str(normal_or_exempt_proof['official_source_hash'])):
        return {'status':'UNKNOWN','reason':'OFFICIAL_SOURCE_HASH_REQUIRED',
                'derivation_version':RULE_DERIVED_LIMIT_VERSION}
    if not normal_or_exempt_proof.get('sample_evidence_refs'):
        return {'status':'UNKNOWN','reason':'VALIDATION_SAMPLE_EVIDENCE_REQUIRED',
                'derivation_version':RULE_DERIVED_LIMIT_VERSION}
    rule=resolve_rule(exchange,board,session,st=st,exception_status='NORMAL_VERIFIED',listing_session=listing_session)
    if rule['status']!='SUPPORTED':
        return {'status':'UNKNOWN','reason':rule.get('reason','RULE_UNSUPPORTED'),
                'rule':rule,'derivation_version':RULE_DERIVED_LIMIT_VERSION}
    calculated=calculate_limits(reference_prev_close,rule,source_sample=source_sample)
    if source_sample is None and normal_or_exempt_proof.get('branch_cross_validated') is not True:
        return {'status':'UNKNOWN','reason':'RULE_BRANCH_CROSS_VALIDATION_REQUIRED',
                'rule':rule,'derivation_version':RULE_DERIVED_LIMIT_VERSION}
    return {'status':'RULE_DERIVED_VERIFIED','limit_up':calculated['limit_up'],
        'limit_down':calculated['limit_down'],'reference_price_value':calculated['reference_prev_close'],
        'reference_price_semantics':normal_or_exempt_proof['reference_price_semantics'],
        'reference_price_receipt':normal_or_exempt_proof['reference_price_receipt'],
        'price_tick':rule['tick'],'rounding_rule':rule['rounding'],'rule_id':rule['rule_id'],
        'rule_version':rule['registry_version'],'effective_interval':{'from':rule['effective_from'],'to':rule['effective_to']},
        'official_source_hash':normal_or_exempt_proof['official_source_hash'],
        'instrument_session_status_ref':normal_or_exempt_proof['source_fact_ids'],
        'normal_or_exempt_proof':normal_or_exempt_proof['proof_status'],
        'source_receipt_ids':tuple(normal_or_exempt_proof['source_receipt_ids']),
        'source_fact_ids':tuple(normal_or_exempt_proof['source_fact_ids']),
        'derivation_version':RULE_DERIVED_LIMIT_VERSION,
        'validation_suite_id':normal_or_exempt_proof['validation_suite_id'],
        'sample_evidence_refs':tuple(normal_or_exempt_proof['sample_evidence_refs']),
        'effective_dependency_hash':normal_or_exempt_proof.get('effective_dependency_hash')}

def abnormal_deviation(stock_end,stock_start,index_end,index_start,*,basis_comparable):
    """2026规则累计偏离值=股票价格比收益减指数价格比收益。

    这不是每日百分点偏离值的简单求和。参考指数为上证A股指数和深证A股指数。
    豁免交易日需要排除，重置日期使用前必须有外部可核验监管披露。
    """
    if not basis_comparable: return None
    vals=[Decimal(str(x)) for x in (stock_end,stock_start,index_end,index_start)]
    if any(not x.is_finite() or x<=0 for x in vals): return None
    return float(vals[0]/vals[1]-vals[2]/vals[3])
