"""R03 source-preserving MarketInput assembly for current captured research.

Business session and actual reception clocks are distinct. No historical PIT
publication proof, historical board status, normal-limit exception state or
adjustment factors are synthesized from a ticker prefix.
"""
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo
from collections import defaultdict
import re
from .contracts import MarketInput,UniverseSnapshot,SecurityRecord,DailyBar,SourceReceipt,ThemeSnapshot,digest
from .provider_comparability import comparable_rows
from .rules import resolve_rule, calculate_limits
from .provider_funds import summary_preview

TZ=ZoneInfo('Asia/Shanghai')
SZSE_DELISTING_NAME_RULE='https://docs.static.szse.cn/www/lawrules/rule/stock/W020260424747613955674.pdf'
def _date(value):
    try:return date.fromisoformat(str(value)[:10])
    except (ValueError,TypeError):return None
def _number(value,positive=False):
    try:
        n=Decimal(str(value))
        return n if n.is_finite() and (n>0 if positive else n>=0) else None
    except (InvalidOperation,ValueError,TypeError):return None
def _bool(value):return True if str(value)=='1' else False if str(value)=='0' else None
def _iid(code):
    code=str(code)
    if re.fullmatch(r'(sh|sz|bj)\.\d{6}',code):return code[3:]+'.'+code[:2].upper()
    if re.fullmatch(r'(sh|sz|bj)\d{6}',code):return code[2:]+'.'+code[:2].upper()
    if re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',code):return code
    return None
def _clock(receipt,session=None):
    seen=datetime.fromisoformat(receipt['first_seen_at']);ingested=datetime.fromisoformat(receipt['ingested_at'])
    return dict(event_time=datetime.combine(session,time(15),TZ) if session else seen,
        source_published_at=seen,first_seen_at=seen,ingested_at=ingested)

def _derived_clock(source_by,refs,session=None):
    """A composed observation cannot predate any real source it contains.

    Business event time stays the market session. Raw receipt clocks remain
    immutable; only this newly assembled revision receives dependency maxima.
    """
    clocks=[_clock(source_by[ref],session) for ref in dict.fromkeys(refs)]
    return {field:max(clock[field] for clock in clocks) for field in
            ('event_time','source_published_at','first_seen_at','ingested_at')}
def _theme_type(name,endpoint):
    if endpoint.startswith('industry'):return 'INDUSTRY_PROXY'
    if not name:return 'UNKNOWN'
    # Fixed, queryable exclusion rule; no LLM invents research themes.
    if re.search(r'融资融券|沪股通|深股通|沪深股通|昨日|涨停|连板|次新|新股|ST板|风险警示|MSCI|富时|标准普尔|沪深300|上证50|中证\d+|机构重仓|社保重仓|基金重仓|证金持股|专精特新',name,re.I):return 'QUALIFICATION'
    return 'EVENT'

def normalize_capture_with_report(results,observation_date,now,sessions):
    day=_date(observation_date)
    if day is None or now.tzinfo is None:raise ValueError('OBSERVATION_DATE_AND_AWARE_CLOCK_REQUIRED')
    sessions=tuple(sorted({_date(s) for s in sessions if _date(s) is not None and _date(s)<=day}))
    if not sessions or sessions[-1]!=day:raise ValueError('OBSERVATION_MUST_BE_VERIFIED_TRADING_SESSION')
    usable=[];receipts={};source_by={};gaps=[]
    for result in results:
        rid=result.get('request_receipt_id');source=next((r for r in result.get('receipts',[]) if r.get('receipt_id')==rid),None)
        if not source:continue
        if datetime.fromisoformat(source['ingested_at'])>now:raise ValueError('CUTOFF_PRECEDES_ACTUAL_CAPTURE_COMPLETION')
        source_by[rid]=source
        params=result.get('parameters',{});end=_date(params.get('end_date') or params.get('date'))
        clock=_clock(source,end)
        coverage=result.get('coverage',{})
        instrument_ids=tuple(sorted(str(v) for v in coverage.get('returned_instrument_ids',()) if v))
        business_sessions=tuple(sorted({_date(v) for v in coverage.get('business_sessions',()) if _date(v) is not None}))
        receipts[rid]=SourceReceipt(receipt_id=rid,provider=result['provider'],dataset=result['endpoint'],content_hash=source['content_hash'],
            field_version=result['field_version'],row_count=len(result.get('rows',[])),pagination_complete=result.get('pagination_complete',False),
            status=result['status'],parameters={k:v if isinstance(v,(str,int,float,bool)) or v is None else str(v) for k,v in params.items()},
            source_url=source.get('source_url'),instrument_ids=instrument_ids,business_sessions=business_sessions,**clock)
        if result['status'] not in ('COMPLETE','PARTIAL'):continue
        usable.append(result)
    usable.sort(key=lambda r:datetime.fromisoformat(source_by[r['request_receipt_id']]['ingested_at']))
    masters={};master_refs=[];master_endpoints=set();history={};adjusted={};basic={};market_states={};theme_names={};theme_results=[]
    factor_captures=defaultdict(list); limit_pool={}; direct_limits={}; malformed_master_count=0; master_latest={};sse_delisting=None
    for result in usable:
        if result['endpoint'] in ('mainboard_sse','master_szse'):master_latest[result['endpoint']]=result['request_receipt_id']
    for result in usable:
        endpoint=result['endpoint'];rid=result['request_receipt_id'];source=source_by[rid]
        if endpoint in ('mainboard_sse','master_szse'):
            # A later directory is a new membership snapshot, not a union that
            # keeps disappeared securities in the discovery denominator.
            if rid!=master_latest[endpoint]:continue
            if result['status']=='COMPLETE' and result['pagination_complete']:master_endpoints.add(endpoint)
            master_refs.append(rid)
            for row in result['rows']:
                code=str(row.get('证券代码') if endpoint=='mainboard_sse' else row.get('A股代码') or '')
                if not re.fullmatch(r'\d{6}',code):
                    malformed_master_count+=1
                    gaps.append(dict(field='instrument_identity',status='UNKNOWN',reason='MALFORMED_OFFICIAL_MASTER_ROW',source_ref=rid,impact='MASTER_DENOMINATOR_UNKNOWN_IDENTITY'))
                    continue
                iid=code+('.SH' if endpoint=='mainboard_sse' else '.SZ')
                listing=_date(row.get('上市日期') if endpoint=='mainboard_sse' else row.get('A股上市日期'))
                board='MAIN' if endpoint=='mainboard_sse' or row.get('板块')=='主板' else 'CHINEXT' if row.get('板块')=='创业板' else 'UNKNOWN'
                masters[iid]=dict(instrument_id=iid,exchange='SSE' if endpoint=='mainboard_sse' else 'SZSE',board=board,
                    security_type='A_COMMON',listed_on=listing,name=str(row.get('证券简称') or row.get('A股简称') or ''),source=rid)
        elif endpoint in ('history','market_daily','history_adjusted'):
            target=adjusted if endpoint=='history_adjusted' else history
            for row in result['rows']:
                iid=_iid(row.get('code'));session=_date(row.get('date'))
                expected_flag='1' if endpoint=='history_adjusted' else '3'
                if iid and session in sessions and str(row.get('adjustflag'))==expected_flag:target[(iid,session)]=(row,rid)
        elif endpoint=='adjustment_factors':
            groups=defaultdict(list)
            for row in result['rows']:
                factor_day=_date(row.get('dividOperateDate'))
                if (iid:=_iid(row.get('code'))) and (factor_day is None or factor_day<=day):groups[iid].append(row)
            statuses=result.get('coverage',{}).get('security_statuses',[])
            complete_codes=set()
            if result['status']=='COMPLETE' and result['pagination_complete']:
                complete_codes={_iid(code) for code in result['parameters'].get('codes',())}
            elif result['status']=='PARTIAL':
                for status in statuses:
                    iid=_iid(status.get('code'))
                    if iid and status.get('status')=='COMPLETE' and status.get('cursor_exhausted') is True and status.get('row_count')==len(groups[iid]):complete_codes.add(iid)
                gaps.append(dict(field='adjustment_factor_batch',status='PARTIAL',reason='PER_SECURITY_COMPLETE_FRAGMENTS_ONLY_BATCH_NOT_COMPLETE',source_ref=rid,
                    failed_securities=[{'instrument_id':_iid(s.get('code')),'error_code':s.get('error_code')} for s in statuses if s.get('status')!='COMPLETE'],impact='ONLY_FAILED_OR_UNVERIFIED_INSTRUMENT_FACTORS_UNAVAILABLE'))
            for iid in complete_codes:
                if iid:factor_captures[iid].append((groups[iid],rid,result['parameters'].get('end_date'),result['parameters'].get('start_date')))
        elif endpoint=='limit_up_pool' and _date(result['parameters'].get('date'))==day and result['status']=='COMPLETE' and result['pagination_complete']:
            for row in result['rows']:
                if re.fullmatch(r'\d{6}',str(row.get('代码',''))):limit_pool[str(row['代码'])]=(row,rid)
        elif endpoint=='daily_limits_eastmoney' and _date(result['parameters'].get('date'))==day and result['status'] in ('COMPLETE','PARTIAL'):
            for row in result['rows']:
                iid=_iid(row.get('code')) or str(row.get('instrument_id') or '')
                session=_date(row.get('date'))
                source_business_day=_date(row.get('source_business_date'))
                try:
                    source_timestamp=datetime.fromisoformat(str(row.get('source_timestamp')).replace('Z','+00:00')).astimezone(TZ).date()
                except (TypeError,ValueError):
                    source_timestamp=None
                if iid and session==day and source_business_day in (None,day) and source_timestamp==day:
                    direct_limits[(iid,session)]=(row,rid)
                elif iid:
                    gaps.append(dict(instrument_id=iid,field='current_limit',status='UNAVAILABLE',
                        reason='DIRECT_LIMIT_SOURCE_BUSINESS_DATE_MISMATCH',source_ref=rid,
                        impact='CURRENT_LIMIT_EVENT'))
        elif endpoint=='delisting_sse' and result['status']=='COMPLETE' and result['pagination_complete']:
            codes={str(row.get('INSTRUMENT_ID','')) for row in result['rows']}
            if all(re.fullmatch(r'\d{6}',code) for code in codes):sse_delisting=(codes,rid)
        elif endpoint=='stock_basic':
            for row in result['rows']:
                if (iid:=_iid(row.get('code'))):basic[iid]=(row,rid)
        elif endpoint=='master_all':
            for row in result['rows']:
                if (iid:=_iid(row.get('code'))):market_states[iid]=(row,rid)
        elif endpoint in ('concept_list','concept_list_sina','industry_list'):
            for row in result['rows']:
                code=row.get('板块代码') or row.get('label');name=row.get('板块名称') or row.get('板块')
                if code:theme_names[(endpoint,str(code))]=(str(name or ''),rid)
        elif endpoint in ('concept_members','concept_members_sina','industry_members'):theme_results.append(result)
    by_instrument=defaultdict(list);adjusted_by_instrument=defaultdict(dict)
    for (iid,session),pair in sorted(history.items()):by_instrument[iid].append((session,pair))
    for (iid,session),pair in adjusted.items():adjusted_by_instrument[iid][session]=pair
    # Preserve source-backed exit observations independently of today's master.
    # A stock_basic code proves source identity, not a historical MAIN board.
    observations=dict(masters);outcome_only=[]
    for iid,(row,rid) in basic.items():
        if iid in observations or iid not in by_instrument:continue
        if not _date(row.get('ipoDate')):
            gaps.append(dict(instrument_id=iid,field='listed_on',status='UNKNOWN',reason='OUTCOME_ONLY_NO_VERIFIED_LISTING_DATE',impact='OUTCOME_IDENTITY_UNAVAILABLE'));continue
        observations[iid]=dict(instrument_id=iid,exchange={'SH':'SSE','SZ':'SZSE','BJ':'BSE'}.get(iid[-2:],'UNKNOWN'),board='UNKNOWN',
            security_type='UNKNOWN',listed_on=_date(row['ipoDate']),name=str(row.get('code_name') or ''),source=rid)
        outcome_only.append(iid)
    securities=[];bars=[];limit_proofs=[];identity_proofs=[];fields=dict(historical_isST=0,historical_tradestatus=0,reference_prev_close=0,turnover_fraction=0,
        adjusted_comparable_rows=0,raw_rows=len(history),raw_mainboard_rows=0,verified_current_limit_rows=0,direct_published_limit_rows=0,
        vendor_pool_rule_cross_validated_limit_rows=0,outcome_only_raw_rows=0)
    for iid,master in sorted(observations.items()):
        listing=master['listed_on'];basic_pair=basic.get(iid)
        if listing is None and basic_pair:listing=_date(basic_pair[0].get('ipoDate'))
        if listing is None:
            gaps.append(dict(instrument_id=iid,field='listed_on',status='UNKNOWN',reason='NO_VERIFIED_LISTING_DATE',impact='QUALIFICATION_UNKNOWN'))
            # Contract requires a date; do not invent one. Expected master count
            # retains this identity even though a SecurityRecord cannot be made.
            continue
        masterclock=_clock(source_by[master['source']]);base={k:v for k,v in master.items() if k not in ('source','listed_on')};base['listed_on']=listing
        if basic_pair and _date(basic_pair[0].get('outDate')):base['delisted_on']=_date(basic_pair[0]['outDate'])
        extra_flags=('OUTCOME_ONLY',) if iid in outcome_only else ()
        current_delisting=None;identity_refs=[]
        if iid in masters and master['board']=='MAIN':
            if master['exchange']=='SSE' and sse_delisting:
                current_delisting=iid[:6] in sse_delisting[0];identity_refs.append(sse_delisting[1])
                identity_proofs.append(dict(instrument_id=iid,field='delisting_period',value=current_delisting,method='OFFICIAL_COMPLETE_CURRENT_EXCLUSION_SET',source_refs=[master['source'],sse_delisting[1]]))
            elif master['exchange']=='SZSE' and master['name'] and day>=date(2026,4,24):
                current_delisting=master['name'].strip().endswith('退')
                identity_proofs.append(dict(instrument_id=iid,field='delisting_period',value=current_delisting,method='INFERENCE_FROM_OFFICIAL_CURRENT_SHORT_NAME_AND_MANDATORY_MARKER_RULE',source_refs=[master['source']],rule_url=SZSE_DELISTING_NAME_RULE,rule_section='9.6.1',rule_sha256='958a07598a1919fe7fed7695c9e778dd029d72a38cf48faeba54cfefdb7c581c'))
        adj_rows=adjusted_by_instrument[iid]
        converted,comparison_status=comparable_rows(iid,by_instrument[iid],adj_rows,factor_captures[iid])
        if comparison_status not in ('VERIFIED_PROVIDER_FACTOR_DEFINITION','DIRECT_HFQ_CAPTURE_ONLY'):
            gaps.append(dict(instrument_id=iid,field='comparable_price_basis',status='UNAVAILABLE' if not converted else 'PARTIAL',reason=comparison_status,impact='V_L_G_RETURNS_AND_OUTCOMES'))
        all_state=market_states.get(iid);suspended=None
        if all_state and _date(next((r['parameters'].get('date') for r in usable if r['request_receipt_id']==all_state[1]),None))==day:
            trading=_bool(all_state[0].get('tradeStatus'));suspended=not trading if trading is not None else None
        if (iid,day) not in history:
            state_refs=tuple(dict.fromkeys((master['source'],*identity_refs,*((basic_pair[1],) if basic_pair else ()),*((all_state[1],) if all_state and suspended is not None else ()))))
            securities.append(SecurityRecord(**base,effective_from=day,effective_to=day+timedelta(days=1),st=None,suspended=suspended,
                delisting_period=current_delisting,source_refs=state_refs,risk_flags=extra_flags+('CURRENT_EXCHANGE_ROSTER_NOT_FULL_HISTORICAL_IDENTITY',),**_derived_clock(source_by,state_refs)))
        for session,(row,rid) in by_instrument[iid]:
            rawclock=_clock(source_by[rid],session);st=_bool(row.get('isST'));trading=_bool(row.get('tradestatus'));susp=not trading if trading is not None else None
            fields['historical_isST']+=st is not None;fields['historical_tradestatus']+=susp is not None
            historical_base=dict(base)
            # Historical ST/trading status are actual provider observations.
            # Current board labels are not silently certified as historical PIT.
            if session!=day:historical_base['board']='UNKNOWN'
            state_refs=tuple(dict.fromkeys((master['source'],rid,*((basic_pair[1],) if basic_pair else ()),*(identity_refs if session==day else ()))))
            securities.append(SecurityRecord(**historical_base,effective_from=session,effective_to=session+timedelta(days=1),st=st,suspended=susp,
                delisting_period=current_delisting if session==day else None,source_refs=state_refs,risk_flags=extra_flags+('HISTORICAL_BOARD_AND_RULE_EXCEPTION_STATUS_UNVERIFIED',),**_derived_clock(source_by,state_refs,session)))
            values={k:_number(row.get(f),positive=k not in ('amount_cny','volume_shares')) for k,f in [('open','open'),('high','high'),('low','low'),('close','close'),('reference_prev_close','preclose'),('volume_shares','volume'),('amount_cny','amount')]}
            turn=_number(row.get('turn'));turn=float(turn/100) if turn is not None and turn<=1000 else None
            fields['reference_prev_close']+=values['reference_prev_close'] is not None;fields['turnover_fraction']+=turn is not None
            comparable={};refs=[rid]
            if session in converted:
                comparable,cmprefs=converted[session];refs.extend(cmprefs);fields['adjusted_comparable_rows']+=1
            limit_values=dict(limit_applicable=None,limit_verified=False)
            direct=direct_limits.get((iid,session)) if session==day and iid in masters and master['board']=='MAIN' else None
            if direct and values['close'] is not None:
                row,limit_ref=direct
                up=_number(row.get('limit_up'),positive=True);down=_number(row.get('limit_down'),positive=True);sample=_number(row.get('close'),positive=True)
                if up is not None and down is not None and sample is not None and abs(sample-values['close'])<=Decimal('0.005') and down<=values['close']<=up and down<up:
                    limit_values=dict(limit_applicable=True,limit_verified=True,limit_up=up,limit_down=down,rule_id='DIRECT_PUBLISHED_EASTMONEY_F51_F52_R04')
                    refs.append(limit_ref);fields['verified_current_limit_rows']+=1;fields['direct_published_limit_rows']+=1
                    limit_proofs.append(dict(instrument_id=iid,session=str(day),verification='DIRECT_PUBLISHED',
                        source='EASTMONEY_STOCK_GET_F51_F52',source_refs=[rid,limit_ref],field_mapping=row.get('field_mapping'),
                        limit_up=str(up),limit_down=str(down),source_timestamp=row.get('source_timestamp')))
                else:
                    gaps.append(dict(instrument_id=iid,field='current_limit',status='UNAVAILABLE',
                        reason='DIRECT_LIMIT_PRICE_DATE_OR_VALUE_CROSS_VALIDATION_FAILED',source_ref=limit_ref,impact='CURRENT_LIMIT_EVENT'))
            pool=limit_pool.get(iid[:6]) if session==day and iid in masters and master['board']=='MAIN' else None
            if not limit_values['limit_verified'] and pool and st is False and values['close'] is not None and values['reference_prev_close'] is not None and sum(listing<=s<=day for s in sessions)>=6:
                # Explicit vendor closing-limit event independently corroborates
                # this date's normal limit; it does not certify historical days.
                rule=resolve_rule(master['exchange'],'MAIN',day,st=st,exception_status='NORMAL_VERIFIED')
                calculated=calculate_limits(values['reference_prev_close'],rule)
                up=_number(calculated['limit_up'],positive=True);sample=_number(pool[0].get('最新价'),positive=True)
                if up is not None and sample==up==values['close'] and values['high'] is not None and values['high']<=up:
                    limit_values=dict(limit_applicable=True,limit_verified=True,limit_up=up,limit_down=_number(calculated['limit_down'],positive=True),rule_id=rule['rule_id'])
                    refs.append(pool[1]);fields['verified_current_limit_rows']+=1;fields['vendor_pool_rule_cross_validated_limit_rows']+=1
                    limit_proofs.append(dict(instrument_id=iid,session=str(day),verification='RAW_REFERENCE_OFFICIAL_RULE_VENDOR_CLOSE_LIMIT_EVENT',source_refs=[rid,pool[1]],rule=rule,limit_up=str(up)))
                else:gaps.append(dict(instrument_id=iid,field='current_limit',status='UNAVAILABLE',reason='VENDOR_POOL_RAW_RULE_CROSS_VALIDATION_FAILED',impact='CURRENT_LIMIT_EVENT'))
            valid=all(values[x] is not None for x in ('open','high','low','close'))
            if master['board']=='MAIN':fields['raw_mainboard_rows']+=1
            if iid in outcome_only:fields['outcome_only_raw_rows']+=1
            bars.append(DailyBar(instrument_id=iid,session=session,**values,turnover_fraction=turn,turnover_basis='BAOSTOCK_TURN_PERCENT_FLOAT_SHARES' if turn is not None else None,
                source_refs=tuple(dict.fromkeys(refs)),quality='VALID' if valid else 'PARTIAL',**limit_values,**comparable,**_derived_clock(source_by,refs,session)))
    code_to_iid={i[:6]:i for i in masters};themes=[]
    for result in theme_results:
        endpoint=result['endpoint'];rid=result['request_receipt_id'];symbol=result['parameters']['symbol']
        registry='concept_list_sina' if endpoint=='concept_members_sina' else 'industry_list' if endpoint=='industry_members' else 'concept_list'
        name,definition_ref=theme_names.get((registry,symbol),('',None))
        members=[]
        for row in result['rows']:
            code=str(row.get('代码') or row.get('code') or '')
            iid=_iid(row.get('symbol')) or code_to_iid.get(code)
            if iid:members.append(iid)
        if not definition_ref or not result['pagination_complete']:
            gaps.append(dict(theme_id=symbol,field='theme_definition_or_members',status='UNKNOWN',reason='DEFINITION_OR_PAGINATION_NOT_VERIFIED',impact='T_L_UNAVAILABLE'));continue
        source=source_by[rid];clock=_derived_clock(source_by,(rid,definition_ref))
        themes.append(ThemeSnapshot(theme_id=registry+':'+symbol,name=name,theme_type=_theme_type(name,endpoint),member_ids=tuple(sorted(set(members))),
            effective_from=now.astimezone(TZ).date(),definition_version='PROVIDER_CONCEPT_TAXONOMY_EXCLUDE_QUALIFICATIONS_R03_V1',source_refs=(rid,definition_ref),**clock))
    # Coalesce only consecutive observed exchange-session states with identical
    # evidence and business values; a missing exchange day remains a real gap.
    session_index={d:i for i,d in enumerate(sessions)};coalesced=[];last_session=None
    excluded_from_signature={'effective_from','effective_to','event_time'}
    for state in sorted(securities,key=lambda s:(s.instrument_id,s.effective_from,s.ingested_at)):
        if coalesced and state.instrument_id==coalesced[-1].instrument_id and last_session in session_index and state.effective_from in session_index and session_index[state.effective_from]==session_index[last_session]+1 and state.model_dump(exclude=excluded_from_signature)==coalesced[-1].model_dump(exclude=excluded_from_signature):
            coalesced[-1]=coalesced[-1].model_copy(update={'effective_to':state.effective_to})
        else:coalesced.append(state)
        last_session=state.effective_from
    securities=coalesced
    refs=tuple(sorted(set(master_refs)));universe_clock=dict(event_time=now,source_published_at=now,first_seen_at=now,ingested_at=now)
    universe=UniverseSnapshot(snapshot_id='R03_MASTER_'+digest(masters)[:24],securities=tuple(securities),source_refs=refs,
        complete=master_endpoints=={'mainboard_sse','master_szse'} and malformed_master_count==0,expected_count=len(masters)+malformed_master_count,**universe_clock)
    gaps.extend([dict(field='verified_daily_limit_and_exception_status',status='UNAVAILABLE',reason='RAW_PROVIDER_HAS_NO_OFFICIAL_DAILY_LIMIT_OR_VERIFIED_NORMAL_EXCEPTION_FLAG',impact='B_G_M_STRUCTURE_DISCOVERY_NOT_COMPLETE'),
        dict(field='historical_delisting_period',status='UNKNOWN',reason='CURRENT_OFFICIAL_EXCLUSION_SET_AND_CURRENT_SHORT_NAME_RULE_NOT_BACKFILLED',impact='HISTORICAL_QUALIFICATION_UNKNOWN'),
        dict(field='historical_board_identity',status='PARTIAL',reason='CURRENT_EXCHANGE_ROSTER_AND_LISTING_DATE_RETAINED_HISTORICAL_ISST_TRADESTATUS_RETAINED_BOARD_PIT_NOT_CERTIFIED',impact='HISTORICAL_QUALIFICATION'),
        dict(field='historical_source_publication_time',status='UNKNOWN',reason='ACTUAL_FIRST_SEEN_RETAINED_NO_RETROACTIVE_PIT_PUBLICATION_PROOF',impact='OLD_CUTOFF_REPLAY_ONLY_CURRENT_CAPTURE_CAN_USE_HISTORY')])
    if fields['adjusted_comparable_rows']<len(bars):gaps.append(dict(field='comparable_price_basis',status='PARTIAL',reason='COMPARABLE_SOURCE_MISSING_OR_REJECTED_SEE_PER_INSTRUMENT_GAPS',
        accepted_methods=['SAME_PROVIDER_RAW_TIMES_VERIFIED_BACK_ADJUST_FACTOR','SINGLE_CAPTURE_HFQ_FLAG1'],
        missing_or_rejected_rows=len(bars)-fields['adjusted_comparable_rows'],impact='V_L_G_RETURNS'))
    if not themes:gaps.append(dict(field='current_theme_members',status='UNAVAILABLE',reason='NO_COMPLETE_CURRENT_RESEARCH_THEME_CAPTURE',impact='T_L'))
    fund_records=defaultdict(list);money_results={}
    # Failed requests remain visible as failures, so do not restrict this scan
    # to usable market rows. No successful score authority is minted here.
    for result in results:
        rid=result.get('request_receipt_id')
        if rid not in source_by:continue
        if result['endpoint']=='money_flow':
            params=result['parameters'];iid=str(params['stock'])+'.'+str(params['market']).upper()
            money_results[iid]=result
        elif result['endpoint']=='lhb_summary' and result['status'] in ('COMPLETE','PARTIAL'):
            for row in result['rows']:
                iid=code_to_iid.get(str(row.get('代码','')))
                if iid and _date(row.get('上榜日'))==day:fund_records[iid].append(summary_preview(row,iid,[rid],_clock(source_by[rid],day)))
    funds=[]
    for iid in sorted(set(fund_records)|set(money_results)):
        money=money_results.get(iid);records=fund_records[iid]
        refs=list(dict.fromkeys([ref for record in records for ref in record['source_refs']]+([money['request_receipt_id']] if money else [])))
        if not refs:continue
        latest=max((source_by[r] for r in refs),key=lambda r:r['ingested_at'])
        funds.append(dict(instrument_id=iid,provider='EASTMONEY_VIA_AKSHARE',money_definition='EASTMONEY_BIG_ORDER_CLASSIFICATION_UNVERIFIED_R03',
            definition_version='FUNDS_EVIDENCE_V1',money_status='FETCH_FAILED' if money and money['status']=='FETCH_FAILED' else 'UNAVAILABLE',
            disclosure_status='WINDOW_UNVERIFIED' if records else 'UNAVAILABLE',disclosure_complete=False,
            money_error_code=money.get('error_code') if money else None,public_summary_records=records,
            source_refs=refs,reason_codes=['PUBLIC_SUMMARY_IS_NOT_SEAT_OR_WINDOW_AUTHORITY','NO_COMPARABLE_THREE_SESSION_MONEY_SOURCE'],**_clock(latest,day)))
    bundle=MarketInput(observation_date=day,sessions=sessions,universe=universe,bars=tuple(bars),themes=tuple(themes),receipts=tuple(receipts.values()),funds=tuple(funds),coverage_type='PROSPECTIVE_CAPTURED')
    report=dict(normalization_version='R03_SOURCE_ASSEMBLY_V1',fields_retained=fields,master_identity_count=len(masters),
        mainboard_identity_count=sum(m['board']=='MAIN' for m in masters.values()),current_master_complete=universe.complete,
        raw_instrument_count=len({b.instrument_id for b in bars}),themes=len(themes),gaps=gaps,limit_cross_validation=limit_proofs,current_identity_proofs=identity_proofs,
        outcome_only_instrument_ids=sorted(outcome_only),malformed_master_rows=malformed_master_count,funds_public_summary_rows=sum(map(len,fund_records.values())),funds_score_authorized_families=0,
        legitimate_empty_permitted=False,empty_block_reason='UNVERIFIED_REQUIRED_EVENTS_OR_SHARED_COMPONENTS',
        actual_capture_cutoff=now.isoformat(),historical_download_not_historical_publication=True)
    return bundle,report

def normalize_capture(results,observation_date,now,sessions):
    return normalize_capture_with_report(results,observation_date,now,sessions)[0]
