"""TEST_FIXTURE-only coverage for cancellation and CLI status read scopes."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from stockfish_core import storage
from stockfish_core.jobs import JobCancelled, JobContext, JobService
from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu.short_horizon_daily_closure_v1 import ShortHorizonDailyClosure


@pytest.fixture
def environment(tmp_path):
    path = tmp_path / 'TEST_FIXTURE_STATUS.sqlite3'
    ordinary = storage.SQLiteStore(path)
    physical = path.stat()
    guarded = cli._ExistingWritableStore(path, expected_identity=(physical.st_dev, physical.st_ino))
    jobs = JobService(guarded, max_workers=1, max_pending=2, recover=False)
    service = SimpleNamespace(store=guarded, jobs=jobs)
    closure = ShortHorizonDailyClosure(service)
    job = jobs.create(closure.job_kind, {'TEST_FIXTURE': True}, job_id='TEST_FIXTURE_STATUS')
    with ordinary.transaction() as writer:
        writer.execute("UPDATE jobs SET state='RUNNING',owner_id=? WHERE job_id=?",
                       (jobs.owner_id, job['job_id']))
    job = jobs.get(job['job_id'])
    try:
        yield SimpleNamespace(ordinary=ordinary, store=guarded, jobs=jobs,
                              service=service, closure=closure, job=job)
    finally:
        jobs.shutdown(wait=True, cancel_pending=False)


def scope_for(store):
    return getattr(storage._READ_CONNECTION_SCOPES, 'values', {}).get(store)


def assert_idle(store):
    scope = scope_for(store)
    assert scope is not None and scope.active_depth == 0 and not scope.in_use
    assert scope.connection is None or not scope.connection.in_transaction
    return scope


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute('SELECT 1')


def watch_connections(monkeypatch, store):
    original, connections, statements = store._connect, [], []
    def connect():
        connection = original()
        connections.append(connection)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(store, '_connect', connect)
    return connections, statements


def install_clock(monkeypatch, sleep):
    clock = SimpleNamespace(now=0.0)
    def advance(seconds):
        assert 0 <= seconds <= .1
        sleep(seconds)
        clock.now += seconds
    monkeypatch.setattr(cli, 'time', SimpleNamespace(monotonic=lambda: clock.now, sleep=advance))
    return clock


def test_status_reuses_one_reader_but_keeps_all_three_fresh_validated_transactions(environment, monkeypatch):
    env = environment
    connections, statements = watch_connections(monkeypatch, env.store)
    counts, intercepted = Counter(), []
    original_hash, original_indexes = storage._hash_text, storage._verify_query_indexes
    def hashed(value):
        counts['migration_hash'] += 1
        return original_hash(value)
    def indexes(connection):
        counts['indexes'] += 1
        return original_indexes(connection)
    monkeypatch.setattr(storage, '_hash_text', hashed)
    monkeypatch.setattr(storage, '_verify_query_indexes', indexes)
    original_get = env.jobs.get
    def get(job_id):
        intercepted.append(job_id)
        return original_get(job_id)
    monkeypatch.setattr(env.jobs, 'get', get)
    def sleep(_):
        assert_idle(env.store)
        with env.ordinary.transaction() as writer:
            writer.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (env.job['job_id'],))
    install_clock(monkeypatch, sleep)
    code, result = cli._job_wait(env.service, env.closure, env.job, 1)
    assert code == 0 and result['job']['state'] == 'SUCCEEDED'
    assert intercepted == [env.job['job_id']] * 2
    assert len(connections) == 1
    assert counts == {'migration_hash': 12, 'indexes': 6}
    assert statements.count('BEGIN') == statements.count('COMMIT') == 6
    assert statements.count('SELECT version,checksum FROM schema_migrations') == 6
    assert statements.count('PRAGMA journal_mode') == 6
    assert sum(sql.startswith("SELECT sql FROM sqlite_master WHERE type='index'") for sql in statements) == 18
    assert sum(sql.startswith('SELECT * FROM jobs WHERE job_id=') for sql in statements) == 2
    assert sum(sql.startswith('SELECT * FROM documents WHERE namespace=') for sql in statements) == 2
    assert sum('FROM job_events' in sql for sql in statements) == 2
    assert scope_for(env.store) is None
    assert_closed(connections[0])


def test_status_scope_deduplicates_same_store_but_not_distinct_instances(environment, monkeypatch):
    env = environment
    physical = env.store.path.stat()
    other = cli._ExistingWritableStore(env.store.path, expected_identity=(physical.st_dev, physical.st_ino))
    env.service.store = other
    left, _ = watch_connections(monkeypatch, env.store)
    right, _ = watch_connections(monkeypatch, other)
    def sleep(_):
        assert assert_idle(env.store) is not assert_idle(other)
        with env.ordinary.transaction() as writer:
            writer.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (env.job['job_id'],))
    install_clock(monkeypatch, sleep)
    code, _ = cli._job_wait(env.service, env.closure, env.job, 1)
    assert code == 0 and len(left) == len(right) == 1 and left[0] is not right[0]
    assert scope_for(env.store) is scope_for(other) is None
    assert_closed(left[0])
    assert_closed(right[0])


def test_status_observes_new_ledger_and_events_after_idle_sleep(environment, monkeypatch):
    env = environment
    ledger = {'TEST_FIXTURE': True, 'revision_id': 'TEST_FIXTURE_REVISION'}
    before_events = env.jobs.events(env.job['job_id'])
    def sleep(_):
        assert_idle(env.store)
        with env.ordinary.transaction() as writer:
            env.ordinary._put_document(writer, env.closure.ledgers, env.job['job_id'], ledger)
            writer.execute("UPDATE jobs SET state='SUCCEEDED' WHERE job_id=?", (env.job['job_id'],))
            env.jobs._event(writer, env.job['job_id'], 'SUCCEEDED', 1.0, {'TEST_FIXTURE': True})
    install_clock(monkeypatch, sleep)
    code, result = cli._job_wait(env.service, env.closure, env.job, 1)
    assert code == 0 and result['ledger'] == ledger
    assert result['events'][:-1] == before_events
    assert result['events'][-1]['state'] == 'SUCCEEDED'
    assert result['events'][-1]['detail'] == {'TEST_FIXTURE': True}


def test_main_status_and_worker_cancel_readers_remain_in_different_threads(environment, monkeypatch):
    env = environment
    ready, release = threading.Event(), threading.Event()
    workers = []
    def worker():
        context = JobContext(env.jobs, env.job['job_id'])
        with env.store.read_connection_scope() as scope:
            context._cancellation_read_scope = scope
            try:
                context.check_cancelled()
                workers.append((scope, scope.connection, threading.get_ident()))
                ready.set()
                assert release.wait(timeout=5)
                with pytest.raises(JobCancelled):
                    context.check_cancelled()
            finally:
                context._cancellation_read_scope = None
    def sleep(_):
        scope = assert_idle(env.store)
        assert scope is not workers[0][0]
        assert scope.connection is not workers[0][1]
        assert threading.get_ident() != workers[0][2]
        with env.ordinary.transaction() as writer:
            writer.execute("UPDATE jobs SET state='CANCELLED',cancel_requested=1 WHERE job_id=?", (env.job['job_id'],))
        release.set()
    install_clock(monkeypatch, sleep)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        try:
            assert ready.wait(timeout=5)
            code, result = cli._job_wait(env.service, env.closure, env.job, 1)
            assert code == 3 and result['job']['cancel_requested'] is True
        finally:
            release.set()
        future.result(timeout=5)
    assert scope_for(env.store) is None and workers[0][0].closed


@pytest.mark.parametrize('owned', [True, False])
def test_timeout_cancels_only_owned_job_outside_read_activation(environment, monkeypatch, owned):
    env = environment
    job = dict(env.job, owner_id=env.jobs.owner_id if owned else 'TEST_FIXTURE_OTHER_OWNER')
    connections, _ = watch_connections(monkeypatch, env.store)
    cancelled = []
    original_cancel = env.closure.cancel
    def cancel(job_id):
        assert_idle(env.store)
        cancelled.append(job_id)
        return original_cancel(job_id)
    monkeypatch.setattr(env.closure, 'cancel', cancel)
    def sleep(_):
        assert_idle(env.store)
        clock.now = 2
    clock = install_clock(monkeypatch, sleep)
    code, result = cli._job_wait(env.service, env.closure, job, 1)
    assert code == 4 and result['status'] == 'WAIT_BUDGET_EXCEEDED'
    assert result['cancel_requested'] is owned
    assert cancelled == ([job['job_id']] if owned else [])
    assert result['result']['job']['cancel_requested'] is owned
    assert scope_for(env.store) is None
    for connection in connections:
        assert_closed(connection)


def test_keyboard_interrupt_preserves_exception_and_cancels_outside_activation(environment, monkeypatch):
    env = environment
    failure = KeyboardInterrupt('TEST_FIXTURE_INTERRUPT')
    connections, _ = watch_connections(monkeypatch, env.store)
    cancelled = []
    original_cancel = env.closure.cancel
    def cancel(job_id):
        assert_idle(env.store)
        cancelled.append(job_id)
        return original_cancel(job_id)
    monkeypatch.setattr(env.closure, 'cancel', cancel)
    def sleep(_):
        assert_idle(env.store)
        raise failure
    install_clock(monkeypatch, sleep)
    with pytest.raises(KeyboardInterrupt) as caught:
        cli._job_wait(env.service, env.closure, env.job, 1)
    assert caught.value is failure and cancelled == [env.job['job_id']]
    assert scope_for(env.store) is None
    for connection in connections:
        assert_closed(connection)


def test_status_exception_closes_reader_without_changing_original_exception(environment, monkeypatch):
    env = environment
    failure = RuntimeError('TEST_FIXTURE_STATUS_FAILURE')
    connections, _ = watch_connections(monkeypatch, env.store)
    original_status = env.closure.status
    def status(job_id):
        original_status(job_id)
        raise failure
    monkeypatch.setattr(env.closure, 'status', status)
    with pytest.raises(RuntimeError) as caught:
        cli._job_wait(env.service, env.closure, env.job, 1)
    assert caught.value is failure and scope_for(env.store) is None
    assert len(connections) == 1
    assert_closed(connections[0])


@pytest.mark.parametrize('corruption', ['checksum', 'unknown_migration', 'index'])
def test_next_status_revalidates_changed_database_schema(environment, monkeypatch, corruption):
    env = environment
    connections, _ = watch_connections(monkeypatch, env.store)
    def sleep(_):
        assert_idle(env.store)
        with env.ordinary.transaction() as writer:
            if corruption == 'checksum':
                writer.execute("UPDATE schema_migrations SET checksum='TEST_FIXTURE_BAD' WHERE version=1")
            elif corruption == 'unknown_migration':
                writer.execute("INSERT INTO schema_migrations VALUES(999,'TEST_FIXTURE_BAD','TEST_FIXTURE')")
            else:
                writer.execute('DROP INDEX documents_namespace_created')
    install_clock(monkeypatch, sleep)
    with pytest.raises((ValueError, storage.IntegrityError)):
        cli._job_wait(env.service, env.closure, env.job, 1)
    assert scope_for(env.store) is None
    assert_closed(connections[0])


def test_every_cancel_check_reads_settings_and_actual_state_then_restores_pollution(environment, monkeypatch):
    env = environment
    context = JobContext(env.jobs, env.job['job_id'])
    connections, statements = watch_connections(monkeypatch, env.store)
    names = ('foreign_keys', 'busy_timeout', 'synchronous', 'recursive_triggers', 'query_only')
    expected = (1, 15000, 2, 1, 1)
    with env.store.read_connection_scope() as scope:
        context._cancellation_read_scope = scope
        try:
            context.check_cancelled()
            statements.clear()
            context.check_cancelled()
            for name in names:
                assert statements.count('PRAGMA ' + name) == 1
                assert not any(sql.startswith('PRAGMA ' + name + '=') for sql in statements)
            assert sum(sql.startswith('SELECT * FROM jobs WHERE job_id=') for sql in statements) == 1
            reader = scope.connection
            reader.execute('PRAGMA query_only=OFF')
            for name, value in zip(names, (0, 1, 0, 0, 0)):
                reader.execute(f'PRAGMA {name}={value}')
            reader.row_factory = lambda *args: 'TEST_FIXTURE_POISON'
            reader.text_factory = bytes
            reader.isolation_level = 'DEFERRED'
            statements.clear()
            with env.ordinary.transaction() as writer:
                writer.execute('UPDATE jobs SET cancel_requested=1 WHERE job_id=?', (env.job['job_id'],))
            with pytest.raises(JobCancelled):
                context.check_cancelled()
            assert scope.connection is reader and not reader.in_transaction
            assert reader.row_factory is sqlite3.Row and reader.text_factory is str and reader.isolation_level is None
            for name, value in zip(names, expected):
                assert statements.count('PRAGMA ' + name) == 1
                assert statements.count(f'PRAGMA {name}={value}') == 1
                assert reader.execute('PRAGMA ' + name).fetchone()[0] == value
        finally:
            context._cancellation_read_scope = None
    assert len(connections) == 1
    assert_closed(connections[0])


@pytest.mark.parametrize('pollution', ['temp_jobs', 'attached_database'])
def test_status_pollution_discards_reader_and_observes_real_latest_state(environment, monkeypatch, tmp_path, pollution):
    env = environment
    connections, _ = watch_connections(monkeypatch, env.store)
    def sleep(_):
        scope = assert_idle(env.store)
        reader = scope.connection
        reader.execute('PRAGMA query_only=OFF')
        if pollution == 'temp_jobs':
            reader.execute('CREATE TEMP TABLE jobs(state TEXT,cancel_requested INTEGER)')
            reader.execute("INSERT INTO temp.jobs VALUES('SUCCEEDED',0)")
        else:
            attached = storage.SQLiteStore(tmp_path / 'TEST_FIXTURE_ATTACHED.sqlite3')
            reader.execute('ATTACH DATABASE ? AS test_fixture', (str(attached.path),))
        with env.ordinary.transaction() as writer:
            writer.execute("UPDATE jobs SET state='CANCELLED',cancel_requested=1 WHERE job_id=?", (env.job['job_id'],))
    install_clock(monkeypatch, sleep)
    code, result = cli._job_wait(env.service, env.closure, env.job, 1)
    assert code == 3 and result['job']['state'] == 'CANCELLED' and result['job']['cancel_requested'] is True
    assert len(connections) == 2 and connections[0] is not connections[1]
    for connection in connections:
        assert_closed(connection)


def test_second_store_scope_failure_cleans_first_scope(environment, monkeypatch):
    env = environment
    failure = RuntimeError('TEST_FIXTURE_SECOND_STORE_SCOPE')
    first = env.store
    physical = first.path.stat()
    other = cli._ExistingWritableStore(first.path, expected_identity=(physical.st_dev, physical.st_ino))
    env.jobs.store = other
    @contextmanager
    def rejected():
        raise failure
        yield
    monkeypatch.setattr(other, 'read_connection_scope', rejected)
    with pytest.raises(RuntimeError) as caught:
        cli._job_wait(env.service, env.closure, env.job, 1)
    assert caught.value is failure
    assert scope_for(first) is scope_for(other) is None
