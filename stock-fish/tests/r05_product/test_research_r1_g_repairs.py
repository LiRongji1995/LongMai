"""G01–G05 isolated TEST_FIXTURE regressions; no production I/O."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import builtins
import pytest
from stockfish_core.yaogu import execution_research_r1 as e, research_r1 as r
from stockfish_core.yaogu.research_r1_adapter import adapt_execution
from stockfish_core.yaogu.research_r1_statistics import assemble, preregistered_family
from stockfish_core.yaogu.research_r1_contracts import effective_spec, evaluability
from stockfish_core.yaogu.research_r1_io import paired_block_comparisons
from tests.r05_product.test_research_r1_repairs import archive_fixture
from tests.support.research_r1_fixture import fixture_export, clocks


def long_tape(n=80):
    p=fixture_export(); old=p['execution_tape']; first=date.fromisoformat(old['sessions'][0])
    days=[(first+timedelta(days=i)).isoformat() for i in range(n)]
    tape=deepcopy(old);tape.update(sessions=days,as_of=days[-1]+'T16:00:00+08:00')
    tape['cost_scenario'].update(effective_to=days[-1])
    for field in ('bars','rules','orders','valuations'):tape[field]={}
    for i,day in enumerate(days):
        tape['rules'][day]={iid:dict(rule,effective_to=days[-1]) for iid,rule in old['rules'][old['sessions'][0]].items()}
        tape['orders'][day]={iid:dict(order,**clocks(day)) for iid,order in old['orders'][old['sessions'][0]].items()}
        tape['bars'][day]={iid:dict(bar,quality='PARTIAL',instrument_id=iid,session=day,**clocks(day,'15:00:00')) for iid,bar in old['bars'][old['sessions'][0]].items()}
        tape['valuations'][day]={iid:dict(price='10.05',quality='PARTIAL',basis='TEST_FIXTURE_NO_ACTION',session=days[i-1],instrument_id=iid,**clocks(days[i-1],'15:00:00')) for iid in tape['bars'][day]} if i else {}
    return tape


def signal(tape,index=0,iid='TEST000'):
    day=tape['sessions'][index]
    return dict(instrument_id=iid,signal_session=day,themes=['A'],published_at=day+'T20:30:00+08:00',evidence_available_at=day+'T20:00:00+08:00')


def statistics(tape, signals, count=40, end_index=45):
    spec=effective_spec(r.config()); days=tape['sessions']; group=lambda:dict(selected_ids=[],coverage=dict(support={}))
    results=[dict(observation_date=d,groups={'R1_FULL':group(),'V2_CONTROL':group()}) for d in days[:count]]
    runs={g:{'10':dict(e.simulate(s,tape),strategy_evaluation_status='EVALUABLE')} for g,s in [('R1_FULL',signals),('V2_CONTROL',[])]}
    manifest=dict(effective_spec=spec,comparison_family=dict(pairs={'R1_FULL-V2_CONTROL':['R1_FULL','V2_CONTROL']},estimands=['DECLARED_UNIVERSE_ACCOUNT']),signal_window=[days[0],days[count-1]],account_evaluation_window=[days[0],days[end_index]],planned_completion_end=days[count+5],evidence_as_of=tape['as_of'])
    return assemble(results,runs,{},manifest,tape,paired_block_comparisons),runs


@pytest.mark.parametrize('bad',[{'quality':'INVALID'},{'open':'10','high':'9','low':'9.9','close':'10.05'}])
def test_g01_archived_invalid_not_closed(tmp_path,bad):
    spec,p=archive_fixture(tmp_path);entry=spec['sessions'][1]
    for a in spec['artifacts']:
        path=tmp_path/a['path'];body=json.loads(path.read_text());row=body['records'][0]
        if row['kind']=='BAR' and row['value']['session']==entry:
            row['value'].update(bad);raw=json.dumps(body).encode();path.write_bytes(raw);a['receipt']['content_hash']=hashlib.sha256(raw).hexdigest()
    tape,diagnostic=adapt_execution(spec,base=tmp_path)
    assert tape is not None,diagnostic
    run=e.simulate([signal(tape)],tape);rec=run['records'][0]
    assert rec['status']=='ENTRY_UNCERTAIN'
    assert Decimal(rec['uncertain_capital_reservation_cny'])>0
    assert run['account_return'] is None


def test_g02_tail_account():
    tape=long_tape();stats,runs=statistics(tape,[signal(tape,39)])
    desc=stats['descriptions']['R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT']
    assert len(desc['date_contributions'])==46
    assert any(x['difference'] for x in desc['date_contributions'][40:])


def test_g03_real_25_session_delay():
    tape=long_tape()
    for day in tape['sessions'][6:31]:tape['bars'][day]['TEST000']['open']='9'
    stats,runs=statistics(tape,[signal(tape)])
    assert runs['R1_FULL']['10']['records'][0]['exit_delay_sessions']==25
    assert stats['effects']['R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT']['status']=='UNAVAILABLE'


def test_g04_resource_import_unavailable(tmp_path,monkeypatch):
    from stockfish_core.storage import SQLiteStore
    from stockfish_core.yaogu.research_r1_storage import BudgetHistory
    store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
    HistoryArtifactStore(store)
    with store.transaction() as con:
        con.execute('CREATE TABLE fixture(payload TEXT)')
        con.executemany('INSERT INTO fixture VALUES(?)',[('{}',)]*2048)
    original=builtins.__import__
    def no_resource(name,*args,**kwargs):
        if name=='resource':raise ModuleNotFoundError('TEST_FIXTURE resource unavailable')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',no_resource)
    owner=BudgetHistory(store)
    try:
        with owner.store.transaction(write=False) as con:assert len(list(con.execute('SELECT payload FROM fixture')))==2048
        assert owner.metrics['memory_measurement']['status']=='MEMORY_MEASUREMENT_UNAVAILABLE'
    finally:owner.close()


def test_g05_empty_reasons_are_not_gaps():
    row=dict(conditions={k:dict(status='PASS',reason_codes=[]) for k in ('sector_context','capital_linkage')})
    assert evaluability(row)['background_missing']==[]


@pytest.mark.parametrize('delay',[19,20,25])
def test_g03_delay_boundaries(delay):
    tape=long_tape()
    for day in tape['sessions'][6:6+delay]:tape['bars'][day]['TEST000']['open']='9'
    stats,runs=statistics(tape,[signal(tape)])
    assert runs['R1_FULL']['10']['records'][0]['exit_delay_sessions']==delay
    key='R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT'
    assert stats['effects'][key]['status']==('ESTIMATED' if delay<20 else 'UNAVAILABLE')
    if delay>=20:assert stats['descriptions'][key]['window']['groups']['R1_FULL']['eligibility']['issues'][0]['reason']=='EXIT_DELAY_REACHES_BLOCK_LENGTH'


@pytest.mark.parametrize('stage',['entry','exit','budget','close'])
@pytest.mark.parametrize('bad',[{'quality':'INVALID'},{'high':'9','low':'9.9'},{'open':'NaN'},{'close':'Infinity'},{'basis':'ADJUSTED'},{'open':True}])
def test_g01_consumer_stages(stage,bad):
    tape=long_tape();days=tape['sessions'];signals=[signal(tape)]
    if stage=='budget':
        tape['valuations'][days[3]]['TEST000'].update(bad);signals.append(signal(tape,2,'TEST001'))
    else:tape['bars'][days[{'entry':1,'exit':6,'close':3}[stage]]]['TEST000'].update(bad)
    run=e.simulate(signals,tape,evaluation_window=[days[0],days[6]])
    if stage=='entry':assert run['records'][0]['status']=='ENTRY_UNCERTAIN'
    elif stage=='exit':assert run['records'][0]['status']=='EXIT_PENDING' and run['open_positions']==1
    elif stage=='budget':assert run['records'][1]['reason']=='PRETRADE_VALUATION_OR_BUDGET_UNAVAILABLE'
    else:assert run['account_curve'][3]['nav'] is None
    assert run['account_return'] is None


def test_g01_partial_field_contract():
    from stockfish_core.yaogu.research_r1_contracts import validated_quote
    opening=dict(open='10',limit_up='11',limit_down='9',quality='PARTIAL')
    assert validated_quote(opening,'OPEN')['field_evidence_level']=='VALIDATED_OPEN_ONLY'
    with pytest.raises(ValueError):validated_quote(opening,'CLOSE')
    with pytest.raises(ValueError):validated_quote(opening,'DAILY')
    assert validated_quote(dict(close='10'),'CLOSE')['quality_declaration']=='UNDECLARED'


@pytest.mark.parametrize('change',['pending','missing_delay'])
def test_g03_pending_unknown(change):
    tape=long_tape();days=tape['sessions']
    if change=='pending':
        for day in days[6:]:tape['bars'][day]['TEST000']['open']='9'
        stats,runs=statistics(tape,[signal(tape)])
        assert stats['effects']['R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT']['status']=='UNAVAILABLE'
    else:
        from stockfish_core.yaogu.research_r1_statistics import delay_eligibility
        rec=e.simulate([signal(tape)],tape)['records'][0];del rec['exit_delay_sessions']
        assert not delay_eligibility([rec],days,days[45],20)['eligible']


@pytest.mark.parametrize('case',['unknown','not_obtained','empty_complete','empty_unproven','not_applicable','parent_source','missing_source','no_counterevidence'])
def test_g05_explicit_fields(case):
    value=dict(status='PASS',reason_codes=[])
    if case=='unknown':value['status']='UNKNOWN'
    if case=='not_obtained':value={}
    if case.startswith('empty_'):value.update(records=[],query_complete=case=='empty_complete',source_refs=['TEST_FIXTURE'])
    if case=='not_applicable':value.update(applicable=False,status='UNKNOWN')
    if case=='parent_source':value.update(source_refs=['TEST_FIXTURE'],child=dict(source_required=True,status='PASS'))
    if case=='missing_source':value.update(source_required=True)
    if case=='no_counterevidence':value.update(counterevidence=[],warnings=[])
    row=dict(conditions=dict(sector_context=value,capital_linkage=dict(status='PASS',reason_codes=[])))
    assert bool(evaluability(row)['background_missing'])==(case in ('unknown','not_obtained','empty_unproven','missing_source'))


@pytest.mark.parametrize('budget',['time','bytes','memory'])
def test_g04_remaining_budgets(tmp_path,monkeypatch,budget):
    from stockfish_core.storage import SQLiteStore
    from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
    from stockfish_core.yaogu import research_r1_storage as storage
    store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3');HistoryArtifactStore(store)
    with store.transaction() as con:
        con.execute('CREATE TABLE fixture(payload TEXT)');con.executemany('INSERT INTO fixture VALUES(?)',[('{}',)]*2048)
    monkeypatch.setattr(storage,'sample_memory',lambda:dict(status='AVAILABLE',method='TEST_FIXTURE_INJECTED_PEAK',unit='bytes',scope='PROCESS',current=None,peak=2*1024**3))
    owner=storage.BudgetHistory(store,seconds=300,byte_budget=1 if budget=='bytes' else 2*1024**3)
    if budget=='time':owner.seconds=-1
    try:
        with pytest.raises(ValueError,match={'time':'TIME_BUDGET','bytes':'BYTE_BUDGET','memory':'RSS_BUDGET'}[budget]):
            with owner.store.transaction(write=False) as con:list(con.execute('SELECT payload FROM fixture'))
    finally:owner.close()
    assert owner._closed
    with store.transaction() as con:con.execute('INSERT INTO fixture VALUES(?)',('{}',))


def test_nonzero_archived_multiday_cli(tmp_path):
    import subprocess,sys
    from pathlib import Path
    from tests.support.research_r1_multiday import build
    paths,_=build(tmp_path/'TEST_FIXTURE')
    cli=Path(__file__).parents[2]/'scripts/codex_research.py';output=tmp_path/'out'
    cmd=[sys.executable,str(cli),'research-r1','--group','R1_FULL','--group','V2_CONTROL','--output',str(output)]
    for path in paths:cmd+=['--input',str(path)]
    run=subprocess.run(cmd,capture_output=True,text=True)
    (tmp_path/'command.json').write_text(json.dumps(dict(command=cmd,exit_code=run.returncode,stdout=run.stdout,stderr=run.stderr)))
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads((output/'result.json').read_text());manifest=json.loads((output/'manifest.json').read_text())
    account=report['execution']['R1_FULL']['10'];records=account['records'];curve=account['account_curve']
    assert len(records)==5 and all(x['status']=='CLOSED' for x in records)
    assert max(x['open_positions'] for x in curve)==2
    assert any(x['net_return']>0 for x in records) and any(x['net_return']<0 for x in records)
    key='R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT';stats=report['statistics']
    assert stats['effects'][key]['status']=='ESTIMATED',stats['descriptions'][key]
    assert stats['descriptions'][key]['window']['tail_session_count']==6
    pairs=stats['descriptions'][key]['date_contributions'];product=1
    for point in pairs:product*=1+point['difference'] # V2 is independently confirmed all cash
    assert not report['execution']['V2_CONTROL']['10']['records']
    assert product-1==pytest.approx(float(curve[-1]['nav'])/1000000-1,abs=1e-12)
    # Independent ledger: whole lots at predeclared 11 CNY, double fees + sell tax.
    cash=Decimal(1000000);ledger=[]
    for rec in records:
        assert rec['quantity']==int(Decimal(rec['budget']['after'])/Decimal('1100'))*100
        qty=Decimal(rec['quantity']);buy=Decimal(rec['entry_price'])*qty;sell=Decimal(rec['exit_price'])*qty
        buy_fee=max(Decimal(5),buy*Decimal('.0003'))+buy*Decimal('.00001')
        sell_fee=max(Decimal(5),sell*Decimal('.0003'))+sell*Decimal('.00051')
        assert Decimal(rec['buy_fees'])==buy_fee and Decimal(rec['sell_fees'])==sell_fee
        cash+=sell-sell_fee-buy-buy_fee
        ledger.append(dict(trade_id=rec['trade_id'],quantity=int(qty),buy=str(buy),sell=str(sell),buy_fee=str(buy_fee),sell_fee=str(sell_fee)))
    assert cash==Decimal(curve[-1]['nav'])
    (tmp_path/'ledger.json').write_text(json.dumps(dict(ledger=ledger,ending_cash=str(cash),compound_return=product-1,manifest_hash=report['manifest_hash'],source_hash=manifest['source_hash'],input_hashes=[x['hash'] for x in manifest['inputs']]),indent=2))
    cmd=[sys.executable,str(cli),'research-r1','--mode','summarize','--saved-result',str(output),'--output',str(tmp_path/'summary')]
    saved=subprocess.run(cmd,capture_output=True,text=True);assert saved.returncode==0,saved.stdout+saved.stderr
    assert json.loads((tmp_path/'summary/summary.json').read_text())['statistics']==stats


@pytest.mark.parametrize('case',['negative_tail','missing_price','missing_signal','not_matured','long_curve','cash_tail'])
def test_g02_window_edge_cases(case):
    tape=long_tape();days=tape['sessions'];signals=[] if case=='cash_tail' else [signal(tape,39)]
    if case=='negative_tail':tape['bars'][days[45]]['TEST000']['open']='9.5'
    if case=='missing_price':del tape['bars'][days[42]]['TEST000']
    if case=='not_matured':tape['as_of']=days[39]+'T16:00:00+08:00'
    stats,runs=statistics(tape,signals)
    key='R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT';desc=stats['descriptions'][key]
    if case in ('missing_price','not_matured'):assert stats['effects'][key]['status']=='UNAVAILABLE'
    elif case=='missing_signal':
        from stockfish_core.yaogu.research_r1_statistics import account_pairs
        manifest=dict(effective_spec=effective_spec(r.config()),signal_window=[days[0],days[39]],account_evaluation_window=[days[0],days[45]])
        pairs,diag=account_pairs([dict(observation_date=d) for d in days[:39]],runs,manifest,tape,'R1_FULL','V2_CONTROL')
        assert diag['missing_signal_sessions']==[days[39]] and not pairs[0]['inference_eligible']
    else:
        assert len(desc['date_contributions'])==46
        total=1
        for p in desc['date_contributions']:total*=1+p['difference']
        assert total-1==pytest.approx(float(runs['R1_FULL']['10']['account_curve'][45]['nav'])/1000000-1)
        if case=='negative_tail':assert total<1
        if case=='cash_tail':assert total==1
        if case=='long_curve':assert len(runs['R1_FULL']['10']['account_curve'])==80


def test_archived_pending_and_invalid_reports(tmp_path):
    from tests.support.research_r1_multiday import build
    from stockfish_core.yaogu.research_r1_io import run_local
    paths,evidence=build(tmp_path/'TEST_FIXTURE',count=1,extra=45,selected=(0,))
    root=paths[0].parent;days=evidence['sessions']
    for artifact in evidence['artifacts']:
        path=root/artifact['path'];body=json.loads(path.read_text());row=body['records'][0]
        if row['kind']=='BAR' and row['value']['instrument_id']=='TEST000' and row['value']['session'] in days[6:31]:
            row['value'].update(open='9',low='8.9');raw=json.dumps(body).encode();path.write_bytes(raw);artifact['receipt']['content_hash']=hashlib.sha256(raw).hexdigest()
    payload=json.loads(paths[0].read_text());payload['execution_evidence']=evidence;paths[0].write_text(json.dumps(payload))
    override=tmp_path/'spec.json';override.write_text(json.dumps(dict(account_evaluation_end=days[-1])))
    run_local(paths,tmp_path/'delayed',groups=['R1_FULL','V2_CONTROL'],execution_spec=override)
    report=json.loads((tmp_path/'delayed/result.json').read_text())
    assert report['execution']['R1_FULL']['10']['records'][0]['exit_delay_sessions']==25
    key='R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT'
    assert report['statistics']['effects'][key]['status']=='UNAVAILABLE'
    assert report['statistics']['descriptions'][key]['window']['groups']['R1_FULL']['eligibility']['issues'][0]['delay_sessions']==25
    # New archived revision, new output. Old archive and report remain intact.
    import shutil
    revised=tmp_path/'TEST_FIXTURE_REVISED';shutil.copytree(root,revised)
    root=revised;paths=[root/paths[0].name]
    for artifact in evidence['artifacts']:
        path=root/artifact['path'];body=json.loads(path.read_text());row=body['records'][0]
        if row['kind']=='BAR' and row['value']['instrument_id']=='TEST000' and row['value']['session']==days[1]:
            row['value']['quality']='INVALID';raw=json.dumps(body).encode();path.write_bytes(raw);artifact['receipt']['content_hash']=hashlib.sha256(raw).hexdigest()
    payload['execution_evidence']=evidence;paths[0].write_text(json.dumps(payload))
    run_local(paths,tmp_path/'invalid',groups=['R1_FULL','V2_CONTROL'],execution_spec=override)
    invalid=json.loads((tmp_path/'invalid/result.json').read_text())
    assert invalid['execution']['R1_FULL']['10']['records'][0]['status']=='ENTRY_UNCERTAIN'
    assert invalid['manifest_hash']!=report['manifest_hash']
    assert invalid['results'][0]['groups']['R1_FULL']['selected_ids']==report['results'][0]['groups']['R1_FULL']['selected_ids']


@pytest.mark.parametrize('platform,multiplier',[('darwin',1),('linux',1024)])
def test_resource_unix_units(monkeypatch,platform,multiplier):
    import sys,types
    from stockfish_core.yaogu.research_r1_storage import sample_memory
    monkeypatch.setattr(sys,'platform',platform)
    monkeypatch.setitem(sys.modules,'resource',types.SimpleNamespace(RUSAGE_SELF=0,getrusage=lambda _:types.SimpleNamespace(ru_maxrss=123)))
    sample=sample_memory()
    assert sample['peak']==123*multiplier and sample['current'] is None and sample['unit']=='bytes'


def test_resource_windows_api_simulated(monkeypatch):
    import sys,ctypes,types
    from stockfish_core.yaogu.research_r1_storage import sample_memory
    class Call:
        def __init__(self,fn):self.fn=fn
        def __call__(self,*args):return self.fn(*args)
    def read(handle,pointer,size):
        pointer._obj.WorkingSetSize=123;pointer._obj.PeakWorkingSetSize=456
        return 1
    monkeypatch.setattr(sys,'platform','win32')
    monkeypatch.setattr(ctypes,'WinDLL',lambda name,**kwargs:types.SimpleNamespace(GetCurrentProcess=Call(lambda:1),GetProcessMemoryInfo=Call(read)),raising=False)
    sample=sample_memory()
    assert sample['method']=='WINDOWS_GET_PROCESS_MEMORY_INFO' and sample['current']==123 and sample['peak']==456
