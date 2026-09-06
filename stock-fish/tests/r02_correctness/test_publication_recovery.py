"""Actual publication/restart/crash boundaries; isolated TEST_FIXTURE stores."""
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from stockfish_core.api import create_app
from stockfish_core.common import UnavailableError
from stockfish_core.jobs import JobService
from stockfish_core.model_registry import ModelRegistry
from stockfish_core.quant import QlibQuantEngine, QuantRequest
from stockfish_core.service import ApplicationService, RecommendationRequest
from stockfish_core.storage import SQLiteStore
from tests.quant.test_quant_boundary import training_request, t
from tests.r02_correctness.test_r01_regressions import wait_terminal


def test_success_then_cancel_repeat_preserves_prior_authority(tmp_path, monkeypatch):
    core = ApplicationService(tmp_path / 'core', store=SQLiteStore(tmp_path / 'db.sqlite3'))
    client = create_app(core).test_client()
    request = training_request().model_dump(mode='json')
    release = threading.Event()
    try:
        first = client.post('/api/qlib/train', json=request).get_json()['task_id']
        success = wait_terminal(core, first)
        assert success['state'] == 'SUCCEEDED', success
        model = core._model(RecommendationRequest())
        authority_before = core.store.get_document('model_publications', 'cpu/' + model.model_version)
        assert authority_before['job_id'] == first
        assert core.jobs.cancel(first)['state'] == 'SUCCEEDED'
        prepared = threading.Event()
        original = core.quant.train
        def paused(value):
            result = original(value)
            assert result.model == model
            prepared.set()
            assert release.wait(10)
            return result
        monkeypatch.setattr(core.quant, 'train', paused)
        second = client.post('/api/qlib/train', json=request).get_json()['task_id']
        assert prepared.wait(10)
        core.jobs.cancel(second)
        release.set()
        assert wait_terminal(core, second)['state'] == 'CANCELLED'
        assert core.registry.get('cpu', model.model_version) == model
        assert core.store.get_document('model_publications', 'cpu/' + model.model_version) == authority_before
        assert len(core.store.list_documents('training_runs')) == 1
    finally:
        release.set()
        core.close()
    reopened = ApplicationService(tmp_path / 'core', store=SQLiteStore(tmp_path / 'db.sqlite3'))
    try:
        assert reopened._model(RecommendationRequest()) == model
        assert reopened.jobs.get(first)['state'] == 'SUCCEEDED'
        assert reopened.jobs.get(second)['state'] == 'CANCELLED'
    finally:
        reopened.close()


@pytest.mark.parametrize('crash_after_commit', [False, True])
def test_real_process_crash_publication_has_atomic_authority(tmp_path, crash_after_commit):
    store = SQLiteStore(tmp_path / 'db.sqlite3')
    registry = ModelRegistry(tmp_path / 'registry', store=store)
    quant = QlibQuantEngine(registry, tmp_path / 'quant')
    with registry.pending_job('crash-training'):
        prepared = quant.train(training_request())
    assert registry.list() == ()
    result_path = Path(prepared.artifact_path) / 'result.json'
    script = '''import os,sys,time
from pathlib import Path
from stockfish_core.storage import SQLiteStore
from stockfish_core.jobs import JobService
from stockfish_core.model_registry import ModelRegistry
from stockfish_core.experiments import TrainingResult
root=Path(sys.argv[1]); after=sys.argv[3]=='True'
store=SQLiteStore(root/'db.sqlite3'); registry=ModelRegistry(root/'registry',store=store)
result=TrainingResult.model_validate_json(Path(sys.argv[2]).read_text())
jobs=JobService(store)
def worker(ctx):
    terminal={'status':'complete','run_id':result.run_id,'training':result.model_dump(mode='json')}
    def publish(connection):
        registry.publish_training(result,connection=connection,job_id=ctx.job_id)
        if not after: os._exit(19)
    ctx.complete(terminal,publish)
    os._exit(20)
jobs.submit('training',{'kind':'TEST_FIXTURE'},worker,job_id='crash-training')
time.sleep(8)
'''
    completed = subprocess.run([sys.executable, '-c', script, str(tmp_path), str(result_path), str(crash_after_commit)],
                               capture_output=True, timeout=12)
    assert completed.returncode == (20 if crash_after_commit else 19), completed.stderr.decode(errors='replace')
    with JobService(SQLiteStore(store.path)) as jobs:
        reopened = ModelRegistry(tmp_path / 'registry', store=jobs.store)
        assert jobs.get('crash-training')['state'] == ('SUCCEEDED' if crash_after_commit else 'INTERRUPTED')
        if crash_after_commit:
            assert reopened.get('cpu', prepared.model.model_version) == prepared.model
            assert len(jobs.store.list_documents('training_runs')) == 1
        else:
            assert reopened.list() == ()
            assert jobs.store.list_documents('training_runs') == []
            with pytest.raises(UnavailableError, match='MODEL_NOT_PUBLISHED'):
                reopened.get('cpu', prepared.model.model_version)
            assert reopened.get_prepared('cpu', prepared.model.model_version) == prepared.model


def test_prepared_model_cannot_be_explicitly_consumed_even_in_research(tmp_path):
    registry = ModelRegistry(tmp_path / 'registry')
    quant = QlibQuantEngine(registry, tmp_path / 'quant')
    with registry.pending_job('TEST_FIXTURE'):
        result = quant.train(training_request())
    request = QuantRequest(model_id='cpu', model_version=result.model.model_version, as_of=t(20),
        data_version='TEST_FIXTURE', universe_version='TEST_FIXTURE', policy_version='fixture', instruments=())
    with pytest.raises(UnavailableError, match='MODEL_NOT_PUBLISHED'):
        quant.rank(request, allow_research=True)
    assert registry.health()['quarantined_models'] == 1


def test_model_health_checks_prepared_bytes_and_publication_scope(tmp_path):
    registry = ModelRegistry(tmp_path / 'registry')
    quant = QlibQuantEngine(registry, tmp_path / 'quant')
    result = quant.train(training_request())
    healthy = registry.health()
    assert healthy['status'] == 'healthy'
    assert healthy['checked_at'] and 'publication_authority' in healthy['scope']
    Path(result.model.artifact_path).write_text('{}', encoding='utf-8')
    health = registry.health()
    assert health['status'] == 'unavailable'
    assert health['detail_code'] == 'MODEL_HASH_MISMATCH'
