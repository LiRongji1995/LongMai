from datetime import datetime,date,timezone
import copy
import pytest
from stockfish_core.yaogu.funds import (calculate_funds as _calculate_funds,score_family_funds as _score_family_funds,deduplicate_disclosures,feature_whitelist,llm_evidence_context,visible_seat_aliases,coappearance)
from tests.r03_evidence.fixtures import funding,with_disclosure
from stockfish_core.yaogu.rules import resolve_rule,calculate_limits,abnormal_deviation

CUTOFF=datetime(2026,9,4,13,tzinfo=timezone.utc)
CLOCK={k:CUTOFF.isoformat() for k in ('event_time','source_published_at','first_seen_at','ingested_at')}
SESSIONS=tuple(date(2026,9,i) for i in (2,3,4))
RECEIPTS=tuple(r for iid in ('000001.SZ','600000.SH') for r in funding(iid,SESSIONS,CUTOFF)[1])

def calculate_funds(evidence,cutoff,observation_date=SESSIONS[-1]):
    return _calculate_funds(evidence,cutoff,observation_date,sessions=SESSIONS,receipts=RECEIPTS,coverage_type='TEST_FIXTURE')

def score_family_funds(evidence,scores,cutoff):
    return _score_family_funds(evidence,scores,cutoff,SESSIONS[-1],sessions=SESSIONS,receipts=RECEIPTS,coverage_type='TEST_FIXTURE')
def evidence(iid='000001.SZ'):
    return funding(iid,SESSIONS,CUTOFF)[0]

def seat(start='2026-09-04',end='2026-09-04',reason='R1'):
    e,r=funding('000001.SZ',SESSIONS,CUTOFF)
    window=tuple(str(d) for d in SESSIONS if start<=str(d)<=end)
    return with_disclosure(e,r,window=window)['disclosures'][0]|dict(reason=reason,seat_id='S1',seat_name='UNKNOWN_SEAT')

def test_fixed_funds_golden_and_not_disclosed_is_not_zero_flow():
    e=evidence();r=calculate_funds(e,CUTOFF,date(2026,9,4))
    assert (r['F_money'],r['F_lhb'],r['F'],r['lhb_raw_net_cny'])==(100,50,80,None)
    assert score_family_funds([e],{'000001.SZ':76.75},CUTOFF)['items']['000001.SZ']['S_funds']==pytest.approx(77.075)

@pytest.mark.parametrize('status',['FETCH_FAILED','NOT_PUBLISHED','UNAVAILABLE','INCOMPARABLE'])
def test_failure_is_never_neutral(status):
    e=evidence();e['disclosure_status']=status
    assert calculate_funds(e,CUTOFF)['F_lhb'] is None

def test_dedup_multiple_reasons_and_daily_three_day_overlap():
    rows=[seat(),seat(reason='R2'),seat('2026-09-02')]
    r=deduplicate_disclosures(rows,CUTOFF,instrument_id='000001.SZ')
    assert len(r)==1 and r[0]['window_start']=='2026-09-02'
    assert sum(x['buy_cny']-x['sell_cny'] for x in r)==10

def test_conflicting_seat_copy_fails():
    with pytest.raises(ValueError):deduplicate_disclosures([seat(),seat()|{'buy_cny':99}],CUTOFF,instrument_id='000001.SZ')

def test_disclosed_scoring_same_window_and_required_denominator():
    e=evidence();e.update(disclosure_status='DISCLOSED',disclosures=[seat()])
    assert calculate_funds(e,CUTOFF)['status']=='UNAVAILABLE'
    original,sources=funding('000001.SZ',SESSIONS,CUTOFF)
    e['window_amounts']=with_disclosure(original,sources)['window_amounts']
    assert calculate_funds(e,CUTOFF)['F_lhb']==100

def test_frozen_family_all_or_none_and_provider_definition():
    e=evidence();scores={'000001.SZ':76.75,'600000.SH':75.}
    r=score_family_funds([e],scores,CUTOFF);assert r['status']=='UNAVAILABLE' and all(v['S_funds'] is None for v in r['items'].values())
    f=evidence('600000.SH');f['provider']='OTHER'
    assert score_family_funds([e,f],scores,CUTOFF)['status']=='UNAVAILABLE'
    assert score_family_funds([e,evidence('600000.SH')],scores,CUTOFF)['status']=='AVAILABLE'

def test_future_envelope_precedes_business_and_outcomes_never_projected():
    e=evidence();future=copy.deepcopy(e);future['first_seen_at']='2026-09-04T14:00:00+00:00';future['net_large_cny']='broken';future['money_rows']=[{'bad':'bad'}]
    assert score_family_funds([e,future],{'000001.SZ':70},CUTOFF)==score_family_funds([e],{'000001.SZ':70},CUTOFF)
    row=seat()|{'上榜后1日':999,'D10_CLOSE_ADJCHRATE':999,'future_return':999}
    assert feature_whitelist(row)==feature_whitelist(seat())
    assert llm_evidence_context([row])==llm_evidence_context([seat()])

def test_alias_clocks_coappearance_no_score_bonus():
    alias=seat()|{'alias':'unverified clue','valid_from':'2026-09-01','valid_to':None,'sample_count':2}
    assert visible_seat_aliases([alias],CUTOFF,'2026-09-04')
    alias['first_seen_at']='2026-09-05T13:00:00+00:00'
    assert visible_seat_aliases([alias],CUTOFF,'2026-09-04')==[]
    assert coappearance([seat()],{'T':['000001.SZ']},CUTOFF)[0]['meaning']=='DISCLOSED_COAPPEARANCE_NOT_COMMON_CONTROL'

@pytest.mark.parametrize('exchange',['SSE','SZSE'])
def test_official_20260706_switch_fixture(exchange):
    old=resolve_rule(exchange,'MAIN','2026-07-03',st=True,exception_status='NORMAL_VERIFIED')
    new=resolve_rule(exchange,'MAIN','2026-07-06',st=True,exception_status='NORMAL_VERIFIED')
    assert old['rate']=='0.05' and new['rate']=='0.10' and new['source_url'].startswith('https://')
    assert calculate_limits('10.03',new,source_sample={'limit_up':'11.03','limit_down':'9.03'})['status']=='CROSS_VALIDATED'

def test_unverified_exemption_and_no_limit_are_distinct():
    assert resolve_rule('SSE','MAIN','2026-09-04',st=False,exception_status='UNKNOWN')['status']=='UNSUPPORTED'
    assert resolve_rule('SZSE','MAIN','2026-09-04',st=False,exception_status='IPO_REGISTERED_FIRST5',listing_session=5)['status']=='NOT_APPLICABLE'
    assert resolve_rule('SZSE','MAIN','2026-09-04',st=False,exception_status='IPO_REGISTERED_FIRST5',listing_session=6)['status']=='UNSUPPORTED'
    assert resolve_rule('SSE','STAR','2026-09-04',st=False,exception_status='NORMAL_VERIFIED')['status']=='UNSUPPORTED'

def test_limit_rounding_minimum_tick_and_deviation():
    rule=resolve_rule('SSE','MAIN','2026-09-04',st=False,exception_status='NORMAL_VERIFIED')
    assert calculate_limits('.01',rule)['limit_up']=='0.02'
    assert calculate_limits('.01',rule)['limit_down']=='0.01'
    assert abnormal_deviation(121,100,110,100,basis_comparable=True)==pytest.approx(.11)
    assert abnormal_deviation(121,100,110,100,basis_comparable=False) is None

def test_provider_seat_missing_side_stays_null_and_outcome_is_absent():
    from stockfish_core.yaogu.providers import AkshareLhbAdapter
    receipt=dict(receipt_id='TEST_FIXTURE',first_seen_at=CUTOFF.isoformat(),ingested_at=CUTOFF.isoformat())
    rows=[{'交易营业部名称':'机构专用','类型':'日涨幅','买入金额':100,'卖出金额':None,'上榜后1日':99}]
    item=AkshareLhbAdapter.normalize_seats(rows,instrument_id='000001.SZ',receipt=receipt,windows_by_reason={})[0]
    assert item['sell_cny'] is None and item['amount_status']=='PARTIAL' and item['window_status']=='UNAVAILABLE'
    assert '上榜后1日' not in item
    assert item['seat_identity_status']=='UNRESOLVED_GROUP_LABEL'
    pair=AkshareLhbAdapter.normalize_seats(rows*2,instrument_id='000001.SZ',receipt=receipt,windows_by_reason={})
    assert pair[0]['seat_id']!=pair[1]['seat_id']

def test_immutable_receipts_sanitized_parameters(tmp_path):
    from stockfish_core.yaogu.providers import archive_receipt
    with pytest.raises(ValueError): archive_receipt(tmp_path,'TEST_FIXTURE','test',{'api_token':'no-output'},[])
    a=archive_receipt(tmp_path,'TEST_FIXTURE','test',{'date':'20260904'},[{'close':10}],pagination_complete=True)
    assert (tmp_path/(a['content_hash']+'.raw.json')).is_file()
    assert a['integrity_scope']=='LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION'

def test_current_capture_fails_closed_before_close_and_after_provider_failure(tmp_path,monkeypatch):
    from stockfish_core.yaogu import providers
    early=datetime(2026,9,4,6,tzinfo=timezone.utc)
    with pytest.raises(ValueError,match='POST_CLOSE_DATA_NOT_READY'): providers.capture_current_market(tmp_path,early)
    def failed(*args,**kwargs):return ({'status':'FETCH_FAILED'},[])
    monkeypatch.setattr(providers,'capture_akshare',failed)
    with pytest.raises(ValueError,match='CURRENT_MARKET_CAPTURE_UNAVAILABLE'): providers.capture_current_market(tmp_path,CUTOFF)
    assert not list(tmp_path.rglob('market-input.json'))

def test_current_capture_uses_real_callback_fields_and_never_backfills_identity(tmp_path,monkeypatch):
    from stockfish_core.yaogu import providers
    import json
    from types import SimpleNamespace
    responses={'stock_info_sh_name_code':[{'证券代码':'605398','证券简称':'TEST_FIXTURE','上市日期':'2020-01-01'}],
               'stock_info_sz_name_code':[],'stock_zt_pool_em':[{'代码':'605398','最新价':11.0}]}
    def fetch(endpoint,parameters,root,**kwargs):
        data=responses[endpoint];receipt=providers.archive_receipt(root,'TEST_FIXTURE',endpoint,parameters,data)
        return receipt,data
    monkeypatch.setattr(providers,'capture_akshare',fetch)
    row={'date':'2026-09-04','preclose':'10','open':'10','high':'11','low':'10','close':'11','volume':'100','amount':'1050','turn':'0.1','isST':'0','tradestatus':'1'}
    data={'sessions':['2026-09-03','2026-09-04'],'records':[{'code':'sh.605398','daily':[row]}],'version':'TEST_FIXTURE'}
    monkeypatch.setattr(providers.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(data)))
    result=providers.capture_current_market(tmp_path,CUTOFF)
    assert result.universe.complete is False and result.universe.securities[0].effective_from==date(2026,9,4)
    assert result.bars[0].limit_verified and result.bars[0].limit_up==11
    assert result.themes==() and result.coverage_type=='RECONSTRUCTED_HISTORICAL'
