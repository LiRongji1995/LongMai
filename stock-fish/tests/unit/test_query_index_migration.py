"""TEST_FIXTURE additive indexes, complete run revisions and old backups."""
import hashlib
import json
import sqlite3

import pytest

from stockfish_core.common import canonical_json
from stockfish_core.snapshot_backup import backup_state, restore_state, verify_backup
from stockfish_core.storage import (SQLiteStore, ReadOnlySQLiteStore, IntegrityError, StorageError,
                                   _SCHEMA)


V1_HASH = 'd3d76e4c47b1323ab3b06bfc3ba400c748c0923be2cb8ef4876b95278f83a2da'
STAMP = '2026-01-01T00:00:00+00:00'


def v1_database(path, *, existing_expression_index=False):
    assert hashlib.sha256(_SCHEMA.encode()).hexdigest() == V1_HASH
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.executescript(_SCHEMA)
        connection.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)')
        connection.execute('INSERT INTO schema_migrations VALUES (1,?,?)', (V1_HASH, STAMP))
        for number in range(90):
            payload = dict(coverage_type='TEST_FIXTURE', run_id='TEST_FIXTURE_RUN_'+str(number % 3),
                           instrument_id='TEST_FIXTURE_IID_'+str(number % 5), revision=number,
                           outcome=None if number % 2 else number, status='UNKNOWN' if number % 2 else 'NORMAL')
            raw = canonical_json(payload)
            connection.execute('INSERT INTO documents VALUES (?,?,?,?,?)',
                               ('TEST_FIXTURE_OUTCOMES', f'TEST_FIXTURE_{number:03}', raw,
                                hashlib.sha256(raw.encode()).hexdigest(), STAMP))
        if existing_expression_index:
            connection.execute("CREATE INDEX IF NOT EXISTS strategy_item_observation_lookup "
                               "ON documents(namespace,json_extract(payload,'$.run_id'),"
                               "json_extract(payload,'$.instrument_id'),created_at,document_id)")
    return path


def saved_rows(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as connection:
        return connection.execute('SELECT * FROM documents ORDER BY namespace,document_id').fetchall()


@pytest.mark.parametrize('existing', [False, True])
def test_v1_upgrade_adopts_existing_index_without_changing_saved_bytes(tmp_path, existing):
    path = v1_database(tmp_path/'TEST_FIXTURE.sqlite3', existing_expression_index=existing)
    before = saved_rows(path)
    store = SQLiteStore(path)
    assert saved_rows(path) == before
    with store.transaction(write=False) as connection:
        assert [row[0] for row in connection.execute('SELECT version FROM schema_migrations ORDER BY version')] == [1, 2]
        assert connection.execute('SELECT checksum FROM schema_migrations WHERE version=1').fetchone()[0] == V1_HASH
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {'documents_namespace_created', 'job_events_job_order', 'strategy_item_observation_lookup'} <= names
    assert SQLiteStore(path).health()['status'] == 'healthy'
    assert store.readiness()['schema_version'] == 2
    assert saved_rows(path) == before


def test_explain_uses_index_for_list_event_and_scoped_run_queries(tmp_path):
    store = SQLiteStore(v1_database(tmp_path/'TEST_FIXTURE.sqlite3'))
    queries = [
        ('documents_namespace_created', 'SELECT * FROM documents WHERE namespace=? ORDER BY created_at,document_id LIMIT ? OFFSET ?', ('TEST_FIXTURE_OUTCOMES', 10, 0)),
        ('job_events_job_order', 'SELECT * FROM job_events WHERE job_id=? ORDER BY event_id', ('TEST_FIXTURE_JOB',)),
        ('strategy_item_observation_lookup', "SELECT * FROM documents WHERE namespace=? AND json_extract(payload,'$.run_id')=?", ('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN_1')),
        ('strategy_item_observation_lookup', "SELECT * FROM documents WHERE namespace=? AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.instrument_id')=? ORDER BY created_at,document_id", ('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN_1', 'TEST_FIXTURE_IID_2')),
    ]
    with store.transaction(write=False) as connection:
        for index, sql, values in queries:
            plan = '\n'.join(row[3] for row in connection.execute('EXPLAIN QUERY PLAN '+sql, values))
            assert 'SEARCH ' in plan and index in plan, plan
            assert 'USE TEMP B-TREE' not in plan, plan


def test_scoped_run_read_matches_full_reference_without_dropping_unknown_or_revisions(tmp_path):
    store = SQLiteStore(v1_database(tmp_path/'TEST_FIXTURE.sqlite3'))
    reference = [json.loads(row[2]) for row in saved_rows(store.path)]
    for reader in (store, ReadOnlySQLiteStore(store.path)):
        wanted = [row for row in reference if row['run_id'] == 'TEST_FIXTURE_RUN_1']
        assert reader.documents_for_run('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN_1') == wanted
        assert any(row['outcome'] is None for row in wanted)
        assert reader.documents_for_run('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN_1', instrument_id='TEST_FIXTURE_IID_2') == [row for row in wanted if row['instrument_id'] == 'TEST_FIXTURE_IID_2']
        assert reader.documents_for_run('TEST_FIXTURE_OUTCOMES', "' OR 1=1 --") == []
        assert reader.documents_for_run('TEST_FIXTURE_OTHER', 'TEST_FIXTURE_RUN_1') == []


def test_scoped_run_still_checks_current_payload_hash(tmp_path):
    store = SQLiteStore(v1_database(tmp_path/'TEST_FIXTURE.sqlite3'))
    with sqlite3.connect(store.path) as connection:
        connection.execute('DROP TRIGGER documents_no_update')
        connection.execute("UPDATE documents SET payload=json_set(payload,'$.outcome',999) WHERE document_id='TEST_FIXTURE_001'")
    with pytest.raises(IntegrityError, match='hash mismatch'):
        store.documents_for_run('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN_1')


def test_scoped_run_orders_across_instruments_and_timestamp_ties(tmp_path):
    store = SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    with store.transaction() as connection:
        for identifier, instrument, timestamp in [('d', 'A', '2026-01-01'), ('c', 'Z', '2026-01-01'),
                                                   ('b', 'B', '2026-01-02'), ('a', 'Y', '2026-01-02')]:
            raw = canonical_json(dict(coverage_type='TEST_FIXTURE', run_id='TEST_FIXTURE_RUN',
                                      instrument_id=instrument, identifier=identifier))
            connection.execute('INSERT INTO documents VALUES (?,?,?,?,?)',
                               ('TEST_FIXTURE_ORDER', identifier, raw, hashlib.sha256(raw.encode()).hexdigest(), timestamp))
    assert [row['identifier'] for row in store.documents_for_run('TEST_FIXTURE_ORDER', 'TEST_FIXTURE_RUN')] == ['c', 'd', 'a', 'b']
    assert [row['identifier'] for row in store.documents_for_run('TEST_FIXTURE_ORDER', 'TEST_FIXTURE_RUN', descending=True, limit=2)] == ['b', 'a']


@pytest.mark.parametrize('version,existing', [(1, False), (1, True), (2, True)])
@pytest.mark.parametrize('instrument', [None, 'TEST_FIXTURE_IID_2'])
@pytest.mark.parametrize('descending,limit', [(False, None), (True, 2)])
def test_actual_scoped_query_plan_order_and_limit(tmp_path, monkeypatch, version, existing,
                                                  instrument, descending, limit):
    path = v1_database(tmp_path/'TEST_FIXTURE.sqlite3', existing_expression_index=existing)
    reader = SQLiteStore(path) if version == 2 else ReadOnlySQLiteStore(path)
    statements = []
    original_connect = reader._connect

    def traced_connection():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(reader, '_connect', traced_connection)
    rows = reader.documents_for_run('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN_1',
                                    instrument_id=instrument, descending=descending, limit=limit)
    reference = [json.loads(row[2]) for row in saved_rows(path)
                 if json.loads(row[2])['run_id'] == 'TEST_FIXTURE_RUN_1'
                 and (instrument is None or json.loads(row[2])['instrument_id'] == instrument)]
    if descending:
        reference.reverse()
    assert rows == (reference if limit is None else reference[:limit])
    sql, = [statement for statement in statements if statement.startswith('SELECT * FROM documents')]
    with reader.transaction(write=False) as connection:
        plan = '\n'.join(row[3] for row in connection.execute('EXPLAIN QUERY PLAN '+sql))
        if existing or version == 2:
            assert 'strategy_item_observation_lookup' in plan and '<expr>=?' in plan, plan
            assert 'documents_namespace_created' not in plan, plan
        if limit is not None:
            assert sql.endswith('LIMIT 2'), sql


@pytest.mark.parametrize('options', [{'limit': 0}, {'limit': True}, {'limit': 10001},
                                    {'limit': '2'}, {'descending': 'DESC'}])
def test_scoped_lookup_validates_optional_order_and_limit(tmp_path, options):
    store = SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    with pytest.raises(ValueError):
        store.documents_for_run('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN', **options)


@pytest.mark.parametrize('namespace,run_id,instrument', [('', 'run', None), ('n', '', None),
                                                       ('n', False, None), ('n', 'run', ''), ('n', 'run', 123)])
def test_scoped_lookup_rejects_ambiguous_identifiers(tmp_path, namespace, run_id, instrument):
    store = SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    with pytest.raises(ValueError):
        store.documents_for_run(namespace, run_id, instrument_id=instrument)


def test_same_named_wrong_index_rolls_back_new_migration(tmp_path):
    path = v1_database(tmp_path/'TEST_FIXTURE.sqlite3')
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE INDEX strategy_item_observation_lookup ON documents(namespace)')
    with pytest.raises(IntegrityError, match='query index definition mismatch'):
        SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE name='documents_namespace_created'").fetchone()[0] == 0


def test_malformed_json_cannot_be_blessed_by_expression_index_migration(tmp_path):
    path = v1_database(tmp_path/'TEST_FIXTURE.sqlite3')
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA ignore_check_constraints=ON')
        connection.execute('INSERT INTO documents VALUES (?,?,?,?,?)', ('TEST_FIXTURE_CORRUPT', 'bad', '{bad', 'bad', STAMP))
    with pytest.raises(StorageError):
        SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE name='documents_namespace_created'").fetchone()[0] == 0


def test_reopened_v2_refuses_removed_query_index(tmp_path):
    store = SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    with sqlite3.connect(store.path) as connection:
        connection.execute('DROP INDEX job_events_job_order')
    assert store.health()['status'] == 'unavailable'
    with pytest.raises(IntegrityError, match='query index definition mismatch'):
        SQLiteStore(store.path)


def test_readonly_v1_never_migrates_and_old_backup_restores_before_upgrade(tmp_path):
    path = v1_database(tmp_path/'TEST_FIXTURE.sqlite3')
    before = saved_rows(path)
    reader = ReadOnlySQLiteStore(path)
    assert len(reader.documents_for_run('TEST_FIXTURE_OUTCOMES', 'TEST_FIXTURE_RUN_1')) == 30
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as connection:
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 1
    backup = tmp_path/'TEST_FIXTURE_BACKUP'
    assert backup_state(path, backup)['status'] == 'VERIFIED'
    backup_hashes = {p.relative_to(backup): hashlib.sha256(p.read_bytes()).hexdigest() for p in backup.rglob('*') if p.is_file()}
    restored = tmp_path/'TEST_FIXTURE_RESTORED'
    assert restore_state(backup, restored)['status'] == 'VERIFIED'
    restored_store = SQLiteStore(restored/'database.sqlite3')
    assert restored_store.health()['status'] == 'healthy'
    assert saved_rows(restored_store.path) == before
    assert verify_backup(backup)['status'] == 'VERIFIED'
    assert {p.relative_to(backup): hashlib.sha256(p.read_bytes()).hexdigest() for p in backup.rglob('*') if p.is_file()} == backup_hashes
