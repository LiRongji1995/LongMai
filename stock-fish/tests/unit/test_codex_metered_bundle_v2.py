"""TEST_FIXTURE actual inner workers and the new bridge-to-frozen-run wiring."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import codex_metered_bundle_v2 as metered
from scripts import codex_sdk_tree_supervisor_v3 as supervisor
from scripts import codex_supervised_premarket_v3 as bridge
from scripts import codex_factor_checkpoint_premarket_v1 as gateway
from stockfish_core.yaogu import premarket_runtime_v1 as native
from tests.unit.test_codex_metered_bundle_v1 import fixture, count, _fixture_inner_worker


def no_legacy_process_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_LEGACY_PS_OR_POPEN_REAPER_FORBIDDEN')
    shared = gateway.bounded.workflow
    for name in ('_tree_sample','_owned_group_members','_stop_owned'):
        monkeypatch.setattr(shared.legacy,name,forbidden)
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(subprocess.Popen,'poll',forbidden)
    monkeypatch.setattr(subprocess.Popen,'wait',forbidden)


def test_actual_inner_worker_uses_native_sampler_and_one_kernel_reaper(fixture,monkeypatch):
    shared,config,command = _fixture_inner_worker(fixture)
    no_legacy_process_calls(monkeypatch)
    before = deepcopy(config)
    parent = native.Budget(config['limits'])
    code,result = metered._live_supervise(shared,parent,config,supervisor_module=supervisor,
        progress=False,_fixture_worker_command=command)
    assert code == 3 and result['status'] == 'FAILED'
    assert result['external_worker_exit_code'] == 3 and result['external_worker_reaped']
    report = result['native_inner_supervision']
    assert report['root_reaped'] and report['root_reap_evidence']['exit_code_proven']
    assert report['root_reap_evidence']['method'] == 'OS_WAITPID_WNOHANG'
    assert report['sample_count'] > 0 and report['observed_peak_root_rss_bytes'] > 0
    assert report['shell_sampling'] is False and report['process_snapshot_bytes'] > 0
    assert config == before
    saved = json.loads((Path(result['job'])/'final-control.json').read_bytes())
    assert saved == result
    code_refs = json.loads((Path(result['job'])/'code-registry.json').read_bytes())
    assert {Path(metered.__file__).absolute(),Path(supervisor.__file__).absolute()} <= {
        Path(ref['path']) for ref in code_refs}
    assert count(parent) == metered.SUPERVISOR_FINAL_REFERENCE_RESERVE_BYTES
    assert result['supervisor_returned_read_bytes'] >= report['process_snapshot_bytes']


def test_snapshot_failure_cannot_become_success_and_cleanup_still_reaps(fixture,monkeypatch):
    shared,config,command = _fixture_inner_worker(fixture,delay=30)
    no_legacy_process_calls(monkeypatch)
    def denied():
        raise supervisor.SupervisorError('TEST_FIXTURE_NATIVE_SNAPSHOT_FAILED')
    monkeypatch.setattr(supervisor,'_snapshot',denied)
    code,result = metered._live_supervise(shared,native.Budget(config['limits']),config,
        supervisor_module=supervisor,progress=False,_fixture_worker_command=command)
    assert code == 3 and result['status'] == 'FAILED'
    assert result['error']['reason'] == 'TEST_FIXTURE_NATIVE_SNAPSHOT_FAILED'
    report = result['native_inner_supervision']
    assert report['root_reaped'] and report['root_reap_evidence']['exit_code_proven']
    assert report['cleanup']['errors'] and result['cleanup_error'] is not None
    assert report['sample_count'] == 0


def test_combined_read_exhaustion_keeps_failure_and_kernel_cleanup(fixture,monkeypatch):
    shared,config,command = _fixture_inner_worker(fixture,delay=30)
    parent = native.Budget(config['limits'])
    original = supervisor.NativeProcessSession.bind
    def bind(session,process):
        result = original(session,process)
        parent.limits['capture_read_bytes'] = count(parent)+1
        return result
    monkeypatch.setattr(supervisor.NativeProcessSession,'bind',bind)
    no_legacy_process_calls(monkeypatch)
    code,result = metered._live_supervise(shared,parent,config,supervisor_module=supervisor,
        progress=False,_fixture_worker_command=command)
    assert code == 4 and result['status'] == 'BUDGET_EXCEEDED'
    assert result['error']['reason'] == 'COMBINED_PARENT_SUPERVISOR_READ_LIMIT_EXCEEDED'
    assert result['native_inner_supervision']['root_reaped']
    assert 'COMBINED_PARENT_SUPERVISOR_READ_LIMIT_EXCEEDED' in parent.metrics['failures']


def test_old_supervisor_module_cannot_be_used_by_new_adapter(fixture):
    from scripts import codex_sdk_tree_supervisor_v2 as old
    with pytest.raises(ValueError,match='NATIVE_V3_SUPERVISOR_REQUIRED'):
        metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget,
                     supervisor_module=old)


@pytest.mark.parametrize('sample_exit_race',[False,True])
def test_full_authentic_fixture_artifacts_can_pass_new_inner_final_seal(fixture,monkeypatch,sample_exit_race):
    from tests.yaogu.test_premarket_workflow_v1 import WorkflowFixtureTests
    from tests.yaogu.test_premarket_shared_workflow_v1 import _execute
    shared = gateway.bounded.workflow
    sources = WorkflowFixtureTests(methodName='runTest')
    # Keep all fixture originals inside this test's explicitly owned root.
    sources.root = fixture.root/'TEST_FIXTURE_complete_sources'
    sources.root.mkdir()
    sources.config = shared.legacy.load_config(overrides=dict(state_root=str(sources.root/'state'),
        capture=dict(offline=True,allow_test_fixture=True)))
    adapters,now = sources._adapters()
    completed,_ = _execute(sources,shared.execute_workflow_shared,'prepared',adapters,now)
    assert completed['status'] == 'COMPLETED_WITH_UNKNOWNS'
    saved = sources.root/'prepared/worker-completion.json'
    script = fixture.root/'TEST_FIXTURE_completed_worker.py'
    script.write_text('import pathlib,sys,time\n'
        'time.sleep(.8)\n'
        "job=next((pathlib.Path(sys.argv[1])/'jobs').iterdir())\n"
        "(job/'worker-completion.json').write_bytes(pathlib.Path(sys.argv[2]).read_bytes())\n"
        'raise SystemExit(2)\n')
    config = shared.legacy.load_config(overrides={**deepcopy(fixture.derived),
        'capture':dict(offline=True,allow_test_fixture=True)})
    original_gate = shared.legacy._latest_gate
    monkeypatch.setattr(shared.legacy,'_latest_gate',
        lambda rows,observed,target,now_arg=None:original_gate(rows,observed,target,now))
    no_legacy_process_calls(monkeypatch)
    if sample_exit_race:
        captured = []
        bind = supervisor.NativeProcessSession.bind
        def capture(session,process):
            captured.append(session)
            return bind(session,process)
        monkeypatch.setattr(supervisor.NativeProcessSession,'bind',capture)
        snapshot = supervisor._snapshot
        calls = []
        def exit_race():
            calls.append(1)
            if len(calls) == 2:
                deadline = time.monotonic()+5
                while time.monotonic() < deadline:
                    if supervisor.psutil.Process(captured[0].pid).status() in supervisor.DEAD:
                        break
                    time.sleep(.01)
                assert supervisor.psutil.Process(captured[0].pid).status() in supervisor.DEAD
                raise supervisor.SupervisorError('TEST_FIXTURE_NATIVE_SAMPLE_EXIT_RACE')
            return snapshot()
        monkeypatch.setattr(supervisor,'_snapshot',exit_race)
    code,result = metered._live_supervise(shared,native.Budget(config['limits']),config,
        supervisor_module=supervisor,progress=False,
        _fixture_worker_command=[sys.executable,'-I','-B',str(script),config['state_root'],str(saved)])
    if sample_exit_race:
        assert code == 3 and result['status'] == 'FAILED',result
        report = result['native_inner_supervision']
        assert report['failure'] == 'TEST_FIXTURE_NATIVE_SAMPLE_EXIT_RACE'
        assert report['sample_count'] > 0 and report['observed_peak_root_rss_bytes'] > 0
        assert report['root_reaped'] and report['root_reap_evidence']['exit_code_proven']
        assert result['external_worker_exit_code'] == 2
        return
    assert code == 2 and result['status'] == 'COMPLETED_WITH_UNKNOWNS',result
    assert result['source_terminal_binding_verified'] and result['core_verified']
    assert result['native_inner_supervision']['root_reap_evidence']['exit_code_proven']
    assert result['native_inner_supervision']['sample_count'] > 0
    assert shared.verify_job(Path(result['job']))['owned_derivatives_verified']


def test_existing_failed_cleanup_cannot_be_silently_accepted_again():
    session = supervisor.NativeProcessSession(dict(max_seconds=10))
    session.process = object()
    session.cleanup = dict(root_reaped=True,all_observed_descendants_gone=True,
                           errors=['TEST_FIXTURE_PREVIOUS_CLEANUP_FAILED'])
    with pytest.raises(supervisor.SupervisorError,match='CLEANUP_UNVERIFIED'):
        session.stop(session)


def test_actual_new_source_registry_passes_all_original_receipt_checks(fixture,monkeypatch):
    """A real fixture core/theme executes in the owned child and original validator."""
    shared = gateway.bounded.workflow
    now = datetime(2026,9,30,13,tzinfo=timezone.utc)
    expected = ['2026-09-30','2026-10-01','2026-09-30']
    config = shared.legacy.load_config(overrides={**deepcopy(fixture.derived),
        'capture':dict(offline=True,allow_test_fixture=True)})
    original = deepcopy(config)
    original['limits'] = deepcopy(gateway.bounded.CAPS)
    gateway.workspace._write(fixture.root/'original.json',original,replace=True)
    gateway.workspace._write(fixture.root/'derived.json',config,replace=True)
    script = fixture.root/'TEST_FIXTURE_actual_full_worker.py'
    script.write_text('''import json,pathlib,socket,sqlite3,sys
sys.path.insert(0,sys.argv[1])
from unittest.mock import patch
from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu import premarket_native_core_v1 as native,premarket_presentation_v1 as presentation
from tests.yaogu.test_premarket_workflow_v1 import WorkflowFixtureTests
from tests.yaogu.test_premarket_shared_workflow_v1 import _clock
sources=WorkflowFixtureTests(methodName='runTest')
sources.root=pathlib.Path(sys.argv[3]);sources.root.mkdir()
job=next((pathlib.Path(sys.argv[2])/'jobs').iterdir())
sources.config=json.loads((job/'configuration.json').read_bytes())
with patch.object(socket.socket,'connect',side_effect=AssertionError('TEST_FIXTURE_NETWORK')), patch.object(sqlite3,'connect',side_effect=AssertionError('TEST_FIXTURE_DB')):
 adapters,now=sources._adapters()
 with patch.object(shared.legacy,'datetime',_clock(now)),patch.object(native,'datetime',_clock(now)),patch.object(presentation,'datetime',_clock(now)):
  result=shared.execute_workflow_shared(sources.config,job,now=now,adapters=adapters,expected_sessions=['2026-09-30','2026-10-01','2026-09-30'])
 raise SystemExit(shared.EXIT_CODES[result['status']])
''')
    latest = shared.legacy._latest_gate
    gate = shared._expected_gate
    monkeypatch.setattr(shared.legacy,'_latest_gate',
        lambda rows,observed,target,now_arg=None:latest(rows,observed,target,now))
    monkeypatch.setattr(shared,'_expected_gate',lambda sessions,now_arg=None:gate(sessions,now))
    no_legacy_process_calls(monkeypatch)
    code,final = metered._live_supervise(shared,native.Budget(config['limits']),config,
        supervisor_module=supervisor,progress=False,expected_sessions=expected,
        _fixture_worker_command=[sys.executable,'-I','-B',str(script),str(gateway.ROOT),
            config['state_root'],str(fixture.root/'TEST_FIXTURE_native_sources')])
    assert code == 2 and final['status'] == 'COMPLETED_WITH_UNKNOWNS',final
    # The real recovery admission correctly rejects synthetic market inputs.
    # Substitute only this admission inside TEST_FIXTURE to exercise the real
    # downstream validator; never add a production fixture-admission bypass.
    with pytest.raises(ValueError,match='RECOVERED_REAL_SOURCE_REQUIRED'):
        gateway.bounded.configuration(fixture.runtime,gateway.bounded.PROFILE)
    monkeypatch.setattr(gateway.bounded,'configuration',lambda runtime,profile:deepcopy(original))
    facade,run = metered.bind(fixture.runtime,fixture.guard,fixture.root/'derived.json',fixture.budget,
                             supervisor_module=supervisor)
    daily = run.__wrapped__.__globals__['daily']
    prepared = gateway.original._prepared(facade.profile)
    snapshot = daily._snapshot(prepared)
    assert len(snapshot['native_process_sources']) == 2
    job = Path(final['job'])
    _,final_ref = gateway.original._control(job/'final-control.json')
    completed,completion_ref = gateway.original._control(job/'worker-completion.json')
    receipt = dict(schema_version=gateway.daily.VERSION,status=final['status'],job=str(job),
        execution_engine='original',expected_sessions=expected,observation_date=expected[0],
        target_session=expected[1],run_id=completed['run_id'],strategy_id=completed['strategy_id'],
        theme_status=completed['theme_status'],final=final_ref,completion=completion_ref,
        execution_exit_code=code,source_authority_restored=False,
        result_role='ARCHIVED_REUSE_NOT_NEW_CAPTURE',
        display_artifacts={key:ref['path'] for key,ref in final['display']['artifacts'].items()})
    checked = daily._validate(prepared,snapshot,receipt,expected[0],expected[1],'original')
    assert checked['verified'] and checked['native_inner_sampling_verified']
    assert checked['complete_daily_delivery'] and checked['source_authority_restored'] is False
    # The V2/old validator rejects the expanded source set; V3 explicitly binds
    # the new set, instead of removing any original code/source/terminal check.
    with pytest.raises(ValueError,match='EXECUTED_SOURCE_CHANGED'):
        gateway.bound_functions(fixture.guard).__globals__['daily']._validate(
            prepared,gateway.bound_functions(fixture.guard).__globals__['daily']._snapshot(prepared),
            receipt,expected[0],expected[1],'original')


def test_real_new_bridge_body_runs_frozen_claim_and_actual_native_supervisor(fixture,monkeypatch):
    """Only registry/calendar sources are fixtures; bridge/run_once/worker are real.

    The actual original worker is offline with no sources, so failure is the
    correct expected terminal state. This proves the adapter is on the new
    bridge's actual dynamic-load path, not just on a directly called helper.
    """
    no_legacy_process_calls(monkeypatch)
    monkeypatch.setattr(gateway.recovery,'load_runtime',lambda *_args:fixture.runtime)
    monkeypatch.setattr(gateway,'SourceGuard',lambda *_args:fixture.guard)
    monkeypatch.setattr(gateway.bounded,'calendar_gate',lambda *_args:None)
    local = datetime.now(timezone.utc).astimezone(gateway.bounded.frozen.SH).date()
    original_ref = bridge.read(fixture.root/'original.json')[1]
    derived_ref = bridge.read(fixture.root/'derived.json')[1]
    references = [gateway.bundle.read(Path(module.__file__).absolute())[1]
                  for module in (bridge,metered,supervisor)]
    context = dict(schema_version=bridge.VERSION,engine='original',runtime='TEST_FIXTURE_metered',
        registry_root=str(fixture.runtime.registry_root),manifest_sha256='TEST_FIXTURE',
        source_references=references,original_configuration=original_ref,
        derived_configuration=derived_ref,observation_date=str(local-timedelta(days=1)),
        target_session=str(local+timedelta(days=1)),request_key='TEST_FIXTURE_bridge_native',
        result_path=str(fixture.root/'must-not-publish.json'))
    path = fixture.root/'context.json'
    bridge.write(path,context)
    args = SimpleNamespace(context=path,context_sha256=bridge.digest(context),bundle=gateway.ROOT)
    preserved = {str(Path(module.__file__)):hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                 for module in (gateway,gateway.bounded,gateway.bounded.workflow,
                                gateway.bounded.workflow.legacy)}
    with pytest.raises(ValueError,match='EXECUTION_FAILED_RETAINED_NO_RETRY'):
        bridge._workflow_body(args,fixture.budget,supervisor_module=supervisor)
    assert not Path(context['result_path']).exists()
    jobs = list((fixture.root/'state/jobs').iterdir())
    assert len(jobs) == 1
    final = json.loads((jobs[0]/'final-control.json').read_bytes())
    assert final['status'] == 'FAILED' and final['external_worker_reaped']
    assert final['native_inner_supervision']['shell_sampling'] is False
    assert final['native_inner_supervision']['root_reap_evidence']['exit_code_proven']
    completion = json.loads((jobs[0]/'worker-completion.json').read_bytes())
    assert completion['status'] == 'FAILED' and completion['metrics']['calls']['requests'] == 0
    operations = list((fixture.runtime.directory/'operations').glob('*.json'))
    assert len(operations) == 1
    assert gateway.workspace._read(operations[0])['phase'] == 'FAILED_OR_UNKNOWN'
    with pytest.raises(ValueError,match='NO_RETRY|RECONCILE'):
        bridge._workflow_body(args,fixture.budget,supervisor_module=supervisor)
    assert len(list((fixture.root/'state/jobs').iterdir())) == 1
    assert {path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in preserved} == preserved
