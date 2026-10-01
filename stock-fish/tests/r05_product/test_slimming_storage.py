"""Behavioral acceptance on real temporary SQLite; TEST_FIXTURE only."""
from datetime import date, datetime
import sqlite3
from types import SimpleNamespace

import pytest

from stockfish_core.jobs import JobService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.daily_read_context import observation_read_context
from stockfish_core.yaogu.daily_observation import observation_report, _observation_report


@pytest.fixture
def service(tmp_path):
    store = SQLiteStore(tmp_path / 'TEST_FIXTURE.sqlite3')
    jobs = JobService(store)
    service = CandidateService.__new__(CandidateService)
    service.store, service.jobs = store, jobs
    service.scheduler = SimpleNamespace(sessions=[date(2026, 9, 21), date(2026, 9, 22)],
        clock=lambda: datetime.fromisoformat('2026-09-22T09:00:00+08:00'))
    yield service
    jobs.shutdown(wait=True)


def test_snapshot_is_consistent_across_concurrent_commit_and_released(service):
    ns = 'daily_primary_attempts'
    with observation_read_context(service) as view:
        service.store.put_document(ns, 'TEST_FIXTURE_late', {'kind': 'TEST_FIXTURE'})
        assert view._all(ns) == []
        assert view.store.get_document(ns, 'TEST_FIXTURE_late') is None
        with pytest.raises(RuntimeError, match='READ_ONLY'):
            view.store.put_document(ns, 'forbidden', {})
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            view.store.connection.execute('DELETE FROM documents')
    with observation_read_context(service) as view:
        assert view._all(ns) == [{'kind': 'TEST_FIXTURE'}]
    assert service.store.get_document(ns, 'TEST_FIXTURE_late') == {'kind': 'TEST_FIXTURE'}


@pytest.mark.parametrize('status', ['NOT_READY', 'FAILED'])
def test_batched_output_equals_original_single_day_machine(service, status):
    row = {'attempt_id': 'TEST_FIXTURE_attempt', 'daily_primary_mode': 'NORMAL_LATEST',
        'expected_observation_session': '2026-09-21', 'timestamp': '2026-09-21T20:35:00+08:00',
        'final_status': status, 'blocking_requirements': ['TEST_FIXTURE_MISSING']}
    service.store.put_document('daily_primary_attempts', row['attempt_id'], row)
    arguments = dict(target_session='2026-09-22', as_of=service.scheduler.clock())
    assert observation_report(service, **arguments) == _observation_report(service, **arguments)


def test_projection_recovery_late_insert_missing_header_and_atomic_interruption(service, monkeypatch):
    store = service.store
    source, projection = 'strategy_experiment_runs', 'strategy_experiment_summaries'
    def writer(connection, value):
        store._put_document(connection, projection, value['run_id'], value)
        if value.get('interrupt'):
            raise RuntimeError('TEST_FIXTURE interruption')
    monkeypatch.setattr(service, '_write_strategy_projection', writer)
    assert service.rebuild_strategy_projections() == {'rebuilt': 0}
    store.put_document(source, 'a', {'run_id': 'a'})
    store.put_document(source, 'b', {'run_id': 'b', 'interrupt': True})
    with pytest.raises(RuntimeError, match='interruption'):
        service.rebuild_strategy_projections()
    assert store.get_document(projection, 'a') == {'run_id': 'a'}
    assert store.get_document(projection, 'b') is None
    monkeypatch.setattr(service, '_write_strategy_projection',
        lambda c, v: store._put_document(c, projection, v['run_id'], v))
    assert service.rebuild_strategy_projections() == {'rebuilt': 1}
    assert service.rebuild_strategy_projections() == {'rebuilt': 0}
    # A late insert with an older timestamp cannot fall behind a migration cursor.
    monkeypatch.setattr('stockfish_core.storage.utc_now',
        lambda: datetime.fromisoformat('2000-01-01T00:00:00+00:00'))
    store.put_document(source, 'late', {'run_id': 'late'})
    with store.transaction() as c:
        # Explicit fault injection ONLY in this disposable TEST_FIXTURE database.
        trigger = c.execute("SELECT sql FROM sqlite_master WHERE name='documents_no_delete'").fetchone()[0]
        c.execute('DROP TRIGGER documents_no_delete')
        c.execute('DELETE FROM documents WHERE namespace=? AND document_id=?', (projection, 'a'))
        c.execute(trigger)
    assert service.rebuild_strategy_projections() == {'rebuilt': 2}
    assert service.rebuild_strategy_projections() == {'rebuilt': 0}


def test_missing_relationship_query_crosses_batch_boundary_without_cursor(service, monkeypatch):
    source, projection = 'strategy_experiment_runs', 'strategy_experiment_summaries'
    with service.store.transaction() as c:
        for i in range(1001):
            key = 'TEST_FIXTURE_' + str(i)
            service.store._put_document(c, source, key, {'run_id': key})
    monkeypatch.setattr(service, '_write_strategy_projection',
        lambda c, v: service.store._put_document(c, projection, v['run_id'], v))
    assert service.rebuild_strategy_projections() == {'rebuilt': 1001}
    assert service.rebuild_strategy_projections() == {'rebuilt': 0}


@pytest.mark.parametrize('corruption', ['hash', 'shape'])
def test_existing_projection_corruption_still_blocks_startup(service, corruption):
    from stockfish_core.storage import IntegrityError
    store = service.store
    for ns in ('strategy_experiment_runs', 'strategy_experiment_summaries'):
        store.put_document(ns, 'TEST_FIXTURE_saved', {'run_id': 'TEST_FIXTURE_saved'})
    with store.transaction() as c:
        # Fault injection in the disposable DB, restoring its exact trigger.
        trigger = c.execute("SELECT sql FROM sqlite_master WHERE name='documents_no_update'").fetchone()[0]
        c.execute('DROP TRIGGER documents_no_update')
        if corruption == 'hash':
            c.execute("UPDATE documents SET payload='{}' WHERE namespace='strategy_experiment_summaries'")
        else:
            from hashlib import sha256
            c.execute("UPDATE documents SET payload='[]', content_hash=? WHERE namespace='strategy_experiment_summaries'",
                      (sha256(b'[]').hexdigest(),))
        c.execute(trigger)
    with pytest.raises(IntegrityError, match='hash mismatch' if corruption == 'hash' else 'not an object'):
        service.rebuild_strategy_projections()
