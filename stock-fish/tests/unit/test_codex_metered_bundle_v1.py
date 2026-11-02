"""TEST_FIXTURE: control metering joins native budgets, never a source bypass."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import builtins
import hashlib
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

from scripts import codex_metered_bundle_v1 as metered
from scripts import codex_factor_checkpoint_premarket_v1 as gateway
from stockfish_core.yaogu import premarket_runtime_v1 as native


class FixtureRuntime(gateway.recovery.RecoveryRuntime):
    def __init__(self,root,marker):
        self.marker=marker
        self.profile=dict(name='TEST_FIXTURE_metered',backend='premarket',source_root=str(gateway.ROOT),
            python=sys.executable,state_root=str(root/'state'),config=str(root/'original.json'),
            report_root=str(root/'reports'))
        self.scope=dict(backend='premarket',device=1,inode=2)
        self.registry_root=root/'registry';self.directory=self.registry_root/'scopes/TEST_FIXTURE'
        self.binding={};self.resources=[];self.active_locks=0
    def check_binding(self): pass
    def guard_ancestor_request(self,identity): self.marker.read_bytes()
    def preflight(self,**kwargs):
        self.marker.read_bytes()
        return dict(allowed=True,reason_codes=[],expected_growth_bytes=0,min_free_bytes=1,
            max_managed_bytes=1<<30,projected_unique_logical_bytes=0,destination_checks=[])
    @contextmanager
    def exclusive(self):
        self.marker.read_bytes();self.active_locks+=1
        try:yield
        finally:
            self.active_locks-=1;self.marker.read_bytes()


class FixtureGuard:
    def __init__(self,runtime,marker):
        self.runtime=runtime;self.marker=marker;self.directory=gateway.ROOT
        self.snapshot={'TEST_FIXTURE':True};self.checks=0
    def check(self): self.checks+=1;self.marker.read_bytes()


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    root=tmp_path/'TEST_FIXTURE_metered_bundle';root.mkdir()
    (root/'state').mkdir();(root/'reports').mkdir()
    marker=root/'marker.bin';marker.write_bytes(b'TEST_FIXTURE_exact_control_bytes')
    runtime=FixtureRuntime(root,marker)
    config=dict(schema_version='STANDALONE_PREMARKET_WORKFLOW_V1',state_root=str(root/'state'),
        minimum_amount_cny=300000000,limits=dict(gateway.bounded.CAPS),
        capture=dict(offline=True,retry_failed=False),news={},seed_sources=[])
    gateway.workspace._write(root/'original.json',config)
    derived=deepcopy(config);derived['limits']['max_seconds']=900
    gateway.workspace._write(root/'derived.json',derived)
    budget=native.Budget(dict(gateway.bounded.CAPS,capture_read_bytes=512*1024**2))
    def denied(*_a,**_k): raise AssertionError('TEST_FIXTURE_NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket.socket,'connect_ex',denied)
    monkeypatch.setattr(socket,'create_connection',denied)
    return SimpleNamespace(root=root,runtime=runtime,guard=FixtureGuard(runtime,marker),
        marker=marker,budget=budget,derived=derived)


def count(budget):return budget.metrics['returned_read_bytes']['capture']


def test_bind_reserves_bounded_controls_and_preserves_original_bindings(fixture):
    original_profile=deepcopy(fixture.runtime.profile)
    functions=(gateway.bounded.configuration,gateway.original._prepared,gateway.recovery.reference)
    facade,run=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    assert count(fixture.budget)>=metered.CONTROL_READ_RESERVE_BYTES
    assert facade.control_read_metering['reserve_is_not_measured_io'] is True
    assert fixture.runtime.profile==original_profile
    assert facade.profile['config']==str(fixture.root/'derived.json')
    assert (gateway.bounded.configuration,gateway.original._prepared,gateway.recovery.reference)==functions
    assert run.__wrapped__.__code__ is gateway.bounded.run.__code__
    assert run.__wrapped__.__globals__['original']._prepared is not functions[1]
    assert run.__wrapped__.__globals__['recovery'].reference is not functions[2]
    assert run.__wrapped__.__globals__['configuration'] is not functions[0]
    assert facade.control_read_metering['single_original_parent_budget_per_invocation'] is True


def test_guard_read_is_counted_once_by_active_native_budget(fixture):
    facade,_=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    before=count(fixture.budget)
    other=native.Budget(gateway.bounded.CAPS)
    with other.meter_files():
        facade.guard.check()
        assert native._ACTIVE_METER is other
    assert count(other)==fixture.marker.stat().st_size
    assert count(fixture.budget)==before
    assert native._ACTIVE_METER is None


def test_exclusive_meters_enter_exit_but_not_action_and_closes_on_exception(fixture):
    facade,_=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    before=count(fixture.budget)
    other=native.Budget(gateway.bounded.CAPS)
    with pytest.raises(RuntimeError,match='TEST_FIXTURE_action'):
        with facade.exclusive():
            assert native._ACTIVE_METER is None and fixture.runtime.active_locks==1
            inner_before=count(fixture.budget)
            fixture.marker.read_bytes()  # Action ownership deliberately belongs to its native meter.
            assert count(fixture.budget)==inner_before
            with other.meter_files():fixture.marker.read_bytes()
            raise RuntimeError('TEST_FIXTURE_action')
    assert fixture.runtime.active_locks==0
    assert count(fixture.budget)>before and count(other)==fixture.marker.stat().st_size
    assert native._ACTIVE_METER is None


def test_exclusive_inside_existing_meter_never_borrows_control_quota(fixture):
    facade,_=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    before=count(fixture.budget);other=native.Budget(gateway.bounded.CAPS)
    with other.meter_files():
        with facade.exclusive():
            assert native._ACTIVE_METER is other
            facade.guard_ancestor_request({'TEST_FIXTURE':True})
            facade.preflight()
    assert count(fixture.budget)==before and count(other)>=5*fixture.marker.stat().st_size


def test_nonbudget_active_marker_cannot_skip_metering(fixture,monkeypatch):
    monkeypatch.setattr(native,'_ACTIVE_METER',object())
    with pytest.raises(ValueError,match='INVALID_ACTIVE_ORIGINAL_METER'):
        with metered.read_scope(fixture.budget):
            raise AssertionError('unreachable')


def test_scope_restores_file_meter_after_read_exception(fixture):
    prior_open=builtins.open
    with pytest.raises(ValueError,match='TEST_FIXTURE'):
        with metered.read_scope(fixture.budget):
            fixture.marker.read_bytes()
            raise ValueError('TEST_FIXTURE_scope_failure')
    assert builtins.open is prior_open and native._ACTIVE_METER is None
    assert count(fixture.budget)==fixture.marker.stat().st_size


def test_entered_lock_is_released_if_meter_terminal_check_fails(fixture,monkeypatch):
    seen=[]
    @contextmanager
    def manager():
        seen.append('enter')
        try:yield
        finally:seen.append('exit')
    old=fixture.budget.check
    failed=[False]
    def checked(stage,**kwargs):
        if stage=='meter_complete' and not failed[0]:
            failed[0]=True
            raise RuntimeError('TEST_FIXTURE_meter_terminal')
        return old(stage,**kwargs)
    monkeypatch.setattr(fixture.budget,'check',checked)
    with pytest.raises(RuntimeError,match='TEST_FIXTURE_meter_terminal'):
        with metered._metered_context(manager(),fixture.budget):
            raise AssertionError('unreachable')
    assert seen==['enter','exit'] and native._ACTIVE_METER is None


def test_sticky_read_failure_still_releases_actual_facade_lock_once(fixture):
    facade,_=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    with pytest.raises(native.BudgetExceeded,match='READ_LIMIT_EXCEEDED:capture'):
        with facade.exclusive():
            assert fixture.runtime.active_locks==1
            fixture.budget.limits['capture_read_bytes']=count(fixture.budget)+fixture.marker.stat().st_size-1
            with metered.read_scope(fixture.budget):fixture.marker.read_bytes()
    assert fixture.runtime.active_locks==0 and native._ACTIVE_METER is None
    assert fixture.budget.metrics['failures']==['READ_LIMIT_EXCEEDED:capture']


def test_sticky_post_enter_failure_releases_actual_lock(fixture,monkeypatch):
    facade,_=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    old=fixture.budget.check
    def checked(stage,**kwargs):
        if stage=='meter_complete' and fixture.runtime.active_locks==1:
            fixture.budget.limits['self_rss_bytes']=1
        return old(stage,**kwargs)
    monkeypatch.setattr(fixture.budget,'check',checked)
    with pytest.raises(native.BudgetExceeded,match='SELF_RSS_LIMIT_EXCEEDED'):
        with facade.exclusive():
            raise AssertionError('unreachable')
    assert fixture.runtime.active_locks==0 and native._ACTIVE_METER is None


def test_meter_entry_failure_exits_already_owned_context_exactly_once(fixture):
    calls=[]
    class Manager:
        def __exit__(self,*exception):calls.append(exception)
    try:fixture.budget._fail('TEST_FIXTURE_METER_ENTRY_REFUSED')
    except native.BudgetExceeded:pass
    with pytest.raises(native.BudgetExceeded,match='TEST_FIXTURE_METER_ENTRY_REFUSED'):
        metered._exit_once(Manager(),fixture.budget,(None,None,None))
    assert calls==[(None,None,None)] and native._ACTIVE_METER is None


def test_original_exception_suppression_semantics_survive_metering(fixture):
    @contextmanager
    def suppress():
        try:yield
        except LookupError:pass
    with metered._metered_context(suppress(),fixture.budget):
        raise LookupError('TEST_FIXTURE_suppressed')
    assert native._ACTIVE_METER is None


@pytest.mark.parametrize('field,value',[('max_seconds',10801),('max_requests',True),('max_sdk_calls',float('nan'))])
def test_derived_limits_cannot_expand_or_coerce_types(fixture,field,value):
    original=deepcopy(fixture.derived);derived=deepcopy(original)
    derived['limits'][field]=value
    with pytest.raises(ValueError,match='DERIVED_LIMITS'):
        metered._derived_only_reduces(original,derived)


def test_derived_strategy_and_source_policy_are_preserved(fixture):
    derived=deepcopy(fixture.derived);derived['capture']['retry_failed']=True
    with pytest.raises(ValueError,match='SEMANTICS_CHANGED'):
        metered._derived_only_reduces(fixture.derived,derived)


def test_reserve_exhaustion_fails_before_any_execution(fixture):
    budget=native.Budget(dict(gateway.bounded.CAPS,capture_read_bytes=1024*1024))
    with pytest.raises(native.BudgetExceeded,match='READ_LIMIT_EXCEEDED'):
        metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',budget)
    assert not (fixture.runtime.directory/'operations').exists()


def test_invocation_factory_rejects_different_or_coerced_limits(fixture):
    factory=metered._shared_budget_factory(native.Budget)
    limits=dict(gateway.bounded.CAPS)
    first=factory(limits)
    assert factory(dict(limits)) is first
    for changed in ({**limits,'max_seconds':limits['max_seconds']-1},
                    {**limits,'max_seconds':float(limits['max_seconds'])},
                    {key:value for key,value in limits.items() if key!='max_seconds'}):
        with pytest.raises(ValueError,match='INVOCATION_BUDGET_LIMITS_CHANGED'):
            factory(changed)
    with pytest.raises(ValueError,match='INVALID_INVOCATION_BUDGET_LIMITS'):
        factory({**limits,'max_requests':True})


def test_actual_frozen_run_prelude_is_metered_and_action_opens_own_budget(fixture,monkeypatch):
    files=[Path(gateway.bounded.__file__),Path(gateway.original.__file__),Path(gateway.recovery.__file__)]
    before={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    native_budgets=[]; totals=[]
    def calendar_gate(_prepared,_config,_observation,_target,budget):
        assert native._ACTIVE_METER is None
        with budget.meter_files():fixture.marker.read_bytes()
        native_budgets.append(budget)
        totals.append(count(budget))
    class StopBeforeSDK(Exception):pass
    called=[]
    def supervise(config,**kwargs):
        assert config==fixture.derived and kwargs['engine']=='original'
        assert native._ACTIVE_METER is None
        called.append(True)
        raise StopBeforeSDK()
    monkeypatch.setattr(gateway.bounded,'calendar_gate',calendar_gate)
    monkeypatch.setattr(gateway.bounded.workflow,'supervise',supervise)
    facade,run=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    previous=count(fixture.budget)
    local=datetime.now(timezone.utc).astimezone(gateway.bounded.frozen.SH).date()
    args=SimpleNamespace(profile=gateway.bounded.PROFILE,observation_date=str(local-timedelta(days=1)),
        target_session=str(local+timedelta(days=1)),request_key='TEST_FIXTURE_metered_run')
    with pytest.raises(StopBeforeSDK):run(facade,args)
    assert called==[True] and len(native_budgets)==2
    assert native_budgets[0] is native_budgets[1]
    assert totals[1]>totals[0]>0
    assert count(fixture.budget)>previous+fixture.marker.stat().st_size
    assert all(count(budget)>0 for budget in native_budgets)
    assert fixture.runtime.active_locks==0 and native._ACTIVE_METER is None
    assert {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}==before
    records=list((fixture.runtime.directory/'operations').glob('*.json'))
    assert len(records)==1 and gateway.workspace._read(records[0])['phase']=='FAILED_OR_UNKNOWN'
    args.request_key='TEST_FIXTURE_metered_another_run'
    args.observation_date=str(local-timedelta(days=2))
    args.target_session=str(local+timedelta(days=2))
    with pytest.raises(StopBeforeSDK):run(facade,args)
    assert len(native_budgets)==4
    assert native_budgets[2] is native_budgets[3]
    assert native_budgets[0] is not native_budgets[2]
    assert totals[2]==totals[0] and totals[3]>totals[2]


def test_frozen_first_calendar_consumption_can_exhaust_action_budget(fixture,monkeypatch):
    budgets=[]
    # A single remaining byte cannot admit the later original action's reads.
    # The first gate's charge is explicit TEST_FIXTURE accounting, not a source.
    def calendar_gate(_prepared,_config,_observation,_target,budget):
        budgets.append(budget)
        budget.charge_read(budget.limits['capture_read_bytes']-1)
    def forbidden(*args,**kwargs):raise AssertionError('TEST_FIXTURE_SDK_MUST_NOT_START')
    monkeypatch.setattr(gateway.bounded,'calendar_gate',calendar_gate)
    monkeypatch.setattr(gateway.bounded.workflow,'supervise',forbidden)
    facade,run=metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget)
    local=datetime.now(timezone.utc).astimezone(gateway.bounded.frozen.SH).date()
    args=SimpleNamespace(profile=gateway.bounded.PROFILE,observation_date=str(local-timedelta(days=1)),
        target_session=str(local+timedelta(days=1)),request_key='TEST_FIXTURE_shared_quota')
    with pytest.raises(native.BudgetExceeded,match='READ_LIMIT_EXCEEDED:capture'):
        run(facade,args)
    assert len(budgets)==1 and count(budgets[0])>budgets[0].limits['capture_read_bytes']
    assert budgets[0].metrics['failures']==['READ_LIMIT_EXCEEDED:capture']
    assert fixture.runtime.active_locks==0 and native._ACTIVE_METER is None


def test_live_read_callback_is_not_double_wrapped_or_charged(fixture):
    parent=native.Budget(gateway.bounded.CAPS)
    parent.charge_read(17)
    gate=metered._LiveSupervisorReads(parent)
    original=[]
    callback=gate.wrap(original.append)
    assert gate.wrap(callback) is callback
    callback(31);callback(7)
    assert original==[31,7] and gate.returned==38
    assert count(parent)==17+metered.SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES
    parent.charge_read(gate.returned)
    assert count(parent)==17+metered.SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES+38


def _fixture_inner_worker(fixture, *, delay=0.8):
    shared=gateway.bounded.workflow
    config=deepcopy(fixture.derived)
    config['capture']['allow_test_fixture']=True
    config=shared.legacy.load_config(overrides=config)
    script=fixture.root/'TEST_FIXTURE_inner_worker.py'
    script.write_text('import json,pathlib,sys,time\n'
        'state=pathlib.Path(sys.argv[1])\n'
        f'time.sleep({delay!r})\n'
        "job=next((state/'jobs').iterdir())\n"
        f"(job/'worker-completion.json').write_text(json.dumps(dict(schema_version={shared.VERSION!r},status='FAILED')))\n"
        'raise SystemExit(3)\n')
    command=[sys.executable,'-I','-B',str(script),config['state_root']]
    return shared,config,command


def test_real_inner_supervisor_counts_all_paths_and_preserves_configuration(fixture,monkeypatch):
    shared,config,command=_fixture_inner_worker(fixture)
    expected=deepcopy(config)
    before_functions={name:getattr(shared.legacy,name) for name in
        ('supervise','_read_control','_owned_group_members')}
    calls={name:0 for name in ('_code_refs','_ref','_verify','_tree_sample','_read_control','_owned_group_members')}
    for name in calls:
        module=shared if name=='_code_refs' else shared.legacy
        original=getattr(module,name)
        def called(*args,_original=original,_name=name,**kwargs):
            calls[_name]+=1
            return _original(*args,**kwargs)
        monkeypatch.setattr(module,name,called)
    parent=native.Budget(config['limits']);parent.charge_read(17)
    code,result=metered._live_supervise(shared,parent,config,
        progress=True,_fixture_worker_command=command)
    assert code==3 and result['status']=='FAILED' and result['external_worker_reaped'] is True
    assert config==expected
    assert json.loads((Path(result['job'])/'configuration.json').read_bytes())==expected
    assert all(calls.values()),calls
    assert count(parent)==17+metered.SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES
    parent.charge_read(result['supervisor_returned_read_bytes'])
    assert count(parent)==17+metered.SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES+result['supervisor_returned_read_bytes']
    assert result['supervisor_returned_read_bytes']>0
    assert shared.legacy.supervise is before_functions['supervise']
    assert native._ACTIVE_METER is None


def test_combined_read_gate_fails_before_worker_is_started(fixture,monkeypatch):
    shared,config,command=_fixture_inner_worker(fixture)
    parent=native.Budget({**config['limits'],'capture_read_bytes':1024*1024})
    parent.charge_read(parent.limits['capture_read_bytes']-
        metered.SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES-1)
    def forbidden(*_args,**_kwargs):raise AssertionError('TEST_FIXTURE_POPEN_MUST_NOT_START')
    monkeypatch.setattr(shared._WorkerSubprocess,'Popen',forbidden)
    code,result=metered._live_supervise(shared,parent,config,
        progress=False,_fixture_worker_command=command)
    assert code==4 and result['status']=='BUDGET_EXCEEDED'
    assert result['error']['reason']=='COMBINED_PARENT_SUPERVISOR_READ_LIMIT_EXCEEDED'
    assert result['external_worker_exit_code'] is None and result['external_worker_reaped'] is True
    assert parent.metrics['failures']==['COMBINED_PARENT_SUPERVISOR_READ_LIMIT_EXCEEDED']


def test_live_gate_failure_uses_original_owned_worker_cleanup(fixture,monkeypatch):
    shared,config,command=_fixture_inner_worker(fixture,delay=30)
    parent=native.Budget({**config['limits'],'capture_read_bytes':64*1024**2})
    stopped=[];original_stop=shared.legacy._stop_owned
    def stop(process):
        original_stop(process);stopped.append(process)
    def overread(_pid,read_meter=None):
        read_meter(parent.limits['capture_read_bytes']+1)
        raise AssertionError('TEST_FIXTURE_LIVE_GATE_DID_NOT_STOP')
    monkeypatch.setattr(shared.legacy,'_tree_sample',overread)
    monkeypatch.setattr(shared.legacy,'_stop_owned',stop)
    code,result=metered._live_supervise(shared,parent,config,
        progress=False,_fixture_worker_command=command)
    assert code==4 and result['status']=='BUDGET_EXCEEDED'
    assert result['error']['reason']=='COMBINED_PARENT_SUPERVISOR_READ_LIMIT_EXCEEDED'
    assert len(stopped)==1 and stopped[0].poll() is not None
    assert result['external_worker_reaped'] is True
