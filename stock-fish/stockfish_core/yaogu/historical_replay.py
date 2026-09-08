"""Audited R05 historical reconstruction from immutable, source-dated captures.

This adapter does not relax EvidenceResolver or overwrite capture clocks. It
constructs new, content-bound receipts for the source's documented historical
fields. Current names/ST/universe membership are never used for past signals.
The current supported historical board authority is SZSE's dated 2021 merger
notice plus dated security existence, stock type and original IPO facts.
Unsupported exchange classification remains UNKNOWN and limits the scope.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date,datetime,time,timedelta
from decimal import Decimal,InvalidOperation
import hashlib
import json
import zipfile
from xml.etree import ElementTree
from pathlib import Path
from zoneinfo import ZoneInfo
from .contracts import DailyBar,HistoricalTimeProof,MarketInput,SecurityRecord,SourceReceipt,UniverseSnapshot,digest
from .provider_comparability import comparable_rows

SH=ZoneInfo('Asia/Shanghai')
VERSION='R12_DATED_REGISTERED_IDENTITY_RECONSTRUCTION_V1'


def file_hash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _day(value):
    try:return date.fromisoformat(str(value)[:10])
    except (ValueError,TypeError):return None


def _number(value):
    try:
        n=Decimal(str(value));return n if n.is_finite() else None
    except (ValueError,TypeError,InvalidOperation):return None


def _iid(code):
    parts=str(code).split('.')
    if len(parts)==2 and parts[0] in ('sz','sh','bj') and len(parts[1])==6 and parts[1].isdigit():
        return parts[1]+'.'+parts[0].upper()
    return None


def _utc(value):return datetime.fromisoformat(str(value).replace('Z','+00:00'))


def verify_capture(root,result):
    """Bind the checkpoint to archived bytes, receipt identity and request scope.

    SHA256 provides local integrity, not vendor authentication. A reconstruction
    can never promote modified checkpoint rows into new trusted source records.
    """
    rid=result['request_receipt_id']
    base=Path(root)
    candidates=[base/'cache'/'raw'/(rid+'.receipt.json')]
    candidates.extend(sorted((base/'raw').glob('**/raw/'+rid+'.receipt.json')))
    receipt_path=next((p for p in candidates if p.is_file()),None)
    if receipt_path is None:raise FileNotFoundError('ORIGINAL_RECEIPT_ARCHIVE_MISSING:'+rid)
    original=json.loads(receipt_path.read_text(encoding='utf-8'))
    embedded=[r for r in result['receipts'] if r.get('receipt_id')==rid]
    identity={k:v for k,v in original.items() if k!='receipt_id'}
    expected=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if expected!=rid or embedded!=[original]:raise ValueError('ORIGINAL_RECEIPT_IDENTITY_MISMATCH')
    raw_path=receipt_path.parent/(original['content_hash']+'.raw.json')
    if file_hash(raw_path)!=original['content_hash']:raise ValueError('ORIGINAL_RAW_HASH_MISMATCH')
    payload=json.loads(raw_path.read_text(encoding='utf-8'))
    if original['dataset']!=result['endpoint'] or original['parameters']!=result['parameters'] or original['provider']!=result['provider']:
        raise ValueError('ORIGINAL_REQUEST_SCOPE_MISMATCH')
    for key in ('rows','status','pagination_complete'):
        if payload.get(key)!=result.get(key):raise ValueError('ORIGINAL_RAW_'+key.upper()+'_MISMATCH')
    if original['status']!=result['status'] or original['pagination_complete']!=result['pagination_complete']:
        raise ValueError('ORIGINAL_RECEIPT_STATUS_MISMATCH')
    for key in ('coverage','gaps'):
        if payload.get(key,{ } if key=='coverage' else [])!=result.get(key,{ } if key=='coverage' else []):
            raise ValueError('ORIGINAL_COVERAGE_MISMATCH')
    if original['row_count'] is not None and original['row_count']!=len(result['rows']):raise ValueError('ORIGINAL_ROW_COUNT_MISMATCH')
    return {'receipt_path':str(receipt_path.resolve()),'raw_path':str(raw_path.resolve()),
            'receipt_file_sha256':file_hash(receipt_path),'raw_file_sha256':original['content_hash']}


def source_policy(source_dir):
    root=Path(source_dir)
    required=('baostock-home.md','baostock-DailyUpdates.md','baostock-StockBasicInfoAPI.md',
              'baostock-stockBasic.md','baostock-factorInfo.md','baostock-adjustment.pdf',
              'szse-mainboard-merger.html','szse-stock-rules.pdf')
    for name in required:
        if not (root/name).is_file():raise ValueError('HISTORICAL_SOURCE_DOCUMENT_MISSING:'+name)
    home=(root/'baostock-home.md').read_text(encoding='utf-8')
    merger=(root/'szse-mainboard-merger.html').read_text(encoding='utf-8')
    if '17:30' not in home or '18:00' not in home or '000001-004999' not in merger:
        raise ValueError('SOURCE_DOCUMENT_SEMANTIC_CHECK_FAILED')
    result={'policy_id':VERSION,'verified_at':datetime.now(SH).isoformat(),
            'source_documents':{name:{'path':str((root/name).resolve()),'sha256':file_hash(root/name)} for name in required},
            'board_authority':{'SZSE':{'effective_from':'2021-04-06','range':['000001','004999'],
                 'requires':['DATED_QUERY_ALL_STOCK','DAILY_A_STOCK_RECORD','STOCK_BASIC_TYPE_1','IPO_DATE'],
                 'url':'https://www.szse.cn/disclosure/notice/general/t20210331_585343.html'}},
            'scope_status':'LIMITED_SCOPE','unknown_scope':['SSE_HISTORICAL_BOARD_CLASSIFICATION_NOT_CERTIFIED'],
            'price_normalization_scope':'ALL_DATED_SZSE_CODES_000001_TO_004999_REGARDLESS_OF_CURRENT_MEMBERSHIP_OR_PERFORMANCE',
            'business_day_clock':'SOURCE_DATED_RECORD','historical_publication_rule':{'market_daily':'17:30','master_all':'17:30','adjustment_factors':'18:00'},
            'capture_clock':'ORIGINAL_FIRST_SEEN_AND_INGESTED_RETAINED',
            'current_master_role':'CROSS_CHECK_ONLY_NOT_HISTORICAL_MEMBERSHIP',
            'stock_basic_role':'ORIGINAL_IPO_DATE_AND_TYPE_CORROBORATION_NOT_CURRENT_ST_OR_CURRENT_NAME',
            'registered_original_identity_alternatives':[
                'BAOSTOCK_STOCK_BASIC_ALL_OR_EXPLICIT_STOCK_BASIC_BATCH',
                'EXCHANGE_A_SHARE_DIRECTORY_ORIGINAL_LISTING_DATE_AND_TYPE_CORROBORATION_ONLY'],
            'revision_limitation':'HISTORICAL_RECONSTRUCTION_WITH_CURRENT_VENDOR_REVISIONS_NOT_ARCHIVED_PIT',
            'limit_prices':'UNVERIFIED_NO_RULE_DERIVATION_IN_THIS_ADAPTER'}
    calendar_docs=('sse-calendar.html','szse-calendar.html')
    result['source_documents'].update({name:{'path':str((root/name).resolve()),'sha256':file_hash(root/name)}
                                       for name in calendar_docs if (root/name).is_file()})
    result['calendar_authority']='ACTUAL_PROVIDER_CALENDAR_CHECKPOINT_REQUIRED_FOR_REPLAY_OPTIONAL_EXCHANGE_CALENDAR_DOCS_ARE_AUDIT_ATTACHMENTS'
    extra=('sse-code-guide-20260831.docx','sse-code-guide-20260831-notice.html','sse-stock-rules-20260424.docx','sse-code-guide-receipts.json')
    if all((root/name).is_file() for name in extra):
        with zipfile.ZipFile(root/extra[0]) as archive:
            xml=ElementTree.fromstring(archive.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        found=set()
        for row in xml.findall('.//w:tr',ns):
            cells=[''.join(c.itertext()) for c in row.findall('w:tc',ns)]
            if len(cells)>=4 and cells[0]=='6' and cells[1] in ('00','01','03','05') and '主板A股股票' in ''.join(cells):found.add('6'+cells[1])
        if found!={'600','601','603','605'}:raise ValueError('SSE_OFFICIAL_CODE_TABLE_SEMANTIC_CHECK_FAILED')
        result['source_documents'].update({name:{'path':str((root/name).resolve()),'sha256':file_hash(root/name)} for name in extra})
        result['board_authority']['SSE']={'effective_from':'2026-08-31','document_last_modified':'2026-08-31T16:38:09+08:00',
            'signal_decision_clock':'20:00 Asia/Shanghai','prefixes':sorted(found),'prior_version_status':'OFFICIAL_20260713_URL_HTTP_404_NOT_BACKFILLED',
            'requires':['DATED_QUERY_ALL_STOCK','DAILY_A_STOCK_RECORD','STOCK_BASIC_TYPE_1','IPO_DATE'],
            'url':'https://www.sse.com.cn/lawandrules/guide/stock/jyglywznylc/zn/c/c_20260831_10830435.shtml'}
        result['unknown_scope']=['SSE_HISTORICAL_BOARD_BEFORE_2026_08_31_UNKNOWN','UNCLASSIFIED_OTHER_SECURITIES_NOT_ASSUMED_ELIGIBLE']
        result['price_normalization_scope']='ALL_SOURCE_DATED_CODES_IN_EXCHANGE_ALLOCATION_AUTHORITY_EFFECTIVE_BY_SIGNAL_T_NO_CURRENT_MEMBERSHIP_OR_PERFORMANCE_FILTER'
    return result


class HistoricalReplayAssembler:
    def __init__(self,capture_dir,policy):
        self.root=Path(capture_dir);self.policy=policy
        for item in policy['source_documents'].values():
            if file_hash(item['path'])!=item['sha256']:raise ValueError('SOURCE_DOCUMENT_HASH_MISMATCH')
        self.results=self._load_capture_results()
        self.raw={};self.masters={};self.basic={};self.factors=defaultdict(list);self.original_refs={}
        identity_facts=defaultdict(list);self.identity_conflicts=set()
        self.calendar=();self.integrity={}
        for r in self.results:
            if r['status'] not in ('COMPLETE','PARTIAL'):continue
            self.integrity[r['request_receipt_id']]=verify_capture(self.root,r)
            rid=r['request_receipt_id'];self.original_refs[rid]=r
            endpoint=r['endpoint']
            if endpoint=='calendar' and r['status']=='COMPLETE' and r['pagination_complete']:
                self.calendar=tuple(sorted({_day(x.get('calendar_date')) for x in r['rows'] if str(x.get('is_trading_day'))=='1' and _day(x.get('calendar_date'))}))
            elif endpoint=='market_daily' and r['status']=='COMPLETE' and r['pagination_complete']:
                d=_day(r['parameters']['date'])
                if any(_day(x.get('date'))!=d or str(x.get('adjustflag'))!='3' for x in r['rows']):
                    raise ValueError('RAW_DAILY_RESPONSE_DATE_OR_ADJUSTMENT_MISMATCH')
                if len({x.get('code') for x in r['rows']})!=len(r['rows']):raise ValueError('DUPLICATE_RAW_DAILY_SECURITY')
                self.raw[d]=r
            elif endpoint=='master_all' and r['status']=='COMPLETE' and r['pagination_complete']:
                d=_day(r['parameters']['date'])
                if len({x.get('code') for x in r['rows']})!=len(r['rows']):raise ValueError('DUPLICATE_DATED_SECURITY')
                self.masters[d]=r
            elif endpoint in ('stock_basic_all','stock_basic') and r['status']=='COMPLETE' and r['pagination_complete'] and r['provider']=='baostock':
                if len({x.get('code') for x in r['rows']})!=len(r['rows']):raise ValueError('DUPLICATE_BASIC_SECURITY')
                for row in r['rows']:
                    if _iid(row.get('code')) and _day(row.get('ipoDate')):
                        identity_facts[row['code']].append((row,rid))
            elif (endpoint in ('mainboard_sse','master_szse') and r['status']=='COMPLETE' and r['pagination_complete']
                  and r['provider']=='akshare' and 'EXCHANGE_A_SHARE_DIRECTORY_ORIGINAL_LISTING_DATE_AND_TYPE_CORROBORATION_ONLY'
                  in policy.get('registered_original_identity_alternatives',())):
                # Only immutable original listing facts and A-share type are
                # corroborated here. Past existence/name/ST/suspension still
                # require that session's master_all AND dated RAW evidence.
                for row in r['rows']:
                    code=str(row.get('证券代码') if endpoint=='mainboard_sse' else row.get('A股代码') or '')
                    listing=_day(row.get('上市日期') if endpoint=='mainboard_sse' else row.get('A股上市日期'))
                    if len(code)!=6 or not code.isdigit() or listing is None:continue
                    code=('sh.' if endpoint=='mainboard_sse' else 'sz.')+code
                    identity_facts[code].append(({'code':code,'ipoDate':str(listing),'type':'1',
                        'original_fact_contract':'EXCHANGE_A_SHARE_DIRECTORY_ORIGINAL_LISTING_DATE_ONLY'},rid))
            elif endpoint=='adjustment_factors':
                grouped=defaultdict(list)
                for row in r['rows']:grouped[row.get('code')].append(row)
                success={s['code'] for s in r.get('coverage',{}).get('security_statuses',[])
                         if s.get('status')=='COMPLETE' and s.get('cursor_exhausted') and s.get('row_count')==len(grouped[s['code']])}
                if r['status']=='COMPLETE' and r['pagination_complete']:success.update(r['parameters']['codes'])
                for code in success:
                    self.factors[code].append((grouped[code],rid,r['parameters']['end_date'],r['parameters']['start_date']))
        for code,facts in identity_facts.items():
            if len({(_day(row['ipoDate']),str(row.get('type'))) for row,_ in facts})!=1:
                self.identity_conflicts.add(code)
            else:
                # Registered BaoStock rows keep their existing exact contract;
                # otherwise use a verified exchange original-listing fact.
                self.basic[code]=min(facts,key=lambda fact:(self.original_refs[fact[1]]['endpoint'] not in ('stock_basic','stock_basic_all'),fact[1]))
        if not self.calendar:raise ValueError('ACTUAL_EXCHANGE_CALENDAR_REQUIRED')
        if not self.basic:raise ValueError('ACTUAL_SECURITY_IPO_FACTS_REQUIRED_REGISTERED_ALTERNATIVES_EXHAUSTED')
        self.policy_hash=digest({**{k:v for k,v in policy.items() if k not in ('verified_at','source_documents')},
            'source_documents':{name:{'sha256':document['sha256']} for name,document in policy['source_documents'].items()}})

    def _load_capture_results(self):
        """Read legacy checkpoints and durable request-queue result documents."""
        loaded=[];seen=set()
        for folder in ('checkpoints','results'):
            for path in sorted((self.root/folder).glob('*.json')):
                result=json.loads(path.read_text(encoding='utf-8'))
                key=result.get('request_receipt_id') or digest([result.get('endpoint'),result.get('parameters'),result.get('completed_at')])
                if key in seen:continue
                seen.add(key);loaded.append(result)
        loaded.sort(key=lambda x:x.get('completed_at',''))
        return loaded

    def build(self,observation_date,*,history_sessions=26,include_security_history=False,history_store=None):
        day=_day(observation_date)
        if day not in self.calendar:raise ValueError('NOT_IN_ACTUAL_EXCHANGE_CALENDAR')
        previous=[d for d in self.calendar if d<=day]
        sse=self.policy.get('board_authority',{}).get('SSE')
        sse_known=bool(sse and day>=_day(sse['effective_from']))
        def in_price_scope(code):
            return (code.startswith('sz.') and '000001'<=code[3:]<='004999') or (sse_known and code.startswith('sh.') and code[3:6] in sse['prefixes'])
        price_days=previous[-history_sessions:]
        security_days=[d for d in price_days if d in self.masters] if include_security_history else [day]
        receipts={};sources=[];gaps=[];seen=datetime.now(SH);raw_total=0;outside_price_scope=0
        def receipt(endpoint,business_day,rows,origin,*,clock_hour=17,clock_minute=30):
            original=self.original_refs[origin]
            base=next(r for r in original['receipts'] if r['receipt_id']==origin)
            content=digest(rows)
            rid='historical_'+digest([VERSION,endpoint,str(business_day),content,origin,self.policy_hash])
            doc={'master_all':'StockBasicInfoAPI','original_ipo_facts':'stockBasic','adjustment_factors':'factorInfo'}.get(endpoint,'DailyUpdates')
            source_url=(base.get('source_url') if endpoint=='original_ipo_facts' and original['provider']!='baostock'
                        else 'https://www.baostock.com/?file='+doc+'.md')
            publication=datetime.combine(business_day,time(clock_hour,clock_minute),SH)
            proof=HistoricalTimeProof(policy_id=VERSION,source_url=source_url,evidence_hash=digest({'policy':self.policy_hash,'origin':origin,'rows':content}),
                event_time_basis='SOURCE_DATED_EVENT_RECORD',publication_time_basis='SOURCE_DOCUMENTED_RELEASE_RULE',
                applies_from=date(1990,1,1),applies_to=day,evidence_refs=(origin,*tuple(x['sha256'] for x in self.policy['source_documents'].values())),verified_at=seen)
            value=SourceReceipt(receipt_id=rid,provider=original['provider'],dataset=endpoint,content_hash=content,field_version=VERSION,
                row_count=len(rows),pagination_complete=True,status='COMPLETE',source_url=source_url,source_type=endpoint,
                parameters={'original_endpoint':original['endpoint'],'original_parameters_hash':digest(original['parameters']),
                            'business_date':str(business_day),'reconstruction_policy_hash':self.policy_hash},
                instrument_ids=tuple(sorted({_iid(x['code']) for x in rows if x.get('code') and _iid(x['code'])})) if endpoint=='adjustment_factors' else (),
                business_sessions=tuple(price_days) if endpoint=='adjustment_factors' else (business_day,),
                event_time=datetime.combine(business_day,time(15),SH),source_published_at=publication,
                first_seen_at=_utc(base['first_seen_at']),ingested_at=_utc(base['ingested_at']),historical_time_proof=proof)
            receipts[rid]=value;sources.append({'normalized_receipt_id':rid,'original_receipt_id':origin,
                'original_capture_result_hash':digest(original),'normalized_content_hash':content,'business_date':str(business_day),
                'clock_interpretation':'SOURCE_DOCUMENTED_RELEASE_RULE_NOT_ORIGINAL_PUBLISH_TIMESTAMP'})
            return rid
        raw_by=defaultdict(list);raw_refs={};dated_states={}
        for d in price_days:
            r=self.raw.get(d)
            if r is None:gaps.append({'session':str(d),'field':'RAW_PRICE','reason':'NO_SUCCESSFUL_DAILY_CAPTURE'});continue
            raw_total+=len(r['rows'])
            rr=receipt('market_daily',d,r['rows'],r['request_receipt_id']);raw_refs[d]=rr
            for row in r['rows']:
                code=row['code'];iid=_iid(code)
                if not in_price_scope(code):
                    outside_price_scope+=1;continue
                if iid:raw_by[code].append((d,(row,rr)));dated_states[(code,d)]=row
        factor_refs={};converted={}
        for code,raw_rows in raw_by.items():
            captures=[]
            for rows,origin,end,start in self.factors.get(code,[]):
                if _day(end) is None or _day(end)<day:
                    gaps.append({'instrument_id':_iid(code),'field':'COMPARABLE_PRICE','reason':'FACTOR_CAPTURE_END_BEFORE_SIGNAL'});continue
                scoped=[r for r in rows if _day(r.get('dividOperateDate')) and _day(r['dividOperateDate'])<=day]
                effective=max((_day(r['dividOperateDate']) for r in scoped),default=None)
                if effective is None:continue
                fr=receipt('adjustment_factors',effective,scoped,origin,clock_hour=18,clock_minute=0)
                captures.append((scoped,fr,str(day),start));factor_refs[(code,origin)]=fr
            converted[code],quality=comparable_rows(_iid(code),raw_rows,{},captures)
            if quality not in ('VERIFIED_PROVIDER_FACTOR_DEFINITION',):
                gaps.append({'instrument_id':_iid(code),'field':'COMPARABLE_PRICE','reason':quality})
        bars=[];bar_count=0
        writer=history_store.writer() if history_store is not None else None
        recent_start=price_days[max(0,len(price_days)-6)]
        for code,rows in raw_by.items():
            for d,(raw,rr) in rows:
                values={key:_number(raw.get(source)) for key,source in [('open','open'),('high','high'),('low','low'),('close','close'),
                         ('reference_prev_close','preclose'),('volume_shares','volume'),('amount_cny','amount')]}
                comparable,refs=converted[code].get(d,({},(rr,)))
                refs=tuple(dict.fromkeys((rr,*refs)))
                clocks={'event_time':datetime.combine(d,time(15),SH),'source_published_at':max(receipts[x].source_published_at for x in refs),
                        'first_seen_at':max(receipts[x].first_seen_at for x in refs),'ingested_at':max(receipts[x].ingested_at for x in refs)}
                invalid=any(values[k] is None or values[k]<=0 for k in ('open','high','low','close','reference_prev_close'))
                if invalid:
                    gaps.append({'instrument_id':_iid(code),'session':str(d),'field':'RAW_PRICE','reason':'INVALID_OHLC_OR_REFERENCE'});continue
                bar=DailyBar(instrument_id=_iid(code),session=d,**values,**comparable,source_refs=refs,**clocks)
                bar_count+=1
                if writer is not None:writer.append(bar)
                if writer is None or d>=recent_start:bars.append(bar)
        securities=[];master_refs=[]
        for d in security_days:
            master=self.masters.get(d)
            if master is None:
                gaps.append({'session':str(d),'field':'DATED_SECURITY_EXISTENCE','reason':'NO_SUCCESSFUL_DATED_MASTER'});continue
            mr=receipt('master_all',d,master['rows'],master['request_receipt_id']);master_refs.append(mr)
            ipo_groups=defaultdict(list);ipo_refs={}
            for code,(b,origin) in self.basic.items():
                if _day(b.get('ipoDate')) and _day(b['ipoDate'])<=d:
                    ipo_groups[origin].append({'code':code,'ipoDate':b['ipoDate'],'type':b.get('type'),
                        'type_semantics':'CURRENT_CORROBORATION_ONLY_NOT_DATED_HISTORICAL_TYPE'})
            for origin,ipo_rows in ipo_groups.items():
                ir=receipt('original_ipo_facts',d,ipo_rows,origin,clock_hour=0,clock_minute=0)
                ipo_refs.update({row['code']:ir for row in ipo_rows})
            for row in master['rows']:
                code=row.get('code');iid=_iid(code)
                if iid is None:continue
                base=self.basic.get(code,(None,None))[0];raw=dated_states.get((code,d));ipo=_day(base.get('ipoDate')) if base else None
                if ipo is None:
                    gaps.append({'instrument_id':iid,'session':str(d),'field':'ORIGINAL_IPO_DATE',
                        'reason':'CONFLICTING_REGISTERED_IDENTITY_FACTS' if code in self.identity_conflicts else 'UNKNOWN_IPO'});continue
                if code not in ipo_refs:continue
                refs=(mr,ipo_refs[code],*((raw_refs[d],) if d in raw_refs else ()))
                name=str(row.get('code_name') or '')
                # Historical A-share existence, stock type and a dated exchange
                # namespace authority must all agree. Current master is unused.
                board_authorized=(code.startswith('sz.') and '000001'<=code[3:]<='004999' and d>=date(2021,4,6)) or (sse and code.startswith('sh.') and code[3:6] in sse['prefixes'] and d>=_day(sse['effective_from']))
                main=bool(board_authorized and base.get('type')=='1' and raw is not None)
                board='MAIN' if main else 'UNKNOWN'
                typ='A_COMMON' if main else 'UNKNOWN'
                st=(str(raw.get('isST'))=='1') if raw is not None and str(raw.get('isST')) in ('0','1') else None
                # Dated raw and dated security list must agree on suspension.
                a=str(raw.get('tradestatus')) if raw else None;b=str(row.get('tradeStatus'))
                suspended=(a=='0') if a in ('0','1') and b==a else None
                delisting=(name.startswith('退市') if code.startswith('sh.') else name.endswith('退')) if name and main else None
                clocks={'event_time':datetime.combine(d,time(15),SH),'source_published_at':max(receipts[x].source_published_at for x in refs),
                        'first_seen_at':max(receipts[x].first_seen_at for x in refs),'ingested_at':max(receipts[x].ingested_at for x in refs)}
                security=SecurityRecord(instrument_id=iid,exchange={'sz':'SZSE','sh':'SSE','bj':'BSE'}[code[:2]],
                    board=board,security_type=typ,listed_on=ipo,effective_from=d,effective_to=d+timedelta(days=1),
                    st=st,suspended=suspended,delisting_period=delisting,name=name,source_refs=refs,
                    risk_flags=('HISTORICAL_RECONSTRUCTION_VENDOR_REVISION_RISK',),**clocks)
                if writer is not None:writer.append(security)
                if writer is None or d>=recent_start:securities.append(security)
        if not master_refs:
            # A missing source is not a fabricated empty successful master.
            clock=datetime.combine(day,time(20),SH)
            universe=UniverseSnapshot(snapshot_id='unavailable_'+str(day),securities=(),complete=False,expected_count=None,
                event_time=clock,source_published_at=clock,first_seen_at=seen,ingested_at=seen)
        else:
            refs=tuple(master_refs);universe=UniverseSnapshot(snapshot_id='historical_universe_'+digest(securities),securities=tuple(securities),
                complete=False,expected_count=len({_iid(x.get('code')) for x in self.masters[day]['rows']} - {None}) if day in self.masters else None,
                source_refs=refs,event_time=datetime.combine(day,time(15),SH),source_published_at=max(receipts[x].source_published_at for x in refs),
                first_seen_at=max(receipts[x].first_seen_at for x in refs),ingested_at=max(receipts[x].ingested_at for x in refs))
        artifact_identity={}
        if writer is not None:
            writer.receipts={rid:r.model_dump(mode='json') for rid,r in receipts.items()}
            artifact_identity=writer.finish()
        bundle=MarketInput(observation_date=day,sessions=self.calendar,universe=universe,bars=tuple(bars),receipts=tuple(receipts.values()),
                           coverage_type='RECONSTRUCTED_HISTORICAL',**artifact_identity)
        if history_store is not None:bundle._history_store=history_store
        audit={'normalizer_version':VERSION,'source_policy_hash':self.policy_hash,'actual_generated_at':seen.isoformat(),
               'signal_information_max_session':str(day),'future_price_rows_in_signal':sum(b.session>day for b in bars),
               'raw_security_records':len(securities),'unique_security_codes':len({s.instrument_id for s in securities}),
               'raw_price_rows_before_explicit_scope':raw_total,'raw_price_rows_outside_explicit_scope':outside_price_scope,
               'normalized_history_bar_count':bar_count,'daily_inline_bar_count':len(bars),**artifact_identity,
               'sources':sources,'gaps':gaps,'scope':'DATED_SSE_AND_SZSE_MAINBOARD_CERTIFIED_SUBSET_OTHER_IDENTITY_UNKNOWN' if sse_known else 'DATED_SZSE_MAINBOARD_CERTIFIED_SUBSET_OTHER_IDENTITY_UNKNOWN',
               'archived_point_in_time':False,'original_capture_clocks_modified':False,'input_hash':digest(bundle)}
        return bundle,audit
