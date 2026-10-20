"""Explicit TEST_FIXTURE coverage for worker-only read connection reuse."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import sqlite3
import threading
import time

import pytest

from stockfish_core import storage
from stockfish_core.jobs import JobCancelled, JobContext, JobService
from stockfish_core.storage import SQLiteStore, StorageError
from stockfish_core.yaogu.r2_closure_cli import _ExistingWritableStore


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / 'TEST_FIXTURE.sqlite3'
    ordinary = SQLiteStore(path)
    physical = path.stat()
    guarded = _ExistingWritableStore(path, expected_identity=(physical.st_dev, physical.st_ino))
    return ordinary, guarded


@contextmanager
def active_scope(store):
    with store.read_connection_scope() as scope:
        with scope.activate():
            yield scope


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute('SELECT 1')


def test_scope_reuses_only_idle_reads_and_keeps_all_transaction_validations(stores, monkeypatch):
    _, store = stores
    calls, statements = [], []
    original = storage._verify_query_indexes
    def indexes(connection):
        calls.append(connection)
        return original(connection)
    monkeypatch.setattr(storage, '_verify_query_indexes', indexes)
    with store.transaction(write=False) as outside:
        pass
    assert_closed(outside)
    calls.clear()
    with active_scope(store):
        for i in range(4):
            with store.transaction(write=False) as connection:
                connection.set_trace_callback(statements.append)
                assert connection.in_transaction
                assert connection.execute('PRAGMA query_only').fetchone()[0] == 1
                if i == 0:
                    first = connection
                assert connection is first
            assert not connection.in_transaction
        assert calls == [first] * 4
    assert_closed(first)
    assert sum(s == 'BEGIN' for s in statements) == 3  # first trace starts inside BEGIN
    assert sum(s == 'COMMIT' for s in statements) == 4
    assert sum('SELECT version,checksum FROM schema_migrations' in s for s in statements) == 3
    assert sum(s == 'PRAGMA journal_mode' for s in statements) == 3
    with store.transaction(write=False) as later:
        assert later is not first
    assert_closed(later)


@pytest.mark.parametrize('corruption', ['checksum', 'unknown_migration', 'dropped_index'])
def test_scope_does_not_cache_schema_or_index_acceptance(stores, corruption):
    ordinary, store = stores
    with active_scope(store):
        with store.transaction(write=False) as first:
            pass
        with ordinary.transaction() as writer:
            if corruption == 'checksum':
                writer.execute("UPDATE schema_migrations SET checksum='TEST_FIXTURE_BAD' WHERE version=1")
            elif corruption == 'unknown_migration':
                writer.execute("INSERT INTO schema_migrations VALUES(999,'TEST_FIXTURE_BAD','TEST_FIXTURE')")
            else:
                name = writer.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL LIMIT 1").fetchone()[0]
                # The V2 contract indexes, rather than an unrelated index.
                name = next(row[0] for row in writer.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")
                            if 'documents' in row[0] and ('run' in row[0] or 'created' in row[0]))
                writer.execute('DROP INDEX "' + name.replace('"', '""') + '"')
        with pytest.raises((StorageError, ValueError)):
            with store.transaction(write=False):
                pytest.fail('CORRUPT_SCHEMA_WAS_ACCEPTED')
        assert_closed(first)


def test_next_checkpoint_observes_external_cancellation_without_reopening(stores):
    ordinary, store = stores
    jobs = JobService(store, max_workers=1, max_pending=2, recover=False)
    try:
        job = jobs.create('TEST_FIXTURE', {'TEST_FIXTURE': True}, job_id='TEST_FIXTURE_CANCEL')
        context = JobContext(jobs, job['job_id'])
        with active_scope(store):
            context.check_cancelled()
            with store.transaction(write=False) as first:
                pass
            with ordinary.transaction() as writer:
                writer.execute('UPDATE jobs SET cancel_requested=1 WHERE job_id=?', (job['job_id'],))
            with pytest.raises(JobCancelled):
                context.check_cancelled()
            # Cancellation is raised after the completed read transaction, so
            # it does not require destroying its healthy connection.
            with ordinary.transaction() as writer:
                writer.execute('UPDATE jobs SET cancel_requested=0 WHERE job_id=?', (job['job_id'],))
            context.check_cancelled()
            with store.transaction(write=False) as again:
                assert again is first
    finally:
        jobs.shutdown(wait=True, cancel_pending=False)


def test_nested_reads_and_writes_keep_committed_snapshot_boundaries(stores):
    _, store = stores
    with active_scope(store):
        with store.transaction(write=False) as outer:
            with store.transaction(write=False) as inner:
                assert inner is not outer
            assert_closed(inner)
            assert outer.in_transaction
        with store.transaction() as writer:
            assert writer is not outer
            store._put_document(writer, 'TEST_FIXTURE', 'uncommitted', {'TEST_FIXTURE': True})
            with store.transaction(write=False) as reader:
                assert reader is outer and reader is not writer
                assert reader.execute("SELECT count(*) FROM documents WHERE namespace='TEST_FIXTURE'").fetchone()[0] == 0
        assert_closed(writer)
        with store.transaction(write=False) as reader:
            assert reader is outer
            assert reader.execute("SELECT count(*) FROM documents WHERE namespace='TEST_FIXTURE'").fetchone()[0] == 1
        with store.transaction(write=False) as reader:
            with pytest.raises(sqlite3.OperationalError):
                reader.execute("DELETE FROM documents WHERE namespace='TEST_FIXTURE'")


def test_read_failure_discards_connection_and_preserves_original_exception(stores):
    _, store = stores
    failure = KeyboardInterrupt('TEST_FIXTURE_ORIGINAL_EXCEPTION')
    with active_scope(store):
        with pytest.raises(KeyboardInterrupt) as caught:
            with store.transaction(write=False) as failed:
                raise failure
        assert caught.value is failure
        assert_closed(failed)
        with store.transaction(write=False) as next_connection:
            assert next_connection is not failed
    assert_closed(next_connection)


def test_scope_exit_on_error_closes_and_removes_thread_state(stores):
    _, store = stores
    with pytest.raises(RuntimeError, match='TEST_FIXTURE'):
        with active_scope(store):
            with store.transaction(write=False) as first:
                pass
            raise RuntimeError('TEST_FIXTURE')
    assert_closed(first)
    with active_scope(store):
        with store.transaction(write=False) as second:
            assert second is not first
    assert_closed(second)


def test_lifetime_scope_alone_does_not_reuse_arbitrary_reads(stores):
    _, store = stores
    with store.read_connection_scope() as scope:
        with store.transaction(write=False) as first:
            pass
        assert_closed(first)
        with store.transaction(write=False) as second:
            assert second is not first
        assert_closed(second)
        with scope.activate():
            with store.transaction(write=False) as opted_in:
                pass
        assert opted_in.execute('SELECT 1').fetchone()[0] == 1
    assert_closed(opted_in)
    with pytest.raises(StorageError, match='ownership'):
        with scope.activate():
            pytest.fail('CLOSED_SCOPE_REACTIVATED')


def test_reader_configuration_is_restored_before_each_borrow(stores):
    _, store = stores
    with active_scope(store):
        with store.transaction(write=False) as first:
            pass
        first.row_factory = lambda *args: 'TEST_FIXTURE_POISON'
        first.text_factory = bytes
        first.isolation_level = 'DEFERRED'
        first.execute('PRAGMA query_only=OFF')
        first.execute('PRAGMA foreign_keys=OFF')
        first.execute('PRAGMA synchronous=OFF')
        first.execute('PRAGMA recursive_triggers=OFF')
        first.execute('PRAGMA busy_timeout=1')
        with store.transaction(write=False) as restored:
            assert restored is first and restored.row_factory is sqlite3.Row and restored.text_factory is str
            assert restored.isolation_level is None
            for name, expected in (('query_only', 1), ('foreign_keys', 1), ('synchronous', 2),
                                   ('recursive_triggers', 1), ('busy_timeout', int(store.timeout * 1000))):
                assert restored.execute('PRAGMA ' + name).fetchone()[0] == expected


@pytest.mark.parametrize('contamination', ['temp_jobs', 'attached_database', 'unfinished_transaction'])
def test_reader_contamination_is_not_reused_for_cancellation(stores, tmp_path, contamination):
    _, store = stores
    with active_scope(store):
        with store.transaction(write=False) as poisoned:
            pass
        poisoned.execute('PRAGMA query_only=OFF')
        if contamination == 'temp_jobs':
            poisoned.execute('CREATE TEMP TABLE jobs(cancel_requested INTEGER,state TEXT)')
            poisoned.execute("INSERT INTO temp.jobs VALUES(0,'RUNNING')")
        elif contamination == 'attached_database':
            attached = SQLiteStore(tmp_path / 'TEST_FIXTURE_ATTACHED.sqlite3')
            poisoned.execute('ATTACH DATABASE ? AS test_fixture', (str(attached.path),))
        else:
            poisoned.execute('BEGIN')
            poisoned.execute("INSERT INTO documents VALUES('TEST_FIXTURE','uncommitted','{}','TEST_FIXTURE','TEST_FIXTURE')")
            with pytest.raises(StorageError, match='unfinished transaction'):
                with store.transaction(write=False):
                    pass
            assert_closed(poisoned)
        with store.transaction(write=False) as clean:
            assert clean is not poisoned
            assert clean.execute('SELECT count(*) FROM sqlite_temp_master').fetchone()[0] == 0
            assert {row[1] for row in clean.execute('PRAGMA database_list')} <= {'main', 'temp'}
            assert clean.execute("SELECT count(*) FROM documents WHERE document_id='uncommitted'").fetchone()[0] == 0
        assert_closed(poisoned)


def test_nested_scopes_do_not_close_the_outer_lease(stores):
    _, store = stores
    with active_scope(store):
        with store.transaction(write=False) as first:
            pass
        with active_scope(store):
            with store.transaction(write=False) as inner:
                assert inner is first
        assert first.execute('SELECT 1').fetchone()[0] == 1
    assert_closed(first)


def test_scopes_are_separate_for_threads_and_store_instances(stores):
    ordinary, store = stores
    barrier = threading.Barrier(2)
    def work():
        with active_scope(store):
            with store.transaction(write=False) as connection:
                pass
            barrier.wait(timeout=5)
            with store.transaction(write=False) as again:
                assert again is connection
            return connection, threading.get_ident()
    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = [f.result(timeout=10) for f in (pool.submit(work), pool.submit(work))]
    assert left[0] is not right[0] and left[1] != right[1]
    with active_scope(store), active_scope(ordinary):
        with store.transaction(write=False) as left:
            with ordinary.transaction(write=False) as right:
                assert left is not right


@pytest.mark.parametrize('change', ['replace', 'remove', 'symlink'])
def test_scope_rejects_physical_database_changes_before_read_or_write(stores, tmp_path, change):
    ordinary, store = stores
    replacement = SQLiteStore(tmp_path / 'TEST_FIXTURE_REPLACEMENT.sqlite3')
    with active_scope(store):
        with store.transaction(write=False) as first:
            pass
        if change == 'replace':
            os.replace(replacement.path, store.path)
        else:
            store.path.unlink()
            if change == 'symlink':
                store.path.symlink_to(replacement.path)
        for write in (False, True):
            with pytest.raises(StorageError, match='identity'):
                with store.transaction(write=write):
                    pytest.fail('PHYSICAL_REPLACEMENT_ACCEPTED')
    assert_closed(first)


def test_scope_rejects_store_path_rebinding(stores, tmp_path):
    _, store = stores
    replacement = SQLiteStore(tmp_path / 'TEST_FIXTURE_OTHER.sqlite3')
    with active_scope(store):
        with store.transaction(write=False):
            pass
        store.path = replacement.path
        with pytest.raises(StorageError, match='ownership'):
            with store.transaction(write=False):
                pass


def test_job_handler_scope_keeps_atomic_publication_and_closes_reads(stores):
    _, store = stores
    jobs = JobService(store, max_workers=1, max_pending=2, recover=False)
    connections = []
    def handler(context):
        context.check_cancelled()
        connections.append(context._cancellation_read_scope.connection)
        with store.transaction(write=False) as reader:
            assert reader is not connections[0]
        assert_closed(reader)
        def write(connection):
            store._put_document(connection, 'TEST_FIXTURE', 'atomic', {'TEST_FIXTURE': True})
            context.check_cancelled()
            with store.transaction(write=False) as reader:
                assert reader is not connections[0] and reader is not connection
                assert context._cancellation_read_scope.connection is connections[0]
                assert reader.execute("SELECT count(*) FROM documents WHERE namespace='TEST_FIXTURE'").fetchone()[0] == 0
        return context.complete({'TEST_FIXTURE': True}, write)
    try:
        jobs.register('TEST_FIXTURE', handler)
        job = jobs.submit('TEST_FIXTURE', {'TEST_FIXTURE': True})
        deadline = time.monotonic() + 5
        while jobs.get(job['job_id'])['state'] not in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert jobs.get(job['job_id'])['state'] == 'SUCCEEDED'
        jobs.shutdown(wait=True, cancel_pending=False)
        assert_closed(connections[0])
        assert store.get_document('TEST_FIXTURE', 'atomic') == {'TEST_FIXTURE': True}
    finally:
        jobs.shutdown(wait=True, cancel_pending=False)


def test_job_handler_external_cancel_prevents_publication(stores):
    _, store = stores
    jobs = JobService(store, max_workers=1, max_pending=2, recover=False)
    ready, resume = threading.Event(), threading.Event()
    def handler(context):
        context.check_cancelled()
        ready.set()
        assert resume.wait(timeout=5)
        context.check_cancelled()
        return context.complete({'TEST_FIXTURE': True}, lambda con:
            store._put_document(con, 'TEST_FIXTURE', 'must_not_exist', {'TEST_FIXTURE': True}))
    try:
        jobs.register('TEST_FIXTURE', handler)
        job = jobs.submit('TEST_FIXTURE', {'TEST_FIXTURE': True})
        assert ready.wait(timeout=5)
        jobs.cancel(job['job_id'])
        resume.set()
        jobs.shutdown(wait=True, cancel_pending=False)
        assert jobs.get(job['job_id'])['state'] == 'CANCELLED'
        assert store.get_document('TEST_FIXTURE', 'must_not_exist') is None
    finally:
        resume.set()
        jobs.shutdown(wait=True, cancel_pending=False)
