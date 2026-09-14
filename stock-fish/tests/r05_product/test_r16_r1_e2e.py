"""TEST_FIXTURE: real Flask routes, real jobs and isolated SQLite transactions."""
from contextlib import contextmanager
from datetime import datetime
import threading
import time

import pytest

from stockfish_core.api import create_app
from stockfish_core.jobs import JobService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.e2e_tracing import DailyPrimaryTrace, TraceRegistry, current_trace
from tests.r05_product.test_r14_daily_fast_path import _service, _wait_job


def assert_measured(trace):
    root = trace.snapshot()
    spans = {row['span_id']: row for row in root['stages']}
    spans[root['span_id']] = root
    for row in root['stages']:
        if row['status'] in {'SKIPPED', 'NOT_MEASURED'}:
            assert row['start'] is row['end'] is row['elapsed_ms'] is None
            continue
        assert row['status'] in {'SUCCESS', 'FAILED'}
        assert row['elapsed_ms'] >= 0
        assert datetime.fromisoformat(row['end']) >= datetime.fromisoformat(row['start'])
        parent = spans[row['parent_span_id']]
        assert parent['start_monotonic_seconds'] <= row['start_monotonic_seconds'] <= row['end_monotonic_seconds'] <= parent['end_monotonic_seconds']


def test_unmeasured_aggregate_and_context_isolation():
    trace = DailyPrimaryTrace(boundary='TEST_FIXTURE')
    trace.record_stage('PERSISTENCE', elapsed_ms=125, success=True)
    with trace.activate():
        with trace.stage('parent'):
            with trace.stage('child'):
                assert current_trace() is trace
            found = []
            thread = threading.Thread(target=lambda: found.append(current_trace()))
            thread.start(); thread.join()
            assert found == [None]
    assert current_trace() is None
    trace.finish()
    assert trace.snapshot()['stages'][0]['status'] == 'NOT_MEASURED'
    assert_measured(trace)
    with pytest.raises(RuntimeError, match='finished'):
        with trace.stage('too_late'):
            pass


def test_actual_api_persistence_serialization_and_repeated_exports(tmp_path, monkeypatch):
    core, input_id, _, _ = _service(tmp_path, monkeypatch)
    try:
        client = create_app(core).test_client()
        accepted = client.post('/api/daily-primary/run', json={'input_id': input_id})
        assert accepted.status_code == 202
        trace = core.candidates.daily_traces.get(accepted.headers['X-Daily-Trace-Id'])
        job = _wait_job(core, accepted.json['job_id'])
        assert job['state'] == 'SUCCEEDED', job
        # Job terminal commit can be visible before the trace continuation is bound.
        deadline = time.monotonic() + 5
        while trace.trace['status'] != 'READY_FOR_EXPORT' and time.monotonic() < deadline:
            time.sleep(.01)
        assert not any(row['name'] == 'RESULT_READY' for row in trace.snapshot()['stages'])
        run_id = job['result']['run_id']
        original = core.store.get_document_bytes('strategy_experiment_runs', run_id)
        response = client.get('/api/strategy-experiments/' + run_id + '/export')
        assert response.status_code == 200
        assert response.headers['X-Daily-Trace-Id'] == trace.trace_id
        assert core.candidates.daily_traces.report(trace)['e2e_verified']
        assert_measured(trace)
        rows = {row['name']: row for row in trace.snapshot()['stages']}
        assert rows['PERSISTENCE']['status'] == rows['RESULT_READY']['status'] == 'SUCCESS'
        assert rows['RESULT_READY']['bytes_written'] == len(response.data)
        frozen = trace.snapshot()
        again = client.get('/api/strategy-experiments/' + run_id + '/export')
        assert again.status_code == 200
        assert again.headers['X-Daily-Trace-Id'] != trace.trace_id
        assert trace.snapshot() == frozen
        assert core.store.get_document_bytes('strategy_experiment_runs', run_id) == original
        # Registry loss/restart cannot reconstruct an earlier monotonic clock.
        core.candidates.daily_traces = TraceRegistry(core.store)
        later = client.get('/api/strategy-experiments/' + run_id + '/export')
        partial = core.candidates.daily_traces.get(later.headers['X-Daily-Trace-Id'])
        assert partial.trace['status'] == 'PARTIAL'
        assert not core.candidates.daily_traces.report(partial)['e2e_verified']
    finally:
        core.close()


@pytest.mark.parametrize('failure', ['write', 'commit'])
def test_persistence_failure_never_publishes_ready(tmp_path, monkeypatch, failure):
    core, input_id, _, _ = _service(tmp_path, monkeypatch)
    try:
        if failure == 'write':
            def fail(*args, **kwargs):
                raise RuntimeError('TEST_FIXTURE_WRITE_FAILURE')
            monkeypatch.setattr(core.candidates, '_write_strategy_result', fail)
        else:
            original = core.store.transaction
            @contextmanager
            def transaction(*, write=True):
                with original(write=write) as con:
                    yield con
                    # Fail on exit after domain writes and job SUCCEEDED UPDATE,
                    # before the actual enclosing SQLite COMMIT. It must roll back.
                    if write and con.execute("SELECT 1 FROM documents WHERE namespace='strategy_experiment_runs'").fetchone():
                        raise RuntimeError('TEST_FIXTURE_COMMIT_FAILURE')
            monkeypatch.setattr(core.store, 'transaction', transaction)
        client = create_app(core).test_client()
        accepted = client.post('/api/daily-primary/run', json={'input_id': input_id})
        job = _wait_job(core, accepted.json['job_id'])
        assert job['state'] == 'FAILED'
        core.jobs.shutdown(cancel_pending=False)
        trace = core.candidates.daily_traces.get(accepted.headers['X-Daily-Trace-Id'])
        assert trace.trace['status'] == 'FAILED'
        assert not core.store.list_documents('strategy_experiment_runs')
        ready = [row for row in trace.snapshot()['stages'] if row['name'] == 'RESULT_READY']
        assert ready[0]['status'] == 'NOT_MEASURED'
        assert_measured(trace)
    finally:
        core.close()


def test_serialization_failure_and_telemetry_failure_are_distinct(tmp_path, monkeypatch):
    core, input_id, _, _ = _service(tmp_path, monkeypatch)
    try:
        client = create_app(core).test_client()
        accepted = client.post('/api/daily-primary/run', json={'input_id': input_id})
        job = _wait_job(core, accepted.json['job_id'])
        core.jobs.shutdown(cancel_pending=False)
        assert job['state'] == 'SUCCEEDED'
        run_id = job['result']['run_id']
        saved = core.store.get_document_bytes('strategy_experiment_runs', run_id)
        original = core.candidates.export_strategy_experiment_json
        monkeypatch.setattr(core.candidates, 'export_strategy_experiment_json',
            lambda *a, **k: (_ for _ in ()).throw(ValueError('TEST_FIXTURE_SERIALIZATION_FAILURE')))
        response = client.get('/api/strategy-experiments/' + run_id + '/export')
        assert response.status_code != 200
        trace = core.candidates.daily_traces.get(accepted.headers['X-Daily-Trace-Id'])
        assert trace.trace['status'] == 'FAILED'
        assert core.jobs.get(job['job_id'])['state'] == 'SUCCEEDED'
        assert core.store.get_document_bytes('strategy_experiment_runs', run_id) == saved
        monkeypatch.setattr(core.candidates, 'export_strategy_experiment_json', original)
        put = core.store.put_document
        def telemetry_fail(namespace, *args, **kwargs):
            if namespace == 'daily_primary_trace_snapshots':
                raise OSError('TEST_FIXTURE_TELEMETRY_DISK_FAILURE')
            return put(namespace, *args, **kwargs)
        monkeypatch.setattr(core.store, 'put_document', telemetry_fail)
        response = client.get('/api/strategy-experiments/' + run_id + '/export')
        assert response.status_code == 200
        assert response.headers['X-Trace-Persistence'] == 'TRACE_PERSIST_FAILED'
        later = core.candidates.daily_traces.get(response.headers['X-Daily-Trace-Id'])
        assert not core.candidates.daily_traces.report(later)['e2e_verified']
    finally:
        core.close()


def test_queued_cancellation_notifies_without_worker_and_retry_gets_new_trace(tmp_path):
    store = SQLiteStore(tmp_path / 'TEST_FIXTURE.sqlite3')
    registry = TraceRegistry(store)
    with JobService(store) as jobs:
        seen = []
        jobs.register('TEST_FIXTURE', lambda ctx: 1, on_terminal=lambda job: seen.append(job['state']))
        job = jobs.create('TEST_FIXTURE', {})
        jobs.cancel(job['job_id'])
        assert seen == ['CANCELLED']
        original = registry.add(DailyPrimaryTrace(boundary='TEST_FIXTURE'))
        first = registry.for_job({'job_id': 'first', 'payload': {'trace_id': original.trace_id}})
        first.finish(status='CANCELLED')
        second = registry.for_job({'job_id': 'retry', 'retry_of': 'first', 'payload': {'trace_id': original.trace_id}})
        assert second is not first
        assert second.trace['causal_trace_id'] == first.trace_id
