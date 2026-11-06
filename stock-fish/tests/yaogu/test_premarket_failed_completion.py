"""TEST_FIXTURE failures retain their cause without obtaining success authority."""
import json
from pathlib import Path
import socket
import sqlite3
import sys

import pytest

from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu import premarket_workflow_v1 as workflow


CORE_NAMES = ('canonical-run.json.gz', 'input.json.gz', 'observation.json',
              'observation.csv', 'all.csv', 'focus.csv', 'unselected.csv', 'manifest.json')


def _run(tmp_path, monkeypatch, status, code):
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_OR_DATABASE_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    config = workflow.load_config(overrides=dict(state_root=str(tmp_path/'TEST_FIXTURE-state'),
        capture=dict(offline=True, allow_test_fixture=True), limits=dict(max_seconds=30)))
    error = dict(type='ValueError' if status != 'BUDGET_EXCEEDED' else 'BudgetExceeded',
        reason='ARCHIVED_REFERENCE_CHANGED' if status != 'BUDGET_EXCEEDED' else 'READ_LIMIT_EXCEEDED:capture',
        reference_path=str(tmp_path/'TEST_FIXTURE-mutated-source.py'))
    script = '\n'.join([
        'import hashlib,json,pathlib',
        'job=next(pathlib.Path('+repr(str(tmp_path/'TEST_FIXTURE-state/jobs'))+').iterdir())',
        'def save(name):',
        ' path=job/name; path.parent.mkdir(parents=True,exist_ok=True)',
        ' raw=b"TEST_FIXTURE partial derivative"; path.write_bytes(raw)',
        ' return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),fp={})',
        'core={name:save("core/"+name) for name in '+repr(CORE_NAMES)+'}',
        'display=dict(artifacts={name:save("display/"+name) for name in ("premarket.json","premarket.csv","premarket.txt")})',
        'completion=dict(schema_version='+repr(shared.VERSION)+',status='+repr(status)+',',
        ' core_verified=True,core_artifacts=core,display=display,error='+repr(error)+')',
        '(job/"worker-completion.json").write_text(json.dumps(completion))',
        'raise SystemExit('+str(code)+')',
    ])
    result_code, result = shared.supervise(config, progress=False,
        _fixture_worker_command=[sys.executable, '-I', '-B', '-c', script])
    return result_code, result, error


@pytest.mark.parametrize('status,code', [('FAILED',3), ('BUDGET_EXCEEDED',4)])
def test_failed_worker_with_partial_display_preserves_error_and_sealed_completion(tmp_path, monkeypatch, status, code):
    actual, result, error = _run(tmp_path, monkeypatch, status, code)
    assert actual == code
    assert result['status'] == status
    assert result['error'] == error
    assert result['external_worker_exit_code'] == code
    assert result['external_worker_reaped'] is True
    assert result['display'] is None and result['core_verified'] is False
    assert result['worker_core_verified'] is True
    assert result['source_terminal_binding_verified'] is False
    assert result['implementation_epoch_verified'] is False
    assert result['partial_artifacts_retained'] is True
    workflow._verify(result['completion'])
    completion = json.loads(Path(result['completion']['path']).read_text())
    assert completion['error'] == error and completion['display']
    assert 'validation_references' not in completion
    assert set(completion['core_artifacts']) == set(CORE_NAMES)
    assert shared.verify_job(result['job'])['status'] == status


@pytest.mark.parametrize('status,code', [('COMPLETE',0), ('COMPLETED_WITH_UNKNOWNS',2)])
def test_success_with_identical_missing_bindings_still_refused(tmp_path, monkeypatch, status, code):
    actual, result, _ = _run(tmp_path, monkeypatch, status, code)
    assert actual == 3 and result['status'] == 'FAILED'
    assert result['error']['reason'] == 'SUCCESS_REQUIRES_FULL_CORE_AND_ORIGINAL_REFERENCE_BINDINGS'
    assert result['display'] is None


def test_content_change_rejection_keeps_original_code_and_identifies_path(tmp_path):
    source = tmp_path/'TEST_FIXTURE-source.py'
    source.write_text('TEST_FIXTURE before')
    reference = workflow._ref(source)
    source.write_text('TEST_FIXTURE after!')
    with pytest.raises(ValueError, match='^ARCHIVED_REFERENCE_CHANGED$') as caught:
        workflow._verify(reference)
    assert workflow._failure_detail(caught.value) == dict(type='ValueError',
        reason='ARCHIVED_REFERENCE_CHANGED', reference_path=str(source))
