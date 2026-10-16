"""Isolated TEST_FIXTURE originals and DB; no real market or service access."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import pickle
import socket
from unittest.mock import patch

import pytest

from stockfish_core.storage import SQLiteStore, canonical_json
from stockfish_core.jobs import JobService
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from tests.support.short_horizon_archive_fixture import fixture_archive, reseal


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('TEST_FIXTURE_NETWORK'))
    return fixture_archive(tmp_path / 'TEST_FIXTURE')


def authenticate(archive, **kwargs):
    return bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'],
        allow_test_fixture=True, **kwargs)


@pytest.fixture
def service(tmp_path):
    root = tmp_path / 'TEST_FIXTURE-state'
    root.mkdir()
    store = SQLiteStore(root / 'fixture.db')
    jobs = JobService(store, recover=False)
    service = object.__new__(CandidateService)
    service.store, service.jobs, service.root, service.allow_test_fixture = store, jobs, root, True
    yield service
    jobs.shutdown()


def test_native_archive_authenticates_without_selector_or_owner_restoration(archive, monkeypatch):
    from stockfish_core.yaogu import strategy_experiments, premarket_native_core_v1
    monkeypatch.setattr(strategy_experiments, 'run_short_horizon_strategy',
        lambda *a, **k: pytest.fail('SELECTOR_MUST_NOT_RUN'))
    monkeypatch.setattr(premarket_native_core_v1, 'run_core', lambda *a, **k: pytest.fail('CORE_MUST_NOT_RUN'))
    cap = authenticate(archive)
    audit = cap.audit()
    assert audit['status'] == 'VERIFIED' and audit['authenticated_source_count'] == len(archive['items'])
    assert audit['run_id'] == archive['run']['run_id']
    assert audit['reexecuted_selector'] is audit['historical_owner_restored'] is audit['native_core_owner_restored'] is False
    assert audit['verification_budget']['terminal_rereads'] == 1
    with pytest.raises(TypeError, match='NOT_SERIALIZABLE'):
        pickle.dumps(cap)
    with pytest.raises(ValueError, match='AUTHENTICATION_REQUIRED'):
        bridge.ArchiveBridgeCapability()


def test_atomic_import_retains_original_run_input_and_is_idempotent(archive, service):
    cap = authenticate(archive)
    raw_run = deepcopy(archive['run'])
    raw_input = json.loads(gzip.decompress(Path(archive['artifacts']['input.json.gz']['path']).read_bytes()))
    before = {ref['path']: Path(ref['path']).read_bytes() for ref in archive['artifacts'].values()}
    cert = bridge.commit_bridge(service, cap)
    cert2 = bridge.commit_bridge(service, authenticate(archive))
    assert cert2 == cert
    assert service.store.get_document('strategy_experiment_runs', cert['run_id']) == raw_run
    assert service.store.get_document('candidate_inputs', cert['input_id']) == raw_input
    assert service.store.get_document_bytes('candidate_inputs', cert['input_id']) == canonical_json(raw_input).encode()
    assert raw_run['source_database_written'] is False and cert['source_database_written'] is True
    assert cert['imported_at'] >= cert['verified_at']
    assert before == {path: Path(path).read_bytes() for path in before}
    shared = bridge.ArchiveVerificationSession()
    run_credential = bridge.require_verified_bridge(service, cert['run_id'], session=shared)
    read1 = shared.read_bytes
    input_credential = bridge.require_verified_bridge_input(service, cert['input_id'], session=shared)
    assert input_credential == run_credential
    assert shared.read_bytes == read1
    assert shared.terminal()['terminal_rereads'] == 2
    assert service.strategy_experiment(cert['run_id']) == raw_run


@pytest.mark.parametrize('field,value', [('status', 'FAILED'), ('core_verified', False),
    ('source_terminal_binding_verified', False), ('implementation_epoch_verified', False),
    ('source_capability_restored', True), ('external_worker_reaped', False)])
def test_rejects_unsealed_or_failed_job(archive, field, value):
    reseal(archive, final={field: value})
    with pytest.raises(ValueError, match='SEALED_SUCCESSFUL'):
        authenticate(archive)


def test_explicit_hash_and_fixture_scope_required(archive):
    with pytest.raises(ValueError, match='FILE_SHA'):
        bridge.authenticate_archive(archive['job'], expected_final_sha256='0' * 64, allow_test_fixture=True)
    with pytest.raises(ValueError, match='EXPLICIT_FIXTURE'):
        bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'])


@pytest.mark.parametrize('role', ['raw', 'receipt', 'result'])
def test_rejects_changed_physical_original(archive, role):
    path = Path(archive['items'][0][role]['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        authenticate(archive)


def test_missing_old_source_requires_explicit_exact_byte_copy(archive, tmp_path):
    key = 'stockfish_core/yaogu/candidate_service.py'
    path = archive['source'] / key
    original = path.read_bytes()
    copy = tmp_path / 'TEST_FIXTURE-old-source.py'
    copy.write_bytes(original)
    path.write_bytes(original + b'\n# TEST_FIXTURE changed implementation\n')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        authenticate(archive)
    cap = authenticate(archive, source_overrides={key: str(copy)})
    proof = cap.audit()['source_proofs'][key]
    assert proof['proof_mode'] == 'EXPLICIT_COPY_CONTENT_PROOF_NOT_ORIGINAL_PHYSICAL_OWNER'
    assert proof['current_reference']['fp'] != proof['original_reference']['fp']
    copy.write_bytes(b'TEST_FIXTURE_WRONG_SOURCE')
    with pytest.raises(ValueError, match='FILE_SHA'):
        authenticate(archive, source_overrides={key: str(copy)})


def test_unknown_source_override_rejected(archive, tmp_path):
    with pytest.raises(ValueError, match='UNREGISTERED_SOURCE_OVERRIDE'):
        authenticate(archive, source_overrides={'../../unknown.py': str(tmp_path)})


def test_export_tamper_rejected_even_with_resealed_archive(archive):
    name = 'all.csv'
    path = Path(archive['artifacts'][name]['path'])
    path.write_bytes(path.read_bytes().replace(b'600001.SH', b'600009.SH'))
    newref = workflow._ref(path)
    archive['artifacts'][name] = newref
    manifest = json.loads(Path(archive['artifacts']['manifest.json']['path']).read_bytes())
    manifest['artifacts'][name] = newref
    manifest['official_exports'][name]['reference'] = newref
    reseal(archive, manifest=manifest)
    with pytest.raises(ValueError, match='OFFICIAL_CSV_MISMATCH'):
        authenticate(archive)


def test_locator_summary_cannot_omit_original_receipt(archive):
    path = archive['job'] / 'capture-summary.json'
    document = json.loads(path.read_bytes())
    document['core_records'].pop()
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='EXACT_CORE_RECEIPT_SET'):
        authenticate(archive)


def test_terminal_rejects_changed_file_and_does_not_commit(archive, service):
    cap = authenticate(archive)
    path = Path(archive['items'][0]['raw']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        bridge.commit_bridge(service, cap)
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document(bridge.RUN_BINDINGS, archive['run']['run_id']) is None


def test_plain_json_certificate_cannot_commit_or_restore_owner(archive, service):
    cap = authenticate(archive)
    document = json.loads(json.dumps(cap.audit()))
    with pytest.raises(ValueError, match='GENUINE_FRESH'):
        bridge.commit_bridge(service, document)
    with pytest.raises(ValueError, match='VERIFIED_BRIDGE_BINDING_REQUIRED'):
        bridge.require_verified_bridge(service, archive['run']['run_id'])


def test_import_rollback_on_projection_failure(archive, service, monkeypatch):
    cap = authenticate(archive)
    def fail(*args):
        raise RuntimeError('TEST_FIXTURE_ATOMIC_ROLLBACK')
    monkeypatch.setattr(service, '_write_strategy_projection', fail)
    with pytest.raises(RuntimeError, match='ATOMIC_ROLLBACK'):
        bridge.commit_bridge(service, cap)
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document('strategy_experiment_runs', archive['run']['run_id']) is None


def test_archived_document_spool_preserves_canonical_unicode_and_text(service, tmp_path):
    document = {'TEST_FIXTURE': '中文 € 😀', 'z': [{'次序': 1}], 'a': None}
    path = tmp_path / 'TEST_FIXTURE-pretty.json.gz'
    path.write_bytes(gzip.compress(json.dumps(document, ensure_ascii=False, indent=4).encode()))
    expected = canonical_json(document).encode()
    digest = hashlib.sha256(expected).hexdigest()
    session = bridge.ArchiveVerificationSession()
    with service.store.transaction() as connection:
        bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'one', digest, session,
            reference=workflow._ref(path), temporary_dir=tmp_path)
        assert connection.execute('SELECT typeof(payload) FROM documents WHERE namespace=?',
            ('TEST_FIXTURE_spool',)).fetchone()[0] == 'text'
    assert service.store.get_document_bytes('TEST_FIXTURE_spool', 'one') == expected
    assert service.store.get_document('TEST_FIXTURE_spool', 'one') == document
    assert not list(tmp_path.glob('stockfish-verified-bridge-*'))
    with service.store.transaction() as connection:
        bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'one', digest, session,
            document=document, temporary_dir=tmp_path)
    with pytest.raises(ValueError, match='DATABASE_PARENT_CHANGED'):
        with service.store.transaction() as connection:
            bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'one', '0' * 64, session,
                document=document, temporary_dir=tmp_path)


@pytest.mark.parametrize('cancel', [False, True])
def test_archived_document_spool_failure_rolls_back_and_removes_temporary_file(service, tmp_path, cancel):
    document = {'TEST_FIXTURE': ['中文' * 1000] * 200}
    armed = False
    def checkpoint():
        if cancel and armed:
            raise RuntimeError('TEST_FIXTURE_SPOOL_CANCELLED')
    session = bridge.ArchiveVerificationSession(checkpoint=checkpoint)
    armed = True
    expected = hashlib.sha256(canonical_json(document).encode()).hexdigest() if cancel else '0' * 64
    with pytest.raises((ValueError, RuntimeError), match='CONTENT_CHANGED|SPOOL_CANCELLED'):
        with service.store.transaction() as connection:
            service.store._put_document(connection, 'TEST_FIXTURE_spool', 'earlier', {'TEST_FIXTURE': True})
            bridge._put_archived_document(connection, 'TEST_FIXTURE_spool', 'new', expected, session,
                document=document, temporary_dir=tmp_path)
    assert service.store.get_document('TEST_FIXTURE_spool', 'earlier') is None
    assert service.store.get_document('TEST_FIXTURE_spool', 'new') is None
    assert not list(tmp_path.glob('stockfish-verified-bridge-*'))


def test_authenticated_metadata_releases_heavy_cache_without_reauthentication(archive, service, monkeypatch):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    original = bridge.authenticate_archive
    calls = []
    def counted(*a, **k):
        calls.append(True)
        return original(*a, **k)
    monkeypatch.setattr(bridge, 'authenticate_archive', counted)
    view = bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    assert 'run' not in view and view['input_metadata']['sessions']
    sessions = list(view['input_metadata']['sessions'])
    view['input_metadata']['sessions'].clear()
    before = (dict(session.files), list(session.locators), session.read_bytes, session.started)
    session.release_cached_archives()
    assert not session.capabilities
    assert (session.files, session.locators, session.read_bytes, session.started) == before
    run_view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    assert len(calls) == 1 and run_view['input_metadata']['sessions'] == sessions
    assert run_view['run'] == archive['run']
    bundle = bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    assert [str(day) for day in bundle.sessions] == sessions
    session.release_cached_archives()
    assert session.terminal()['borrowed_values'] == 2


@pytest.mark.parametrize('target', ['run', 'input'])
def test_borrowed_objects_cannot_be_mutated_after_cache_release(archive, service, target):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    bundle = bridge.load_verified_bridge_input(service, cert['input_id'], session=session)
    session.release_cached_archives()
    if target == 'run':
        view['run']['results'][bridge.STRATEGY]['counts']['TEST_FIXTURE_MUTATION'] = 1
    else:
        object.__setattr__(bundle, 'coverage_type', 'UNVERIFIABLE_AS_OF')
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        session.terminal()


def test_verified_loader_cannot_use_plain_metadata_other_session_or_other_store(archive, service, tmp_path):
    from types import SimpleNamespace
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    view = bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    with pytest.raises(ValueError, match='GENUINE_SESSION_REQUIRED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=view)
    with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=bridge.ArchiveVerificationSession())
    other = SimpleNamespace(store=SQLiteStore(tmp_path / 'TEST_FIXTURE-other.db'))
    with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
        bridge.load_verified_bridge_input(other, cert['input_id'], session=session)
    # Even a second store object at the same path must authenticate for itself.
    other = SimpleNamespace(store=SQLiteStore(service.store.path))
    with pytest.raises(ValueError, match='VERIFIED_INPUT_VIEW_REQUIRED'):
        bridge.load_verified_bridge_input(other, cert['input_id'], session=session)


def test_release_preserves_original_file_terminal_guard(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    session.release_cached_archives()
    path = Path(archive['items'][0]['raw']['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        session.terminal()


def test_verified_view_rechecks_database_binding_after_release(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    session = bridge.ArchiveVerificationSession()
    bridge.authenticated_bridge_view(service, input_id=cert['input_id'], session=session)
    session.release_cached_archives()
    binding = service.store.get_document(bridge.INPUT_BINDINGS, cert['input_id'])
    binding['run_id'] = 'TEST_FIXTURE_other_run'
    raw = canonical_json(binding)
    with service.store.transaction() as connection:
        # Simulate offline corruption in this isolated fixture, then restore
        # the production immutability trigger before the verifier reads it.
        connection.execute('DROP TRIGGER documents_no_update')
        connection.execute('UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?',
            (raw, hashlib.sha256(raw.encode()).hexdigest(), bridge.INPUT_BINDINGS, cert['input_id']))
        connection.execute("CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END")
    with pytest.raises(ValueError, match='VERIFIED_VIEW_BINDING_CHANGED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)


def test_import_releases_expanded_run_during_input_insert_and_same_cap_reimports(archive, service, monkeypatch):
    cap = authenticate(archive)
    original = bridge._put_archived_document
    observed = []
    def inspect(connection, namespace, *args, **kwargs):
        if namespace == 'candidate_inputs':
            observed.append(bridge._state(cap).get('run') is None)
        return original(connection, namespace, *args, **kwargs)
    monkeypatch.setattr(bridge, '_put_archived_document', inspect)
    cert = bridge.commit_bridge(service, cap)
    assert bridge._state(cap).get('run') is None
    assert bridge.commit_bridge(service, cap) == cert
    assert observed == [True, True]
    session = cap._session
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    assert view['run'] == archive['run']
    session.terminal()


def test_run_original_changed_after_input_insert_rolls_back_everything(archive, service, monkeypatch):
    cap = authenticate(archive)
    original = bridge._put_archived_document
    path = Path(archive['artifacts']['canonical-run.json.gz']['path'])
    def corrupt(connection, namespace, *args, **kwargs):
        result = original(connection, namespace, *args, **kwargs)
        if namespace == 'candidate_inputs':
            path.write_bytes(path.read_bytes() + b'TEST_FIXTURE_CHANGED_AFTER_INPUT_INSERT')
        return result
    monkeypatch.setattr(bridge, '_put_archived_document', corrupt)
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        bridge.commit_bridge(service, cap)
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document('strategy_experiment_runs', archive['run']['run_id']) is None
    assert service.store.get_document(bridge.CERTIFICATES, cap.audit()['bridge_id']) is None
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        bridge.commit_bridge(service, cap)


def test_sqlite_payload_callback_releases_bytes_before_constraints_and_unregisters(service, tmp_path, monkeypatch):
    import sqlite3
    document = {'TEST_FIXTURE': '中文 callback lifecycle'}
    expected = hashlib.sha256(canonical_json(document).encode()).hexdigest()
    events, alive = [], [False]
    class TrackedBytes(bytes):
        def __new__(cls, raw):
            alive[0] = True
            return super().__new__(cls, raw)
        def __del__(self):
            alive[0] = False
            events.append('bytes_released')
    original = bridge.tempfile.TemporaryFile
    class Spool:
        def __init__(self, *a, **k):
            self.file = original(*a, **k)
        def __getattr__(self, key):
            return getattr(self.file, key)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return self.file.__exit__(*a)
        def read(self, size=-1):
            return TrackedBytes(self.file.read(size))
    monkeypatch.setattr(bridge.tempfile, 'TemporaryFile', Spool)
    session = bridge.ArchiveVerificationSession()
    with service.store.transaction() as connection:
        def observed(value):
            assert not alive[0] and json.loads(value) == document
            events.append('insert_trigger')
            return 1
        connection.create_function('TEST_FIXTURE_after_payload', 1, observed)
        connection.execute("CREATE TRIGGER TEST_FIXTURE_after_insert AFTER INSERT ON documents WHEN NEW.namespace='TEST_FIXTURE_udf' BEGIN SELECT TEST_FIXTURE_after_payload(NEW.payload); END")
        bridge._put_archived_document(connection, 'TEST_FIXTURE_udf', 'one', expected, session,
            document=document, temporary_dir=tmp_path)
        with pytest.raises(sqlite3.Error):
            connection.execute('SELECT _stockfish_verified_bridge_payload_v1()')
    assert events == ['bytes_released', 'insert_trigger']
    assert service.store.get_document('TEST_FIXTURE_udf', 'one') == document


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_sqlite_payload_callback_restores_original_exception_and_cleans_up(service, tmp_path, error_type):
    import sqlite3
    import sys
    failure = error_type('TEST_FIXTURE_CALLBACK_CANCELLED')
    def checkpoint():
        if sys._getframe(2).f_code.co_name == 'payload':
            raise failure
    session = bridge.ArchiveVerificationSession(checkpoint=checkpoint)
    document = {'TEST_FIXTURE': 'callback interruption'}
    expected = hashlib.sha256(canonical_json(document).encode()).hexdigest()
    with pytest.raises(error_type) as captured:
        with service.store.transaction() as connection:
            service.store._put_document(connection, 'TEST_FIXTURE_udf', 'earlier', document)
            try:
                bridge._put_archived_document(connection, 'TEST_FIXTURE_udf', 'one', expected, session,
                    document=document, temporary_dir=tmp_path)
            except BaseException:
                with pytest.raises(sqlite3.Error):
                    connection.execute('SELECT _stockfish_verified_bridge_payload_v1()')
                raise
    assert captured.value is failure
    assert service.store.get_document('TEST_FIXTURE_udf', 'earlier') is None
    assert service.store.get_document('TEST_FIXTURE_udf', 'one') is None
    assert not list(tmp_path.glob('stockfish-verified-bridge-*'))


def _offline_fixture_document(service, namespace, document_id, payload=None):
    """Simulate offline damage only inside an isolated TEST_FIXTURE database."""
    with service.store.transaction() as connection:
        trigger = 'documents_no_delete' if payload is None else 'documents_no_update'
        operation = 'DELETE' if payload is None else 'UPDATE'
        connection.execute('DROP TRIGGER ' + trigger)
        if payload is None:
            connection.execute('DELETE FROM documents WHERE namespace=? AND document_id=?', (namespace, document_id))
        else:
            raw = canonical_json(payload)
            connection.execute('UPDATE documents SET payload=?,content_hash=? WHERE namespace=? AND document_id=?',
                (raw, hashlib.sha256(raw.encode()).hexdigest(), namespace, document_id))
        connection.execute("CREATE TRIGGER " + trigger + " BEFORE " + operation + " ON documents BEGIN SELECT RAISE(ABORT, 'immutable documents'); END")


def test_saved_csv_retains_original_noncanonical_nested_key_order(tmp_path, service):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-csv-order', observation_date='2026-09-15')
    cert = bridge.commit_bridge(service, authenticate(archive))
    saved = service.strategy_experiment(cert['run_id'])
    original = Path(archive['artifacts']['all.csv']['path']).read_bytes()
    regenerated = service._format_strategy_experiment_csv(saved, strategy_id=bridge.STRATEGY, selection='all').encode()
    assert regenerated != original, 'TEST_FIXTURE must expose canonical nested-key reordering'
    for selection in bridge.CSV_SELECTIONS:
        expected = Path(archive['artifacts'][selection + '.csv']['path']).read_bytes()
        assert service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY,
            selection=selection).encode() == expected
    assert service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY).encode() == original
    assert cert['run_document_hash'] == bridge.digest_json_stream(saved)


@pytest.mark.parametrize('missing', ['csv', 'binding', 'certificate'])
def test_saved_csv_missing_parent_or_original_fails_closed(archive, service, missing):
    cert = bridge.commit_bridge(service, authenticate(archive))
    namespace, key = {'csv': (bridge.CSV_EXPORTS, cert['run_id'] + ':all'),
        'binding': (bridge.RUN_BINDINGS, cert['run_id']),
        'certificate': (bridge.CERTIFICATES, cert['bridge_id'])}[missing]
    _offline_fixture_document(service, namespace, key)
    with pytest.raises(ValueError, match='CSV_'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='focus')


@pytest.mark.parametrize('damage', ['text', 'selection', 'parent_hash', 'reference'])
def test_saved_csv_self_hashed_corruption_is_not_authority(archive, service, damage):
    cert = bridge.commit_bridge(service, authenticate(archive))
    key = cert['run_id'] + ':all'
    document = service.store.get_document(bridge.CSV_EXPORTS, key)
    if damage == 'text':
        document['csv_text'] += '\nTEST_FIXTURE_CHANGED'
        raw = document['csv_text'].encode()
        document['csv_sha256'], document['csv_bytes'] = hashlib.sha256(raw).hexdigest(), len(raw)
    elif damage == 'selection':
        document = service.store.get_document(bridge.CSV_EXPORTS, cert['run_id'] + ':focus')
    elif damage == 'parent_hash':
        document['run_document_hash'] = '0' * 64
    else:
        document['original_reference']['sha256'] = '0' * 64
    _offline_fixture_document(service, bridge.CSV_EXPORTS, key, document)
    with pytest.raises(ValueError, match='CSV_'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all')


def test_saved_csv_valid_other_run_binding_cannot_be_substituted(archive, service, tmp_path):
    first = bridge.commit_bridge(service, authenticate(archive))
    other = fixture_archive(tmp_path / 'TEST_FIXTURE-other-csv', observation_date='2026-09-15')
    second = bridge.commit_bridge(service, authenticate(other))
    replacement = service.store.get_document(bridge.RUN_BINDINGS, second['run_id'])
    _offline_fixture_document(service, bridge.RUN_BINDINGS, first['run_id'], replacement)
    with pytest.raises(ValueError, match='CSV_RUN_BINDING_REQUIRED'):
        service.export_strategy_experiment_csv(first['run_id'], strategy_id=bridge.STRATEGY, selection='all')


def test_saved_csv_public_request_cannot_be_substituted_with_another_complete_run(archive, service, tmp_path):
    first = bridge.commit_bridge(service, authenticate(archive))
    other = fixture_archive(tmp_path / 'TEST_FIXTURE-other-request', observation_date='2026-09-15')
    second = bridge.commit_bridge(service, authenticate(other))
    replacement = service.strategy_experiment(second['run_id'])
    _offline_fixture_document(service, 'strategy_experiment_runs', first['run_id'], replacement)
    with pytest.raises(ValueError, match='STRATEGY_EXPERIMENT_RUN_ID_MISMATCH'):
        service.export_strategy_experiment_csv(first['run_id'], strategy_id=bridge.STRATEGY, selection='all')


@pytest.mark.parametrize('remaining', ['source', 'input_binding'])
def test_saved_csv_orphan_bridge_state_cannot_fall_back_to_rendering(archive, service, remaining):
    cert = bridge.commit_bridge(service, authenticate(archive))
    for namespace in (bridge.CSV_EXPORTS, bridge.RUN_BINDINGS, bridge.CERTIFICATES,
                      bridge.INPUT_BINDINGS if remaining == 'source' else bridge.SOURCES):
        with service.store.transaction(write=False) as connection:
            keys = [row[0] for row in connection.execute('SELECT document_id FROM documents WHERE namespace=?',
                (namespace,)).fetchall()]
        for key in keys:
            _offline_fixture_document(service, namespace, key)
    with pytest.raises(ValueError, match='CSV_ORPHAN_BRIDGE_STATE'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all')
    # A shared input binding for a different run does not mark this run as bridged.
    if remaining == 'input_binding':
        binding = service.store.get_document(bridge.INPUT_BINDINGS, cert['input_id'])
        binding['run_id'] = 'TEST_FIXTURE_OTHER_RUN_WITH_SHARED_INPUT'
        _offline_fixture_document(service, bridge.INPUT_BINDINGS, cert['input_id'], binding)
        assert bridge.saved_csv_export(service, archive['run'], bridge.STRATEGY, 'all') is None


def test_saved_csv_collision_rolls_back_original_parents_and_source_registration(archive, service):
    from stockfish_core.storage import ImmutableConflict
    run_id = archive['run']['run_id']
    service.store.put_document(bridge.CSV_EXPORTS, run_id + ':all', {'TEST_FIXTURE': 'conflicting original'})
    with pytest.raises(ImmutableConflict):
        bridge.commit_bridge(service, authenticate(archive))
    assert service.store.get_document('candidate_inputs', archive['run']['input_id']) is None
    assert service.store.get_document('strategy_experiment_runs', run_id) is None
    assert service.store.list_documents(bridge.CERTIFICATES) == []
    assert service.store.list_documents(bridge.SOURCES) == []
    assert len(service.store.list_documents(bridge.CSV_EXPORTS)) == 1


def test_unbridged_csv_falls_back_and_offline_encoder_does_not_gain_authority(archive, service):
    run = archive['run']
    assert bridge.saved_csv_export(service, run, bridge.STRATEGY, 'all') is None
    class Encoder(CandidateService):
        def strategy_experiment(self, run_id):
            assert run_id == run['run_id']
            return run
    encoder = object.__new__(Encoder)
    expected = Path(archive['artifacts']['all.csv']['path']).read_bytes()
    assert encoder.export_strategy_experiment_csv(run['run_id'], strategy_id=bridge.STRATEGY).encode() == expected
    genuine = object.__new__(CandidateService)
    with pytest.raises(AttributeError):
        genuine.export_strategy_experiment_csv(run['run_id'], strategy_id=bridge.STRATEGY)


@pytest.mark.parametrize('target', ['passed_run', 'stored_run'])
def test_saved_csv_requires_actual_complete_frozen_run_hash(archive, service, target):
    cert = bridge.commit_bridge(service, authenticate(archive))
    changed = deepcopy(archive['run'])
    changed['TEST_FIXTURE_NEW_FIELD'] = 'must change complete canonical hash'
    if target == 'stored_run':
        _offline_fixture_document(service, 'strategy_experiment_runs', cert['run_id'], changed)
        changed = archive['run']
    with pytest.raises(ValueError, match='CSV_FROZEN_RUN_IDENTITY_MISMATCH|BRIDGED_DATABASE_PARENT_CHANGED'):
        bridge.saved_csv_export(service, changed, bridge.STRATEGY, 'all')


def test_missing_saved_csv_only_fresh_authenticated_commit_repairs(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    _offline_fixture_document(service, bridge.CSV_EXPORTS, cert['run_id'] + ':all')
    with pytest.raises(ValueError, match='CSV_COMPLETE_ORIGINAL_SET_REQUIRED'):
        service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all')
    assert bridge.commit_bridge(service, authenticate(archive)) == cert
    expected = Path(archive['artifacts']['all.csv']['path']).read_bytes()
    assert service.export_strategy_experiment_csv(cert['run_id'], strategy_id=bridge.STRATEGY, selection='all').encode() == expected


@pytest.mark.parametrize('changed', ['raw', 'source'])
def test_commit_terminal_rejects_original_change_after_csv_and_binding_writes(archive, service, monkeypatch, changed):
    cap = authenticate(archive)
    path = Path(archive['items'][0]['raw']['path']) if changed == 'raw' else archive['source'] / 'stockfish_core/yaogu/candidate_service.py'
    put = service.store._put_document
    def mutate_after_binding(connection, namespace, key, payload):
        result = put(connection, namespace, key, payload)
        if namespace == bridge.INPUT_BINDINGS:
            assert connection.execute('SELECT COUNT(*) FROM documents WHERE namespace=?', (bridge.CSV_EXPORTS,)).fetchone()[0] == 4
            path.write_bytes(path.read_bytes() + b'\nTEST_FIXTURE_CHANGED_BEFORE_COMMIT')
        return result
    monkeypatch.setattr(service.store, '_put_document', mutate_after_binding)
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY|REGISTERED_FILE_CHANGED'):
        bridge.commit_bridge(service, cap)
    for namespace in (bridge.CSV_EXPORTS, bridge.CERTIFICATES, bridge.SOURCES, bridge.RUN_BINDINGS,
                      'candidate_inputs', 'strategy_experiment_runs'):
        assert service.store.list_documents(namespace) == []


@pytest.mark.parametrize('budget', [{'max_total_read_bytes': 10}, {'max_unique_bytes': 10}, {'max_files': 1},
    {'max_rss_bytes': 1}, {'max_seconds': 1e-12}])
def test_shared_budget_is_bounded(archive, budget):
    with pytest.raises(ValueError, match='BUDGET_EXCEEDED'):
        authenticate(archive, session=bridge.ArchiveVerificationSession(**budget))


def test_cancellation_checkpoint_aborts_authentication(archive):
    count = 0
    def checkpoint():
        nonlocal count
        count += 1
        if count > 8:
            raise RuntimeError('TEST_FIXTURE_CANCELLED')
    with pytest.raises(RuntimeError, match='CANCELLED'):
        authenticate(archive, session=bridge.ArchiveVerificationSession(checkpoint=checkpoint))


def test_stored_source_corruption_cannot_be_reused(archive, service, monkeypatch):
    cert = bridge.commit_bridge(service, authenticate(archive))
    original = service.store.get_document
    monkeypatch.setattr(service.store, 'get_document',
        lambda namespace, key: None if namespace == bridge.SOURCES else original(namespace, key))
    with pytest.raises(ValueError, match='SOURCE_REGISTRATION_CHANGED'):
        bridge.require_verified_bridge(service, cert['run_id'])


def test_legitimate_other_certificate_cannot_answer_requested_key(archive, service):
    cert = bridge.commit_bridge(service, authenticate(archive))
    binding = service.store.get_document(bridge.RUN_BINDINGS, cert['run_id'])
    service.store.put_document(bridge.RUN_BINDINGS, 'TEST_FIXTURE_WRONG_RUN', binding)
    with pytest.raises(ValueError, match='REQUESTED_BRIDGE_IDENTITY_MISMATCH'):
        bridge.require_verified_bridge(service, 'TEST_FIXTURE_WRONG_RUN')


def test_closed_or_forged_jobs_do_not_authorize_import(archive, service):
    cap = authenticate(archive)
    service.jobs.shutdown()
    with pytest.raises(ValueError, match='JOB_LOCK_OWNER'):
        bridge.commit_bridge(service, cap)
    original = service.jobs
    from types import SimpleNamespace
    service.jobs = SimpleNamespace(owner_id='TEST_FIXTURE_FORGED', _closed=False)
    try:
        with pytest.raises(ValueError, match='JOB_LOCK_OWNER'):
            bridge.commit_bridge(service, cap)
    finally:
        service.jobs = original


@pytest.mark.parametrize('budget', [{'max_total_read_bytes': float('inf')}, {'max_files': 1.5},
    {'max_seconds': float('nan')}, {'max_seconds': float('inf')}, {'max_rss_bytes': True}])
def test_nonfinite_and_fractional_budgets_are_rejected(budget):
    with pytest.raises(ValueError, match='BUDGET_REQUIRED'):
        bridge.ArchiveVerificationSession(**budget)


def test_inline_reconstructed_coverage_is_preserved_never_promoted(tmp_path, service):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-reconstructed', coverage_type='RECONSTRUCTED_HISTORICAL')
    cap = authenticate(archive)
    audit = cap.audit()
    assert audit['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    assert audit['historical_owner_restored'] is False
    cert = bridge.commit_bridge(service, cap)
    bundle = service.store.get_document('candidate_inputs', cert['input_id'])
    assert bundle['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    assert bundle['history_artifact_ref'] is bundle['history_artifact_hash'] is None
    assert all(receipt['historical_time_proof'] is None for receipt in bundle['receipts'])
    assert service.strategy_experiment(cert['run_id']) == archive['run']


def test_reconstructed_archive_still_requires_three_original_producers(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-reconstructed', coverage_type='RECONSTRUCTED_HISTORICAL')
    path = archive['job'] / 'capture-summary.json'
    document = json.loads(path.read_bytes())
    document['core_records'][0]['producer_files'] = []
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='ORIGINAL_THREE_PRODUCER_FILES_REQUIRED'):
        authenticate(archive)


def test_dated_fixture_uses_official_sessions_without_changing_product(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-dated', observation_date='2026-09-01',
        seen_at='2026-09-01T12:00:00+00:00', generated_at='2026-09-01T13:00:00+00:00')
    audit = authenticate(archive).audit()
    assert audit['observation_date'] == '2026-09-01' and audit['target_session'] == '2026-09-02'
    assert audit['authenticated_source_count'] == 139


def test_fifo_replacement_during_open_is_nonblocking_and_refused(tmp_path, monkeypatch):
    import os
    if not hasattr(os, 'mkfifo'):
        pytest.skip('OS has no FIFO API')
    path = tmp_path / 'TEST_FIXTURE-original'
    path.write_bytes(b'TEST_FIXTURE')
    original_open = bridge.os.open
    def replaced_open(target, flags):
        path.unlink()
        os.mkfifo(path)
        return original_open(target, flags)
    monkeypatch.setattr(bridge.os, 'open', replaced_open)
    with pytest.raises(ValueError, match='OPENED_FILE_IDENTITY_CHANGED'):
        bridge.ArchiveVerificationSession().read(path)


@pytest.mark.parametrize('mode', ['UNVERIFIABLE', 'HISTORICAL_PROOF'])
def test_unverifiable_input_or_serialized_historical_proof_cannot_be_promoted(archive, mode):
    path = Path(archive['artifacts']['input.json.gz']['path'])
    document = json.loads(gzip.decompress(path.read_bytes()))
    if mode == 'UNVERIFIABLE':
        document['coverage_type'] = 'UNVERIFIABLE_AS_OF'
    else:
        document['receipts'][0]['historical_time_proof'] = dict(policy_id='TEST_FIXTURE_PROOF',
            source_url='https://TEST_FIXTURE.invalid/proof', evidence_hash='a' * 64,
            event_time_basis='SOURCE_DATED_EVENT_RECORD', publication_time_basis='SOURCE_PUBLICATION_TIMESTAMP',
            applies_from='2026-01-01', applies_to='2026-12-31', evidence_refs=['TEST_FIXTURE_REF'],
            verified_at='2026-09-30T12:00:00+00:00')
    path.write_bytes(gzip.compress(json.dumps(document).encode(), mtime=0))
    ref = workflow._ref(path)
    archive['artifacts']['input.json.gz'] = ref
    manifest = json.loads(Path(archive['artifacts']['manifest.json']['path']).read_bytes())
    manifest['artifacts']['input.json.gz'] = ref
    reseal(archive, manifest=manifest)
    with pytest.raises(ValueError, match='EXPLICIT_FIXTURE_REQUIRED|WITHOUT_HISTORICAL_OWNER_REQUIRED'):
        authenticate(archive)


def test_cached_capture_original_producer_can_be_a_distinct_older_checkout(tmp_path):
    import shutil
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-cached', coverage_type='RECONSTRUCTED_HISTORICAL')
    copies = {}
    for ref in archive['items'][0]['producer_files']:
        path = tmp_path / 'TEST_FIXTURE-older-worker' / Path(ref['path']).name
        path.parent.mkdir(exist_ok=True)
        shutil.copyfile(ref['path'], path)
        copies[ref['path']] = workflow._ref(path)
    for item in archive['items']:
        item['producer_files'] = [copies[ref['path']] for ref in item['producer_files']]
    locator = archive['job'] / 'capture-summary.json'
    locator.write_text(json.dumps({'core_records': archive['items']}), encoding='utf-8')
    archive['completion']['validation_references'].extend(copies.values())
    archive['final']['source_reference_count'] = len(archive['completion']['validation_references'])
    reseal(archive)
    cap = authenticate(archive)
    assert cap.audit()['coverage_type'] == 'RECONSTRUCTED_HISTORICAL'
    path = Path(next(iter(copies.values()))['path'])
    path.write_bytes(path.read_bytes() + b'\n# TEST_FIXTURE alteration\n')
    with pytest.raises(ValueError, match='PHYSICAL_IDENTITY'):
        authenticate(archive)


def test_session_capability_cache_has_no_global_strong_reference_leak(archive):
    import gc
    import weakref
    session = bridge.ArchiveVerificationSession()
    cap = authenticate(archive, session=session)
    assert authenticate(archive, session=session) is cap
    session_ref, cap_ref = weakref.ref(session), weakref.ref(cap)
    del session, cap
    gc.collect()
    assert session_ref() is None and cap_ref() is None


def test_immutable_reimport_validates_original_bytes_without_reexpanding_input(archive, service, monkeypatch):
    cap = authenticate(archive)
    cert = bridge.commit_bridge(service, cap)
    original = bridge._decompress
    input_sha = archive['artifacts']['input.json.gz']['sha256']
    def refuse_input(raw, session):
        assert hashlib.sha256(raw).hexdigest() != input_sha, 'REEXPANDED_IMMUTABLE_INPUT'
        return original(raw, session)  # A released run is intentionally reread.
    monkeypatch.setattr(bridge, '_decompress', refuse_input)
    assert bridge.commit_bridge(service, cap) == cert
    assert bridge.require_verified_bridge(service, cert['run_id'], session=cap._session)['input_hash'] == cert['input_hash']
