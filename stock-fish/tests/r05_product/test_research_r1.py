from copy import deepcopy
from decimal import Decimal
from fractions import Fraction as F
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import research_r1 as r
from stockfish_core.yaogu import execution_research_r1 as e
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.research_r1_io import run_local, persist_isolated
from stockfish_core.storage import SQLiteStore
from tests.support.research_r1_fixture import fixture_export, clocks


@pytest.fixture(scope='module')
def payload():
    return fixture_export()


@pytest.fixture(scope='module')
def computed(payload):
    return r.run_research(MarketInput.model_validate(payload['input']), cutoff=payload['cutoff'])


def test_v2_golden_and_identity(computed):
    golden = json.loads((Path(__file__).resolve().parents[1] / 'support/r1_v2_golden.json').read_text())
    assert digest(computed['v2_control']) == golden['v2_complete_result_digest']
    from stockfish_core.yaogu.validation_identity import current_validation_identity
    assert current_validation_identity() == golden['validation_identity']


@pytest.mark.parametrize('value,status', [(499999999,'FAIL'),(500000000,'PASS'),(500000001,'PASS'),(None,'UNKNOWN')])
def test_amount_boundary(value,status):
    assert r.compare(value,500000000,'amount')['status'] == status


@pytest.mark.parametrize('close,current,previous,status', [(21,20,20,'PASS'),(20,20,20,'FAIL'),(21,20,21,'FAIL'),(19,20,None,'FAIL'),(21,20,None,'UNKNOWN')])
def test_ma20_boundaries(close,current,previous,status):
    assert r.ma20_trend(close,current,previous)['status'] == status


def accel(**changes):
    values = dict(returns={'1':F('.03'),'3':F('.08'),'5':F('.15'),'10':F('.25')},rs={'1':F('.01'),'3':F('.04'),'5':F('.1')},momentum='RISING',high_turnover_days=3)
    values.update(changes)
    return r.acceleration(**values)


@pytest.mark.parametrize('r5,r10,expected', [('.15',None,'PASS'),(None,'.25','PASS'),('.149999','.249999','FAIL'),(None,'.24','UNKNOWN')])
def test_accel_or(r5,r10,expected):
    returns={'1':F('.01'),'3':F('.02'),'5':F(r5) if r5 else None,'10':F(r10) if r10 else None}
    assert accel(returns=returns)['status'] == expected


@pytest.mark.parametrize('change', [dict(momentum='FALLING'),dict(momentum='STABLE'),dict(high_turnover_days=2),dict(rs={'1':0,'3':1,'5':1}),dict(rs={'1':1,'3':0,'5':1}),dict(rs={'1':1,'3':1,'5':0})])
def test_accel_required_conditions(change):
    assert accel(**change)['status'] == 'FAIL'


def test_accel_known_fail_beats_missing():
    assert accel(momentum='UNKNOWN',high_turnover_days=2)['status']=='FAIL'
    assert accel(momentum='UNKNOWN')['status']=='UNKNOWN'
    assert accel(high_turnover_days=None)['status']=='UNKNOWN'
    assert accel(returns={'1':1,'3':1,'5':F('.01'),'10':F('.02')})['status']=='FAIL'


def isolated_row(computed):
    row=deepcopy(computed['v2_control']['items'][0])
    row['conditions']['liquidity_bottom']=r.condition('FAIL','TURNOVER',amount_cny=500000000)
    row['trend_strength']=r.condition('UNKNOWN','MA250_MISSING',trend_state='UNKNOWN')
    row['supply_pressure']=r.condition('UNKNOWN','SAM_REPEATED_UNRECLAIMED_UPPER_SHADOW_SUPPLY_WARNING')
    row['market_status']={'ecosystem_state':'RETREAT'}
    row['sector_candidates']=[]
    row['funds_candidates']=[]
    row['conditions']['setup_identification']['stage_candidates']={s:r.condition('UNKNOWN','MISSING') for s in r.v2.STAGE_IDS}
    row['conditions']['setup_identification']['stage_candidates']['START']=r.condition('PASS','START')
    facts=dict(path={'close_limit_down':r.v2.FALSE},amount=r.condition('PASS','AMOUNT'),ma20=r.condition('PASS','MA20'),funds=[],acceleration=r.condition('UNKNOWN','ACC'))
    return row,facts


def test_independent_setup_background_turnover_and_order(computed):
    row,facts=isolated_row(computed)
    a=r.adapt_row(row,facts,'R1_FULL')
    b=r.adapt_row(row,facts,'R1_FULL',display_order=list(reversed(r.v2.STAGE_IDS)))
    assert a['candidate_status']==b['candidate_status']=='CORE_PASS'
    assert a['matched_setups']==['START']
    assert a['setups']['TREND_PULLBACK']['status']=='UNKNOWN'
    assert a['background']['supply_pressure']['status']=='UNKNOWN'
    assert r.adapt_row(row,facts,'SEMANTIC_BASE')['candidate_status']=='CORE_FAIL'
    row['conditions']['setup_identification']['stage_candidates']['START']=r.condition('FAIL','INTERNAL_TURNOVER')
    assert r.adapt_row(row,facts,'R1_FULL')['candidate_status']=='CORE_UNKNOWN'


@pytest.mark.parametrize('flag,expected',[(r.v2.TRUE,'CORE_FAIL'),(r.v2.UNKNOWN,'CORE_UNKNOWN')])
def test_close_limit_safety(computed,flag,expected):
    row,facts=isolated_row(computed)
    facts['path']['close_limit_down']=flag
    result=r.adapt_row(row,facts,'R1_FULL')
    assert result['candidate_status']==expected
    if flag==r.v2.TRUE:
        assert all(x['status']=='NOT_EVALUATED' for x in result['setups'].values())


def test_funds_only_not_sector_strength(computed):
    row,facts=isolated_row(computed)
    funds=[dict(theme_id='A',funds_condition=r.condition('PASS','FUNDS'))]
    assert r.theme_gate('START',row,funds,theme_hard=False,funds_hard=True)['status']=='PASS'
    assert r.theme_gate('START',row,funds,theme_hard=True,funds_hard=True)['status']!='PASS'


def test_manifest_single_changes():
    spec=r.config()
    assert len(spec['groups'])==12
    for name, definition in spec['groups'].items():
        parent=definition['parent']
        if parent:
            a,b=r.group_options(parent),r.group_options(name)
            assert {k:b[k] for k in b if a[k]!=b[k]}==definition['changes']


def signal(payload,iid='TEST000',offset=0):
    tape=payload['execution_tape']
    day=tape['sessions'][offset]
    return dict(instrument_id=iid,signal_session=day,published_at=day+'T20:30:00+08:00',evidence_available_at=day+'T20:00:00+08:00',themes=['A'])


def test_full_execution_deadline_cost_and_intervals(payload):
    tape=deepcopy(payload['execution_tape'])
    result=e.simulate([signal(payload)],tape)
    rec=result['records'][0]
    assert rec['status']=='CLOSED'
    assert rec['entry_session']==tape['sessions'][1]
    assert rec['actual_exit_session']==tape['sessions'][6]
    assert rec['net_return']<0
    assert Decimal(rec['buy_fees'])>=5 and Decimal(rec['sell_fees'])>=5
    assert rec['quantity']%100==0
    assert e.simulate([signal(payload)],tape,slippage_bps=0)['records'][0]['net_return']>rec['net_return']
    assert e.simulate([signal(payload)],tape,slippage_bps=30)['records'][0]['net_return']<rec['net_return']


@pytest.mark.parametrize('published', ['2026-09-14T09:15:00','invalid'])
def test_timezone_or_publication_missing(payload,published):
    s=signal(payload);s['published_at']=published
    assert e.simulate([s],payload['execution_tape'])['records'][0]['status']=='UNAVAILABLE'


def test_late_signal_and_future_order(payload):
    tape=deepcopy(payload['execution_tape']);s=signal(payload)
    s['published_at']=tape['sessions'][1]+'T09:15:00+08:00'
    assert e.simulate([s],tape)['records'][0]['status']=='LATE_SIGNAL'
    s=signal(payload)
    tape['orders'][tape['sessions'][1]]['TEST000'].update(clocks(tape['sessions'][1],'09:30:00'))
    assert e.simulate([s],tape)['records'][0]['reason']=='ORDER_RULE_OR_COST_NOT_AVAILABLE_AT_CUTOFF'


@pytest.mark.parametrize('patch,status', [({'open':'11'},'ENTRY_NOT_FILLED'),({'suspended':True},'ENTRY_NOT_FILLED'),({'suspended':None},'ENTRY_UNCERTAIN'),({'open':None},'ENTRY_UNCERTAIN'),({'limit_verified':False},'ENTRY_UNCERTAIN'),({'corporate_actions_complete':False},'ENTRY_UNCERTAIN')])
def test_entry_evidence_and_no_retry(payload,patch,status):
    tape=deepcopy(payload['execution_tape']);tape['bars'][tape['sessions'][1]]['TEST000'].update(patch)
    result=e.simulate([signal(payload)],tape)
    assert result['records'][0]['status']==status
    assert result['account_curve'][-1]['cash']=='1000000'
    assert result['mean_net_return'] is None


def test_exit_delay_censor_and_capital(payload):
    tape=deepcopy(payload['execution_tape'])
    tape['bars'][tape['sessions'][6]]['TEST000']['open']='9'
    result=e.simulate([signal(payload)],tape)
    assert result['records'][0]['exit_delay_sessions']==1
    for session in tape['sessions'][6:]:
        tape['bars'][session]['TEST000']['open']='9'
    result=e.simulate([signal(payload)],tape)
    rec=result['records'][0]
    assert rec['status']=='EXIT_PENDING' and rec['censored']
    assert result['open_positions']==1 and result['mean_net_return'] is None


def test_basis_missing_does_not_make_return(payload):
    tape=deepcopy(payload['execution_tape'])
    for session in tape['sessions'][6:]:
        tape['bars'][session]['TEST000']['basis']='OTHER'
    assert e.simulate([signal(payload)],tape)['records'][0]['status']=='EXIT_PENDING'


def test_quantities_do_not_use_future_open(payload):
    a=deepcopy(payload['execution_tape']);b=deepcopy(a)
    b['bars'][b['sessions'][1]]['TEST000']['open']='10.80'
    x=e.simulate([signal(payload)],a)['records'][0]
    y=e.simulate([signal(payload)],b)['records'][0]
    assert x['quantity']==y['quantity']


def test_allocation_multi_theme_and_order():
    candidates=[dict(instrument_id=str(i),themes=['A','B'] if i<3 else ['B']) for i in range(5)]
    a,_=e.allocate(candidates,Decimal(1000000),Decimal(1000000),{})
    b,_=e.allocate(candidates[::-1],Decimal(1000000),Decimal(1000000),{})
    assert a==b
    assert sum(x['after'] for x in a.values())<=400000
    assert all(x['after']<=200000 for x in a.values())
    _,coverage=e.allocate([dict(instrument_id='X',themes=None)],Decimal(1000),Decimal(1000),{})
    assert coverage=='THEME_COVERAGE_INCOMPLETE'
    limited,_=e.allocate(candidates,Decimal(1000000),Decimal(500000),{'held':dict(value=Decimal(450000),themes=['B'])})
    assert all(x['after']==0 for x in limited.values())


def test_repeated_signal_no_extension(payload):
    result=e.simulate([signal(payload),signal(payload,offset=1)],payload['execution_tape'])
    assert result['records'][1]['status']=='ALREADY_HELD_OR_DUPLICATE_NO_EXTENSION'
    assert result['records'][0]['actual_exit_session']==payload['execution_tape']['sessions'][6]


def test_store_isolated_and_idempotent(computed,tmp_path):
    store=SQLiteStore(tmp_path/'test.sqlite3')
    a=persist_isolated(store,computed)
    assert a==persist_isolated(store,computed)
    assert store.get_document('research_r1_isolated',a)==computed
    assert not store.list_documents('daily_research')
    with pytest.raises(ValueError):
        persist_isolated(store,dict(computed,coverage_type='PROSPECTIVE_CAPTURED'))


def test_cli_flow_no_overwrite_input_mutation(payload,tmp_path):
    path=tmp_path/'input.json';path.write_text(json.dumps(payload));before=path.read_bytes()
    output=tmp_path/'result'
    result=run_local([path],output,groups=['V2_CONTROL','R1_FULL'])
    assert result['status']=='SUCCEEDED' and before==path.read_bytes()
    report=json.loads((output/'result.json').read_text())
    assert report['results'][0]['coverage_type']=='TEST_FIXTURE'
    assert '10' in report['execution']['R1_FULL']
    assert report['execution']['V2_CONTROL']['10']['account_return'] is None
    assert report['results'][0]['groups']['R1_FULL']['selected_ids']
    with pytest.raises(FileExistsError):
        run_local([path],output)
    with pytest.raises(ValueError):
        run_local([path],tmp_path/'other',groups=['V2_CONTROL'],frozen_manifest=output/'manifest.json')


def test_paired_statistics_missing_blocks_and_holm():
    from stockfish_core.yaogu.research_r1_io import paired_block_comparisons
    pairs=[dict(session_index=i,difference=.01+(i%3)*.001) for i in range(60)]
    result=paired_block_comparisons({'A':pairs,'B':pairs[:20]},repetitions=100)
    assert result['B']['status']=='UNAVAILABLE'
    assert result['A']['effect']>0
    assert result['A']['holm_adjusted_p']>=result['A']['p_value']
    missing=deepcopy(pairs);missing[10]['difference']=None
    assert paired_block_comparisons({'A':missing})['A']['status']=='UNAVAILABLE'
    assert paired_block_comparisons({'A':pairs[:30]+pairs[31:]})['A']['status']=='UNAVAILABLE'


def test_unknown_pool_is_not_legal_empty(payload):
    p=deepcopy(payload['input'])
    for row in p['universe']['securities']:
        row['st']=None
    result=r.run_research(MarketInput.model_validate(p),cutoff=payload['cutoff'],groups=['R1_FULL'])
    assert result['groups']['R1_FULL']['status']=='INSUFFICIENT_EVIDENCE'
    assert result['qualification']['eligibility_unknown']>0


def test_real_execution_is_fail_closed(payload):
    tape=dict(payload['execution_tape'],coverage_type='RECONSTRUCTED_HISTORICAL')
    result=e.simulate([signal(payload)],tape)
    assert result['status']=='UNAVAILABLE'
    assert 'net_return' not in result['records'][0]


def test_short_history_does_not_globally_block_ma20(payload):
    p=deepcopy(payload['input'])
    first=p['sessions'][-100]
    p['bars']=[b for b in p['bars'] if b['session']>=first]
    result=r.run_research(MarketInput.model_validate(p),cutoff=payload['cutoff'],groups=['R1_FULL'])
    target=next(x for x in result['groups']['R1_FULL']['items'] if x['instrument_id']=='TEST000')
    for setup in ('START','ACCELERATION','HIGH_LEVEL_REPAIR'):
        assert target['setups'][setup]['conditions']['trend']['status']=='PASS'
    assert target['setups']['TREND_PULLBACK']['conditions']['trend']['status']=='UNKNOWN'


def test_multiple_setup_matches_preserved_once(computed):
    row,facts=isolated_row(computed)
    facts['acceleration']=r.condition('PASS','ACC')
    result=r.adapt_row(row,facts,'R1_FULL')
    assert result['matched_setups']==['ACCELERATION','START']
    other=r.adapt_row(row,facts,'R1_FULL',display_order=list(reversed(r.v2.STAGE_IDS)))
    assert other['matched_setups']==result['matched_setups']
    assert other['primary_setup']!=result['primary_setup']


def test_no_same_open_sale_proceeds_reuse(payload):
    tape=deepcopy(payload['execution_tape'])
    # Make calendar long enough for day-5 new signals and keep flat valuations.
    from datetime import date,timedelta
    last=date.fromisoformat(tape['sessions'][-1])
    for n in range(1,7):
        session=(last+timedelta(days=n)).isoformat();prev=tape['sessions'][-1]
        tape['sessions'].append(session)
        tape['bars'][session]=deepcopy(tape['bars'][prev])
        tape['rules'][session]=deepcopy(tape['rules'][prev])
        tape['orders'][session]=deepcopy(tape['orders'][prev])
        tape['valuations'][session]={iid:dict(price='10.05',session=prev,basis='TEST_FIXTURE_NO_ACTION',**clocks(prev,'15:00:00')) for iid in tape['bars'][session]}
    tape['as_of']=tape['sessions'][-1]+'T16:00:00+08:00'
    tape['cost_scenario']['effective_to']=tape['sessions'][-1]
    for rules in tape['rules'].values():
        for rule in rules.values():rule['effective_to']=tape['sessions'][-1]
    signals=[]
    for i in range(5):
        s=signal(payload,f'TEST{i:03d}');s['themes']=[f'T{i}'];signals.append(s)
    s=signal(payload,'TEST005',offset=5);s['themes']=['NEW'];signals.append(s)
    result=e.simulate(signals,tape)
    rec=result['records'][-1]
    # Five holdings consume ~91% of cash (sized at preknown limit 11 vs fill 10.01).
    assert Decimal(rec['budget']['after']) < 100000


def test_source_and_input_identity_binding(payload,computed):
    assert computed['input_hash']==digest(payload['input'])
    for name in ('research_r1.py','execution_research_r1.py','research_r1_io.py'):
        assert any(path.endswith(name) for path in computed['source_files'])
    assert r.identity()['config_hash']==digest(r.config())


def test_read_only_export_history_binding(tmp_path,payload):
    from stockfish_core.yaogu.history_artifacts import compact_input
    from stockfish_core.yaogu.research_r1_io import load_input
    store=SQLiteStore(tmp_path/'isolated.sqlite3')
    bundle=compact_input(MarketInput.model_validate(payload['input']),store)
    p=tmp_path/'export.json'
    p.write_text(json.dumps(dict(payload,input=bundle.model_dump(mode='json'))))
    before=store.path.read_bytes()
    loaded,bound=load_input(p,history_db=store.path)
    assert bound._history_store.store.read_only
    assert bound.history_artifact_hash==bundle.history_artifact_hash
    assert before==store.path.read_bytes()
    with pytest.raises(ValueError,match='READ_ONLY_HISTORY'):
        load_input(p)


def test_atomic_output_no_overwrite(tmp_path):
    from stockfish_core.yaogu.research_r1_io import write_new
    path=tmp_path/'r.json';write_new(path,{'a':1})
    with pytest.raises(FileExistsError):write_new(path,{'a':2})
    assert json.loads(path.read_text())=={'a':1}
    assert not list(tmp_path.glob('*.tmp'))


def test_source_change_during_run_refuses_result(tmp_path,payload,monkeypatch):
    path=tmp_path/'input.json';path.write_text(json.dumps(payload))
    original=r.run_research
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        old=r.identity
        monkeypatch.setattr(r,'identity',lambda:dict(old(),source_hash='changed'))
        return result
    monkeypatch.setattr(r,'run_research',changed)
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        run_local([path],tmp_path/'out',groups=['R1_FULL'])
    assert (tmp_path/'out/manifest.json').exists()
    assert not (tmp_path/'out/result.json').exists()


def test_accel_ablation_explanations_match_new_decision(computed):
    for row in computed['groups']['V2_ACCEL_ONLY']['items']:
        assert row['status']==row['decision_status']==row['decision_layer']['decision_status']
        assert row['strategy_id']=='STAGE_AWARE_RESEARCH_R1'
        assert row['watch_reasons']==row['decision_layer'].get('watch_reasons',[])
        assert row['condition_statuses']=={k:v['status'] for k,v in row['conditions'].items()}
