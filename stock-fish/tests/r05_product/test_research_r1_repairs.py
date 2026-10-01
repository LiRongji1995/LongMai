"""R1 reporting regressions. All generated market evidence is TEST_FIXTURE."""
from copy import deepcopy
import json
from decimal import Decimal
import pytest
from stockfish_core.yaogu import research_r1 as r
from stockfish_core.yaogu import execution_research_r1 as e
from stockfish_core.yaogu.research_r1_io import selection_comparison, run_local
from stockfish_core.yaogu.contracts import MarketInput
from tests.support.research_r1_fixture import fixture_export


def test_f01_known_behavior_watch_is_common():
    # Decision-layer pure-function counterexample; real run/report separately below.
    parts = {k:r.condition('PASS', k) for k in ('liquidity_bottom','trend_state','setup_identification','behavioral_confirmation','market_compatibility','same_theme_context','critical_counterevidence')}
    parts['behavioral_confirmation'] = r.condition('FAIL','KNOWN_BEHAVIOR_FAILURE')
    old = dict(instrument_id='TEST',decision_status='WATCH',conditions=parts)
    new = dict(instrument_id='TEST',candidate_status='CORE_FAIL',common=r.condition('PASS','OK'),setups={'START':r.all_of({'behavior':parts['behavioral_confirmation']})})
    result=dict(v2_control=dict(qualified_ids=[],items=[old]),groups={'R1_FULL':dict(items=[new],selected_ids=[],counts={'CORE_FAIL':1})})
    comparison=selection_comparison(result)['R1_FULL']
    assert comparison['common_evaluable_ids']==['TEST']
    assert comparison['newly_evaluable_ids']==[]


def test_f02_common_failure_in_real_report(tmp_path):
    p=fixture_export()
    for bar in p['input']['bars']:
        if bar['session']==p['input']['observation_date']:bar['amount_cny']='1'
    path=tmp_path/'input.json';path.write_text(json.dumps(p))
    run_local([path],tmp_path/'out',groups=['R1_FULL'])
    report=json.loads((tmp_path/'out/result.json').read_text())
    counts=report['selection_comparisons'][0]['R1_FULL']['independent_condition_counts']
    assert counts.get('common:liquidity:FAIL')==8


def test_f03_candidates_do_not_override_limited_scope(tmp_path):
    p=fixture_export()
    # Keep the passing security; make another security's dated eligibility unknown.
    p['input']['universe']['securities'][1]['st']=None
    path=tmp_path/'input.json';path.write_text(json.dumps(p))
    run_local([path],tmp_path/'out',groups=['R1_FULL'])
    report=json.loads((tmp_path/'out/result.json').read_text())
    assert report['results'][0]['groups']['R1_FULL']['selected_ids']
    assert report['execution']['R1_FULL']['10']['account_return'] is None


def test_f04_effective_spec_drives_account(monkeypatch):
    p=fixture_export(); spec=r.config();spec.update(initial_nav_cny=2000000,single_name_cap=.05)
    monkeypatch.setattr(r,'config',lambda:deepcopy(spec))
    day=p['execution_tape']['sessions'][0]
    signal=dict(instrument_id='TEST000',signal_session=day,themes=['A'],**p['publication'])
    result=e.simulate([signal],p['execution_tape'])
    assert result['initial_nav_cny']==2000000
    assert Decimal(result['records'][0]['budget']['before'])==100000


def archive_fixture(tmp_path):
    """Actual archived JSON bytes and SourceReceipt, never a verified tape mock."""
    import hashlib
    from datetime import date,timedelta
    from tests.support.research_r1_fixture import clocks
    p=fixture_export();tape=p['execution_tape'];sessions=tape['sessions'];artifacts=[]
    def add(kind,value):
        ref='TEST_FIXTURE_'+str(len(artifacts));value=dict(value,source_refs=[ref]);value.setdefault('event_time',value['source_published_at'])
        raw=json.dumps({'records':[dict(kind=kind,value=value)]}).encode();name=ref+'.json';(tmp_path/name).write_bytes(raw)
        receipt=dict(receipt_id=ref,provider='TEST_FIXTURE',dataset='TEST_FIXTURE',content_hash=hashlib.sha256(raw).hexdigest(),field_version='TEST_FIXTURE_V1',row_count=1,pagination_complete=True,status='COMPLETE',
            **{k:value[k] for k in ('event_time','source_published_at','first_seen_at','ingested_at')})
        artifacts.append(dict(path=name,receipt=receipt))
    first=sessions[0];end=(date.fromisoformat(sessions[-1])+timedelta(days=1)).isoformat()
    add('CALENDAR',dict(sessions=sessions,**clocks(first)))
    cost=dict(tape['cost_scenario'],effective_to=end,commission_status='PREDECLARED_HYPOTHETICAL');add('COST',cost)
    for index,day in enumerate(sessions):
        add('SECURITY',dict(instrument_id='TEST000',exchange='SSE',board='MAIN',security_type='A_COMMON',listed_on='2020-01-01',effective_from=day,effective_to=(date.fromisoformat(day)+timedelta(days=1)).isoformat(),st=False,suspended=False,**clocks(day)))
        add('RULE',dict(tape['rules'][day]['TEST000'],instrument_id='TEST000',session=day,exchange='SSE',rule_id='TEST_RULE',effective_to=end,**clocks(day)))
        add('ORDER_LIMIT',dict(instrument_id='TEST000',session=day,rule_id='TEST_RULE',limit_up='11',**clocks(day)))
        add('BAR',dict(instrument_id='TEST000',session=day,open='10',high='10.1',low='9.9',close='10.05',limit_up='11',limit_down='9',limit_applicable=True,limit_verified=True,rule_id='TEST_RULE',**clocks(day,'15:00:00')))
        add('ACTION_COVERAGE',dict(instrument_id='TEST000',session=day,from_session=first,through_session=day,complete=True,events=[],**clocks(day,'15:00:00')))
    return dict(contract='R1_ARCHIVED_EXECUTION_EVIDENCE_V2',coverage_type='TEST_FIXTURE',sessions=sessions,report_as_of=tape['as_of'],artifacts=artifacts),p


def test_archive_to_order_fill_exit_and_hash_rejection(tmp_path):
    from stockfish_core.yaogu.research_r1_adapter import adapt_execution,ResolvedTape
    spec,p=archive_fixture(tmp_path)
    tape,report=adapt_execution(spec,base=tmp_path)
    assert report['status']=='ADAPTED',report
    assert isinstance(tape,ResolvedTape)
    signal=dict(instrument_id='TEST000',signal_session=tape['sessions'][0],themes=['A'],**p['publication'])
    result=e.simulate([signal],tape)
    assert result['records'][0]['status']=='CLOSED',result['records']
    assert tape['orders'][tape['sessions'][1]]['TEST000']['order_type']=='PREDECLARED_SIMULATION_ORDER'
    path=tmp_path/spec['artifacts'][0]['path'];path.write_text('{}')
    rejected,report=adapt_execution(spec,base=tmp_path)
    assert rejected is None and report['reason']=='EXECUTION_SOURCE_HASH_MISMATCH'


@pytest.mark.parametrize('key,value', [('single_name_cap',float('nan')),('theme_cap',1.1),('entry_offset',7),('order_cutoff','09:15:00 UTC'),('bootstrap_repetitions',0),('slippage_bps_per_side',[10,0])])
def test_invalid_effective_spec_rejected(key,value):
    from stockfish_core.yaogu.research_r1_contracts import effective_spec
    spec=r.config();spec[key]=value
    with pytest.raises(ValueError):effective_spec(spec)


def test_effective_offsets_slips_theme_and_cost():
    from stockfish_core.yaogu.research_r1_contracts import effective_spec
    p=fixture_export();spec=r.config();spec.update(entry_offset=2,exit_offset=7,slippage_bps_per_side=[0,10,20])
    s=dict(instrument_id='TEST000',signal_session=p['execution_tape']['sessions'][0],themes=['A'],**p['publication'])
    result=e.simulate([s],p['execution_tape'],spec=spec,slippage_bps=20)
    assert result['records'][0]['entry_session']==p['execution_tape']['sessions'][2]
    assert result['records'][0]['planned_exit_session']==p['execution_tape']['sessions'][7]
    spec['single_name_cap']=.1;spec['theme_cap']=.1
    budgets,_=e.allocate([dict(instrument_id=str(i),themes=['A']) for i in range(2)],Decimal(1000000),Decimal(1000000),{},spec=effective_spec(spec))
    assert sum(x['after'] for x in budgets.values())==100000
    p['execution_tape']['cost_scenario']['minimum_commission']='1000'
    result=e.simulate([s],p['execution_tape'],spec=spec)
    rec=result['records'][0]
    assert Decimal(rec['buy_fees'])==1000+Decimal(rec['entry_price'])*rec['quantity']*Decimal('.00001')
    assert effective_spec(json.loads(json.dumps(spec)))==effective_spec(spec)


def test_shared_detail_reconstruction(tmp_path):
    from stockfish_core.yaogu.research_r1_storage import expand
    p=fixture_export();path=tmp_path/'input.json';path.write_text(json.dumps(p))
    run_local([path],tmp_path/'out',groups=['R1_FULL'])
    report=json.loads((tmp_path/'out/result.json').read_text())
    restored=expand(tmp_path/'out',report['results'][0]['detail'])
    before=r.run_research(MarketInput.model_validate(p['input']),cutoff=p['cutoff'],groups=['R1_FULL'])
    assert {k:v for k,v in restored['groups']['R1_FULL'].items() if k not in ('setup_selected_ids','reference_coverage')}==before['groups']['R1_FULL']
    assert restored['v2_control']==before['v2_control']


@pytest.mark.parametrize('failure,unknown,core,policy', [('behavioral_confirmation',None,True,True),(None,'same_theme_context',True,False),(None,'trend_state',False,False),('liquidity_bottom','trend_state',True,True)])
def test_f01_dependency_short_circuit(failure,unknown,core,policy):
    from stockfish_core.yaogu.research_r1_contracts import evaluability
    keys=('liquidity_bottom','trend_state','setup_identification','behavioral_confirmation','market_compatibility','same_theme_context','critical_counterevidence')
    conditions={k:r.condition('PASS',k) for k in keys}
    if failure:conditions[failure]=r.condition('FAIL','KNOWN_FAIL')
    if unknown:conditions[unknown]=r.condition('UNKNOWN','NECESSARY_UNKNOWN')
    row=dict(decision_status='WATCH',conditions=conditions)
    result=evaluability(row)
    assert result['core_facts_evaluable']==core
    assert result['selection_decidable']==policy


def test_f03_real_pass_and_unknown_subset(tmp_path):
    p=fixture_export();day=p['input']['observation_date']
    replacement=[dict(bar,instrument_id='TEST001') for bar in p['input']['bars'] if bar['instrument_id']=='TEST000' and bar['session']==day]
    p['input']['bars']=[bar for bar in p['input']['bars'] if bar['instrument_id']!='TEST001']+replacement
    path=tmp_path/'input.json';path.write_text(json.dumps(p))
    run_local([path],tmp_path/'out',groups=['R1_FULL'])
    report=json.loads((tmp_path/'out/result.json').read_text());group=report['results'][0]['groups']['R1_FULL']
    assert 'TEST000' in group['selected_ids']
    assert group['coverage']['support']['TEST001']['selection_decidable'] is False
    assert report['execution']['R1_FULL']['10']['account_return'] is None
    assert report['execution']['R1_FULL']['10']['observed_subset']['account_return'] is not None


def passing_dependencies():
    p=fixture_export();result=r.run_research(MarketInput.model_validate(p['input']),cutoff=p['cutoff'],groups=['R1_FULL'])
    row=deepcopy(result['v2_control']['items'][0])
    row['conditions']['liquidity_bottom']=r.condition('PASS','OK')
    row['trend_strength']=r.condition('PASS','OK',trend_state='STRONG_UPTREND')
    row['supply_pressure']=r.condition('PASS','OK')
    row['market_status']=dict(ecosystem_state='EXPANSION')
    row['sector_candidates']=[dict(theme_id='A',setup_compatibility={s:dict(context_status='SUPPORTIVE') for s in r.v2.STAGE_IDS})]
    row['funds_candidates']=[dict(theme_id='A',funds_condition=r.condition('PASS','OK'))]
    candidates={s:r.condition('FAIL','KNOWN_OTHER_SETUP_FAIL') for s in r.v2.STAGE_IDS};candidates['START']=r.condition('PASS','OK')
    row['conditions']['setup_identification']['stage_candidates']=candidates
    facts=dict(path={'close_limit_down':r.v2.FALSE},amount=r.condition('PASS','OK'),ma20=r.condition('PASS','OK'),funds=row['funds_candidates'],acceleration=candidates['ACCELERATION'])
    return row,facts


@pytest.mark.parametrize('group,blocking', [('SEM_NO_GLOBAL_TURNOVER','turnover'),('SEM_SETUP_TREND','trend'),('SEM_MARKET_TAG','market'),('SEM_SUPPLY_TAG','supply'),('SEM_THEME_HARD_FUNDS_TAG','funds'),('SEM_THEME_TAG_FUNDS_HARD','theme'),('SEM_THEME_TAG_FUNDS_TAG','both')])
def test_ablation_at_exact_dependency(group,blocking):
    row,facts=passing_dependencies()
    assert r.adapt_row(row,facts,'SEMANTIC_BASE')['candidate_status']=='CORE_PASS'
    if blocking=='turnover':row['conditions']['liquidity_bottom']=r.condition('FAIL','TURNOVER_FAIL')
    if blocking=='trend':row['trend_strength']=r.condition('UNKNOWN','MA250_MISSING',trend_state='UNKNOWN')
    if blocking=='market':row['market_status']['ecosystem_state']='RETREAT'
    if blocking=='supply':row['supply_pressure']=r.condition('UNKNOWN','SAM_REPEATED_UNRECLAIMED_UPPER_SHADOW_SUPPLY_WARNING')
    if blocking in ('funds','both'):row['funds_candidates'][0]['funds_condition']=r.condition('FAIL','FUNDS_FAIL')
    if blocking in ('theme','both'):row['sector_candidates'][0]['setup_compatibility']['START']['context_status']='NEGATIVE'
    assert r.adapt_row(row,facts,'SEMANTIC_BASE')['candidate_status']!='CORE_PASS'
    assert r.adapt_row(row,facts,group)['candidate_status']=='CORE_PASS'


def test_unknown_limit_guard_is_full_only():
    row,facts=passing_dependencies();facts['path']['close_limit_down']=r.v2.UNKNOWN
    assert r.adapt_row(row,facts,'SEMANTIC_BASE')['candidate_status']=='CORE_PASS'
    assert r.adapt_row(row,facts,'R1_FULL')['candidate_status']=='CORE_UNKNOWN'


def test_acceleration_and_independent_switch_discriminants():
    row,facts=passing_dependencies()
    stages=row['conditions']['setup_identification']
    stages['stage_candidates']['START']=r.condition('FAIL','START_FAIL')
    facts['acceleration']=r.condition('PASS','ACC_PASS')
    assert r.adapt_row(row,facts,'V2_SETUP_ONLY')['candidate_status']=='CORE_FAIL'
    assert r.adapt_row(row,facts,'SEMANTIC_BASE')['candidate_status']=='CORE_PASS'
    assert r.adapt_row(row,facts,'V2_ACCEL_ONLY')['decision_status']=='FOCUS'
    stages['stage_candidates']['HIGH_LEVEL_REPAIR']=r.condition('PASS','REPAIR_PASS')
    stages['stage_candidates']['TREND_PULLBACK']=r.condition('PASS','PULLBACK_PASS')
    facts['acceleration']=r.condition('FAIL','ACC_FAIL')
    row['market_status']['ecosystem_state']='REPAIR'
    facts['path'].update(daily_path_status='AVAILABLE',_low_ret=1,_close_ret=1,_recovery=0)
    assert r.adapt_row(row,facts,'V2_ACCEL_ONLY')['decision_status']=='WATCH'
    assert r.adapt_row(row,facts,'V2_SETUP_ONLY')['candidate_status']=='CORE_PASS'


def test_frozen_r1_all_decisions_and_reasons_unchanged():
    from pathlib import Path
    from stockfish_core.yaogu.contracts import digest
    frozen=json.loads((Path(__file__).parents[1]/'support/r1_frozen_decisions_8990e5a.json').read_text())
    p=fixture_export();result=r.run_research(MarketInput.model_validate(p['input']),cutoff=p['cutoff'])
    assert result['input_hash']==frozen['input_hash']
    for group,expected in frozen['groups'].items():
        assert digest({k:result['groups'][group][k] for k in frozen['keys']})==expected


def test_cli_statistics_and_saved_summary(tmp_path):
    import subprocess,sys
    from pathlib import Path
    from datetime import date,datetime,timedelta
    p=fixture_export();first=date.fromisoformat(p['input']['observation_date']);tape=p.pop('execution_tape')
    tape['sessions']=[(first+timedelta(days=i)).isoformat() for i in range(11)]
    tape['as_of']=tape['sessions'][-1]+'T16:00:00+08:00'
    def shift(v,n):
        if isinstance(v,list):return [shift(x,n) for x in v]
        if isinstance(v,dict):return {k:shift(x,n) for k,x in v.items()}
        if isinstance(v,str):
            try:
                if len(v)==10:return (date.fromisoformat(v)+timedelta(days=n)).isoformat()
                if len(v)>10 and v[10]=='T':return (datetime.fromisoformat(v)+timedelta(days=n)).isoformat()
            except ValueError:pass
        return v
    cli=str(Path(__file__).parents[2]/'scripts/codex_research.py')
    override=tmp_path/'execution.json';override.write_text(json.dumps(dict(block_length_sessions=2,bootstrap_repetitions=39,seed=123)))
    args=[sys.executable,cli,'research-r1','--execution-spec',str(override),'--output',str(tmp_path/'out')]
    for index in range(4):
        payload=shift(p,index);payload['execution_tape']=tape
        for bar in payload['input']['bars']:
            if bar['session']==payload['input']['observation_date']:bar['amount_cny']='1'
        path=tmp_path/f'TEST_FIXTURE_{index}.json';path.write_text(json.dumps(payload));args+=['--input',str(path)]
    run=subprocess.run(args,capture_output=True,text=True)
    assert run.returncode==0,run.stdout+run.stderr
    report=json.loads((tmp_path/'out/result.json').read_text());stats=report['statistics']
    assert stats['effects']['R1_FULL-V2_CONTROL:DECLARED_UNIVERSE_ACCOUNT']['status']=='ESTIMATED'
    assert stats['effects']['R1_FULL-V2_CONTROL:CONDITIONAL_SIGNAL']['status']=='UNAVAILABLE'
    assert stats['family_size']==96
    saved=subprocess.run([sys.executable,cli,'research-r1','--mode','summarize','--saved-result',str(tmp_path/'out'),'--output',str(tmp_path/'summary')],capture_output=True,text=True)
    assert saved.returncode==0,saved.stdout+saved.stderr
    assert json.loads((tmp_path/'summary/summary.json').read_text())['statistics']==stats
    # Tampered completion must be rejected rather than quietly recomputed.
    (tmp_path/'out/completion.json').write_text('{}')
    saved=subprocess.run([sys.executable,cli,'research-r1','--mode','summarize','--saved-result',str(tmp_path/'out'),'--output',str(tmp_path/'bad')],capture_output=True,text=True)
    assert saved.returncode==2


def test_statistics_constant_effect_exact_boundary():
    from stockfish_core.yaogu.research_r1_io import paired_block_comparisons
    positive=[dict(session_index=i,difference=.01) for i in range(40)]
    negative=[dict(session_index=i,difference=-.01) for i in range(40)]
    result=paired_block_comparisons({'positive':positive,'negative':negative,'missing':[]},repetitions=99)
    assert result['positive']['effect']==pytest.approx(.01)
    assert result['negative']['effect']==pytest.approx(-.01)
    assert result['positive']['p_value']==.01
    assert result['positive']['holm_adjusted_p']==.03
    assert result['negative']['holm_adjusted_p']==.03


def test_f01_behavior_counterexample_through_report(tmp_path,monkeypatch):
    """Explicit upstream daily-path counterexample; no final decisions mocked."""
    from fractions import Fraction
    original=r.v2._behavioral_confirmation
    def contradictory_path(setup,path,stages):
        return original(setup,dict(path,daily_path_status='AVAILABLE',_low_ret=Fraction(1),_close_ret=Fraction(1),_recovery=Fraction(0)),stages)
    monkeypatch.setattr(r.v2,'_behavioral_confirmation',contradictory_path)
    p=fixture_export();path=tmp_path/'input.json';path.write_text(json.dumps(p))
    run_local([path],tmp_path/'out',groups=['R1_FULL','V2_CONTROL'])
    report=json.loads((tmp_path/'out/result.json').read_text())
    comparison=report['selection_comparisons'][0]['R1_FULL']
    assert 'TEST000' in comparison['common_selection_decidable_ids']
    assert 'TEST000' not in comparison['newly_evaluable_ids']
    assert report['results'][0]['groups']['V2_CONTROL']['coverage']['support']['TEST000']['strategy_label']=='WATCH'


def test_adapter_missing_action_reserves_uncertain_capital(tmp_path):
    from stockfish_core.yaogu.research_r1_adapter import adapt_execution
    spec,p=archive_fixture(tmp_path)
    spec['artifacts']=[a for a in spec['artifacts'] if json.loads((tmp_path/a['path']).read_text())['records'][0]['kind']!='ACTION_COVERAGE']
    tape,diagnostic=adapt_execution(spec,base=tmp_path)
    assert tape is not None
    assert tape['bars'][tape['sessions'][1]]['TEST000']['no_corporate_action_since_signal'] is None
    signal=dict(instrument_id='TEST000',signal_session=tape['sessions'][0],themes=['A'],**p['publication'])
    result=e.simulate([signal],tape)
    assert result['records'][0]['status']=='ENTRY_UNCERTAIN'
    assert Decimal(result['records'][0]['uncertain_capital_reservation_cny'])>0
    assert result['account_return'] is None
    assert result['account_curve'][-1]['nav'] is None


def test_archive_fake_reference_and_date_rejected(tmp_path):
    import hashlib
    from stockfish_core.yaogu.research_r1_adapter import adapt_execution
    spec,_=archive_fixture(tmp_path)
    artifact=spec['artifacts'][0];path=tmp_path/artifact['path'];document=json.loads(path.read_text())
    document['records'][0]['value']['source_refs']=['FORGED_REFERENCE']
    raw=json.dumps(document).encode();path.write_bytes(raw);artifact['receipt']['content_hash']=hashlib.sha256(raw).hexdigest()
    tape,diagnostic=adapt_execution(spec,base=tmp_path)
    assert tape is None and diagnostic['reason']=='EXECUTION_SOURCE_MEMBERSHIP_MISMATCH'


def test_nonfixture_publication_requires_explicit_assumption():
    from stockfish_core.yaogu.research_r1_io import signals_for
    result=dict(v2_control={'items':[]},observation_date='2026-09-11',decision_cutoff='2026-09-11T20:00:00+08:00',signal_evidence_type='RECONSTRUCTED_HISTORICAL')
    publication=dict(published_at='2026-09-11T20:30:00+08:00',evidence_available_at='2026-09-11T20:00:00+08:00')
    assert signals_for(result,['TEST'],publication)[0]['published_at'] is None
    publication.update(mode='HISTORICAL_ASSUMPTION',assumed_published_at=publication['published_at'])
    assert signals_for(result,['TEST'],publication)[0]['published_at'] is not None
    publication['assumed_published_at']='2026-09-11T19:00:00+08:00'
    assert signals_for(result,['TEST'],publication)[0]['published_at'] is None


def test_bounded_context_equals_original_v2_history(tmp_path):
    from stockfish_core.storage import SQLiteStore
    from stockfish_core.yaogu.history_artifacts import compact_input
    from stockfish_core.yaogu.research_r1_io import load_input
    p=fixture_export();bundle=compact_input(MarketInput.model_validate(p['input']),SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    path=tmp_path/'input.json';path.write_text(json.dumps(dict(p,input=bundle.model_dump(mode='json'))))
    _,original=load_input(path,history_db=tmp_path/'TEST_FIXTURE.sqlite3')
    _,bounded=load_input(path,history_db=tmp_path/'TEST_FIXTURE.sqlite3')
    expected=r.v2._public(r.v2._evaluate_rows(original,cutoff=p['cutoff'])[1])
    actual=r.run_research(bounded,cutoff=p['cutoff'],groups=['R1_FULL'])['v2_control']
    assert actual==expected


def test_mixed_fixture_tape_rejected(tmp_path):
    p=fixture_export();p['execution_tape']['coverage_type']='RECONSTRUCTED_HISTORICAL'
    path=tmp_path/'input.json';path.write_text(json.dumps(p))
    with pytest.raises(ValueError,match='MIXED_EXECUTION'):
        run_local([path],tmp_path/'out',groups=['R1_FULL'])


@pytest.mark.parametrize('field,value',[('leverage',True),('same_open_sale_reuse',True),('order_construction','FUTURE_OPEN_SIZE')])
def test_fixed_policy_cannot_be_silently_overridden(field,value):
    from stockfish_core.yaogu.research_r1_contracts import effective_spec
    spec=effective_spec(r.config());spec[field]=value
    with pytest.raises(ValueError,match='UNSUPPORTED_FIXED'):effective_spec(spec)


def test_read_snapshot_cannot_mix_concurrent_partition_mutation(tmp_path):
    import hashlib
    from stockfish_core.storage import SQLiteStore,IntegrityError
    from stockfish_core.yaogu.history_artifacts import compact_input,_json
    from stockfish_core.yaogu.research_r1_io import load_input
    p=fixture_export();store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3');original=MarketInput.model_validate(p['input']);bundle=compact_input(original,store)
    path=tmp_path/'input.json';path.write_text(json.dumps(dict(p,input=bundle.model_dump(mode='json'))))
    _,bound=load_input(path,history_db=store.path);owner=bound._history_store
    manifest=owner.manifest(bundle.history_artifact_ref,bundle.history_artifact_hash)
    extra=original.bars[0].model_copy(update={'instrument_id':'TEST_CONCURRENT'});session=str(extra.session)
    part=next(x for x in manifest['partitions'] if x['session']==session)
    raw=_json(extra);key=hashlib.sha256(raw.encode()).hexdigest()
    with store.transaction() as con:
        con.execute('INSERT INTO history_evidence_rows VALUES(?,?,?,?,?)',(key,'bar',extra.instrument_id,session,raw))
        con.execute('INSERT INTO history_partition_members VALUES(?,?,?,?,?)',(part['partition_hash'],key,'bar',extra.instrument_id,session))
    assert not list(owner.iter_rows(bundle.history_artifact_ref,'bar',instrument_id=extra.instrument_id))
    owner.close()
    with pytest.raises(IntegrityError,match='CORRUPT_HISTORY_PARTITION'):load_input(path,history_db=store.path)


def test_f04_run_local_manifest_and_budget_match(tmp_path):
    p=fixture_export();path=tmp_path/'input.json';path.write_text(json.dumps(p))
    spec=tmp_path/'execution.json';spec.write_text(json.dumps(dict(initial_nav_cny=2000000,single_name_cap=.05)))
    run_local([path],tmp_path/'out',groups=['R1_FULL'],execution_spec=spec)
    report=json.loads((tmp_path/'out/result.json').read_text());manifest=json.loads((tmp_path/'out/manifest.json').read_text())
    account=report['execution']['R1_FULL']['10']
    assert account['initial_nav_cny']==2000000
    assert Decimal(account['records'][0]['budget']['before'])==100000
    assert account['spec_hash'] in manifest['effective_execution_specs']
    assert manifest['effective_spec']['single_name_cap']==.05


def test_archive_evidence_through_research_report(tmp_path):
    spec,p=archive_fixture(tmp_path);p.pop('execution_tape');p['execution_evidence']=spec
    path=tmp_path/'input.json';path.write_text(json.dumps(p))
    run_local([path],tmp_path/'out',groups=['R1_FULL'])
    report=json.loads((tmp_path/'out/result.json').read_text())
    assert report['execution_adapter'][0]['status']=='ADAPTED'
    assert report['execution']['R1_FULL']['10']['records'][0]['status']=='CLOSED'
    assert report['execution']['R1_FULL']['10']['records'][0]['evidence_level']=='ASSUMED_OPEN_FILL'


def test_shared_details_reject_manifest_identity_conflict(tmp_path):
    from stockfish_core.yaogu.research_r1_storage import verify_shared_report
    p=fixture_export();path=tmp_path/'input.json';path.write_text(json.dumps(p))
    run_local([path],tmp_path/'out',groups=['R1_FULL'])
    report=json.loads((tmp_path/'out/result.json').read_text())
    forged=dict(report,manifest_hash='0'*64)
    with pytest.raises(ValueError,match='RESULT_DETAIL_IDENTITY_CONFLICT'):
        verify_shared_report(tmp_path/'out',forged)


@pytest.mark.parametrize('case',['wrong_security','late_order','conflicting_rule','unknown_limit_applicability'])
def test_adapter_scope_time_and_conflict(case,tmp_path):
    import hashlib
    from stockfish_core.yaogu.research_r1_adapter import adapt_execution
    spec,p=archive_fixture(tmp_path);entry=spec['sessions'][1]
    target=None
    for artifact in spec['artifacts']:
        body=json.loads((tmp_path/artifact['path']).read_text());row=body['records'][0]
        kind='ORDER_LIMIT' if case=='late_order' else 'BAR' if case=='unknown_limit_applicability' else 'RULE'
        if row['kind']==kind and row['value'].get('session')==entry:target=artifact;document=body;break
    value=document['records'][0]['value']
    if case=='wrong_security':value['instrument_id']='TEST_OTHER'
    elif case=='unknown_limit_applicability':value['limit_applicable']=None
    elif case=='late_order':
        for key in ('event_time','source_published_at','first_seen_at','ingested_at'):value[key]=entry+'T09:16:00+08:00'
    else:
        target=deepcopy(target);target['path']='TEST_FIXTURE_conflict.json';target['receipt']['receipt_id']='TEST_FIXTURE_conflict'
        value['source_refs']=['TEST_FIXTURE_conflict'];value['lot_size']=200;spec['artifacts'].append(target)
    raw=json.dumps(document).encode();(tmp_path/target['path']).write_bytes(raw);target['receipt']['content_hash']=hashlib.sha256(raw).hexdigest()
    tape,diagnostic=adapt_execution(spec,base=tmp_path)
    if case=='conflicting_rule':
        assert tape is None and diagnostic['reason']=='CONFLICTING_EXECUTION_EVIDENCE:RULE'
    else:
        signal=dict(instrument_id='TEST000',signal_session=spec['sessions'][0],themes=['A'],**p['publication'])
        result=e.simulate([signal],tape)
        assert result['records'][0]['status']==('ENTRY_UNCERTAIN' if case=='unknown_limit_applicability' else 'UNAVAILABLE')
        assert result['records'][0]['instrument_id']=='TEST000'
        assert result['account_return'] is None
