"""Independent TEST_FIXTURE physical originals; no observed data or live access."""
from copy import copy, deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import builtins
import json
from pathlib import Path
import socket
import sqlite3

import pytest

from stockfish_core.yaogu import physical_historical_pair_v1 as subject
from stockfish_core.yaogu import short_horizon_strength as native_r1
from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader
from tests.yaogu import test_physical_historical_research as originals


@pytest.fixture(autouse=True)
def deny_live_access(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_DB_SDK_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(sqlite3, 'connect', denied)
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in ('baostock', 'akshare'):
            return denied()
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)


def fixture(tmp_path, *, participation='PASS', missing_market=False):
    def strong(rows, _master):
        rows[0].update(open='13', high='15.1', low='12.9', close='15', preclose='12.9',
                       amount='5000000000')
    data = originals.fixture(tmp_path, source_edit=strong)
    manifest, policy, sources, window = data
    changed = []
    for item in sources['raw_receipt_sources']:
        typed = item['typed_receipt']
        if typed['dataset'] != 'market_daily':
            changed.append(item)
            continue
        day = typed['parameters']['date']
        if missing_market and day == str(window[-3]):
            continue
        rows = json.loads(Path(item['raw']['path']).read_bytes())['rows']
        if day in {str(value) for value in window[-5:]}:
            rows[0]['turn'] = {'PASS':'5', 'FAIL':'4.9999', 'UNKNOWN':''}[participation]
        changed.append(originals.capture(tmp_path/'TEST_FIXTURE-pair-market', 'market_daily', rows,
            {'date':day}, datetime(2026,5,21,12,tzinfo=timezone.utc)))
    sources['raw_receipt_sources'] = changed
    originals.write(manifest, sources)
    return manifest, policy, sources, window


def run(data, *, deadline=None):
    return subject.run_physical_historical_pair(data[0], data[1], data[3][-1],
        deadline or datetime.now(timezone.utc)+timedelta(seconds=60))


def row(result, iid='000001.SZ'):
    return next(item for item in result['comparison']['rows'] if item['instrument_id'] == iid)


def test_real_issuer_same_input_cutoff_exact_native_pair(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    before = {str(path):path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    issuer, native = subject.issue_saved_r2_source_reader, subject.se.run_short_horizon_strategy
    issued, calls, inner = [], [], []
    collector = native_r1.run_short_horizon_strength
    def issued_once(**kwargs):
        value = issuer(**kwargs); issued.append(value); return value
    def counted(bundle, **kwargs):
        calls.append((id(bundle), kwargs.copy()))
        return native(bundle, **kwargs)
    def collected(*args, **kwargs):
        inner.append(1); return collector(*args, **kwargs)
    monkeypatch.setattr(subject, 'issue_saved_r2_source_reader', issued_once)
    monkeypatch.setattr(subject.se, 'run_short_horizon_strategy', counted)
    monkeypatch.setattr(native_r1, 'run_short_horizon_strength', collected)
    result = run(data)
    assert len(issued) == 1 and len(calls) == 2 and len(inner) == 2
    assert calls[0][0] == calls[1][0]
    assert [call[1]['strategy_id'] for call in calls] == [subject.R1, subject.R2]
    assert calls[0][1]['cutoff'] == calls[1][1]['cutoff'] == subject.stamp(result['decision_cutoff'])
    assert '_source_payload_reader' not in calls[0][1]
    assert type(calls[1][1]['_source_payload_reader']) is SourceArtifactReader
    assert calls[1][1]['_source_payload_reader'] is issued[0].reader
    assert calls[1][1]['_source_receipts'] is issued[0].receipts
    assert all('_frozen_base_run' not in call[1] for call in calls)
    left, right = (result['native_runs'][strategy] for strategy in (subject.R1,subject.R2))
    assert left['input_hash'] == right['input_hash'] == result['input_hash']
    assert left['qualification'] == right['qualification']
    assert result['native_attempts'] == {subject.R1:1, subject.R2:1}
    assert result['native_r1_evaluations_expected'] == 2
    assert result['comparison']['unchanged_base_evidence_verified'] is True
    assert result['historical_PIT_count'] == result['complete_independent_cycles'] == 0
    assert result['historical_theme_membership'] == 'UNKNOWN'
    assert result['default_service_connected'] is False
    assert result['input']['universe']['complete'] is False
    current = next(bar for bar in result['input']['bars'] if bar['instrument_id']=='000001.SZ'
                   and bar['session']=='2026-05-20')
    assert Decimal(current['comparable_close']) == Decimal(current['close'])*Decimal('1.2345')
    assert Fraction(str(current['turnover_fraction'])) == Fraction(1,20)
    assert current['amount_cny'] == '5000000000'
    facts = row(result)
    assert facts['amount_cny_exact'] == {'numerator':5000000000, 'denominator':1}
    assert facts['base_evidence_hash'] == facts['r2_base_evidence_hash']
    market = result['comparison']['market_turnover_context']
    assert market['status'] == 'AVAILABLE'
    assert market['median_amount_cny'] == {'numerator':500000000, 'denominator':1}
    assert facts['liquidity']['reference_cny'] == {'numerator':100100000, 'denominator':1}
    assert all(Path(path).read_bytes() == raw for path,raw in before.items())
    assert set(str(path) for path in tmp_path.rglob('*') if path.is_file()) == set(before)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('participation,decision,selected_state', [
    ('PASS','WATCH',True), ('FAIL','REJECTED',False), ('UNKNOWN','UNKNOWN',None),
])
def test_participation_same_native_r1_and_three_value_r2(tmp_path, participation, decision, selected_state):
    result = run(fixture(tmp_path, participation=participation))
    facts = row(result)
    assert facts['r1_decision_status'] == 'WATCH' and facts['r1_selected_state'] is True
    assert facts['participation_status'] == participation
    assert facts['r2_decision_status'] == decision and facts['r2_selected_state'] is selected_state
    assert facts['r2_research_selected'] is (participation == 'PASS')
    assert result['comparison']['participation_counts'][participation] >= 1


def test_missing_market_denominator_unknown_not_four_day_median(tmp_path):
    result = run(fixture(tmp_path, missing_market=True))
    market = result['comparison']['market_turnover_context']
    assert len(market['window_sessions']) == 5 and market['status'] == 'UNKNOWN'
    assert market['median_amount_cny'] is None
    assert sum(item['status'] == 'COMPLETE' for item in market['observations']) == 4
    assert all(item['liquidity']['reference_cny'] is None for item in result['comparison']['rows'])
    assert any(gap['field'] == 'DAILY_RAW_AND_MASTER' for gap in result['audit']['gaps'])


@pytest.mark.parametrize('bad_input', ['json', 'callback'])
def test_saved_json_and_arbitrary_callback_are_not_entry_sources(tmp_path, bad_input):
    data = fixture(tmp_path)
    source = data[2] if bad_input == 'json' else lambda _rid:b'{}'
    with pytest.raises(ValueError, match='REAL_SOURCE_AND_POLICY_PATHS_REQUIRED'):
        subject.run_physical_historical_pair(source, data[1], data[3][-1],
            datetime.now(timezone.utc)+timedelta(seconds=60))


def test_fake_issuer_rejected_before_build_or_arms(tmp_path, monkeypatch):
    data = fixture(tmp_path)
    monkeypatch.setattr(subject, 'issue_saved_r2_source_reader', lambda **kwargs:{'reader':lambda rid:b'{}'})
    monkeypatch.setattr(subject.physical, '_build', lambda *args:pytest.fail('BUILD_AFTER_FAKE_ISSUER'))
    with pytest.raises(ValueError, match='GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED'):run(data)


@pytest.mark.parametrize('attack', ['shallow','deep','constructor','forged','pid','cutoff','factory_repeat','deadline'])
def test_factory_object_pid_cutoff_and_deadline_are_sealed(tmp_path, monkeypatch, attack):
    data = fixture(tmp_path)
    factory = subject._new_pair_binding
    def checked(*args, **kwargs):
        owner = factory(*args, **kwargs)
        with pytest.raises(ValueError):
            if attack == 'shallow':copy(owner)
            elif attack == 'deep':deepcopy(owner)
            elif attack == 'constructor':subject._PairBinding()
            elif attack == 'forged':
                fake=object.__new__(subject._PairBinding); fake.__dict__.update(owner.__dict__); fake.validate()
            elif attack == 'pid':
                pid=subject.os.getpid()
                with monkeypatch.context() as changes:
                    changes.setattr(subject.os,'getpid',lambda:pid+1); owner.validate()
            elif attack == 'cutoff':owner.consume(owner.cutoff+timedelta(microseconds=1))
            elif attack == 'factory_repeat':factory(*args, **kwargs)
            elif attack == 'deadline':
                end=owner.deadline.end
                try:
                    owner.deadline.end=end+timedelta(seconds=1); owner.validate()
                finally:owner.deadline.end=end
        owner.validate()
        return owner
    monkeypatch.setattr(subject,'_new_pair_binding',checked)
    assert run(data)['native_attempts'] == {subject.R1:1, subject.R2:1}


def test_completed_binding_repeat_and_state_reset_rejected(tmp_path, monkeypatch):
    data=fixture(tmp_path); factory=subject._new_pair_binding; owners=[]
    def kept(*args, **kwargs):
        owner=factory(*args, **kwargs); owners.append(owner); return owner
    monkeypatch.setattr(subject,'_new_pair_binding',kept)
    run(data); owner=owners[0]
    with pytest.raises(ValueError,match='NATIVE_PAIR_ALREADY_CONSUMED'):owner.consume(owner.cutoff)
    with pytest.raises(ValueError,match='ILLEGAL_PAIR_STATE_TRANSITION'):owner._state('READY')
    owner.state='READY'
    with pytest.raises(ValueError,match='GENUINE_PAIR_FACTORY_BINDING_CHANGED'):owner.consume(owner.cutoff)


def test_failure_sealed_and_no_retry_of_either_arm(tmp_path, monkeypatch):
    data=fixture(tmp_path); factory=subject._new_pair_binding; native=subject.se.run_short_horizon_strategy
    owners=[]; calls=[]
    def kept(*args, **kwargs):
        owner=factory(*args, **kwargs); owners.append(owner); return owner
    def failed(*args, **kwargs):
        calls.append(kwargs['strategy_id'])
        if kwargs['strategy_id'] == subject.R2:raise RuntimeError('TEST_FIXTURE_R2_FAILURE')
        return native(*args, **kwargs)
    monkeypatch.setattr(subject,'_new_pair_binding',kept)
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',failed)
    deadline=datetime.now(timezone.utc)+timedelta(seconds=60)
    with pytest.raises(RuntimeError,match='TEST_FIXTURE_R2_FAILURE'):run(data,deadline=deadline)
    owner=owners[0]
    assert owner.state == 'FAILED' and calls == [subject.R1,subject.R2]
    with pytest.raises(ValueError,match='NATIVE_PAIR_ALREADY_CONSUMED'):owner.consume(owner.cutoff)
    with pytest.raises(ValueError,match='REGISTERED_JOB_ALREADY_ATTEMPTED'):run(data,deadline=deadline)
    assert calls == [subject.R1,subject.R2]


@pytest.mark.parametrize('target', ['input','raw','policy','issuer_registry','policy_registry','implementation'])
def test_drift_at_first_native_return_prevents_second_arm(tmp_path, monkeypatch, target):
    data=fixture(tmp_path); native=subject.se.run_short_horizon_strategy; factory=subject._new_pair_binding
    identity=subject.source_identity; owners=[]; calls=[]
    def kept(*args, **kwargs):
        owner=factory(*args, **kwargs); owners.append(owner); return owner
    def changed(bundle, **kwargs):
        calls.append(kwargs['strategy_id']); result=native(bundle, **kwargs); owner=owners[0]
        if target == 'input':bundle.__dict__['bars']=()
        elif target == 'raw':
            path=Path(data[2]['raw_receipt_sources'][0]['raw']['path']); path.write_bytes(path.read_bytes()+b' ')
        elif target == 'policy':
            path=data[1]/'szse-mainboard-merger.html'; path.write_bytes(path.read_bytes()+b' changed')
        elif target == 'issuer_registry':owner.issued.registry.files.clear()
        elif target == 'policy_registry':
            owner.policy_registry.files.clear(); owner._policy_registry_hash=subject.digest_json_stream(owner.policy_registry.files)
        elif target == 'implementation':
            monkeypatch.setattr(subject,'source_identity',lambda:dict(identity(),source_hash='0'*64))
        return result
    monkeypatch.setattr(subject,'_new_pair_binding',kept)
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError):run(data)
    assert calls == [subject.R1] and owners[0].state == 'FAILED'


def test_first_returned_native_result_drift_during_second_arm_rejected(tmp_path, monkeypatch):
    data=fixture(tmp_path); native=subject.se.run_short_horizon_strategy; prior=[]
    def changed(bundle, **kwargs):
        result=native(bundle, **kwargs)
        if kwargs['strategy_id'] == subject.R1:prior.append(result)
        else:prior[0]['results'][subject.R1]['items'][0]['exact_features']['amount']={'numerator':1,'denominator':1}
        return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError,match='SEALED_NATIVE_RUN_CHANGED'):run(data)


@pytest.mark.parametrize('target', ['base_fact','extra_fact','reason','market','run_id','eligibility',
    'outer_input_id','coverage_type','inner_source_hash','outer_source_files','data_watermark',
    'style_status','watch_reasons','extra_row'])
def test_tampered_native_r2_result_rejected(tmp_path, monkeypatch, target):
    data=fixture(tmp_path); native=subject.se.run_short_horizon_strategy
    def changed(bundle, **kwargs):
        result=native(bundle, **kwargs)
        if kwargs['strategy_id'] != subject.R2:return result
        item=result['results'][subject.R2]['items'][0]
        if target == 'base_fact':item['exact_features']['amount']={'numerator':1,'denominator':1}
        elif target == 'extra_fact':item['exact_features']['unregistered_predictive_fact']=1
        elif target == 'reason':item['reason_codes'].append('TEST_FIXTURE_FAKE_REASON')
        elif target == 'market':result['results'][subject.R2]['market_turnover_context']['median_amount_cny']=None
        elif target == 'run_id':result['run_id']='TEST_FIXTURE_FAKE_RUN'
        elif target == 'eligibility':result['qualification']['eligible_ids']=['TEST_FIXTURE_FAKE_IID']
        elif target == 'outer_input_id':result['input_id']='TEST_FIXTURE_DIFFERENT_INPUT_ID'
        elif target == 'coverage_type':
            result['coverage_type']=result['results'][subject.R2]['coverage_type']='PROSPECTIVE_CAPTURED'
        elif target == 'inner_source_hash':result['results'][subject.R2]['source_hash']='0'*64
        elif target == 'outer_source_files':result['source_file_hashes']={}
        elif target == 'data_watermark':result['data_watermark']='1990-01-01T00:00:00+00:00'
        elif target == 'style_status':item['style_status']='FAIL'
        elif target == 'watch_reasons':item['watch_reasons']=['TEST_FIXTURE_FAKE_WATCH_REASON']
        elif target == 'extra_row':item['unregistered_predictive_fact']=1
        return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',changed)
    with pytest.raises(ValueError):run(data)


def test_all_eight_independent_review_probes_reject_in_pure_comparison(tmp_path):
    # JSON here is only a mathematics-test input; no source/factory is restored.
    result=run(fixture(tmp_path))
    bundle=MarketInput.model_validate(result['input'])
    cutoff=subject.stamp(result['decision_cutoff'])
    for target in ('outer_input_id','coverage_type','inner_source_hash','outer_source_files',
                   'data_watermark','style_status','watch_reasons','extra_row'):
        native=deepcopy(result['native_runs']); changed=native[subject.R2]
        inner=changed['results'][subject.R2]; item=inner['items'][0]
        if target == 'outer_input_id':changed['input_id']='TEST_FIXTURE_DIFFERENT_INPUT_ID'
        elif target == 'coverage_type':changed['coverage_type']=inner['coverage_type']='PROSPECTIVE_CAPTURED'
        elif target == 'inner_source_hash':inner['source_hash']='0'*64
        elif target == 'outer_source_files':changed['source_file_hashes']={}
        elif target == 'data_watermark':changed['data_watermark']='1990-01-01T00:00:00+00:00'
        elif target == 'style_status':item['style_status']='FAIL'
        elif target == 'watch_reasons':item['watch_reasons']=['TEST_FIXTURE_FAKE_WATCH_REASON']
        elif target == 'extra_row':item['unregistered_predictive_fact']=1
        with pytest.raises(ValueError):
            subject._compare(native[subject.R1],native[subject.R2],bundle,result['input_hash'],cutoff)


def test_initial_identity_seals_native_memory_configuration_before_first_arm(tmp_path, monkeypatch):
    data=fixture(tmp_path); build=subject.physical._build; calls=[]
    def changed(*args, **kwargs):
        bundle,audit=build(*args, **kwargs)
        monkeypatch.setitem(native_r1.CONFIG['stages']['STRONG_START'],'ret1_min','9')
        return bundle,audit
    monkeypatch.setattr(subject.physical,'_build',changed)
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',lambda *args,**kwargs:calls.append(1))
    with pytest.raises(ValueError,match='IMPLEMENTATION_CHANGED'):run(data)
    assert calls == []


def test_deadline_interrupts_native_and_no_second_attempt(tmp_path, monkeypatch):
    import time
    data=fixture(tmp_path); native=subject.se.run_short_horizon_strategy; calls=[]
    def delayed(bundle, **kwargs):
        calls.append(kwargs['strategy_id']); result=native(bundle, **kwargs)
        time.sleep(5); return result
    monkeypatch.setattr(subject.se,'run_short_horizon_strategy',delayed)
    deadline=datetime.now(timezone.utc)+timedelta(seconds=2)
    with pytest.raises(TimeoutError,match='REGISTERED_DEADLINE_EXCEEDED'):run(data,deadline=deadline)
    assert calls == [subject.R1]
    with pytest.raises(ValueError,match='REGISTERED_JOB_ALREADY_ATTEMPTED'):run(data,deadline=deadline)


def test_active_caller_timer_refused_without_issuer_or_timer_changes(tmp_path, monkeypatch):
    import signal
    data=fixture(tmp_path); calls=[]
    monkeypatch.setattr(subject,'issue_saved_r2_source_reader',lambda **kwargs:calls.append(1))
    old_handler=signal.getsignal(signal.SIGALRM); old_timer=signal.getitimer(signal.ITIMER_REAL)
    handler=lambda *_:None
    try:
        signal.signal(signal.SIGALRM,handler); signal.setitimer(signal.ITIMER_REAL,10)
        with pytest.raises(ValueError,match='ACTIVE_CALLER_TIMER_CONFLICT'):run(data)
        assert calls == [] and signal.getsignal(signal.SIGALRM) is handler
        remaining, interval=signal.getitimer(signal.ITIMER_REAL)
        assert 0 < remaining <= 10 and interval == 0
    finally:
        signal.setitimer(signal.ITIMER_REAL,0); signal.signal(signal.SIGALRM,old_handler)
        if old_timer[0]:signal.setitimer(signal.ITIMER_REAL,*old_timer)
