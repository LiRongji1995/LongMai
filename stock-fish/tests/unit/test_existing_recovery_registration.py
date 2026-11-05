"""TEST_FIXTURE: existing recovery metadata belongs to the one workspace ledger."""
import json
from pathlib import Path
import sys
import pytest
from scripts import codex_workspace as workspace
from stockfish_core.file_integrity import content_fingerprint


def ref(path):
    return dict(path=str(path), **content_fingerprint(path))


@pytest.fixture
def recovery(tmp_path):
    root = (tmp_path / 'TEST_FIXTURE_recovery').resolve()
    state, reports = root / 'state', root / 'reports'
    state.mkdir(parents=True)
    reports.mkdir()
    config = root / 'config.json'
    config.write_text(json.dumps(dict(state_root=str(state), limits=dict(max_seconds=12, max_requests=2))))
    profile = dict(schema_version=workspace.PROFILE_VERSION, name='TEST_FIXTURE_old', backend='premarket',
        state_root=str(state), report_root=str(reports), source_root=str(Path(__file__).resolve().parents[2]),
        config=str(config), python=sys.executable, storage=dict(
            roots=[dict(name='fixture', path=str(root), role='runtime')], target_root='fixture',
            min_free_bytes=0, max_managed_bytes=16*1024**2, expected_growth_bytes=1024))
    path = root / 'profile.json'
    path.write_text(json.dumps(profile))
    registry = root / 'registry'
    workspace.register_profile(path, registry, make_default=True)
    old = workspace.load_runtime(profile['name'], registry)
    identity = dict(backend='premarket', observation_date='2026-10-28', target_session='2026-10-29', request_key='TEST_FIXTURE_failed')
    def fail():
        raise RuntimeError('TEST_FIXTURE_FAILURE')
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_FAILURE'):
        old.run_once(identity, fail, lambda receipt: True)
    history = dict(scope=old.scope, directory=str(old.directory / 'operations'), resources=old.resources,
        registry=ref(registry / 'runtimes' / (profile['name'] + '.json')), config=ref(config),
        default=ref(registry / 'default.json'), operations=[dict(identity=identity, phase='FAILED_OR_UNKNOWN', reference=ref(p))
            for p in (old.directory / 'operations').glob('*.json')])
    recovered_state = state / 'epochs/TEST_FIXTURE_existing'
    recovered_state.mkdir(parents=True)
    recovered_config = root / 'recovered-config.json'
    recovered_config.write_text(json.dumps(dict(state_root=str(recovered_state), limits=dict(max_seconds=5, max_requests=1))))
    recovered_profile = dict(old.profile, name='TEST_FIXTURE_recovered', state_root=str(recovered_state), config=str(recovered_config))
    binding = workspace._binding(recovered_profile)
    plan = dict(schema_version='CODEX_RUNTIME_RECOVERY_V1', registry_root=str(registry), profile=recovered_profile,
        binding=binding, config=ref(recovered_config), history=history, test_fixture=True,
        source_authority_restored=False, cumulative_budget=dict(spent_requests=1, remaining_requests=1))
    plan['epoch_id'] = 'runtime_recovery_' + workspace._digest(plan)
    epoch = registry / 'recovery-epochs' / (plan['epoch_id'] + '.json')
    workspace._write(epoch, plan)
    workspace._write(registry / 'runtimes' / (recovered_profile['name'] + '.json'),
        dict(schema_version='CODEX_RUNTIME_RECOVERY_V1', profile=recovered_profile, binding=binding, epoch=ref(epoch)))
    return registry, recovered_profile, old, identity, epoch, recovered_config


def test_existing_recovery_keeps_locks_failure_budget_and_default(recovery):
    registry, profile, old, identity, epoch, config = recovery
    before = {p: p.read_bytes() for p in (epoch, config, registry / 'default.json')}
    runtime = workspace.load_runtime(profile['name'], registry, allow_test_fixture=True)
    assert set(old.resources) <= set(runtime.resources)
    assert runtime.profile['config'] == str(config)
    for key in (identity['request_key'], 'TEST_FIXTURE_new_key'):
        with pytest.raises(ValueError, match='ANCESTOR_REQUEST_RECONCILE_NO_RETRY'):
            runtime.run_once(dict(identity, request_key=key), lambda: pytest.fail('retried failure'), lambda receipt: True)
    assert {p: p.read_bytes() for p in before} == before
    assert not (runtime.directory / 'operations').exists()
    from scripts.codex_daily_v2 import _operations
    rows = _operations(runtime)['operations']
    assert len(rows) == 1
    assert rows[0]['phase'] == 'FAILED_OR_UNKNOWN'
    assert rows[0]['scope'] == old.scope and rows[0]['inherited'] is True


def test_recovery_epoch_same_bytes_replacement_and_tamper(recovery):
    registry, profile, old, identity, epoch, config = recovery
    replacement = epoch.with_suffix('.replacement')
    replacement.write_bytes(epoch.read_bytes())
    replacement.replace(epoch)
    workspace.load_runtime(profile['name'], registry, allow_test_fixture=True).check_binding()
    epoch.write_bytes(epoch.read_bytes().replace(b'"remaining_requests":1', b'"remaining_requests":9'))
    with pytest.raises(ValueError):
        workspace.load_runtime(profile['name'], registry, allow_test_fixture=True)


def test_ancestor_record_cannot_be_removed(recovery):
    registry, profile, old, identity, epoch, config = recovery
    runtime = workspace.load_runtime(profile['name'], registry, allow_test_fixture=True)
    next((old.directory / 'operations').glob('*.json')).unlink()
    with pytest.raises((ValueError, OSError)):
        runtime.check_binding()


@pytest.mark.parametrize('backend', ['local-research', 'local-outcomes'])
def test_ancestor_business_request_cannot_hide_behind_new_key(recovery, backend):
    registry, profile, old, identity, epoch, config = recovery
    runtime = workspace.load_runtime(profile['name'], registry, allow_test_fixture=True)
    if backend == 'local-research':
        original = dict(backend=backend, request_key='TEST_FIXTURE_old_local', database='TEST_FIXTURE.db',
                        command='strategy_experiments', input_id='TEST_FIXTURE_input', run_id=None)
    else:
        original = dict(backend=backend, request_key='TEST_FIXTURE_old_outcome',
                        business=dict(command='r2-daily-run', database='TEST_FIXTURE.db', input_id='TEST_FIXTURE_input'))
    operation_id = workspace._digest(dict(scope=old.scope, identity=original))
    workspace._write(old.directory / 'operations' / (operation_id + '.json'),
        dict(schema_version=workspace.VERSION, scope=old.scope, operation_id=operation_id,
             identity=original, phase='FAILED_OR_UNKNOWN'))
    with pytest.raises(ValueError, match='ANCESTOR_REQUEST_RECONCILE_NO_RETRY'):
        runtime.run_once(dict(original, request_key='TEST_FIXTURE_changed_key'),
                         lambda: pytest.fail('duplicate ancestor business'), lambda receipt: True)
