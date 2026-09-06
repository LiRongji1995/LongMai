"""Independent reconstruction/provenance boundary tests. Every capture is synthetic TEST_FIXTURE."""
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import hashlib,json
from pathlib import Path
import pytest
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.historical_replay import HistoricalReplayAssembler,source_policy
from stockfish_core.yaogu.providers import archive_receipt


DAYS=['2026-08-27','2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04']
CODES=['sz.000001','sh.600000','sz.300001']


def write_capture(root,endpoint,parameters,rows):
    raw={'status':'COMPLETE','pagination_complete':True,'rows':rows,
         'coverage':{'cursor_exhausted':True,'evidence_class':'TEST_FIXTURE_ENGINEERING_ONLY'},
         'field_version':'BAOSTOCK_00.9.30_R03_V1','first_seen_at':datetime.now(timezone.utc).isoformat(),
         'gaps':[],'failure_category':None,'error_code':None,'diagnostics':[],'source_url':'https://baostock.com/'}
    receipt=archive_receipt(root/'cache/raw','baostock',endpoint,parameters,raw,status='COMPLETE',pagination_complete=True,
                            field_version=raw['field_version'],source_url='https://baostock.com/',first_seen_at=raw['first_seen_at'])
    result={**raw,'request_id':digest([endpoint,parameters]),'provider':'baostock','endpoint':endpoint,
            'parameters':parameters,'receipts':[receipt],'request_receipt_id':receipt['receipt_id'],
            'completed_at':receipt['ingested_at']}
    path=root/'checkpoints'/(result['request_id']+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result),encoding='utf-8')
    return path,result


def fixture_capture(root,*,stock_type='1',ipo='2010-01-01',factor_end=DAYS[-1]):
    docs=root/'source-docs';docs.mkdir(parents=True,exist_ok=True)
    names=['baostock-home.md','baostock-DailyUpdates.md','baostock-StockBasicInfoAPI.md','baostock-stockBasic.md','baostock-factorInfo.md',
           'baostock-adjustment.pdf','szse-mainboard-merger.html','szse-stock-rules.pdf','sse-calendar.html','szse-calendar.html']
    for name in names:(docs/name).write_text('TEST_FIXTURE_ONLY 17:30 18:00 000001-004999',encoding='utf-8')
    policy=source_policy(docs)
    write_capture(root,'calendar',{'start_date':'2010-01-01','end_date':DAYS[-1]},
                  [{'calendar_date':day,'is_trading_day':'1'} for day in DAYS])
    write_capture(root,'stock_basic_all',{},[{'code':code,'ipoDate':ipo,'outDate':'','type':stock_type if code==CODES[0] else '1','status':'1','code_name':'TEST_CURRENT_NAME_UNUSED'} for code in CODES])
    for index,day in enumerate(DAYS):
        write_capture(root,'master_all',{'date':day},[{'code':code,'code_name':'TEST_DATED_NAME','tradeStatus':'1'} for code in CODES])
        write_capture(root,'market_daily',{'date':day,'adjustflag':'3'},[{'code':code,'date':day,'adjustflag':'3',
            'open':str(10+index),'high':str(12+index),'low':str(9+index),'close':str(11+index),'preclose':str(10+index),
            'volume':'1000000','amount':'20000000','isST':'0','tradestatus':'1'} for code in CODES])
    factors=[{'code':code,'dividOperateDate':'2010-01-01','backAdjustFactor':'1','foreAdjustFactor':'1','adjustFactor':'1'} for code in CODES]
    factors += [{'code':code,'dividOperateDate':'2026-09-04','backAdjustFactor':'2','foreAdjustFactor':'1','adjustFactor':'2'} for code in CODES]
    write_capture(root,'adjustment_factors',{'codes':CODES,'start_date':'1990-01-01','end_date':factor_end},[r for r in factors if r['dividOperateDate']<=factor_end])
    return policy


def checkpoint(root,endpoint,day=None):
    for p in (root/'checkpoints').glob('*.json'):
        r=json.loads(p.read_text(encoding='utf-8'))
        if r['endpoint']==endpoint and (day is None or r['parameters'].get('date')==day):return p,r
    raise AssertionError('fixture checkpoint missing')


def test_verified_capture_preserves_clocks_and_truncates_future_prices_actions(tmp_path):
    policy=fixture_capture(tmp_path)
    assembler=HistoricalReplayAssembler(tmp_path,policy)
    bundle,audit=assembler.build('2026-09-02',history_sessions=20,include_security_history=True)
    assert bundle.coverage_type=='RECONSTRUCTED_HISTORICAL'
    assert bundle.bars and max(b.session for b in bundle.bars)==date(2026,9,2)
    assert all(b.comparable_close==b.close for b in bundle.bars) # 9/4 factor of 2 is future at T.
    assert all(b.first_seen_at.date()>=date(2026,9,6) and b.ingested_at>=b.first_seen_at for b in bundle.bars)
    assert audit['future_price_rows_in_signal']==0 and audit['original_capture_clocks_modified'] is False
    originals={r['request_receipt_id']:r['receipts'][0] for r in assembler.results}
    normalized={r.receipt_id:r for r in bundle.receipts}
    for source in audit['sources']:
        actual=normalized[source['normalized_receipt_id']];original=originals[source['original_receipt_id']]
        assert actual.first_seen_at==datetime.fromisoformat(original['first_seen_at'])
        assert actual.ingested_at==datetime.fromisoformat(original['ingested_at'])


def test_only_dated_certified_szse_subset_no_limits_invented(tmp_path):
    policy=fixture_capture(tmp_path);bundle,_=HistoricalReplayAssembler(tmp_path,policy).build('2026-09-02')
    states={s.instrument_id:s for s in bundle.universe.securities}
    assert states['000001.SZ'].board=='MAIN' and states['000001.SZ'].security_type=='A_COMMON'
    assert states['000001.SZ'].name=='TEST_DATED_NAME'
    assert states['600000.SH'].board=='UNKNOWN' and states['300001.SZ'].board=='UNKNOWN'
    assert all(not b.limit_verified and b.limit_up is None and b.limit_down is None for b in bundle.bars)
    assert bundle.universe.complete is False


@pytest.mark.parametrize('mutation',['checkpoint_rows','raw_bytes','receipt_hash','endpoint','parameters'])
def test_source_capture_tamper_is_rejected_before_reconstruction(tmp_path,mutation):
    policy=fixture_capture(tmp_path);path,result=checkpoint(tmp_path,'market_daily','2026-09-02');receipt=result['receipts'][0]
    if mutation=='checkpoint_rows':result['rows'][0]['close']='999';path.write_text(json.dumps(result),encoding='utf-8')
    elif mutation=='raw_bytes':(tmp_path/'cache/raw'/(receipt['content_hash']+'.raw.json')).write_text('{"rows":[]}',encoding='utf-8')
    elif mutation=='receipt_hash':
        saved=tmp_path/'cache/raw'/(receipt['receipt_id']+'.receipt.json');value=json.loads(saved.read_text());value['content_hash']='0'*64;saved.write_text(json.dumps(value),encoding='utf-8')
    elif mutation=='endpoint':result['endpoint']='master_all';path.write_text(json.dumps(result),encoding='utf-8')
    else:result['parameters']['date']='2026-09-03';path.write_text(json.dumps(result),encoding='utf-8')
    with pytest.raises((ValueError,FileNotFoundError)):
        HistoricalReplayAssembler(tmp_path,policy)


@pytest.mark.parametrize('fact,value',[('stock_type','2'),('ipo','2011-01-01')])
def test_original_ipo_and_type_are_bound_into_normalized_source(tmp_path,fact,value):
    left=tmp_path/'left';right=tmp_path/'right'
    a,_=HistoricalReplayAssembler(left,fixture_capture(left)).build('2026-09-02')
    b,_=HistoricalReplayAssembler(right,fixture_capture(right,**{fact:value})).build('2026-09-02')
    def facts(bundle):return next(r for r in bundle.receipts if r.dataset=='original_ipo_facts')
    assert facts(a).content_hash!=facts(b).content_hash
    state=next(s for s in b.universe.securities if s.instrument_id=='000001.SZ')
    assert facts(b).receipt_id in state.source_refs
    if fact=='stock_type':assert state.board=='UNKNOWN' and state.security_type=='UNKNOWN'
    else:assert state.listed_on==date(2011,1,1)


def test_factor_snapshot_must_cover_signal_not_only_have_left_anchor(tmp_path):
    policy=fixture_capture(tmp_path,factor_end='2026-09-01')
    bundle,audit=HistoricalReplayAssembler(tmp_path,policy).build('2026-09-02')
    assert bundle.bars and all(b.comparable_close is None and b.comparable_basis is None for b in bundle.bars)
    assert any(row['reason']=='FACTOR_CAPTURE_END_BEFORE_SIGNAL' for row in audit['gaps'])


def test_document_changed_after_policy_freeze_rejects(tmp_path):
    policy=fixture_capture(tmp_path)
    Path(policy['source_documents']['szse-mainboard-merger.html']['path']).write_text('changed TEST_FIXTURE',encoding='utf-8')
    with pytest.raises(ValueError,match='SOURCE_DOCUMENT_HASH_MISMATCH'):
        HistoricalReplayAssembler(tmp_path,policy)


def add_fixture_sse_authority(policy):
    # Explicit synthetic authority exercises the production dated policy branch.
    policy['board_authority']['SSE']={'effective_from':'2026-08-31','prefixes':['600','601','603','605'],
        'document_last_modified':'2026-08-31T16:38:09+08:00','signal_decision_clock':'20:00 Asia/Shanghai',
        'url':'https://www.sse.com.cn/TEST_FIXTURE_ONLY'}
    return policy


def test_sse_authority_is_not_backdated_before_august_31(tmp_path):
    policy=add_fixture_sse_authority(fixture_capture(tmp_path));assembler=HistoricalReplayAssembler(tmp_path,policy)
    before,_=assembler.build('2026-08-28');after,_=assembler.build('2026-08-31')
    old=next(s for s in before.universe.securities if s.instrument_id=='600000.SH')
    new=next(s for s in after.universe.securities if s.instrument_id=='600000.SH')
    assert old.board=='UNKNOWN' and old.security_type=='UNKNOWN'
    assert new.board=='MAIN' and new.security_type=='A_COMMON'
    assert new.effective_from==date(2026,8,31)
    assert new.source_published_at<=datetime.fromisoformat('2026-08-31T20:00:00+08:00')


def test_sse_delisting_prefix_and_szse_suffix_are_distinct(tmp_path):
    policy=add_fixture_sse_authority(fixture_capture(tmp_path))
    for day,names in [('2026-08-31',{'sz.000001':'示例退','sh.600000':'退市示例'}),
                      ('2026-09-01',{'sz.000001':'退市示例','sh.600000':'示例退'})]:
        write_capture(tmp_path,'master_all',{'date':day},[{'code':code,'code_name':names.get(code,'TEST_DATED_NAME'),'tradeStatus':'1'} for code in CODES])
    assembler=HistoricalReplayAssembler(tmp_path,policy)
    first,_=assembler.build('2026-08-31');second,_=assembler.build('2026-09-01')
    def named(bundle):return {s.instrument_id:s for s in bundle.universe.securities}
    for iid in ('000001.SZ','600000.SH'):
        assert named(first)[iid].delisting_period is True
        assert named(second)[iid].delisting_period is False
