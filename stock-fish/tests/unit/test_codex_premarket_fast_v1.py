"""TEST_FIXTURE full-content encoding and complete original-workflow guards."""
from datetime import date
from decimal import Decimal
from functools import partial
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType
from unittest.mock import patch

import pytest

from scripts import codex_premarket_fast_v1 as fast
from stockfish_core.yaogu import history_artifacts as history
from stockfish_core.yaogu import premarket_native_core_v1 as native
from stockfish_core.yaogu import research_context_artifacts as context
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from tests.yaogu.test_premarket_shared_workflow_v1 import _execute, _read
from tests.yaogu import test_premarket_workflow_v1 as fixture_module
from tests.yaogu.test_theme_evolution_r1 import fixture_market


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_AND_DATABASE_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)


@pytest.fixture
def fixture():
    value = fixture_module.WorkflowFixtureTests(methodName='runTest')
    value.setUp()
    try:
        yield value
    finally:
        value.doCleanups()


def _budget():
    return Budget(dict(max_seconds=180, capture_read_bytes=256*1024**2,
                       self_rss_bytes=2*1024**3))


@pytest.mark.parametrize('profile', fast.PROFILES)
def test_complete_json_vectors_and_late_nested_changes(profile):
    reference = history.digest_json_stream
    vectors = [None, True, False, [], {}, (), 0, -0.0, 2**80, 1e-7,
        {'中文': '题材\U0001f422', 'escape': '\n\t"\\', 'decimal': Decimal('12.30'),
         'date': date(2026, 9, 30), 'nested': [{'empty': [], 'minus': -0.0}, None, 10**50]},
        {1: 'integer key', 2: ['tuple', (1, 2)]}]
    expected = [reference(value) for value in vectors]
    mutable = {'nested': [{'parent': {'first_seen_at': 'TEST_FIXTURE'}}]}
    before = reference(mutable)
    with fast.execution_scope(profile, _budget()) as metrics:
        assert [history.digest_json_stream(value) for value in vectors] == expected
        assert history.digest_json_stream(mutable) == before
        mutable['nested'][0]['parent']['first_seen_at'] = 'TEST_FIXTURE_LATE'
        assert history.digest_json_stream(mutable) == reference(mutable) != before
    assert history.digest_json_stream is reference
    assert metrics['bindings_restored'] is True
    assert metrics['calls']['digest_json_stream']['count'] == len(vectors) + 2
    assert metrics['hash_policy'] == 'FULL_CANONICAL_CONTENT_EVERY_CALL_NO_MEMOIZATION'


@pytest.mark.parametrize('profile', fast.PROFILES)
@pytest.mark.parametrize('kind', ['nan', 'infinity', 'surrogate', 'cycle', 'mixed_keys'])
def test_invalid_vectors_keep_reference_exception_type(profile, kind):
    vector = {'nan': {'x': float('nan')}, 'infinity': [float('inf')],
              'surrogate': {'x': '\ud800'}, 'cycle': [], 'mixed_keys': {'a': 1, 1: 2}}[kind]
    if kind == 'cycle':
        vector.append(vector)
    reference = history.digest_json_stream
    with pytest.raises(Exception) as expected:
        reference(vector)
    with fast.execution_scope(profile, _budget()) as metrics:
        with pytest.raises(type(expected.value)):
            history.digest_json_stream(vector)
    assert metrics['calls']['digest_json_stream']['failures'] == 1
    assert history.digest_json_stream is reference


@pytest.mark.parametrize('profile', fast.PROFILES)
@pytest.mark.parametrize('container', ['tuple', 'list_fallback'])
def test_full_typed_input_and_reference_fallback(profile, container):
    bundle, _, _ = fixture_market(5)
    if container == 'list_fallback':
        bundle = bundle.model_copy(update={'bars': list(bundle.bars)})
    reference = history.digest_market_input
    expected = reference(bundle)
    with fast.execution_scope(profile, _budget()) as metrics:
        assert history.digest_market_input(bundle) == expected
        changed = bundle.model_copy(update={'bars': bundle.bars[:-1]})
        assert history.digest_market_input(changed) == reference(changed) != expected
    assert history.digest_market_input is reference
    assert metrics['calls']['digest_market_input']['count'] == 2


def test_nested_scope_and_exception_restore_every_original_binding():
    references = (history.digest_json_stream, history.digest_market_input,
                  native.digest_json_stream, context.digest_market_input)
    with pytest.raises(RuntimeError, match='TEST_FIXTURE_PRIMARY'):
        with fast.execution_scope('canonical', _budget()) as metrics:
            with pytest.raises(ValueError, match='NESTED_ENCODING_SCOPE_FORBIDDEN'):
                with fast.execution_scope('reference', _budget()):
                    pass
            raise RuntimeError('TEST_FIXTURE_PRIMARY')
    assert references == (history.digest_json_stream, history.digest_market_input,
                          native.digest_json_stream, context.digest_market_input)
    assert metrics['bindings_restored'] and fast._ACTIVE is False


@pytest.mark.parametrize('module,name', [(native, 'digest_json_stream'), (context, 'digest_market_input')])
def test_changed_import_binding_is_rejected_before_scope(module, name):
    reference = history.digest_json_stream
    with patch.object(module, name, lambda value: 'TEST_FIXTURE_FORGED'):
        with pytest.raises(ValueError, match='ENCODER_BINDING_CHANGED'):
            with fast.execution_scope('canonical', _budget()):
                pass
    assert history.digest_json_stream is reference and fast._ACTIVE is False


@pytest.mark.parametrize('prefix', ['stockfish_core.yaogu.', 'scripts.'])
def test_binding_tamper_and_dynamic_import_alias_are_restored(prefix):
    reference = history.digest_json_stream
    name = prefix + 'TEST_FIXTURE_dynamic_alias'
    module = ModuleType(name)
    try:
        with pytest.raises(ValueError, match='BINDING_CHANGED_DURING_EXECUTION'):
            with fast.execution_scope('canonical', _budget()):
                module.digest_json_stream = history.digest_json_stream
                sys.modules[name] = module
                history.digest_json_stream = lambda value: 'TEST_FIXTURE_FORGED'
        assert history.digest_json_stream is module.digest_json_stream is reference
    finally:
        sys.modules.pop(name, None)


@pytest.mark.parametrize('outcome', ['normal', 'exception', 'budget'])
def test_real_lazy_shared_imports_restore_after_scope_exit(tmp_path, outcome):
    # A fresh interpreter guarantees both real scripts are first imported while
    # the scope is active, regardless of this pytest process's import order.
    source = r'''
from pathlib import Path
import runpy
import sys
root, outcome = Path(sys.argv[1]), sys.argv[2]
runpy.run_path(str(root/'scripts/bootstrap_daily_runtime.py'))['installed_identity']()
sys.path.insert(0, str(root))
from scripts import codex_premarket_fast_v1 as fast
from stockfish_core.yaogu import history_artifacts as history
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
names = ('digest_json_stream', 'digest_market_input')
references = {name: getattr(history, name) for name in names}
assert 'scripts.codex_native_theme_shared_v1' not in sys.modules
assert 'scripts.codex_daily_session_v1' not in sys.modules
budget = Budget(dict(max_seconds=180, capture_read_bytes=256*1024**2))
try:
    with fast.execution_scope('canonical', budget) as metrics:
        from scripts import codex_native_theme_shared_v1 as theme
        from scripts import codex_daily_session_v1 as pipeline
        assert all(getattr(module, name) is getattr(history, name)
                   and getattr(module, name) is not references[name]
                   for module in (theme, pipeline) for name in names)
        if outcome == 'exception':
            raise RuntimeError('TEST_FIXTURE_PRIMARY')
        if outcome == 'budget':
            budget.started -= 181
except RuntimeError as error:
    if outcome == 'exception':
        assert str(error) == 'TEST_FIXTURE_PRIMARY'
    else:
        assert outcome == 'budget' and isinstance(error, BudgetExceeded)
        assert 'TOTAL_TIME_LIMIT' in str(error)
else:
    assert outcome == 'normal'
assert metrics['bindings_restored'] and fast._ACTIVE is False
assert all(getattr(module, name) is references[name]
           for module in (history, theme, pipeline) for name in names)
# Loaded script aliases now participate in binding and terminal checks too.
second = Budget(dict(max_seconds=180, capture_read_bytes=256*1024**2))
try:
    with fast.execution_scope('canonical', second):
        assert theme.digest_json_stream is history.digest_json_stream
        theme.digest_json_stream = lambda value: 'TEST_FIXTURE_FORGED'
except ValueError as error:
    assert 'ENCODER_BINDING_CHANGED_DURING_EXECUTION' in str(error)
else:
    raise AssertionError('TEST_FIXTURE_SCRIPT_TAMPER_NOT_REJECTED')
assert all(getattr(module, name) is references[name]
           for module in (history, theme, pipeline) for name in names)
# No subsequent script call may consult either invocation's expired budget.
budget.started -= 181
second.started -= 181
assert theme.source_identity()['implementation_version'] == theme.VERSION
assert pipeline.digest_json_stream({'TEST_FIXTURE': True}) == references['digest_json_stream']({'TEST_FIXTURE': True})
print('TEST_FIXTURE_REAL_SCRIPT_BINDINGS_RESTORED')
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-X',
        'pycache_prefix=' + str(tmp_path/'TEST_FIXTURE_absent_bytecode'), '-c', source,
        str(fast.ROOT), outcome], cwd=fast.ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'TEST_FIXTURE_REAL_SCRIPT_BINDINGS_RESTORED'


def test_another_live_thread_and_nonmain_caller_are_rejected():
    release = threading.Event()
    thread = threading.Thread(target=lambda: release.wait(10))
    thread.start()
    try:
        with pytest.raises(ValueError, match='SINGLE_THREAD_PROCESS_REQUIRED'):
            with fast.execution_scope('canonical', _budget()):
                pass
    finally:
        release.set(); thread.join()
    errors = []
    def enter():
        try:
            with fast.execution_scope('canonical', _budget()):
                pass
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=enter)
    thread.start(); thread.join()
    assert len(errors) == 1 and 'SINGLE_THREAD_PROCESS_REQUIRED' in str(errors[0])
    assert fast._ACTIVE is False


def test_mid_scope_thread_is_rejected_and_bindings_restore():
    reference = history.digest_json_stream
    release, thread = threading.Event(), None
    try:
        with pytest.raises(ValueError, match='SINGLE_THREAD_PROCESS_REQUIRED'):
            with fast.execution_scope('canonical', _budget()):
                thread = threading.Thread(target=lambda: release.wait(10))
                thread.start()
                history.digest_json_stream({'TEST_FIXTURE': True})
    finally:
        release.set()
        if thread is not None:
            thread.join()
    assert history.digest_json_stream is reference and fast._ACTIVE is False


def test_owned_scope_source_same_bytes_new_inode_is_rejected(tmp_path):
    path = tmp_path/'TEST_FIXTURE_source.py'
    path.write_text('TEST_FIXTURE')
    budget = _budget()
    reference = budget.read(path)[1]
    old = history.digest_json_stream
    with patch.object(fast, '_scope_sources', return_value=[reference]):
        with pytest.raises(BudgetExceeded, match='CHANGED|MISMATCH'):
            with fast.execution_scope('canonical', budget):
                replacement = path.with_suffix('.TEST_FIXTURE_new')
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
    assert history.digest_json_stream is old and fast._ACTIVE is False


def test_encoder_primary_failure_is_not_replaced_by_exit_budget_failure():
    budget = _budget()
    check = budget.check
    primary = ValueError('TEST_FIXTURE_ENCODING_PRIMARY')
    def fail(value, **kwargs):
        budget.check = lambda *args, **kw: (_ for _ in ()).throw(BudgetExceeded('TEST_FIXTURE_EXIT_BUDGET'))
        raise primary
    canonical = fast.first._load()['short_horizon_archive_bridge_v1']
    reference = history.digest_json_stream
    with patch.object(canonical, 'digest_json_stream', fail):
        with pytest.raises(ValueError, match='TEST_FIXTURE_ENCODING_PRIMARY') as caught:
            with fast.execution_scope('canonical', budget):
                history.digest_json_stream({'TEST_FIXTURE': True})
    budget.check = check
    assert caught.value is primary
    assert any('TEST_FIXTURE_EXIT_BUDGET' in note for note in primary.__notes__)
    assert history.digest_json_stream is reference


def test_full_fixture_original_reference_and_canonical_keep_all_results(fixture):
    adapters, now = fixture._adapters()
    reference = history.digest_json_stream
    plain, _ = _execute(fixture, fast.original.execute_workflow, 'plain', adapters, now)
    measured, _ = _execute(fixture, partial(fast.execute_workflow_fast, profile='reference'), 'reference', adapters, now)
    actual, _ = _execute(fixture, fast.execute_workflow_fast, 'canonical', adapters, now)
    assert actual['status'] == measured['status'] == plain['status'] == 'COMPLETED_WITH_UNKNOWNS'
    assert actual['schema_version'] == measured['schema_version'] == fast.VERSION
    assert actual['phases'] == measured['phases'] == plain['phases']
    assert actual['run_id'] == measured['run_id'] == plain['run_id']
    assert actual['display']['counts'] == measured['display']['counts'] == plain['display']['counts']
    for role in ('all.csv', 'observation.csv', 'focus.csv', 'unselected.csv', 'observation.json'):
        expected = Path(plain['core_artifacts'][role]['path']).read_bytes()
        assert expected == Path(measured['core_artifacts'][role]['path']).read_bytes()
        assert expected == Path(actual['core_artifacts'][role]['path']).read_bytes()
    parents = []
    journal_bytes = []
    for result in (plain, measured, actual):
        path = Path(result['theme_artifacts']['native-theme-parent.json.gz']['path'])
        parent = _read(path)
        declared_hash = parent.pop('context_content_hash')
        assert history.digest_json_stream(parent) == declared_hash
        rows = {}
        for name, ref in parent['execution_provenance']['audit_manifest']['files'].items():
            journal = path.parent/'price-journal'/name
            assert fast.original.fingerprint(journal) == ref.pop('fp')
            rows[name] = journal.read_bytes()
            assert len(rows[name]) == ref['bytes']
            assert hashlib.sha256(rows[name]).hexdigest() == ref['sha256']
        parents.append(parent)
        journal_bytes.append(rows)
    # New physical output files must have their own real 7FP. Verify each first;
    # then compare every remaining parent field and the complete audit bytes.
    assert parents[0] == parents[1] == parents[2]
    assert journal_bytes[0] == journal_bytes[1] == journal_bytes[2]
    assert actual['encoding_execution']['bindings_restored'] is True
    assert actual['encoding_execution']['calls']['digest_market_input']['count'] > 0
    assert actual['encoding_execution']['calls']['digest_json_stream']['count'] > 0
    assert actual['metrics']['calls']['requests'] == 0
    assert actual['metrics']['limits'] == fixture.config['limits']
    assert history.digest_json_stream is reference
    assert _read(fixture.root/'canonical/worker-completion.json') == json.loads(json.dumps(actual))


def test_exhausted_theme_budget_preserves_original_primary_and_core(fixture):
    adapters, now = fixture._adapters()
    fixture.config['limits']['theme_read_bytes'] = 1
    value, _ = _execute(fixture, fast.execute_workflow_fast, 'limited-theme', adapters, now)
    assert value['status'] == 'BUDGET_EXCEEDED' and value['core_verified'] is True
    assert value['display'] is None and 'READ_LIMIT_EXCEEDED:theme' in value['error']['reason']
    assert Path(value['core_artifacts']['canonical-run.json.gz']['path']).is_file()
    assert value['encoding_execution']['bindings_restored'] is True
    assert _read(fixture.root/'limited-theme/worker-completion.json') == json.loads(json.dumps(value))


def test_preflight_encoding_source_budget_failure_is_sealed(fixture):
    adapters, now = fixture._adapters()
    fixture.config['limits']['capture_read_bytes'] = 1
    value, _ = _execute(fixture, fast.execute_workflow_fast, 'limited-capture', adapters, now)
    assert value['status'] == 'BUDGET_EXCEEDED' and value['core_verified'] is False
    assert value['display'] is None and 'READ_LIMIT_EXCEEDED:capture' in value['error']['reason']
    assert _read(fixture.root/'limited-capture/worker-completion.json') == value


def test_late_scope_terminal_failure_cannot_publish_success(fixture):
    adapters, now = fixture._adapters()
    original_check = Budget.check
    def check(self, stage=None, **kwargs):
        if stage == 'encoding_scope_terminal':
            raise ValueError('TEST_FIXTURE_SCOPE_FINAL_FAILURE')
        return original_check(self, stage, **kwargs)
    with patch.object(Budget, 'check', check):
        value, _ = _execute(fixture, fast.execute_workflow_fast, 'terminal-failed', adapters, now)
    assert value['status'] == 'FAILED' and value['display'] is None
    assert value['core_verified'] is True
    assert value['error']['reason'] == 'TEST_FIXTURE_SCOPE_FINAL_FAILURE'
    assert value['encoding_execution']['bindings_restored'] is True
    assert _read(fixture.root/'terminal-failed/worker-completion.json') == json.loads(json.dumps(value))


def test_new_guard_keeps_own_version_and_original_deadline(tmp_path):
    path = tmp_path/'TEST_FIXTURE-final.json'
    path.write_bytes(b'TEST_FIXTURE reserved')
    limits = {'max_seconds': 30, 'output_bytes': 1024**2}
    guard = fast._SupervisorGuard(limits)
    value = {'schema_version': fast.VERSION, 'status': 'COMPLETED_WITH_UNKNOWNS', 'TEST_FIXTURE': True}
    guard.seal(path, value)
    assert value['execution_version'] == fast.VERSION
    assert value['whole_workflow_wall_enforced'] is True
    guard.started -= 60
    with pytest.raises(BudgetExceeded, match='TOTAL_TIME_LIMIT'):
        guard.seal(path, value)


def test_real_owned_offline_worker_reports_failure_and_is_reaped(fixture):
    fixture.config['limits']['max_seconds'] = 30
    code, result = fast.supervise(fixture.config, progress=False)
    assert code == 3 and result['status'] == 'FAILED', result
    assert result['schema_version'] == fast.VERSION
    assert result['external_worker_exit_code'] == 3 and result['external_worker_reaped'] is True
    completion = _read(Path(result['job'])/'worker-completion.json')
    assert completion['schema_version'] == fast.VERSION
    assert completion['status'] == 'FAILED' and completion['core_verified'] is False
    assert completion['metrics']['calls']['requests'] == 0
    assert completion['encoding_execution']['bindings_restored'] is True
    assert fast.verify_job(result['job'])['source_authority_restored'] is False


def test_daily_default_and_production_fixture_escape_remain_closed(tmp_path):
    config = fast.original.load_config(overrides={'state_root': str(tmp_path/'state')})
    with patch.object(fast.os, 'getpgrp', return_value=os.getpid()+1):
        with pytest.raises(ValueError, match='ISOLATED_OWNED_WORKER_REQUIRED'):
            fast.execute_workflow_fast(config, tmp_path/'job', adapters={'core': lambda: None})
    with pytest.raises(ValueError, match='ADAPTERS_REQUIRE_EXPLICIT_TEST_FIXTURE'):
        fast.supervise(config, _fixture_worker_command=[sys.executable, '-c', 'pass'])
    with pytest.raises(ValueError, match='ORIGINAL_ENGINE_REQUIRED'):
        fast.supervise(config, engine='shared')
    with pytest.raises(SystemExit) as error:
        fast.main(['premarket', '--config', 'TEST_FIXTURE'])
    assert error.value.code == 2
    assert not (tmp_path/'state').exists() and not (tmp_path/'job').exists()
