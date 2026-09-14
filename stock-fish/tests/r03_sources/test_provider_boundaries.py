"""TEST_FIXTURE: provider assembly boundaries, isolated from daily state."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from copy import deepcopy
import pytest

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.provider_requests import validate_request
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report, _theme_type
from stockfish_core.yaogu.provider_comparability import comparable_rows

NOW = datetime(2026, 9, 6, 1, tzinfo=timezone.utc)
SESSIONS = tuple(date(2026, 8, 27) + timedelta(days=i) for i in (0, 1, 4, 5, 6, 7, 8))
DAY = SESSIONS[-1]


def result(endpoint, rows, params=None, *, stamp=NOW, complete=True, identity=''):
    rid=digest([endpoint,rows,params,str(stamp),identity])
    return dict(endpoint=endpoint,provider='TEST_FIXTURE',parameters=params or {},rows=rows,
                request_receipt_id=rid,field_version='TEST_FIXTURE_R03',status='COMPLETE' if complete else 'PARTIAL',
                pagination_complete=complete,receipts=[dict(receipt_id=rid,content_hash=digest(rows),
                first_seen_at=stamp.isoformat(),ingested_at=stamp.isoformat(),source_url='https://example.invalid/TEST_FIXTURE')])


def raw(code='sh.600000', session=DAY, **changes):
    return dict(date=str(session),code=code,open='10',high='11',low='10',close='11',preclose='10',
                volume='100000',amount='1100000',adjustflag='3',turn='2',tradestatus='1',isST='0') | changes


def master(code='600000', name='测试公司'):
    return {'证券代码':code,'证券简称':name,'上市日期':'2000-01-01'}


def base(rows=None):
    return [result('mainboard_sse',[master()]), result('master_szse',[]),
            result('market_daily',rows if rows is not None else [raw()],{'date':str(DAY)})]


def normalize(results):
    return normalize_capture_with_report(results,DAY,NOW,SESSIONS)


def test_explicit_eight_security_request_is_not_diagnostic_six():
    codes=[f'{600000+i}.SH' for i in range(8)]
    assert len(validate_request('history',dict(codes=codes,start_date='2026-08-03',end_date='2026-09-04'))['codes'])==8


@pytest.mark.parametrize('params',[
    dict(codes=['600000.SH']*2,start_date='2026-09-01',end_date='2026-09-04'),
    dict(codes=[f'{600000+i}.SH' for i in range(101)],start_date='2026-09-01',end_date='2026-09-04'),
    dict(codes=['600000.SH'],start_date='2026-09-05',end_date='2026-09-04'),
    dict(codes=['600000.SH'],start_date='2026-09-01',end_date='2026-09-04',file_path='D:/private'),
])
def test_request_contract_rejects_unsafe_or_silent_truncation(params):
    with pytest.raises(ValueError):validate_request('history',params)


def test_full_daily_raw_fields_and_units_survive():
    bundle,report=normalize(base());bar=bundle.bars[0];state=bundle.universe.securities[0]
    assert bar.reference_prev_close==Decimal('10') and bar.volume_shares==100000
    assert bar.amount_cny==1100000 and bar.turnover_fraction==.02
    assert state.st is False and state.suspended is False
    assert report['fields_retained']['historical_isST']==1


def test_adjusted_price_cannot_masquerade_as_raw():
    bundle,_=normalize(base([raw(adjustflag='1')]))
    assert not bundle.bars


def test_future_capture_cannot_be_backdated_by_business_session():
    rs=base();rs.append(result('market_daily',[raw()],{'date':str(DAY)},stamp=NOW+timedelta(seconds=1)))
    with pytest.raises(ValueError,match='CUTOFF_PRECEDES'):normalize(rs)


def test_authoritative_receipt_not_upstream_sidecar_defines_completeness():
    rs=base();rs[0]['receipts'].insert(0,dict(receipt_id='upstream',pagination_complete=False))
    bundle,_=normalize(rs)
    assert bundle.universe.complete and all(r.receipt_id!='upstream' for r in bundle.receipts)


def test_latest_master_replaces_old_snapshot_membership():
    rs=base();rs[0]=result('mainboard_sse',[master('600001')],stamp=NOW-timedelta(seconds=10))
    rs.append(result('mainboard_sse',[master()]))
    bundle,_=normalize(rs)
    assert bundle.universe.expected_count==1
    assert {s.instrument_id for s in bundle.universe.securities}=={'600000.SH'}


def test_malformed_master_identity_stays_in_expected_denominator():
    rs=base();rs[0]=result('mainboard_sse',[master(),master('???')])
    bundle,report=normalize(rs)
    assert bundle.universe.expected_count==2 and not bundle.universe.complete
    assert report['malformed_master_rows']==1


def test_missing_listing_date_not_invented_or_dropped_from_expected():
    rs=base();rs[0]=result('mainboard_sse',[master()|{'上市日期':None}])
    bundle,report=normalize(rs)
    assert bundle.universe.expected_count==1 and not bundle.universe.securities
    assert any(g.get('reason')=='NO_VERIFIED_LISTING_DATE' for g in report['gaps'])


def test_current_identity_is_not_backfilled_into_history():
    rs=base();rs.append(result('history',[raw(session=SESSIONS[-2])],{'start_date':str(SESSIONS[-2]),'end_date':str(DAY),'codes':['sh.600000']}))
    bundle,_=normalize(rs)
    previous=next(s for s in bundle.universe.securities if s.effective_from==SESSIONS[-2])
    assert previous.board=='UNKNOWN' and previous.st is False and previous.delisting_period is None


def test_outcome_only_disappeared_identity_and_prices_retained():
    rs=base([raw(),raw('sh.600001')]);rs.append(result('stock_basic',[dict(code='sh.600001',ipoDate='2001-01-01',outDate='2026-09-03',type='1',status='0')],{'codes':['sh.600001']}))
    bundle,report=normalize(rs)
    state=next(s for s in bundle.universe.securities if s.instrument_id=='600001.SH')
    assert 'OUTCOME_ONLY' in state.risk_flags and state.board=='UNKNOWN'
    assert state.delisted_on==date(2026,9,3) and state.listed_on==date(2001,1,1)
    assert bundle.universe.expected_count==1 and len(bundle.bars)==2
    assert report['outcome_only_instrument_ids']==['600001.SH']


def test_outcome_only_missing_ipo_keeps_exact_failure_identity():
    rs=base([raw(),raw('sh.600001')]);rs.append(result('stock_basic',[dict(code='sh.600001',ipoDate='',outDate='2026-09-03')]))
    _,report=normalize(rs)
    assert any(g.get('instrument_id')=='600001.SH' and g['reason']=='OUTCOME_ONLY_NO_VERIFIED_LISTING_DATE' for g in report['gaps'])


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity',None])
def test_nonfinite_close_is_isolated_security_gap(value):
    rs=base([raw(close=value),raw('sh.600001')]);rs[0]=result('mainboard_sse',[master(),master('600001')])
    bundle,_=normalize(rs)
    bars={b.instrument_id:b for b in bundle.bars}
    assert bars['600000.SH'].close is None and bars['600001.SH'].close==11


def test_official_empty_sse_exclusion_set_is_known_false():
    rs=base()+[result('delisting_sse',[])]
    bundle,report=normalize(rs)
    assert bundle.universe.securities[0].delisting_period is False
    assert report['current_identity_proofs'][0]['method']=='OFFICIAL_COMPLETE_CURRENT_EXCLUSION_SET'


def test_failed_or_partial_sse_exclusion_set_not_known_empty():
    rs=base()+[result('delisting_sse',[],complete=False)]
    bundle,_=normalize(rs)
    assert bundle.universe.securities[0].delisting_period is None


@pytest.mark.parametrize('name,value',[('测试公司',False),('测试退',True),('',None)])
def test_szse_current_official_name_rule_inference_is_explicit(name,value):
    rs=[result('mainboard_sse',[]),result('master_szse',[{'A股代码':'000001','A股简称':name,'A股上市日期':'2000-01-01','板块':'主板'}]),result('market_daily',[raw('sz.000001')],{'date':str(DAY)})]
    bundle,report=normalize(rs)
    assert bundle.universe.securities[0].delisting_period is value
    if value is not None:assert report['current_identity_proofs'][0]['method'].startswith('INFERENCE_')


def test_vendor_limit_pool_cross_validation_does_not_shrink_master():
    rs=base([raw(),raw('sh.600001',close='10.9')]);rs[0]=result('mainboard_sse',[master(),master('600001')])
    rs.append(result('limit_up_pool',[{'代码':'600000','最新价':11}],{'date':str(DAY)}))
    bundle,report=normalize(rs)
    assert bundle.universe.expected_count==2 and len(bundle.bars)==2
    bars={b.instrument_id:b for b in bundle.bars}
    assert bars['600000.SH'].limit_up==11 and bars['600000.SH'].limit_verified
    assert not bars['600001.SH'].limit_verified
    assert report['fields_retained']['verified_current_limit_rows']==1


def test_mismatched_pool_price_does_not_authorize_limit():
    rs=base()+[result('limit_up_pool',[{'代码':'600000','最新价':10.99}],{'date':str(DAY)})]
    bundle,_=normalize(rs)
    assert not bundle.bars[0].limit_verified


def test_new_theme_is_current_capture_not_backfilled_session():
    rs=base()+[result('concept_list_sina',[{'label':'gn_x','板块':'产业事件'}]),result('concept_members_sina',[{'code':'600000'}],{'symbol':'gn_x'})]
    bundle,_=normalize(rs)
    assert bundle.themes[0].effective_from==date(2026,9,6)
    assert bundle.themes[0].first_seen_at==NOW


@pytest.mark.parametrize('name,endpoint,expected',[('融资融券','concept_members','QUALIFICATION'),('专精特新','concept_members_sina','QUALIFICATION'),('电力','industry_members','INDUSTRY_PROXY')])
def test_qualification_and_industry_proxy_not_research_theme(name,endpoint,expected):
    assert _theme_type(name,endpoint)==expected


def factor(day='2000-01-01',value='2'):
    return dict(dividOperateDate=day,backAdjustFactor=value)


def test_factor_back_adjustment_exact_and_incremental_basis_stable():
    original=[factor()];new=original+[factor('2026-09-03','3')]
    rows=[(DAY,(raw(),'raw'))]
    # The first capture only certifies prices through September 2. Its old
    # expectation for September 4 improperly assumed no future action.
    prior_day=date(2026,9,2)
    a,_=comparable_rows('600000.SH',[(prior_day,(raw(session=prior_day),'raw'))],{},[(original,'factor1','2026-09-02','1990-01-01')])
    b,_=comparable_rows('600000.SH',rows,{},[(original,'factor1','2026-09-02','1990-01-01'),(new,'factor2','2026-09-04','1990-01-01')])
    assert a[prior_day][0]['comparable_close']==22 and b[DAY][0]['comparable_close']==33
    assert a[prior_day][0]['comparable_basis']==b[DAY][0]['comparable_basis']


def test_conflicting_historical_factor_cannot_mix_captures():
    values,status=comparable_rows('600000.SH',[(DAY,(raw(),'raw'))],{},[([factor()],'f1'),([factor(value='3')],'f2')])
    assert not values and status=='FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW'


def test_late_added_corporate_action_inside_prior_complete_window_is_revision():
    values,status=comparable_rows('600000.SH',[(DAY,(raw(),'raw'))],{},
        [([factor()],'f1','2026-09-04'),([factor(),factor('2026-09-03','3')],'f2','2026-09-04')])
    assert not values and status=='FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW'


def test_hfq_disagreement_blocks_comparable_series():
    adj={DAY:(raw(adjustflag='1',close='21'),'adj')}
    values,status=comparable_rows('600000.SH',[(DAY,(raw(),'raw'))],adj,[([factor()],'f1',str(DAY),'1990-01-01')])
    assert not values and status=='RAW_FACTOR_HFQ_CROSS_VALIDATION_FAILED'


def test_no_left_factor_never_fills_one():
    values,status=comparable_rows('600000.SH',[(DAY,(raw(),'raw'))],{},[([factor('2026-09-05')],'f1')])
    assert not values and status=='FACTOR_LEFT_BOUNDARY_OR_RAW_GAP'


def test_future_factor_cannot_change_earlier_price():
    values,_=comparable_rows('600000.SH',[(DAY,(raw(),'raw'))],{},[([factor(),factor('2026-09-05','999')],'f1','2026-09-05','1990-01-01')])
    assert values[DAY][0]['comparable_close']==22


def test_missing_factor_leaves_separate_capture_bases():
    adjusted={SESSIONS[-2]:(raw(adjustflag='1'),'a'),DAY:(raw(adjustflag='1'),'b')}
    values,_=comparable_rows('600000.SH',[],adjusted,[])
    assert values[SESSIONS[-2]][0]['comparable_basis']!=values[DAY][0]['comparable_basis']


def test_funds_summary_future_labels_and_narrative_are_not_projected():
    rs=base()+[result('lhb_summary',[{'代码':'600000','上榜日':str(DAY),'龙虎榜净买额':123,'龙虎榜买入额':223,'龙虎榜卖出额':100,
        '上榜后1日':99,'上榜后2日':-99,'上榜后5日':88,'上榜后10日':77,'解读':'future narrative','上榜原因':'公开原因'}],{'start_date':str(DAY),'end_date':str(DAY)})]
    bundle,report=normalize(rs)
    fund=bundle.funds[0];record=fund['public_summary_records'][0]
    assert record['disclosed_net_cny']==123 and not any('上榜后' in k for k in record)
    assert 'future narrative' not in str(record)
    assert fund['disclosure_status']=='WINDOW_UNVERIFIED' and not fund['disclosure_complete']
    assert report['funds_score_authorized_families']==0


def test_impossible_turnover_isolated_not_entire_bundle_failure():
    rs=base([raw(turn='1001'),raw('sh.600001')]);rs[0]=result('mainboard_sse',[master(),master('600001')])
    bundle,_=normalize(rs)
    bars={b.instrument_id:b for b in bundle.bars}
    assert bars['600000.SH'].turnover_fraction is None and bars['600001.SH'].turnover_fraction==.02


def test_composite_bar_waits_for_actual_later_hfq_receipt():
    from stockfish_core.yaogu.visibility import EvidenceResolver
    rs=[result('mainboard_sse',[master()],stamp=NOW-timedelta(seconds=20)),result('master_szse',[],stamp=NOW-timedelta(seconds=20)),
        result('market_daily',[raw()],{'date':str(DAY)},stamp=NOW-timedelta(seconds=10)),
        result('history_adjusted',[raw(adjustflag='1')],{'start_date':str(DAY),'end_date':str(DAY)},stamp=NOW-timedelta(seconds=1))]
    bundle,_=normalize(rs);bar=bundle.bars[0]
    resolver=EvidenceResolver(bundle.receipts,cutoff=NOW,coverage_type=bundle.coverage_type,sessions=SESSIONS,observation_date=DAY)
    gate=resolver.resolve(bar,instrument_id=bar.instrument_id,session=DAY,require_session=True)
    assert gate.allowed,gate.reason_codes
    assert bar.first_seen_at==NOW-timedelta(seconds=1)
    assert bar.event_time.date()==DAY


def test_composite_security_waits_for_later_status_and_basic_receipts():
    from stockfish_core.yaogu.visibility import EvidenceResolver
    rs=[result('mainboard_sse',[master()],stamp=NOW-timedelta(seconds=20)),result('master_szse',[],stamp=NOW-timedelta(seconds=20)),
        result('market_daily',[raw()],{'date':str(DAY)},stamp=NOW-timedelta(seconds=10)),
        result('stock_basic',[dict(code='sh.600000',ipoDate='2000-01-01',outDate='',type='1')],stamp=NOW-timedelta(seconds=2)),
        result('delisting_sse',[],stamp=NOW-timedelta(seconds=1))]
    bundle,_=normalize(rs);state=bundle.universe.securities[0]
    resolver=EvidenceResolver(bundle.receipts,cutoff=NOW,coverage_type=bundle.coverage_type,sessions=SESSIONS,observation_date=DAY)
    gate=resolver.resolve(state,instrument_id=state.instrument_id,session=DAY)
    assert gate.allowed,gate.reason_codes
    assert state.first_seen_at==NOW-timedelta(seconds=1)


def test_composite_theme_waits_for_later_registry_receipt():
    from stockfish_core.yaogu.visibility import EvidenceResolver
    rs=base()+[result('concept_list_sina',[{'label':'gn_x','板块':'产业事件'}],stamp=NOW-timedelta(seconds=1)),
        result('concept_members_sina',[{'code':'600000'}],{'symbol':'gn_x'},stamp=NOW-timedelta(seconds=10))]
    bundle,_=normalize(rs);theme=bundle.themes[0]
    resolver=EvidenceResolver(bundle.receipts,cutoff=NOW,coverage_type=bundle.coverage_type,sessions=SESSIONS,observation_date=DAY)
    gate=resolver.resolve(theme)
    assert gate.allowed,gate.reason_codes
    assert theme.first_seen_at==NOW-timedelta(seconds=1)


def test_partial_factor_batch_uses_only_proven_complete_security_fragment():
    rs=base([raw(),raw('sh.600001')]);rs[0]=result('mainboard_sse',[master(),master('600001')])
    factors=result('adjustment_factors',[factor()|{'code':'sh.600000'}],dict(codes=['sh.600000','sh.600001'],start_date='1990-01-01',end_date=str(DAY)),complete=False)
    factors['coverage']={'security_statuses':[dict(code='sh.600000',status='COMPLETE',row_count=1,cursor_exhausted=True),
        dict(code='sh.600001',status='FETCH_FAILED',row_count=0,cursor_exhausted=False,error_code='BAOSTOCK_ERROR_10001001')]}
    bundle,report=normalize(rs+[factors]);bars={b.instrument_id:b for b in bundle.bars}
    assert bars['600000.SH'].comparable_close==22 and bars['600001.SH'].comparable_close is None
    assert next(r for r in bundle.receipts if r.dataset=='adjustment_factors').status=='PARTIAL'
    assert any(g.get('reason')=='PER_SECURITY_COMPLETE_FRAGMENTS_ONLY_BATCH_NOT_COMPLETE' for g in report['gaps'])


def test_partial_factor_row_count_mismatch_does_not_authorize_fragment():
    factors=result('adjustment_factors',[factor()|{'code':'sh.600000'}],dict(codes=['sh.600000'],start_date='1990-01-01',end_date=str(DAY)),complete=False)
    factors['coverage']={'security_statuses':[dict(code='sh.600000',status='COMPLETE',row_count=2,cursor_exhausted=True)]}
    bundle,_=normalize(base()+[factors]);assert bundle.bars[0].comparable_close is None


def test_later_complete_empty_factor_snapshot_cannot_resurrect_old_factor():
    factors=result('adjustment_factors',[factor()|{'code':'sh.600000'}],dict(codes=['sh.600000'],start_date='1990-01-01',end_date=str(DAY)),stamp=NOW-timedelta(seconds=1))
    empty=result('adjustment_factors',[],dict(codes=['sh.600000'],start_date='1990-01-01',end_date=str(DAY)))
    bundle,report=normalize(base()+[factors,empty]);assert bundle.bars[0].comparable_close is None
    assert any(g.get('reason')=='FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW' for g in report['gaps'])


def test_failed_factor_retry_preserves_prior_verified_factor_fact():
    factors=result('adjustment_factors',[factor()|{'code':'sh.600000'}],dict(codes=['sh.600000'],start_date='1990-01-01',end_date=str(DAY)),stamp=NOW-timedelta(seconds=1))
    failure=result('adjustment_factors',[],dict(codes=['sh.600000'],start_date='1990-01-01',end_date=str(DAY)),complete=False)
    failure['status']='FETCH_FAILED'
    bundle,_=normalize(base()+[factors,failure]);assert bundle.bars[0].comparable_close==22
