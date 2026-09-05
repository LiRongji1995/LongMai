import json
import threading
import time
from pathlib import Path

import pytest

from stockfish_core.api import create_app
from stockfish_core.experiments import TrainRequest
from tests.integration.core_fixture import fixture_service


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv('STOCKFISH_API_KEY', raising=False)
    core, _, model = fixture_service(tmp_path)
    request = TrainRequest.model_validate_json((Path(model.artifact_path).parent / 'request.json').read_text())
    client = create_app(core).test_client()
    yield client, core, model, request
    core.close()


def wait_job(core, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = core.jobs.get(job_id)
        if job['state'] in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            return job
        time.sleep(.01)
    raise AssertionError('bounded fixture job did not finish')


def test_finetune_requires_strict_envelope_and_actual_base(api):
    client, core, model, training = api
    assert client.post('/api/qlib/finetune', json=training.model_dump(mode='json')).status_code == 400
    payload = {'training': training.model_dump(mode='json'), 'base_model_id': model.model_id,
               'base_model_version': model.model_version}
    assert client.post('/api/qlib/finetune', json=payload | {'warm_start': True}).status_code == 400
    assert client.post('/api/qlib/finetune', json=payload | {'base_model_id': '../unsafe'}).status_code == 400
    response = client.post('/api/qlib/finetune', json=payload)
    assert response.status_code == 202, response.text
    job = wait_job(core, response.json['task_id'])
    assert job['state'] == 'SUCCEEDED', job
    assert job['kind'] == 'finetune'
    result = job['result']
    assert result['method'] == 'FULL_CLEAN_WINDOW_REFIT' and not result['warm_start']
    saved = core.store.get_document('training_runs', result['run_id'])
    assert saved == result['training']
    refit = json.loads((Path(saved['artifact_path']) / 'refit_provenance.json').read_text())
    assert refit['base_model_version'] == model.model_version
    assert not refit['warm_start']


def test_cancel_wins_without_publishing_training_record(api, monkeypatch):
    client, core, model, training = api
    computed, release = threading.Event(), threading.Event()
    original = core.quant.train
    artifact = {}
    def blocked(request):
        result = original(request)
        artifact['run_id'] = result.run_id
        computed.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(core.quant, 'train', blocked)
    response = client.post('/api/qlib/train', json=training.model_dump(mode='json'))
    assert response.status_code == 202
    assert computed.wait(3)
    job_id = response.json['task_id']
    core.jobs.cancel(job_id)
    release.set()
    job = wait_job(core, job_id)
    assert job['state'] == 'CANCELLED'
    assert job['result'] is None
    assert core.store.get_document('training_runs', artifact['run_id']) is None
