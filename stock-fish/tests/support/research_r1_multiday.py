"""Synthetic TEST_FIXTURE only: archived multi-day, nonzero execution acceptance."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from tests.support.research_r1_fixture import fixture_export, clocks


def shift(value,n):
    if isinstance(value,list):return [shift(v,n) for v in value]
    if isinstance(value,dict):return {k:shift(v,n) for k,v in value.items()}
    if isinstance(value,str):
        try:
            if len(value)==10:return (date.fromisoformat(value)+timedelta(days=n)).isoformat()
            if len(value)>10 and value[10]=='T':return (datetime.fromisoformat(value)+timedelta(days=n)).isoformat()
        except ValueError:pass
    return value


def build(root, *, count=40, extra=6, selected=(0,1,10,11,39), outcome='mixed'):
    root.mkdir(parents=True,exist_ok=True)
    p=fixture_export();old=p.pop('execution_tape');first=date.fromisoformat(p['input']['observation_date'])
    days=[(first+timedelta(days=i)).isoformat() for i in range(count+extra)]
    end=(date.fromisoformat(days[-1])+timedelta(days=1)).isoformat();artifacts=[]
    def add(kind,value):
        ref='TEST_FIXTURE_'+str(len(artifacts));value=dict(value,source_refs=[ref]);value.setdefault('event_time',value['source_published_at'])
        raw=json.dumps({'records':[dict(kind=kind,value=value)]}).encode();name=ref+'.json';(root/name).write_bytes(raw)
        receipt=dict(receipt_id=ref,provider='TEST_FIXTURE',dataset='TEST_FIXTURE',content_hash=hashlib.sha256(raw).hexdigest(),field_version='TEST_FIXTURE_V1',row_count=1,pagination_complete=True,status='COMPLETE',**{k:value[k] for k in ('event_time','source_published_at','first_seen_at','ingested_at')})
        artifacts.append(dict(path=name,receipt=receipt))
    add('CALENDAR',dict(sessions=days,**clocks(days[0])))
    add('COST',dict(old['cost_scenario'],effective_to=end,commission_status='PREDECLARED_HYPOTHETICAL'))
    for index,day in enumerate(days):
        for iid in ('TEST000','TEST001'):
            add('SECURITY',dict(instrument_id=iid,exchange='SSE',board='MAIN',security_type='A_COMMON',listed_on='2020-01-01',effective_from=day,effective_to=(date.fromisoformat(day)+timedelta(days=1)).isoformat(),st=False,suspended=False,**clocks(day)))
            add('RULE',dict(old['rules'][old['sessions'][0]][iid],instrument_id=iid,session=day,exchange='SSE',rule_id='TEST_RULE',effective_to=end,**clocks(day)))
            add('ORDER_LIMIT',dict(instrument_id=iid,session=day,rule_id='TEST_RULE',limit_up='11',**clocks(day)))
            opened='10.5' if index in (6,16,45) else '9.5' if index in (7,17) else '10'
            if outcome=='negative' and index==45:opened='9.5'
            bar=dict(instrument_id=iid,session=day,quality='VALID',open=opened,high='10.8',low='9.2',close='10.05',limit_up='11',limit_down='9',limit_applicable=True,limit_verified=True,rule_id='TEST_RULE',**clocks(day,'15:00:00'))
            add('BAR',bar)
            add('ACTION_COVERAGE',dict(instrument_id=iid,session=day,from_session=days[0],through_session=day,complete=True,events=[],**clocks(day,'15:00:00')))
    evidence=dict(contract='R1_ARCHIVED_EXECUTION_EVIDENCE_V2',coverage_type='TEST_FIXTURE',sessions=days,report_as_of=days[-1]+'T16:00:00+08:00',artifacts=artifacts)
    paths=[]
    for index in range(count):
        payload=shift(p,index)
        # Independent input snapshots: rename target and peer to create overlap,
        # never inject final decisions, account curves or statistics.
        if index in (1,11):
            def swap(v):
                if isinstance(v,list):return [swap(x) for x in v]
                if isinstance(v,dict):return {k:swap(x) for k,x in v.items()}
                return {'TEST000':'TEST001','TEST001':'TEST000'}.get(v,v) if isinstance(v,str) else v
            payload=swap(payload)
        target='TEST001' if index in (1,11) else 'TEST000'
        for bar in payload['input']['bars']:
            if bar['session']==days[index]:
                if index not in selected or bar['instrument_id']!=target:bar['amount_cny']='1'
                else:bar['turnover_fraction']=.01;bar['volume_shares']='1000000'
        payload['execution_evidence']=evidence
        path=root/f'input_{index:02}.json';path.write_text(json.dumps(payload));paths.append(path)
    return paths,evidence
