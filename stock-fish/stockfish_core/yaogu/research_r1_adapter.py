"""Resolve archived normalized evidence into a simulation capability.

Archives are hash-bound to SourceReceipt and retain four clocks. This validates
local evidence integrity/provenance, not cryptographic authenticity of providers.
No tape supplied by a caller can grant itself this capability with verified=true.
"""
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from .contracts import DailyBar, SecurityRecord, SourceReceipt, digest
from .visibility import EvidenceResolver
from .execution_research_r1 import deadline, stamp


class ResolvedTape(dict):
    def seal(self):self.resolved_hash=digest(self);return self
    def intact(self):return self.resolved_hash==digest(self)


def adapt_execution(spec, *, base):
    diagnostics=[]; records=defaultdict(list); receipts=[]; total=0
    try:
        if spec['contract']!='R1_ARCHIVED_EXECUTION_EVIDENCE_V2':raise ValueError('UNSUPPORTED_EXECUTION_ARCHIVE')
        sessions=spec['sessions']
        if sessions!=sorted(set(sessions)) or not sessions:raise ValueError('INVALID_EXECUTION_CALENDAR')
        as_of=stamp(spec['report_as_of']);coverage=spec['coverage_type']
        if coverage not in ('TEST_FIXTURE','RECONSTRUCTED_HISTORICAL','PROSPECTIVE_CAPTURED'):raise ValueError('UNSUPPORTED_SIGNAL_EVIDENCE_TYPE')
        root=Path(base).resolve()
        for artifact in spec['artifacts']:
            path=(root/artifact['path']).resolve()
            if not path.is_relative_to(root):raise ValueError('SOURCE_PATH_OUTSIDE_ARCHIVE')
            size=path.stat().st_size;total+=size
            if size>8*1024**2 or total>128*1024**2:raise ValueError('RECOVERABLE_EXECUTION_ARCHIVE_BUDGET')
            raw=path.read_bytes(); receipt=SourceReceipt.model_validate(artifact['receipt'])
            if hashlib.sha256(raw).hexdigest()!=receipt.content_hash:raise ValueError('EXECUTION_SOURCE_HASH_MISMATCH')
            document=json.loads(raw)
            if receipt.status!='COMPLETE' or not receipt.pagination_complete or receipt.row_count!=len(document['records']):raise ValueError('INCOMPLETE_EXECUTION_SOURCE')
            receipts.append(receipt)
            for row in document['records']:
                value=row['value']
                if value.get('source_refs')!=[receipt.receipt_id]:raise ValueError('EXECUTION_SOURCE_MEMBERSHIP_MISMATCH')
                if row['kind'] in ('RULE','COST','CALENDAR') and coverage!='TEST_FIXTURE':
                    host=urlparse(receipt.source_url or '').hostname or ''
                    authorities=('sse.com.cn','szse.cn','chinaclear.cn','mof.gov.cn','chinatax.gov.cn')
                    if not any(host==domain or host.endswith('.'+domain) for domain in authorities):raise ValueError('OFFICIAL_RULE_SOURCE_REQUIRED')
                records[row['kind']].append(value)
        def resolve(kind, cutoff, *, iid=None, session=None):
            resolver=EvidenceResolver(receipts,cutoff=cutoff,coverage_type=coverage,sessions=sessions,record_rows=False)
            matches=[]
            for value in records[kind]:
                if iid is not None and value.get('instrument_id')!=iid:continue
                if value.get('session') is not None and value['session']!=session:continue
                proof=resolver.resolve(value,instrument_id=iid,session=date.fromisoformat(session) if session else None,
                    require_session=kind=='BAR',require_complete=True)
                if proof.allowed:matches.append(value)
                else:diagnostics.append(dict(kind=kind,instrument_id=iid,session=session,reasons=list(proof.reason_codes)))
            unique={digest(x):x for x in matches}
            if len(unique)>1:raise ValueError('CONFLICTING_EXECUTION_EVIDENCE:'+kind)
            return next(iter(unique.values()),None)
        calendar=resolve('CALENDAR',deadline(sessions[0]))
        if not calendar or calendar.get('sessions')!=sessions:raise ValueError('DATED_CALENDAR_EVIDENCE_REQUIRED')
        cost=resolve('COST',deadline(sessions[0]))
        if not cost or cost.get('commission_status')!='PREDECLARED_HYPOTHETICAL':raise ValueError('EXPLICIT_HYPOTHETICAL_COMMISSION_SCENARIO_REQUIRED')
        cost=dict(cost,verified=True)
        # Resolver intervals are half-open, engine dates inclusive. Do not invent a rule end.
        def inclusive(value):
            from datetime import timedelta
            if not value.get('effective_to'):raise ValueError('SUPPORTED_RULE_INTERVAL_REQUIRED')
            return dict(value,verified=True,effective_to=(date.fromisoformat(value['effective_to'])-timedelta(days=1)).isoformat())
        cost=inclusive(cost)
        tape=ResolvedTape(coverage_type=coverage,sessions=sessions,as_of=spec['report_as_of'],cost_scenario=cost,bars={},rules={},orders={},valuations={})
        ids=sorted({v['instrument_id'] for v in records['BAR']})
        for index,session in enumerate(sessions):
            tape['bars'][session]={};tape['rules'][session]={};tape['orders'][session]={};tape['valuations'][session]={}
            for iid in ids:
                bar=resolve('BAR',as_of,iid=iid,session=session)
                security=resolve('SECURITY',deadline(session),iid=iid,session=session)
                rule=resolve('RULE',deadline(session),iid=iid,session=session)
                action=resolve('ACTION_COVERAGE',as_of,iid=iid,session=session)
                if security:security=SecurityRecord.model_validate(security).model_dump(mode='json')
                if rule and security and security['exchange'] in ('SSE','SZSE') and security['board']=='MAIN' and security['security_type']=='A_COMMON':
                    if rule.get('exchange')!=security['exchange'] or rule.get('settlement')!='T_PLUS_1':raise ValueError('DATED_RULE_SECURITY_MISMATCH')
                    tape['rules'][session][iid]=inclusive(rule)
                    limit=resolve('ORDER_LIMIT',deadline(session),iid=iid,session=session)
                    if limit and limit.get('rule_id')==rule.get('rule_id') and limit.get('limit_up') is not None:
                        tape['orders'][session][iid]=dict(limit,limit_price=limit['limit_up'],order_type='PREDECLARED_SIMULATION_ORDER')
                if bar:
                    parsed=DailyBar.model_validate(bar)
                    action_complete=bool(action and action.get('from_session','9999')<=sessions[0] and action.get('through_session','0000')>=session and action.get('complete') is True and isinstance(action.get('events'),list))
                    no_action=(action.get('events')==[]) if action_complete else None
                    tape['bars'][session][iid]=dict(bar,basis='RAW_CNY',suspended=security.get('suspended') if security else None,
                        corporate_actions_complete=action_complete,no_corporate_action_since_signal=no_action)
                    if not no_action:diagnostics.append(dict(kind='ACTION_COVERAGE',instrument_id=iid,session=session,reasons=['UNSUPPORTED_CORPORATE_ACTION' if action_complete else 'COMPLETE_NO_ACTION_EVIDENCE_MISSING']))
                    if parsed.limit_verified and (parsed.limit_applicable is not True or not rule or parsed.rule_id!=rule.get('rule_id')):tape['bars'][session][iid]['limit_verified']=False
                if index:
                    previous=sessions[index-1];mark=resolve('BAR',deadline(session),iid=iid,session=previous)
                    if mark and mark.get('close') is not None:
                        prior_action=resolve('ACTION_COVERAGE',deadline(session),iid=iid,session=previous)
                        prior_complete=bool(prior_action and prior_action.get('complete') is True and isinstance(prior_action.get('events'),list) and prior_action.get('from_session','9999')<=sessions[0] and prior_action.get('through_session','0000')>=previous)
                        safe=(prior_action.get('events')==[]) if prior_complete else None
                        tape['valuations'][session][iid]=dict(mark,price=mark['close'],basis='RAW_CNY',no_corporate_action_since_signal=safe)
        tape['adapter_identity']=dict(contract=spec['contract'],archive_hash=digest(spec),source_hashes=[r.content_hash for r in receipts])
        tape.seal()
        return tape,dict(status='ADAPTED',coverage_type=coverage,record_counts={k:len(v) for k,v in records.items()},bytes_verified=total,gaps=diagnostics,
            real_data_validation='REAL_DATA_NOT_VALIDATED' if coverage=='TEST_FIXTURE' else 'ARCHIVED_EVIDENCE_RESOLVED',authenticity='LOCAL_SOURCE_RECEIPT_AND_MEMBERSHIP_VERIFIED_NOT_PROVIDER_SIGNATURE')
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return None,dict(status='UNAVAILABLE',reason=str(exc),gaps=diagnostics,bytes_read=total)
